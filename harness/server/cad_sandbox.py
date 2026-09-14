"""Execute project programs with no network, home, credentials or other projects."""
import asyncio
import os
from pathlib import Path
import shutil
import signal

ROOT = Path(__file__).resolve().parents[1]


async def execute(directory, command, *, timeout=None, helpers=()):
    runtime = ROOT / 'apps/freecad-extracted'
    if not shutil.which('bwrap') or not (runtime / 'usr/bin/python').is_file():
        raise ValueError('CAD execution needs bubblewrap and the bundled FreeCAD runtime')
    args = ['prlimit', '--as=8589934592', '--fsize=67108864', '--',
            'bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
            '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
            '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--ro-bind', '/etc/passwd', '/etc/passwd', '--ro-bind', '/etc/group', '/etc/group',
            '--ro-bind', str(runtime), '/opt/cad', '--bind', str(directory), '/work', '--chdir', '/work',
            '--setenv', 'PATH', '/opt/cad/usr/bin:/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
            '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'QT_QPA_PLATFORM', 'offscreen',
            '--setenv', 'OMP_NUM_THREADS', '1', '--setenv', 'OPENBLAS_NUM_THREADS', '1',
            '--setenv', 'PYTHONHOME', '/opt/cad/usr', '--setenv', 'PYTHONPATH', '/opt/cad/usr/lib:/']
    for name in helpers:
        args += ['--ro-bind', str(ROOT / 'server' / name), '/' + name]
    args += ['--ro-bind', str(ROOT / 'server/cad_paths.py'), '/cad_paths.py', '--ro-bind', str(ROOT / 'server/svg_path.py'), '/svg_path.py']
    scad = ROOT / 'apps/openscad-extracted'
    if (scad / 'AppRun').is_file():
        args += ['--ro-bind', str(scad), '/opt/scad']
    args += command
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    output = bytearray()
    omitted = 0
    try:
        async with asyncio.timeout(timeout):
            while chunk := await proc.stdout.read(65536):
                output.extend(chunk)
                if len(output) > 1024 * 1024:
                    extra = len(output) - 1024 * 1024
                    del output[:extra]
                    omitted += extra
            await proc.wait()
    finally:
        # Kill background descendants too, including a shell that exited early.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if proc.returncode is None:
            await proc.wait()
    text = output.decode(errors='replace')
    if omitted:
        text = f'[Earlier {omitted} output bytes omitted]\n' + text
    return {'exitCode': proc.returncode, 'output': text}
