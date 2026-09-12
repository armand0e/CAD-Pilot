#!/usr/bin/env bash
set -euo pipefail
pi_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
node_dir="$pi_dir/../.node"
if ! command -v node >/dev/null; then
  if [[ ! -x "$node_dir/bin/node" ]]; then
    case "$(uname -s)-$(uname -m)" in
      Linux-x86_64) platform=linux-x64 ;;
      Linux-aarch64) platform=linux-arm64 ;;
      Darwin-arm64) platform=darwin-arm64 ;;
      Darwin-x86_64) platform=darwin-x64 ;;
      *) echo 'Install Node >=22.19, then rerun this command.' >&2; exit 1 ;;
    esac
    version=v24.21.0
    archive="node-$version-$platform.tar.xz"
    tmp="$(mktemp -d)"
    trap 'rm -rf -- "$tmp"' EXIT
    curl -fL --retry 3 "https://nodejs.org/dist/$version/$archive" -o "$tmp/$archive"
    curl -fL --retry 3 "https://nodejs.org/dist/$version/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
    python3 - "$tmp" "$archive" <<'PY'
import hashlib, pathlib, sys
root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
expected = next(line.split()[0] for line in (root / 'SHASUMS256.txt').read_text().splitlines() if line.endswith('  ' + name))
if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
    raise SystemExit('Node download checksum mismatch')
PY
    mkdir -p "$node_dir"
    tar -xJf "$tmp/$archive" --strip-components=1 -C "$node_dir"
  fi
  export PATH="$node_dir/bin:$PATH"
fi
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22 || (a===22 && b<19)) process.exit(1)' || { echo 'Pi needs Node >=22.19.' >&2; exit 1; }
npm ci --prefix "$pi_dir" --omit=dev --ignore-scripts --no-audit --no-fund
