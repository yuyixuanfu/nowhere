"""World state singleton for the nowhere walking simulation."""

from __future__ import annotations

import json
import os
import pathlib
from collections import deque
from datetime import datetime, timedelta

_SAVE_DIR = pathlib.Path(os.environ.get("NOWHERE_HOME") or str(pathlib.Path.home() / ".nowhere"))
_SAVE_FILE = _SAVE_DIR / "journey.json"


class WorldState:
    """Mutable world state that tracks position, path, and walk timing."""

    def __init__(self) -> None:
        self.pos: tuple[float, float] | None = None
        self.path: list[dict] = []  # each step {"lat","lon","elevation","dist_km"}
        self.landed_at: datetime | None = None  # UTC, door open moment
        self.elapsed_hours: float = 0.0  # walk-accumulated travel time
        self.mode: str = "land"  # "land"|"water"
        self.messages: deque = deque(maxlen=20)  # human messages
        self.last_env: dict | None = None  # last env snapshot (salience delta)
        self.env_pos: tuple[float, float] | None = None  # last_env 采集时的坐标
        self.env_at: datetime | None = None  # last_env 采集时的时间
        self.place_name: str | None = None  # hint name from landing pool
        self.last_text: str = ""  # most recent body report prose
        self.radio_station: dict | None = None  # sticky station for current area
        self.radio_pos: tuple[float, float] | None = None  # where station was picked
        self.postcards: list[dict] = []  # 寄出的明信片(带邮戳)
        self.biome: str | None = None  # 落点 biome(城市/荒野味道分流)
        self.seen_cards: set[str] = set()  # 方志已见卡 key
        self.seen_humanities: set[str] = set()  # 人文层已见卡 key
        self.souvenir: dict | None = None  # 身上带的东西 {"name", "from", "desc"}
        self.visit_counts: dict[str, int] = {}  # 本次旅程的地方到访次数
        # ── Walk discovery context ────────────────────────────────────
        self.last_surface: str | None = None  # surface from previous step
        self.last_elevation: float = 0.0  # elevation from previous step
        self.steps_since_discovery: int = 0  # counter for pacing discoveries
        # ── Scene dedup ─────────────────────────────────────────────
        self.recent_scenes: list[str] = []  # last N scene texts to avoid repetition
        # ── Narrative continuity ──────────────────────────────────────
        self.narrative: dict = {
            "direction": None,      # current walk direction (Chinese label)
            "distance_walked": 0,   # meters walked in current direction
            "last_feature": None,   # last notable feature encountered
            "discoveries": [],      # things found along the way
            "mood": "neutral",      # current emotional state
        }

    def now(self) -> datetime | None:
        """Return the current simulated UTC time: landed_at + elapsed_hours."""
        if self.landed_at is None:
            return None
        return self.landed_at + timedelta(hours=self.elapsed_hours)

    def save(self) -> None:
        """Persist journey to disk."""
        _SAVE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "pos": list(self.pos) if self.pos else None,
            "path": self.path[-50:],  # keep last 50 steps (not entire history)
            "landed_at": self.landed_at.isoformat() if self.landed_at else None,
            "elapsed_hours": self.elapsed_hours,
            "mode": self.mode,
            "messages": [m if isinstance(m, dict) else {"content": m, "encountered": False} for m in self.messages],
            "place_name": self.place_name,
            "last_text": self.last_text,
            "biome": self.biome,
            "seen_cards": list(self.seen_cards),
            "seen_humanities": list(self.seen_humanities),
            "souvenir": self.souvenir,
            "postcards": self.postcards[-20:],  # keep last 20
            "radio_station": self.radio_station,
            "env_pos": list(self.env_pos) if self.env_pos else None,
            "env_at": self.env_at.isoformat() if self.env_at else None,
            "visit_counts": self.visit_counts,
            "last_surface": self.last_surface,
            "last_elevation": self.last_elevation,
            "steps_since_discovery": self.steps_since_discovery,
            "narrative": self.narrative,
            "recent_scenes": self.recent_scenes[-10:],  # keep last 10
        }
        _SAVE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "WorldState | None":
        """Load saved journey from disk, or None if no save exists."""
        if not _SAVE_FILE.exists():
            return None
        try:
            data = json.loads(_SAVE_FILE.read_text(encoding="utf-8"))
            s = cls()
            if data.get("pos"):
                s.pos = tuple(data["pos"])
            s.path = data.get("path", [])
            if data.get("landed_at"):
                s.landed_at = datetime.fromisoformat(data["landed_at"])
            s.elapsed_hours = data.get("elapsed_hours", 0.0)
            s.mode = data.get("mode", "land")
            for m in data.get("messages", []):
                s.messages.append(m)
            s.place_name = data.get("place_name")
            s.last_text = data.get("last_text", "")
            s.biome = data.get("biome")
            s.seen_cards = set(data.get("seen_cards", []))
            s.seen_humanities = set(data.get("seen_humanities", []))
            s.souvenir = data.get("souvenir")
            s.visit_counts = data.get("visit_counts", {})
            s.last_surface = data.get("last_surface")
            s.last_elevation = data.get("last_elevation", 0.0)
            s.steps_since_discovery = data.get("steps_since_discovery", 0)
            s.narrative = data.get("narrative", {
                "direction": None, "distance_walked": 0,
                "last_feature": None, "discoveries": [], "mood": "neutral",
            })
            s.recent_scenes = data.get("recent_scenes", [])
            s.postcards = data.get("postcards", [])
            s.radio_station = data.get("radio_station")
            if data.get("env_pos"):
                s.env_pos = tuple(data["env_pos"])
            if data.get("env_at"):
                s.env_at = datetime.fromisoformat(data["env_at"])
            return s
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to load journey from %s: %s", _SAVE_FILE, exc)
            return None

    def clear(self) -> None:
        """Clear saved journey."""
        if _SAVE_FILE.exists():
            _SAVE_FILE.unlink()

    def record_journey_visit(self, place: str) -> int:
        """Record a visit to a place within this journey. Returns visit number."""
        self.visit_counts[place] = self.visit_counts.get(place, 0) + 1
        return self.visit_counts[place]
