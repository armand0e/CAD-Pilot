"""Repair one explicitly named, pinned source cache file; never touch shards or training.

Kept outside the frozen builder fingerprint. Run only while the data chain is stopped.
The old cache file is quarantined only AFTER a verified replacement has downloaded.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from cad1000.hf import DEFAULT_REPO, DEFAULT_REVISION, download_file, list_tree


def valid(path, entry):
    if not path.is_file() or path.stat().st_size != entry['size']:
        return False
    digest = hashlib.sha256() if entry.get('lfs') else hashlib.sha1(f"blob {entry['size']}\0".encode())
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == (entry['lfs']['oid'] if entry.get('lfs') else entry['oid'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Exact app/workflow/filename in the v3 source cache')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9_-]+/[a-f0-9-]{36}/(?:clip\.mp4|events\.json|metadata\.json|task_desc\.json|rubrics\.json|narration\.json)', args.source):
        parser.error('Expected a single known source filename and workflow UUID')
    cache = ROOT / 'data/raw-v3-full-cache'
    target = cache / args.source
    if any(p.is_symlink() for p in (cache, target.parent.parent, target.parent, target)):
        raise ValueError('Refusing symlinked cache paths')
    run = ROOT / 'runs/v3-data-only-20260908'
    with (run / 'chain.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        entry = next(e for e in list_tree(DEFAULT_REPO, DEFAULT_REVISION, str(Path(args.source).parent)) if e['path'] == args.source)
        if valid(target, entry):
            print('Already matches pinned size and digest; no changes', flush=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.repair-', dir=target.parent) as directory:
            replacement = Path(directory) / target.name
            for attempt in range(3):
                print(f'Downloading pinned {args.source}, attempt {attempt + 1}; expected {entry["size"]} bytes', flush=True)
                download_file(DEFAULT_REPO, DEFAULT_REVISION, args.source, replacement)
                if valid(replacement, entry):
                    break
                if attempt == 2:
                    raise ValueError('Replacement failed pinned size/digest checks; original cache preserved')
            archive = Path(tempfile.mkdtemp(prefix='cache-repair-', dir=run))
            receipt = {'source': args.source, 'revision': DEFAULT_REVISION, 'entry': entry, 'time': time.time(),
                       'old_size': target.stat().st_size if target.exists() else None, 'training_started': False}
            (archive / 'receipt.json').write_text(json.dumps(receipt, indent=2))
            if target.exists():
                target.rename(archive / target.name)
            os.replace(replacement, target)
            print(f'Pinned size/digest verified. Original cache quarantined at {archive}. Shards unchanged.', flush=True)


if __name__ == '__main__':
    main()
