#!/usr/bin/env bash
# Pair a GitHub checkout with the account that generated the onboarding command.
set -euo pipefail
umask 077
if [ "$#" -ne 3 ]; then
  echo 'Open your CADPilot account → Connect computer → Generate connection command.' >&2
  echo 'Usage: harness/connect.sh PORTAL_URL INSTANCE_ID PAIRING_TOKEN' >&2
  exit 2
fi
portal_url=${1%/}
instance_id=$2
pairing_token=$3
if ! [[ "$portal_url" =~ ^https://[a-zA-Z0-9.-]+(:[0-9]+)?$ || "$portal_url" =~ ^http://(localhost|127\.0\.0\.1|host\.docker\.internal)(:[0-9]+)?$ ]]; then
  echo 'Expected an HTTPS portal origin (local HTTP is supported for development).' >&2
  exit 2
fi
if ! [[ "$instance_id" =~ ^[a-f0-9]{32}$ && "$pairing_token" =~ ^[a-zA-Z0-9_-]{20,200}$ ]]; then
  echo 'Invalid pairing details. Generate a fresh command in onboarding.' >&2
  exit 2
fi
command -v docker >/dev/null || { echo 'Install Docker with Compose first, then rerun this command.' >&2; exit 1; }
docker compose version >/dev/null
docker info >/dev/null
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workspace_dir="$repo_dir/harness/.docker/workers/$instance_id"
mkdir -p "$workspace_dir/searxng"
chmod 700 "$workspace_dir"
chmod 755 "$workspace_dir/searxng"
pending_env=$(mktemp "$workspace_dir/worker.env.XXXXXX")
trap 'rm -f "$pending_env"' EXIT
{
  printf 'CADPILOT_PORTAL_URL=%s\nCADPILOT_INSTANCE_ID=%s\nCADPILOT_PAIRING_TOKEN=%s\n' \
    "$portal_url" "$instance_id" "$pairing_token"
  # Keep network overrides across source updates and account re-pairing, without
  # sourcing the dotenv file as shell code.
  if [ -f "$workspace_dir/worker.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in CADPILOT_DNS_PRIMARY=*|CADPILOT_DNS_SECONDARY=*) printf '%s\n' "$line" ;; esac
    done < "$workspace_dir/worker.env"
  fi
} > "$pending_env"
mv "$pending_env" "$workspace_dir/worker.env"
if [ ! -f "$workspace_dir/searxng/settings.yml" ]; then
  search_secret=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  printf 'use_default_settings: true\nserver:\n  secret_key: %s\n  limiter: false\n  image_proxy: false\nsearch:\n  formats: [html, json]\n' \
    "$search_secret" > "$workspace_dir/searxng/settings.yml"
  # Suspended engines return within a run (defaults: an hour after a CAPTCHA, longer for Cloudflare).
  printf '  suspended_times:\n' >> "$workspace_dir/searxng/settings.yml"
  for pair in SearxEngineAccessDenied:60 SearxEngineCaptcha:300 SearxEngineTooManyRequests:60 \
              cf_SearxEngineCaptcha:600 cf_SearxEngineAccessDenied:300 recaptcha_SearxEngineCaptcha:600; do
    printf '    %s: %s\n' "${pair%%:*}" "${pair##*:}" >> "$workspace_dir/searxng/settings.yml"
  done
  # Beyond the default brave/duckduckgo/google cse, which public rate limits suspend together.
  printf 'engines:\n' >> "$workspace_dir/searxng/settings.yml"
  for engine in bing startpage qwant mojeek yahoo yep; do
    printf '  - name: %s\n    disabled: false\n' "$engine" >> "$workspace_dir/searxng/settings.yml"
  done
fi
chmod 644 "$workspace_dir/searxng/settings.yml"
# Stable project names also preserve volumes from the earlier ZIP installer.
{
  printf '#!/usr/bin/env bash\nset -euo pipefail\n'
  printf 'export CADPILOT_WORKSPACE_DIR=%q\n' "$workspace_dir"
  printf 'exec docker compose --project-directory %q --env-file %q -f %q -p %q "$@"\n' \
    "$repo_dir" "$workspace_dir/worker.env" "$repo_dir/compose.worker.yaml" "cadpilot-${instance_id:0:8}"
} > "$workspace_dir/cadpilot"
chmod 700 "$workspace_dir/cadpilot"
echo 'Building FreeCAD, OpenSCAD and Pi from the GitHub source; starting SearXNG…'
echo 'The first build downloads several GB. Docker caches completed layers for retries.'
if ! "$workspace_dir/cadpilot" up -d --build --wait; then
  echo 'Setup has not connected. Recent CAD logs:' >&2
  "$workspace_dir/cadpilot" logs --tail=30 cad >&2 || true
  printf 'Check connection details: %q exec -T cad python -m server.connector\n' "$workspace_dir/cadpilot" >&2
  exit 1
fi
printf '\nCAD services started. Return to %s/onboarding to check the connection and choose your model.\n' "$portal_url"
printf 'Workspace commands: %q {logs --tail=100 cad | stop | up -d | exec cad bash}\n' "$workspace_dir/cadpilot"
echo 'If onboarding stays disconnected, check the logs or generate a fresh connection command.'
