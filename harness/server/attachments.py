"""Images the model can look at: user uploads, research pictures, drawing pages, saved views.

Everything is re-encoded through PIL to a bounded JPEG so no untrusted bytes reach the
model or the browser as-is.
"""
import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageOps

NAME = re.compile(r'[a-f0-9]{16}\.jpg\Z')
MAX_SIDE = 2000


def store_image(directory, content, label='', max_side=MAX_SIDE):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Attachment directory is a symlink')
    directory.mkdir(exist_ok=True, mode=0o700)
    try:
        image = Image.open(io.BytesIO(content))
        if image.width * image.height > 40_000_000:
            raise ValueError('Image is too large')
        image.load()
    except Exception:
        raise ValueError('Not a readable image (PNG/JPEG/WebP/GIF)') from None
    if image.width * image.height > 40_000_000:
        raise ValueError('Image is too large')
    image = ImageOps.exif_transpose(image).convert('RGB')
    original = image.copy()
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=92, optimize=True)
    data = buffer.getvalue()
    name = hashlib.sha256(str(original.size).encode() + original.tobytes()).hexdigest()[:16] + '.jpg'
    path = directory / name
    if not path.exists():
        pending = directory / (name + '.pending')
        pending.write_bytes(data)
        pending.replace(path)
    # Retain decoded full resolution pixels for later inspection/cropping, without
    # retaining executable metadata or trusting the uploaded file's extension.
    original_path = directory / (name + '.original.png')
    if not original_path.exists():
        original.save(original_path, 'PNG')
    return {'id': name, 'label': str(label)[:120], 'width': image.width, 'height': image.height, 'bytes': len(data)}


def image_path(project_path, name):
    if isinstance(name, str) and (cad := re.fullmatch(r'cad:(r[0-9]{4}):(iso|top|front|right)', name)):
        from .projects import Project
        root = Path(project_path)
        return Project(root.parent, root.name).file(cad[1], f'view-{cad[2]}.png')
    if isinstance(name, str) and (archived := re.fullmatch(r'context:([a-f0-9]{64})', name)):
        root = Path(project_path) / 'pi'
        path = root / 'images' / archived[1]
        if root.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError('No such archived image')
        if hashlib.sha256(path.read_bytes()).hexdigest() != archived[1]:
            raise ValueError('Archived image integrity check failed')
        return path
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('Unknown image')
    for folder in ('attachments', 'research-images'):
        path = Path(project_path) / folder / name
        if path.is_file() and not path.is_symlink():
            return path
    raise ValueError('Unknown image')


def image_part(path):
    """Use the actual image format, including PNG CAD renders."""
    import base64
    data = Path(path).read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME[image.format]
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,' + base64.b64encode(data).decode()}}


def saved_image_ids(project_path):
    """Discover originals even after Pi compaction or a worker restart."""
    root = Path(project_path)
    ids = set()
    for folder in ('attachments', 'research-images'):
        directory = root / folder
        if directory.is_dir() and not directory.is_symlink():
            ids.update(path.name for path in directory.iterdir() if NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink())
    directory = root / 'pi/images'
    if directory.is_dir() and not directory.is_symlink() and not directory.parent.is_symlink():
        ids.update('context:' + path.name for path in directory.iterdir()
                   if re.fullmatch(r'[a-f0-9]{64}', path.name) and path.is_file() and not path.is_symlink())
    return sorted(ids)


def inspect_image(project_path, name, crop=None):
    path = image_path(project_path, name)
    original = path.with_name(path.name + '.original.png')
    with Image.open(path) as shown:
        shown_size = shown.size
    with Image.open(original if original.is_file() and not original.is_symlink() else path) as source:
        image = source.copy()
    width, height = image.size
    if crop:
        # The model chooses pixels in the picture it was shown, which may be a
        # downscaled copy of the stored original: scale into the original and clamp.
        sx, sy = width / max(1, shown_size[0]), height / max(1, shown_size[1])
        left, top, right, bottom = [int(round(v * f)) for v, f in zip(crop, (sx, sy, sx, sy))]
        left, top = max(0, min(left, width - 1)), max(0, min(top, height - 1))
        right, bottom = max(left + 1, min(right, width)), max(top + 1, min(bottom, height))
        if right - left < 2 or bottom - top < 2 or crop[2] <= crop[0] or crop[3] <= crop[1]:
            raise ValueError(f'Crop must be [left, top, right, bottom] with right > left and bottom > top, within {shown_size[0]} x {shown_size[1]} pixels as shown')
        crop = [left, top, right, bottom]
        image = image.crop(crop)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    stored = store_image(Path(project_path) / 'research-images', buffer.getvalue(), label=f'Inspection of {name}; crop={crop}')
    provenance = image_provenance(project_path, name)
    if crop:
        provenance = {**provenance, 'crops': provenance['crops'] + [list(crop)]}
    if stored['id'] != name:
        from .projects import atomic_json
        atomic_json(Path(project_path) / 'research-images' / (stored['id'] + '.provenance.json'), provenance)
    return stored | {'path': str(image_path(project_path, stored['id'])), 'source_id': name, 'crop': crop,
                     'original_width': width, 'original_height': height, 'shown_width': shown_size[0], 'shown_height': shown_size[1],
                     'provenance': provenance}


def image_provenance(project_path, name):
    """Keep crop coordinates relative to each preceding image, through restarts."""
    path = image_path(project_path, name)
    metadata = path.with_name(path.name + '.provenance.json')
    if metadata.is_file() and not metadata.is_symlink():
        return json.loads(metadata.read_text())
    return {'original_image_id': name, 'crops': []}
