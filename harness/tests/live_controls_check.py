"""Stock-model test of native control grounding in a disposable FreeCAD document."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from server.native import read_native_state


async def main():
    output = Path(tempfile.mkdtemp(prefix="controls-",dir=ROOT / "runs/harness-checks"))
    base = "http://127.0.0.1:7802"
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(40):
            try:
                (await client.get(base + "/api/status")).raise_for_status()
                break
            except httpx.ConnectError:
                if attempt == 39:
                    raise
                await asyncio.sleep(.25)
        response = await client.post(base + "/api/sessions",json={"app_id":"appimage-freecad"})
        response.raise_for_status()
        sid = response.json()["id"]
        try:
            async with websockets.connect(f"ws://127.0.0.1:7802/ws/agent/{sid}") as socket:
                await socket.recv()  # Initial snapshot, not a task result.
                await socket.send(json.dumps({"t":"start","mode":"auto","task":
                    "Create a new FreeCAD document with exactly one empty Parametric Body. Do not create any sketch or geometry. Stop once that empty Body exists."}))
                events = []
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    event = json.loads(await asyncio.wait_for(socket.recv(),30))
                    events.append(event)
                    if event["t"] in {"intent","error","done","pause"}:
                        print(json.dumps(event),flush=True)
                    if event["t"] == "control" and not event["locked"] or event["t"] == "pause" and event["paused"]:
                        break
                snapshot = (await client.get(base + f"/api/sessions/{sid}/activity")).json()
                native = read_native_state(ROOT / "harness/state" / sid)
                (output / "activity.json").write_text(json.dumps(snapshot,indent=2))
                (output / "native.json").write_text(json.dumps(native,indent=2))
                assert native and not native.get("observer_error"), native
                bodies = [o for o in native["objects"] if o["type"] == "PartDesign::Body"]
                assert len(bodies) == 1, native
                assert not any(o["type"].startswith(("Sketcher::","PartDesign::Pad","Part::Feature")) for o in native["objects"])
                assert not snapshot["active"] and any(e["t"] == "done" for e in events), snapshot
                print(json.dumps({"native_grounded_stock_model":"passed","output":str(output),"steps":snapshot["completed_steps"]}))
        finally:
            await client.delete(base + f"/api/sessions/{sid}")


if __name__ == "__main__":
    asyncio.run(main())
