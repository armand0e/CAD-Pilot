"""One-shot, explicitly allowlisted v0/v1/v2 generated-data cleanup.

Never touches raw sources, v3 outputs/evidence, checkpoints or CAD projects.
Dry run by default. --execute archives small metadata, then permanently removes
only the listed obsolete derived directories. They can be rebuilt from sources.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
TARGETS = (
    'data/shards',
    'data/processed/corpus-v0', 'data/processed/corpus-v1', 'data/processed/corpus-v2',
    'data/processed/policy-v0', 'data/processed/policy-v1', 'data/processed/policy-v2',
    'data/processed/trajectory-v0-64k', 'data/processed/trajectory-v1-64k', 'data/processed/trajectory-v2-64k',
    'data/processed/smoke-v1', 'data/processed/smoke-v1-policy', 'data/processed/policy-smoke',
    'data/processed/sample-v1', 'data/processed/sample-v2',
)


def targets():
    found = []
    for relative in TARGETS:
        target = REPO / relative
        if not target.exists():
            continue
        if target.is_symlink() or not target.is_dir() or target.resolve() != target:
            raise ValueError(f'Not an exact real allowlisted directory: {target}')
        found.append(target)
    return found


def check_idle(paths):
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            links = [proc / 'cwd', *list((proc / 'fd').iterdir())]
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue
        for link in links:
            try:
                value = os.readlink(link).removesuffix(' (deleted)')
            except (OSError, ProcessLookupError):
                continue
            if any(value == str(path) or value.startswith(str(path) + '/') for path in paths):
                raise ValueError(f'Cleanup target is in use by PID {proc.name}: {value}')


def snapshot(paths):
    entries, seen = [], set()
    for path in paths:
        count = size = 0
        for child in path.rglob('*'):
            if child.is_symlink() or not child.is_file():
                continue
            stat = child.stat()
            count += 1
            identity = stat.st_dev, stat.st_ino
            if identity not in seen:
                size += stat.st_blocks * 512
                seen.add(identity)
        entries.append({'path': str(path.relative_to(REPO)), 'files': count, 'allocated_bytes': size})
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    paths = targets()
    check_idle(paths)
    entries = snapshot(paths)
    print(json.dumps({'targets': entries, 'allocated_gib': sum(e['allocated_bytes'] for e in entries) / 2**30,
                      'execute': args.execute}), flush=True)
    if not args.execute:
        return
    output = Path(tempfile.mkdtemp(prefix='obsolete-data-cleanup-', dir=REPO / 'runs'))
    free_before = shutil.disk_usage(REPO).free
    metadata = []
    archive = output / 'metadata.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in paths:
            for child in path.rglob('*'):
                if child.is_symlink() or not child.is_file() or child.suffix not in ('.json', '.md', '.yaml') or child.stat().st_size > 2 * 1024**2:
                    continue
                relative = str(child.relative_to(REPO))
                digest = hashlib.sha256(child.read_bytes()).hexdigest()
                tar.add(child, arcname=relative, recursive=False)
                metadata.append({'path': relative, 'sha256': digest})
    # Verify the retained evidence before any irreversible removal.
    expected = {item['path']: item['sha256'] for item in metadata}
    with tarfile.open(archive, 'r:gz') as tar:
        actual = {}
        for member in tar:
            if not member.isfile():
                raise ValueError(f'Unexpected metadata archive entry: {member.name}')
            with tar.extractfile(member) as handle:
                actual[member.name] = hashlib.sha256(handle.read()).hexdigest()
        if actual != expected:
            raise ValueError('Metadata archive did not match source hashes; no data removed')
    report = {'started': time.time(), 'targets': entries, 'metadata': metadata, 'metadata_archive': archive.name,
              'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'removed': [],
              'recovery': 'Generated data permanently removed; regenerate from retained raw/pinned sources. Metadata archive is not a full dataset backup.',
              'free_bytes_before': free_before}
    record = output / 'cleanup.json'
    record.write_text(json.dumps(report, indent=2))
    check_idle(paths)
    # Refuse a changed/symlinked target after metadata collection.
    if targets() != paths:
        raise ValueError('Cleanup target inventory changed')
    for target in paths:
        if target.is_symlink() or target.resolve() != target:
            raise ValueError(f'Target changed: {target}')
        shutil.rmtree(target)
        report['removed'].append(str(target.relative_to(REPO)))
        record.write_text(json.dumps(report, indent=2))
        print('Removed obsolete derived data: ' + str(target.relative_to(REPO)), flush=True)
    report.update(completed=time.time(), free_bytes_after=shutil.disk_usage(REPO).free)
    report['net_freed_gib'] = (report['free_bytes_after'] - free_before) / 2**30
    record.write_text(json.dumps(report, indent=2))
    print(json.dumps({'report': str(record), 'net_freed_gib': report['net_freed_gib']}), flush=True)


if __name__ == '__main__':
    main()
