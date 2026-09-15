"""Integration checks: native geometry rewards, including plausible-but-wrong artifacts."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "training"))
from cad_policy.cad_tasks import make_task
from cad_policy.cad_sandbox import sandbox_command
from cad_policy.cad_environment import CADEnvironment


def main():
    output = ROOT / "runs/v3-reward-checks"
    output.mkdir(exist_ok=True)
    fixtures = Path(tempfile.mkdtemp(prefix="fixtures-", dir=output))
    task = make_task(7, app="freecad", difficulty="plate")
    (fixtures / "task.json").write_text(json.dumps(task))
    command = sandbox_command("freecad", fixtures, extra_binds=[(ROOT / "scripts/reward_fixtures.py", "/fixtures.py")])
    subprocess.run(command + ["/opt/cad/usr/bin/python", "/fixtures.py", "/work/task.json", "/work"], check=True, timeout=60)
    results = []
    for app in ("freecad", "openscad"):
        for case in ("correct", "missing_hole", "wrong_size", "wrong_hole_position"):
            trial = Path(tempfile.mkdtemp(prefix=f"{app}-{case}-", dir=output))
            work = trial / "work"
            work.mkdir()
            app_task = task | {"app": app, "artifact": "submission.FCStd" if app == "freecad" else "submission.scad"}
            if app == "freecad":
                shutil.copyfile(fixtures / f"{case}.FCStd", work / app_task["artifact"])
            else:
                length = task["length"] + (2 if case == "wrong_size" else 0)
                code = f"$fn=96; difference() {{cube([{length},{task['width']},{task['height']}]);"
                for i, (x, y) in enumerate(( (x, y) for x in (8, task["length"] - 8) for y in (8, task["width"] - 8))):
                    if case == "missing_hole" and i == 0:
                        continue
                    if case == "wrong_hole_position" and i == 0:
                        x += 2
                    code += f"translate([{x},{y},-1]) cylinder(r=2.5,h={task['height'] + 2});"
                (work / app_task["artifact"]).write_text(code + "}")
            env = object.__new__(CADEnvironment)
            env.task, env.directory, env.work = app_task, trial, work
            result = env.reward()
            assert result["success"] == (case == "correct"), (app, case, result)
            results.append({"app": app, "case": case, **result})
            print(json.dumps(results[-1]), flush=True)
    (output / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
