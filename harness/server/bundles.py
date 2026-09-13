"""Downloadable per-account Compose setup, with a build fallback before publishing."""
import io
import secrets
import zipfile
from pathlib import Path

import yaml

SEARXNG_IMAGE = 'docker.io/searxng/searxng@sha256:2f3d065068121ea3e366104b409d8a16ba10d9eb95a104dd94c63ccaad64cbc3'


def runtime_bundle(pairing, origin, image='', source_root=None):
    repository = Path(source_root or Path(__file__).resolve().parents[2])
    cad = {
        'image': image or 'cadpilot:paired', 'platform': 'linux/amd64', 'init': True, 'restart': 'unless-stopped',
        'command': ['uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '7800', '--no-proxy-headers'],
        'environment': {'CADPILOT_AUTH_ENABLED': '0', 'CADPILOT_NATIVE_OPERATIONS': '1',
            'CADPILOT_PORTAL_URL': origin, 'CADPILOT_INSTANCE_ID': pairing['id'],
            'CADPILOT_PAIRING_FILE': '/run/secrets/pairing_token', 'CADPILOT_SEARXNG_URL': 'http://searxng:8080',
            'CADPILOT_MODEL_BASE_URL': 'http://host.docker.internal:8000/v1'},
        'extra_hosts': ['host.docker.internal:host-gateway'], 'env_file': ['pairing.env'],
        'dns': ['${CADPILOT_DNS_PRIMARY:-1.1.1.1}', '${CADPILOT_DNS_SECONDARY:-1.0.0.1}'],
        'volumes': ['projects:/opt/cadpilot/harness/projects', 'state:/opt/cadpilot/harness/state', 'knowledge:/opt/cadpilot/harness/knowledge'],
        'shm_size': '1gb', 'cap_drop': ['ALL'],
        'security_opt': ['seccomp=unconfined', 'apparmor=unconfined', 'systempaths=unconfined', 'no-new-privileges:true'],
        'depends_on': {'searxng': {'condition': 'service_healthy'}},
        'healthcheck': {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7800/healthz', timeout=3)"], 'interval': '15s', 'timeout': '5s', 'retries': 5}
    }
    if not image:
        cad['build'] = {'context': './source', 'dockerfile': 'harness/docker/Dockerfile'}
    compose = {'name': 'cadpilot-' + pairing['id'][:8], 'services': {'cad': cad, 'searxng': {
        'image': SEARXNG_IMAGE, 'restart': 'unless-stopped',
        'volumes': ['./searxng:/etc/searxng:ro', 'search_cache:/var/cache/searxng'],
        'healthcheck': {'test': ['CMD', 'wget', '-q', '--spider', 'http://127.0.0.1:8080/healthz'], 'interval': '15s', 'timeout': '5s', 'retries': 5}}},
        'volumes': {name: {} for name in ('projects', 'state', 'knowledge', 'search_cache')},
        'x-cadpilot-pairing': 'One-use pairing credential is loaded from the private pairing.env file'}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        def write(name, value):
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100644 if name == 'searxng/settings.yml' else 0o100600) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
        write('compose.yaml', yaml.safe_dump(compose, sort_keys=False))
        write('pairing.env', 'CADPILOT_PAIRING_TOKEN=' + pairing['token'] + '\n')
        write('searxng/settings.yml', yaml.safe_dump({'use_default_settings': True,
            'server': {'secret_key': secrets.token_hex(32), 'limiter': False, 'image_proxy': False}, 'search': {'formats': ['html', 'json']}}))
        command = 'docker compose up -d --build --wait' if not image else 'docker compose pull\ndocker compose up -d --wait'
        write('README.txt', f'CADPilot — your CAD computer\n\nInstall Docker with Compose (Linux x86_64, or Docker Desktop with amd64 support).\nExtract this folder, open a terminal here, and run:\n\n{command}\n\nReturn to {origin}/onboarding to configure your model and enter the studio.\nThe pairing token expires in 24 hours. If a first build takes longer, generate a fresh setup, replace pairing.env, then rerun Compose.\nProjects remain in Docker volumes when the container is stopped or updated.\nStop: docker compose stop\nStart: docker compose up -d\nLogs: docker compose logs --tail=50 cad\nShell: docker compose exec cad bash\nDo not share this folder: it contains your account pairing code.\nNo inbound port or extra tunnel is required on this computer.\n')
        if not image:
            patterns = ['src/cad1000/*.py', 'harness/server/*.py', 'harness/server/*.FCMacro', 'harness/server/*.cfg', 'harness/server/guides/*.md',
                        'harness/web/**/*', 'harness/knowledge/*.md', 'harness/config.yaml', 'harness/requirements-lock.txt',
                        'harness/pi/*.mjs', 'harness/pi/package.json', 'harness/pi/package-lock.json',
                        'harness/docker/Dockerfile', 'harness/docker/entrypoint.sh', 'harness/docker/smoke.py', '.dockerignore']
            for pattern in patterns:
                for path in repository.glob(pattern):
                    if path.is_file() and not path.is_symlink():
                        write('source/' + str(path.relative_to(repository)), path.read_bytes())
    return buffer.getvalue()
