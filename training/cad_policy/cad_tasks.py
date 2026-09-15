"""Parametric CAD tasks. Training and transfer-test parameter seeds are disjoint."""
import random


def make_task(seed: int, *, app: str, split: str = "train", difficulty: str = "box"):
    if split not in {"train", "validation", "test"}:
        raise ValueError(split)
    rng = random.Random(f"cad-task-v1/{split}/{seed}")
    lengths = {"train": [40, 50, 60, 70, 80], "validation": [45, 55, 65, 75], "test": [48, 58, 68, 78]}
    length, width, height = rng.choice(lengths[split]), rng.choice([30, 40, 50]), rng.choice([6, 8, 10])
    holes = difficulty == "plate"
    extension = "FCStd" if app == "freecad" else "scad"
    prompt = f"Create a {length} × {width} × {height} mm rectangular {'plate' if holes else 'solid block'} at the origin, extending along positive X, Y and Z."
    if holes:
        prompt += " Add four 5 mm diameter through mounting holes along Z, centered 8 mm from each adjacent X/Y edge."
    prompt += f" Save the finished native document to /work/submission.{extension}."
    return {"id": f"{split}/{app}/{difficulty}/{seed}", "version": "cad-task-v1", "split": split, "app": app,
            "length": length, "width": width, "height": height, "holes": holes, "radius": 2.5, "offset": 8,
            "prompt": prompt, "artifact": f"submission.{extension}"}
