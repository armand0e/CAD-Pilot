#!/usr/bin/env bash
set -euo pipefail
script_path="$(cd "$(dirname "$0")" && pwd)/docker.sh"
cd "$(dirname "$script_path")/.."
compose=(docker compose --env-file harness/.docker/compose.env -f compose.yaml)
initialize() {
  python3 - <<'PY'
import os
import secrets
from pathlib import Path
root = Path('harness/.docker')
root.mkdir(mode=0o700, exist_ok=True)
(root / 'searxng').mkdir(mode=0o755, exist_ok=True)
files = {
    root / 'owner-password': (secrets.token_urlsafe(24) + '\n', 0o600),
    root / 'compose.env': ('# Local Docker settings; overrides persist across builds.\nCADPILOT_PORT=7801\nCADPILOT_OWNER=admin\n', 0o600),
    root / 'searxng/settings.yml': (
        'use_default_settings: true\nserver:\n  secret_key: ' + secrets.token_hex(32) +
        '\n  limiter: false\n  image_proxy: false\nsearch:\n  formats: [html, json]\n'
        # Suspended engines return within a run (defaults: an hour after a CAPTCHA, longer for Cloudflare).
        + '  suspended_times:\n' + ''.join(f'    {key}: {seconds}\n' for key, seconds in (
            ('SearxEngineAccessDenied', 60), ('SearxEngineCaptcha', 300), ('SearxEngineTooManyRequests', 60),
            ('cf_SearxEngineCaptcha', 600), ('cf_SearxEngineAccessDenied', 300), ('recaptcha_SearxEngineCaptcha', 600)))
        # Beyond the default brave/duckduckgo/google cse, which public rate limits suspend together.
        + 'engines:\n' + ''.join(f'  - name: {name}\n    disabled: false\n' for name in ('bing', 'qwant', 'mojeek', 'yahoo', 'yep')), 0o644),
}
for path, (body, mode) in files.items():
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        continue
    with os.fdopen(fd, 'w') as output:
        output.write(body)
PY
}
action=${1:-up}
if [ "$#" -gt 0 ]; then shift; fi
case "$action" in
  portal-up) initialize; docker compose --env-file harness/.docker/compose.env -f compose.portal.yaml up -d --build --wait "$@" ;;
  portal-logs) docker compose -f compose.portal.yaml logs --tail=100 -f "$@" ;;
  portal-stop) docker compose -f compose.portal.yaml stop "$@" ;;
  init) initialize ;;
  build) initialize; "${compose[@]}" build "$@" ;;
  up) initialize; "${compose[@]}" up -d --build --wait "$@"; "$script_path" url ;;
  connect|shell) "${compose[@]}" exec cad bash "$@" ;;
  url) python3 - <<'PY'
import os
from pathlib import Path
values = dict(line.split('=', 1) for line in Path('harness/.docker/compose.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
print('CADPilot: http://127.0.0.1:' + os.environ.get('CADPILOT_PORT', values.get('CADPILOT_PORT', '7801')))
print('Login: ' + os.environ.get('CADPILOT_OWNER', values.get('CADPILOT_OWNER', 'admin')) + '; initial password: harness/.docker/owner-password')
PY
    ;;
  reset-password) "${compose[@]}" exec cad python -m server.auth reset ;;
  check) "${compose[@]}" exec -T cad python docker/smoke.py ;;
  logs) "${compose[@]}" logs --tail=100 -f "$@" ;;
  status) "${compose[@]}" ps "$@" ;;
  stop) "${compose[@]}" stop "$@" ;;
  down) "${compose[@]}" down "$@" ;;
  *) echo 'Usage: harness/docker.sh {init|build|up|connect|check|url|reset-password|logs|status|stop|down|portal-up|portal-logs|portal-stop}' >&2; exit 2 ;;
esac
