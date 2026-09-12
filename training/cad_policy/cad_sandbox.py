"""Read-only application sandbox; writable trial files only, no network or user home."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def sandbox_command(app: str, work: Path, *, display: str | None = None, extra_binds=()):
    if app not in {"freecad", "openscad"}:
        raise ValueError(app)
    root = REPO / "harness/apps" / f"{app}-extracted"
    command = ["prlimit", "--as=4294967296", "--cpu=720", "--", "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
               "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
               "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
               "--dir", "/home/cad", "--setenv", "HOME", "/home/cad", "--setenv", "PATH", "/usr/bin:/bin",
               "--setenv", "LANG", "C.UTF-8", "--setenv", "QT_QPA_PLATFORM", "offscreen",
               "--setenv", "LIBGL_ALWAYS_SOFTWARE", "1", "--setenv", "LP_NUM_THREADS", "2", "--setenv", "OMP_NUM_THREADS", "2",
               "--ro-bind", str(root), "/opt/cad", "--bind", str(work.resolve()), "/work", "--chdir", "/work"]
    if os.getuid() != 1000:
        raise RuntimeError("Sandbox passwd mapping must be provisioned for this UID")
    command.extend(["--ro-bind", str(REPO / "training/sandbox/passwd"), "/etc/passwd",
                    "--ro-bind", str(REPO / "training/sandbox/group"), "/etc/group"])
    if Path("/etc/fonts").exists():
        command.extend(["--ro-bind", "/etc/fonts", "/etc/fonts"])
    if display:
        if not display.startswith(":") or not display[1:].isdigit():
            raise ValueError("Only a dedicated local display is allowed")
        socket = f"/tmp/.X11-unix/X{display[1:]}"
        command.extend(["--ro-bind", socket, socket, "--setenv", "DISPLAY", display, "--setenv", "QT_QPA_PLATFORM", "xcb"])
    for source, destination in extra_binds:
        command.extend(["--ro-bind", str(Path(source).resolve()), destination])
    if app == "freecad":
        command.extend(["--setenv", "PYTHONHOME", "/opt/cad/usr", "--setenv", "PYTHONPATH", "/opt/cad/usr/lib"])
    return command


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("app", choices=["freecad", "openscad"])
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    log = os.open(args.work.parent / "app.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(log)
    command = sandbox_command(args.app, args.work, display=os.environ["DISPLAY"])
    command += ["/opt/cad/AppRun"]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
