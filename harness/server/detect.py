"""Detect installed CAD applications (native binaries, flatpaks, snaps, .desktop entries)."""

from __future__ import annotations

import configparser
import shutil
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Known CAD applications: id -> (display name, candidate binaries, flatpak ids, snap names)
KNOWN_APPS: dict[str, dict] = {
    "freecad": {"name": "FreeCAD", "binaries": ["freecad", "FreeCAD"], "flatpaks": ["org.freecad.FreeCAD", "org.freecadweb.FreeCAD"], "snaps": ["freecad"], "accent": "#e74c3c"},
    "openscad": {"name": "OpenSCAD", "binaries": ["openscad"], "flatpaks": ["org.openscad.OpenSCAD"], "snaps": ["openscad"], "accent": "#f1c40f"},
    "librecad": {"name": "LibreCAD", "binaries": ["librecad"], "flatpaks": ["org.librecad.librecad"], "snaps": ["librecad"], "accent": "#3498db"},
    "qcad": {"name": "QCAD", "binaries": ["qcad"], "flatpaks": [], "snaps": ["qcad"], "accent": "#9b59b6"},
    "solvespace": {"name": "SolveSpace", "binaries": ["solvespace"], "flatpaks": ["com.solvespace.SolveSpace"], "snaps": ["solvespace"], "accent": "#1abc9c"},
    "kicad": {"name": "KiCad", "binaries": ["kicad"], "flatpaks": ["org.kicad.KiCad"], "snaps": ["kicad"], "accent": "#2ecc71"},
    "sweethome3d": {"name": "Sweet Home 3D", "binaries": ["sweethome3d"], "flatpaks": ["com.sweethome3d.Sweethome3d"], "snaps": [], "accent": "#95a5a6"},
}
DESKTOP_DIRS = [Path("/usr/share/applications"), Path.home() / ".local/share/applications", Path("/var/lib/flatpak/exports/share/applications"), Path.home() / ".local/share/flatpak/exports/share/applications"]


@dataclass
class DetectedApp:
    id: str
    name: str
    command: list[str]
    source: str  # binary | flatpak | snap | desktop | dev
    accent: str = "#7f8c8d"
    comment: str = ""
    extras: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "command": self.command, "source": self.source, "accent": self.accent, "comment": self.comment}


def _flatpak_ids() -> set[str]:
    try:
        out = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True, timeout=10)
        return {line.strip() for line in out.stdout.splitlines() if line.strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()


def _snap_names() -> set[str]:
    try:
        out = subprocess.run(["snap", "list"], capture_output=True, text=True, timeout=10)
        return {line.split()[0] for line in out.stdout.splitlines()[1:] if line.split()}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()


def _desktop_cad_entries(seen_names: set[str]) -> list[DetectedApp]:
    found: list[DetectedApp] = []
    for directory in DESKTOP_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.glob("*.desktop"):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except Exception:  # noqa: BLE001 - malformed desktop files abound
                continue
            categories = entry.get("Categories", "")
            if "Engineering" not in categories and "CAD" not in categories:
                continue
            name = entry.get("Name", path.stem)
            if name.lower() in seen_names or entry.get("NoDisplay", "").lower() == "true":
                continue
            exec_line = entry.get("Exec", "")
            try:
                command = [part for part in shlex.split(exec_line) if not part.startswith("%")]
            except ValueError:
                continue
            if not command:
                continue
            seen_names.add(name.lower())
            found.append(DetectedApp(id=f"desktop-{path.stem.lower()}", name=name, command=command, source="desktop", comment=entry.get("Comment", "")))
    return found


def detect_apps(*, include_dev: bool = False) -> list[DetectedApp]:
    apps: list[DetectedApp] = []
    flatpaks, snaps = _flatpak_ids(), _snap_names()
    seen: set[str] = set()
    for app_id, spec in KNOWN_APPS.items():
        for binary in spec["binaries"]:
            path = shutil.which(binary)
            if path:
                apps.append(DetectedApp(app_id, spec["name"], [path], "binary", spec["accent"]))
                break
        else:
            flatpak = next((f for f in spec["flatpaks"] if f in flatpaks), None)
            if flatpak:
                apps.append(DetectedApp(app_id, spec["name"], ["flatpak", "run", flatpak], "flatpak", spec["accent"]))
            elif any(s in snaps for s in spec["snaps"]):
                snap = next(s for s in spec["snaps"] if s in snaps)
                apps.append(DetectedApp(app_id, spec["name"], ["snap", "run", snap], "snap", spec["accent"]))
        seen.add(spec["name"].lower())
    apps.extend(_appimage_entries())
    apps.extend(_desktop_cad_entries(seen))
    if include_dev:
        for binary, name in (("xterm", "xterm (dev)"), ("xclock", "xclock (dev)"), ("glxgears", "glxgears (dev)")):
            path = shutil.which(binary)
            if path:
                apps.append(DetectedApp(f"dev-{binary}", name, [path], "dev", "#566573", "development target, not a CAD app"))
    return apps


def _appimage_entries() -> list[DetectedApp]:
    """Portable AppImages extracted under harness/apps/<name>-extracted/ (no root needed)."""
    apps_dir = Path(__file__).resolve().parents[1] / "apps"
    found = []
    accents = {"freecad": "#e74c3c", "openscad": "#f1c40f"}
    names = {"freecad": "FreeCAD (AppImage)", "openscad": "OpenSCAD (AppImage)"}
    for extracted in sorted(apps_dir.glob("*-extracted")):
        app_run = extracted / "AppRun"
        if app_run.is_file():
            key = extracted.name.replace("-extracted", "")
            found.append(DetectedApp(
                id=f"appimage-{key}", name=names.get(key, key.title()), command=[str(app_run)],
                source="appimage", accent=accents.get(key, "#8e44ad"),
            ))
    return found


def missing_system_tools() -> list[str]:
    missing = []
    if not (shutil.which("Xvfb") or shutil.which("Xephyr")):
        missing.append("Xvfb or Xephyr")
    if not any(shutil.which(tool) for tool in ("openbox", "matchbox-window-manager", "metacity")):
        missing.append("openbox, matchbox-window-manager, or metacity")
    return missing
