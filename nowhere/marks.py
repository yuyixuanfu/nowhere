"""Local bookmark storage — save / list / get named places."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _marks_path() -> Path:
    """Return the path to marks.json, reading NOWHERE_HOME on every call."""
    base = os.environ.get("NOWHERE_HOME") or str(Path.home() / ".nowhere")
    return Path(base) / "marks.json"


def _load() -> list[dict]:
    p = _marks_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _dump(marks: list[dict]) -> None:
    p = _marks_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(marks, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────

def save(name: str, lat: float, lon: float, note: str = "", overwrite: bool = False) -> None:
    """Save a bookmark by *name*.

    Raises ``ValueError`` if *name* already exists and *overwrite* is ``False``.
    """
    marks = _load()
    existing = [m for m in marks if m["name"] == name]
    if existing and not overwrite:
        raise ValueError(f"「{name}」已经标过了。要覆盖的话用 mark 的覆盖选项。")
    entry = {
        "name": name,
        "lat": lat,
        "lon": lon,
        "note": note,
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    marks = [m for m in marks if m["name"] != name]
    marks.append(entry)
    _dump(marks)


def all() -> list[dict]:  # noqa: A001 — shadows builtin intentionally
    """Return every saved bookmark."""
    return _load()


def get(name: str) -> dict | None:  # noqa: A001
    """Return the bookmark with *name*, or ``None``."""
    for m in _load():
        if m["name"] == name:
            return m
    return None
