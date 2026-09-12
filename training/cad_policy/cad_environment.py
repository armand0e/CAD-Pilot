"""Isolated GUI rollout environment and inaccessible native-geometry reward computation."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from .cad_sandbox import REPO, sandbox_command

sys.path.insert(0, str(REPO / "harness"))
from server.sessions import SessionManager
from server.agent import AgentRunner


class CADEnvironment:
    def __init__(self, task, directory):
        self.task, self.directory = task, Path(directory)
        self.work = self.directory / "work"
        self.work.mkdir(parents=True, exist_ok=False)
        self.manager = SessionManager(width=1280, height=800)
        command = [str(REPO / "harness/.venv/bin/python"), str(REPO / "training/cad_policy/cad_sandbox.py"), task["app"], str(self.work)]
        self.session = self.manager.create({"id": "isolated-eval", "name": task["app"], "command": command})
        self.executor = AgentRunner(self.session, {"agent": {}, "policy": {}})
        from PIL import ImageStat
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.session.app_proc.poll() is not None:
                self.close()
                raise RuntimeError("Sandboxed CAD application failed to launch; see app.log")
            image = self.observe()
            if max(ImageStat.Stat(image.resize((64, 40))).stddev) > 5:
                time.sleep(1)
                break
            time.sleep(.5)
        else:
            self.close()
            raise RuntimeError("CAD startup produced no usable screenshot within 45 seconds")

    def observe(self):
        return self.session.screen.capture_image()

    def step(self, actions):
        for action in actions:
            self.executor._execute(action)
        time.sleep(.3)

    def reward(self):
        artifact = self.work / self.task["artifact"]
        if not artifact.is_file() or artifact.is_symlink() or artifact.stat().st_size > 50 * 1024 ** 2:
            return {"reward": 0.0, "success": False, "reason": "missing_or_invalid_native_file"}
        grade_dir = self.directory / "grader"
        grade_dir.mkdir(exist_ok=True)
        # Snapshot the artifact; the live application cannot change the file being graded.
        snapshot = grade_dir / self.task["artifact"]
        shutil.copyfile(artifact, snapshot)
        (grade_dir / "task.json").write_text(json.dumps(self.task))
        target = f"/work/{snapshot.name}"
        try:
            if self.task["app"] == "openscad":
                command = sandbox_command("openscad", grade_dir) + ["/opt/cad/AppRun", "-o", "/work/submission.stl", target]
                subprocess.run(command, check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                target = "/work/submission.stl"
            command = sandbox_command("freecad", grade_dir, extra_binds=[(REPO / "training/cad_policy/geometry_grader.py", "/grader.py")])
            command += ["/opt/cad/usr/bin/python", "/grader.py", "/work/task.json", target, "/work/result.json"]
            subprocess.run(command, check=True, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return json.loads((grade_dir / "result.json").read_text())
        except (subprocess.SubprocessError, OSError, ValueError):
            return {"reward": 0.0, "success": False, "reason": "grader_failed_or_timed_out"}

    def close(self):
        self.manager.close_all()
