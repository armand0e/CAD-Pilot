"""Read bounded, fresh snapshots from the fixed in-app observer. No model-driven execution."""
import json
import time


def read_native_state(state_dir):
    if state_dir is None:
        return None
    path = state_dir / "native-state.json"
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            return None
        with path.open("rb") as handle:
            data = handle.read(65537)
        if len(data) > 65536:
            return None
        value = json.loads(data)
        if not isinstance(value, dict) or value.get("schema") != 1 or value.get("application") != "FreeCAD":
            return None
        timestamp = value.get("timestamp")
        if type(timestamp) not in (int, float) or not 0 <= time.time() - timestamp <= 5:
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None
