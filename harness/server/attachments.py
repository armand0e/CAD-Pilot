"""Images the model can look at: user uploads, research pictures, drawing pages, saved views.

Everything is re-encoded through PIL to a bounded JPEG so no untrusted bytes reach the
model or the browser as-is.
"""
import hashlib
import io
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


def inspect_image(project_path, name, crop=None):
    path = image_path(project_path, name)
    original = path.with_name(path.name + '.original.png')
    with Image.open(original if original.is_file() and not original.is_symlink() else path) as source:
        image = source.copy()
    width, height = image.size
    if crop:
        left, top, right, bottom = crop
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError(f'Crop must fit within {width} x {height} pixels')
        image = image.crop(crop)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    stored = store_image(Path(project_path) / 'research-images', buffer.getvalue(), label=f'Inspection of {name}; crop={crop}')
    return stored | {'path': str(image_path(project_path, stored['id'])), 'original_width': width, 'original_height': height}
