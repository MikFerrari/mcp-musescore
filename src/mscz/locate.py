"""Find the file behind the score open in MuseScore, and keep backups before editing it.

The plugin API does not expose a score's path, but MuseScore lists open files in
session/session.json and recently used ones in recent_files.json in its data directory.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .score_file import ScoreFileError

SCORE_SUFFIXES = (".mscz", ".mscx")


def musescore_data_dirs() -> List[Path]:
    if os.environ.get("MUSESCORE_DATA_DIR"):
        return [Path(os.environ["MUSESCORE_DATA_DIR"])]
    home = Path.home()
    dirs = [
        home / ".local" / "share" / "MuseScore" / "MuseScore4",
        home / "Library" / "Application Support" / "MuseScore" / "MuseScore4",
    ]
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "MuseScore" / "MuseScore4")
    return dirs


def _listed_paths(path: Path) -> List[Path]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    paths = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            entry = entry.get("path") or entry.get("filePath")
        if isinstance(entry, str):
            paths.append(Path(entry))
    return paths


def find_score_file(score_name: str, explicit: Optional[str] = None) -> Path:
    """Path of the open score: `explicit` if given, else MuseScore's open/recent files matched by name."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ScoreFileError(f"Score file not found: {path}")
        return path

    session: List[Path] = []
    recent: List[Path] = []
    for data_dir in musescore_data_dirs():
        session += _listed_paths(data_dir / "session" / "session.json")
        recent += _listed_paths(data_dir / "recent_files.json")
    session = [p for p in session if p.suffix.lower() in SCORE_SUFFIXES and p.is_file()]
    recent = [p for p in recent if p.suffix.lower() in SCORE_SUFFIXES and p.is_file()]

    for pool in (session, recent):
        matches = list(dict.fromkeys(p for p in pool if p.stem == score_name))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            listed = ", ".join(str(m) for m in matches)
            raise ScoreFileError(f"Several files are named '{score_name}' ({listed}); pass score_path")
    if len(set(session)) == 1:
        return session[0]
    raise ScoreFileError(
        f"Could not find the file of the open score '{score_name}'. Save it in MuseScore, or pass score_path."
    )


def backup_dir() -> Path:
    return Path(os.environ.get("MCP_MUSESCORE_BACKUP_DIR") or Path.home() / ".mcp-musescore" / "backups")


def backup_score(path: Path) -> Path:
    """Copy a score into the backup directory as <name>.<timestamp><suffix>."""
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = directory / f"{path.stem}.{stamp}{path.suffix}"
    shutil.copy2(path, target)
    return target


def list_backups(path: Path) -> List[Path]:
    """Backups of a score, newest first."""
    directory = backup_dir()
    if not directory.is_dir():
        return []
    prefix = f"{path.stem}."
    found = [
        p for p in directory.iterdir()
        if p.name.startswith(prefix) and p.suffix == path.suffix and p.stem[len(prefix):][:8].isdigit()
    ]
    return sorted(found, key=lambda p: p.name, reverse=True)
