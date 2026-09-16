"""Execute project programs with no network, home, credentials or other projects."""
import asyncio
import os
from pathlib import Path
import shutil
import signal

ROOT = Path(__file__).resolve().parents[1]


async def execute(directory, command, *, timeout=None, helpers=(), images=()):
    """Run a command in the sandbox; `images` are (host directory, name) pairs mounted read-only at /work/images/<name>."""
    runtime = ROOT / 'apps/freecad-extracted'
    if not shutil.which('bwrap') or not (runtime / 'usr/bin/python').is_file():
        raise ValueError('CAD execution needs bubblewrap and the bundled FreeCAD runtime')
    # Dense organic/character meshes (e.g. an MB-Lab figure) produce large intermediate files and
    # need memory headroom: 512 MB max file, 12 GiB address space (was 64 MB / 8 GiB, which failed
    # character builds with "File too large" and forced the model to decimate).
    args = ['prlimit', '--as=12884901888', '--fsize=536870912', '--',
            'bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
            '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
            '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--ro-bind', '/etc/passwd', '/etc/passwd', '--ro-bind', '/etc/group', '/etc/group',
            '--ro-bind', str(runtime), '/opt/cad', '--bind', str(directory), '/work', '--chdir', '/work',
            # Models type python3; the bundled FreeCAD Python (numpy, PIL) answers to both names.
            '--tmpfs', '/cadbin', '--symlink', '/opt/cad/usr/bin/python', '/cadbin/python3',
            '--setenv', 'PATH', '/cadbin:/opt/cad/usr/bin:/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
            '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'QT_QPA_PLATFORM', 'offscreen',
            '--setenv', 'OMP_NUM_THREADS', '1', '--setenv', 'OPENBLAS_NUM_THREADS', '1',
            '--setenv', 'PYTHONHOME', '/opt/cad/usr', '--setenv', 'PYTHONPATH', '/opt/cad/usr/lib:/']
    for name in helpers:
        args += ['--ro-bind', str(ROOT / 'server' / name), '/' + name]
    args += ['--ro-bind', str(ROOT / 'server/cad_paths.py'), '/cad_paths.py', '--ro-bind', str(ROOT / 'server/svg_path.py'), '/svg_path.py']
    for host_dir, name in images:
        if Path(host_dir).is_dir():
            args += ['--ro-bind', str(host_dir), '/work/images/' + name]
    scad = ROOT / 'apps/openscad-extracted'
    if (scad / 'AppRun').is_file():
        args += ['--ro-bind', str(scad), '/opt/scad']
    blender = ROOT / 'apps/blender-extracted'
    if (blender / 'blender').is_file():
        args += ['--ro-bind', str(blender), '/opt/blender']
        # MB-Lab (parametric human/anime bases) as a Blender addon: expose it via a dedicated
        # BLENDER_USER_SCRIPTS dir so `bpy.ops.mbast.*` is available to model.bpy in the sandbox.
        mblab = ROOT / 'apps/mblab'
        if (mblab / 'addons/MB_Lab/__init__.py').is_file():
            args += ['--ro-bind', str(mblab), '/opt/mblab', '--setenv', 'BLENDER_USER_SCRIPTS', '/opt/mblab']
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
