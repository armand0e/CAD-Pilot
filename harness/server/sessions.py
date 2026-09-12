"""CAD session lifecycle: one virtual X display + one CAD process per session."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from .xscreen import XScreen
from .processes import ProcessLog, stop_process
from .native import read_native_state
from .edit_guard import reconcile

BASE_DISPLAY = 51


@dataclass
class Session:
    id: str
    app: dict[str, Any]
    display: str
    width: int
    height: int
    xserver: subprocess.Popen
    app_proc: subprocess.Popen
    screen: XScreen
    created: float = field(default_factory=time.time)
    agent: Any = None  # AgentRunner while a task is active
    wm_proc: Any = None
    state_dir: Path | None = None
    process_log: Any = None
    project: Any = None
    engine: str = 'hybrid'
    manual_changes: bool = False
    agent_changes: bool = False
    content_baseline: Any = None
    content_check_after: float = 0

    def to_json(self) -> dict[str, Any]:
        native = read_native_state(self.state_dir)
        if 'freecad' in self.app.get('id', ''):
            reconcile(self, native)
        return {
            "id": self.id,
            "app": {"id": self.app["id"], "name": self.app["name"], "accent": self.app.get("accent")},
            "display": self.display,
            "width": self.width,
            "height": self.height,
            "app_alive": self.app_proc.poll() is None,
            "agent_active": bool(self.agent and self.agent.active),
            "native_state_available": native is not None,
            "project_id": self.project.id if self.project else None,
            "engine": self.engine,
            "manual_changes": self.manual_changes,
            "agent_changes": self.agent_changes,
        }


class SessionManager:
    def __init__(self, *, width: int = 1440, height: int = 900, max_sessions: int = 4, state_root: Path | None = None) -> None:
        self.width, self.height = width, height
        self.sessions: dict[str, Session] = {}
        self._next_display = BASE_DISPLAY
        self._create_lock = threading.Lock()
        self.max_sessions = max_sessions
        self._creating = 0
        self.state_root = state_root or Path(__file__).resolve().parents[1] / "state"

    def _start_xserver(self, display: str) -> subprocess.Popen:
        if shutil.which("Xvfb"):
            command = ["Xvfb", display, "-screen", "0", f"{self.width}x{self.height}x24", "-nolisten", "tcp"]
        elif shutil.which("Xephyr"):
            if not os.environ.get("DISPLAY"):
                raise RuntimeError("Xephyr needs a parent DISPLAY; install Xvfb for headless use")
            command = ["Xephyr", display, "-screen", f"{self.width}x{self.height}", "-nolisten", "tcp", "-title", f"CADPilot {display}"]
        else:
            raise RuntimeError("no X server available: install Xvfb (headless) or Xephyr")
        proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if proc.poll() is not None:
                    raise RuntimeError(f"X server for {display} exited with {proc.returncode}")
                XScreen(display).close()
                lock = Path(f"/tmp/.X{display.lstrip(':')}-lock")
                if not lock.exists() or lock.read_text().strip() != str(proc.pid):
                    raise RuntimeError(f"Display {display} is not owned by the newly launched X server")
                return proc
            except Exception:  # noqa: BLE001 - server not up yet
                if proc.poll() is not None:
                    raise RuntimeError(f"X server for {display} exited with {proc.returncode}")
                time.sleep(0.25)
        stop_process(proc)
        raise RuntimeError(f"X server on {display} did not come up")

    def create(self, app: dict[str, Any]) -> Session:
        with self._create_lock:
            if len(self.sessions) + self._creating >= self.max_sessions:
                raise RuntimeError(f"Session limit ({self.max_sessions}) reached. Close an unused session first.")
            self._creating += 1
            while Path(f"/tmp/.X11-unix/X{self._next_display}").exists() or Path(f"/tmp/.X{self._next_display}-lock").exists():
                self._next_display += 1
            display = f":{self._next_display}"
            self._next_display += 1
        try:
            return self._create_on_display(app, display)
        finally:
            with self._create_lock:
                self._creating -= 1

    def _create_on_display(self, app, display):
        session_id = uuid.uuid4().hex[:8]
        state_dir = self.state_root / session_id
        state_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        env = dict(os.environ)
        env["DISPLAY"] = display
        # Private preferences prevent test/user sessions sharing recovery dialogs or UI state.
        for name, directory in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")):
            location = state_dir / directory
            location.mkdir(mode=0o700)
            env[name] = str(location)
        xserver = self._start_xserver(display)
        wm = shutil.which("openbox") or shutil.which("matchbox-window-manager") or shutil.which("metacity")
        wm_proc = app_proc = process_log = screen = None
        try:
            if not wm:
                raise RuntimeError("A window manager is required for reliable CAD dialogs. Install openbox, matchbox-window-manager, or metacity.")
            if wm:
                wm_command = [wm]
                if Path(wm).name == "metacity":
                    wm_command += ["--sm-disable", "--no-composite"]
                # Never let a nested window manager register with the host desktop bus.
                if shutil.which("dbus-run-session"):
                    wm_command = ["dbus-run-session", "--", *wm_command]
                wm_proc = subprocess.Popen(wm_command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            screen = XScreen(display)
            deadline = time.monotonic() + 5
            while not screen.has_window_manager():
                if wm_proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("The session window manager did not become ready; no CAD document was opened.")
                time.sleep(.05)
            app_command = list(app["command"])
            if app.get('native_project') and app.get('id') in {'openscad', 'appimage-openscad'}:
                # Open an owned empty editor instead of the welcome dialog, which has no
                # standard File > Open shortcut. No existing user document is changed.
                blank = state_dir / 'untitled.scad'
                blank.write_text('// New CADPilot project\n')
                app_command.append(str(blank))
            if app.get("id") in {"freecad", "appimage-freecad"}:
                if app.get('native_project'):
                    preferences = state_dir / 'user.cfg'
                    shutil.copyfile(Path(__file__).with_name('freecad-project.cfg'), preferences)
                    app_command.extend(['--user-cfg', str(preferences)])
                env["CADPILOT_NATIVE_STATE"] = str(state_dir / "native-state.json")
                env["CADPILOT_OBSERVER_SCRIPT"] = str(Path(__file__).with_name("freecad_observer.py"))
                app_command.append(str(Path(__file__).with_name("freecad_observer.FCMacro")))
            app_proc = subprocess.Popen(app_command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            process_log = ProcessLog(app_proc.stdout, state_dir / "application.log")
        except Exception:
            if screen:
                screen.close()
            for proc in (app_proc, wm_proc, xserver):
                stop_process(proc)
            if process_log:
                process_log.close()
            raise
        session = Session(
            id=session_id, app=app, display=display,
            width=screen.width, height=screen.height, xserver=xserver, app_proc=app_proc, screen=screen, wm_proc=wm_proc, state_dir=state_dir, process_log=process_log,
        )
        with self._create_lock:
            self.sessions[session.id] = session
        return session

    def close(self, session_id: str) -> None:
        with self._create_lock:
            session = self.sessions.pop(session_id, None)
        if not session:
            return
        session.screen.close()
        for proc in (session.app_proc, session.wm_proc, session.xserver):
            stop_process(proc)
        if session.process_log:
            session.process_log.close()

    def close_all(self) -> None:
        for session_id in list(self.sessions):
            self.close(session_id)
