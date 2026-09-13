import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from server.distribution import connection_command, connection_commands

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = 'a' * 32
TOKEN = 'test_pairing_' + 'b' * 32


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cad install ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        CADPILOT_INSTALL_DIR=str(self.root / 'CAD Pilot'),
                        CAD_TEST_LOG=str(self.root / 'calls.jsonl'))
        self.script('docker', '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CAD_TEST_LOG'], 'a') as out:
    out.write(json.dumps({'args': sys.argv[1:], 'workspace': os.environ.get('CADPILOT_WORKSPACE_DIR')}) + '\\n')
''')

    def script(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def checkout(self):
        repo = Path(self.env['CADPILOT_INSTALL_DIR'])
        (repo / 'harness').mkdir(parents=True)
        shutil.copyfile(ROOT / 'harness/connect.sh', repo / 'harness/connect.sh')
        shutil.copyfile(ROOT / 'compose.worker.yaml', repo / 'compose.worker.yaml')
        return repo

    def run_connect(self, token=TOKEN, origin='https://cad.armand0e.com'):
        repo = Path(self.env['CADPILOT_INSTALL_DIR'])
        return subprocess.run(['bash', str(repo / 'harness/connect.sh'), origin, IDENTITY, token],
                              env=self.env, text=True, capture_output=True)

    def test_first_install_repair_preserves_search_settings_and_volume_names(self):
        repo = self.checkout()
        result = self.run_connect()
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = repo / 'harness/.docker/workers' / IDENTITY
        config = workspace / 'worker.env'
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
        settings = (workspace / 'searxng/settings.yml').read_text()
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        with config.open('a') as handle:
            handle.write('CADPILOT_DNS_PRIMARY=9.9.9.9\n')
        result = self.run_connect(token='fresh_' + 'c' * 32)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(TOKEN, config.read_text())
        self.assertIn('CADPILOT_DNS_PRIMARY=9.9.9.9', config.read_text())
        self.assertEqual((workspace / 'searxng/settings.yml').read_text(), settings)
        calls = [json.loads(line) for line in Path(self.env['CAD_TEST_LOG']).read_text().splitlines()]
        starts = [call for call in calls if 'up' in call['args']]
        self.assertEqual(len(starts), 2)
        for call in starts:
            self.assertEqual(call['workspace'], str(workspace))
            self.assertEqual(call['args'][call['args'].index('-p') + 1], 'cadpilot-' + IDENTITY[:8])
            self.assertNotIn(TOKEN, str(call))

    def test_invalid_pairing_is_rejected_without_starting_docker(self):
        self.checkout()
        for origin, token in [('https://cad.example/$(touch bad)', TOKEN),
                              ('http://remote.example', TOKEN), ('https://cad.example', 'x\nINJECT=1')]:
            result = self.run_connect(origin=origin, token=token)
            self.assertEqual(result.returncode, 2)
        self.assertFalse(Path(self.env['CAD_TEST_LOG']).exists())

    def test_download_failure_does_not_execute_installer(self):
        self.script('curl', '#!/bin/sh\necho "touch \'$CADPILOT_INSTALL_DIR\'"\nexit 22\n')
        command = connection_command({'id': IDENTITY, 'token': TOKEN}, 'https://cad.armand0e.com')
        result = subprocess.run(['bash', '-c', command], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path(self.env['CADPILOT_INSTALL_DIR']).exists())

    def test_download_never_receives_pairing_and_arguments_are_literal(self):
        self.script('curl', '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CAD_TEST_LOG'], 'w') as out: json.dump(sys.argv[1:], out)
print('printf "%s\\\\n" "$@"')
''')
        origin = 'https://cad.example/$(touch should-not-exist)'
        command = connection_command({'id': IDENTITY, 'token': TOKEN}, origin)
        result = subprocess.run(['bash', '-c', command], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [origin, IDENTITY, TOKEN])
        self.assertNotIn(TOKEN, Path(self.env['CAD_TEST_LOG']).read_text())

    @unittest.skipUnless(os.environ.get('CADPILOT_TEST_PWSH') or shutil.which('pwsh'), 'PowerShell is optional for cross-platform command checks')
    def test_powershell_passes_the_installer_and_literal_pairing_arguments_to_wsl(self):
        self.script('wsl.exe', '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CAD_TEST_LOG'], 'w') as out:
    json.dump({'args': sys.argv[1:], 'stdin': sys.stdin.read()}, out)
''')
        origin = "https://cad.example/quote'$(literal)"
        command = connection_commands({'id': IDENTITY, 'token': TOKEN}, origin)['windows']
        source = "function Invoke-WebRequest { [pscustomobject]@{ Content = '# ASCII installer fixture' } }\n" + command
        script = self.root / 'check.ps1'
        script.write_text(source)
        result = subprocess.run([os.environ.get('CADPILOT_TEST_PWSH') or shutil.which('pwsh'),
                                 '-NoProfile', '-NonInteractive', '-File', str(script)],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        received = json.loads(Path(self.env['CAD_TEST_LOG']).read_text())
        self.assertEqual(received['args'], ['--exec', 'bash', '-s', '--', origin, IDENTITY, TOKEN])
        self.assertEqual(received['stdin'].strip(), '# ASCII installer fixture')
        self.assertTrue((ROOT / 'install.sh').read_bytes().isascii())
        self.assertNotIn(b'\r', (ROOT / 'install.sh').read_bytes())

    def test_existing_unrelated_repository_is_not_modified(self):
        self.checkout()
        self.script('git', '#!/bin/sh\nprintf "%s\\n" https://example.test/unrelated.git\n')
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), 'https://cad.armand0e.com', IDENTITY, TOKEN],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('another repository', result.stderr)
        self.assertFalse((Path(self.env['CADPILOT_INSTALL_DIR']) / 'harness/.docker').exists())

    def test_worker_compose_has_no_published_ports_and_matches_bundle_services(self):
        import io
        import zipfile
        import yaml
        from server.bundles import runtime_bundle, SEARXNG_IMAGE
        compose = yaml.safe_load((ROOT / 'compose.worker.yaml').read_text())
        with zipfile.ZipFile(io.BytesIO(runtime_bundle({'id': IDENTITY, 'token': TOKEN}, 'https://cad.example', 'cadpilot:paired'))) as archive:
            bundle = yaml.safe_load(archive.read('compose.yaml'))
        for name, service in compose['services'].items():
            self.assertNotIn('ports', service)
            self.assertEqual(service['image'], bundle['services'][name]['image'])
        self.assertEqual(compose['services']['searxng']['image'], SEARXNG_IMAGE)
        self.assertEqual(compose['services']['cad']['volumes'], bundle['services']['cad']['volumes'])
        self.assertEqual(compose['services']['cad']['security_opt'], bundle['services']['cad']['security_opt'])

    def test_source_bundle_contains_installed_workspace_resources(self):
        import io
        import zipfile
        from server.bundles import runtime_bundle
        from server.source_workspace import SUPPLIED_FILES
        with zipfile.ZipFile(io.BytesIO(runtime_bundle({'id': IDENTITY, 'token': TOKEN}, 'https://cad.example'))) as archive:
            for path in SUPPLIED_FILES.values():
                self.assertEqual(archive.read('source/' + path.relative_to(ROOT).as_posix()), path.read_bytes())
            self.assertNotIn('source/harness/knowledge/source-workspace.md', archive.namelist())
