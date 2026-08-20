"""World state singleton for the nowhere walking simulation."""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from collections import deque
from datetime import datetime, timedelta, timezone

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
        self.heading: float = 0.0  # degrees, 0=north, clockwise
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
        self.quotes: list[dict] = []  # 原话 [{text, place, pos, sim_time}], max 50 FIFO
        self.mishap_seen: list[str] = []  # 已触发的意外卡 ID
        self.mishap_tag: str | None = None  # 当前活跃的意外标记
        self.visit_counts: dict[str, int] = {}  # 本次旅程的地方到访次数
        # ── Walk discovery context ────────────────────────────────────
        self.last_surface: str | None = None  # surface from previous step
        self.last_elevation: float = 0.0  # elevation from previous step
        self.steps_since_discovery: int = 0  # counter for pacing discoveries
        self.radio_steps_since: int = 999  # steps since last radio mention (start high so first walk sees radio)
        self.walk_step_counter: int = 0  # total walk steps for rhythm gating
        self.steps_since_content: int = 0  # Card 40: consecutive steps with zero content
        # ── Scene dedup ─────────────────────────────────────────────
        self.recent_scenes: list[str] = []  # last N scene texts to avoid repetition
        self.recent_touch_sentences: list[str] = []  # last N touch/smell sentences to avoid repetition
        # ── Narrative continuity ──────────────────────────────────────
        self.narrative: dict = {
            "direction": None,      # current walk direction (Chinese label)
            "distance_walked": 0,   # meters walked in current direction
            "last_feature": None,   # last notable feature encountered
            "discoveries": [],      # things found along the way
            "mood": "neutral",      # current emotional state
        }
        # ── Journey log (append-only, farewell/return events) ─────────
        self.journey_log: list[dict] = []
        # ── People tracking (Card 41: 卡中人) ─────────────────────────
        self.seen_people: set[str] = set()  # keys like "喀什/卡孜姆"
        self.last_person: dict | None = None  # last encountered person entry
        self.last_person_place: str | None = None  # place name of last person
        self.talk_count: int = 0  # lines spoken to last_person (rotation)
        self.person_encountered_this_walk: bool = False  # one encounter per walk
        # ── Wilderness depth tracking (Card 40: honest boundaries) ────
        self.wilderness_depth_km: float = 0.0  # distance from nearest known place/water feature
        # ── Errand (Card 42: 差事) ────────────────────────────────────
        self.errand: dict | None = None  # active errand {kind, ...}
        self.errand_letter_taken_this_journey: bool = False  # max 1 letter per journey
        self.errand_festival_mentioned_this_journey: bool = False  # max 1 festival wind per journey
        self.intent: str | None = None  # Card 12: intent bias for salience/localcolor
        # ── Card 16: blind door ────────────────────────────────────────
        self.blind: bool = False  # blind mode: place name hidden
        self.blind_clues: int = 0  # how many clues given during blind
        # ── Card 17: door key ──────────────────────────────────────────
        self.door_key: str | None = None  # deterministic key for door
        # ── Card 18: drift cards ───────────────────────────────────────
        self.drift_seen: list[str] = []  # seen drift card texts this journey
        # ── Card 20: odometer ──────────────────────────────────────────
        self.total_distance_km: float = 0.0  # per-journey distance walked
        # ── Card 50: body state (能动·会变·不可逆·阻力) ───────────────
        self.whim: str | None = None  # active small desire (max 1 per journey)
        self.whim_steps_since: int = 999  # steps since last whim (999 = allow new)
        self.hunger: float = 0.0  # 0-10, +0.5/hour sim time, clear on eat
        self.cold: float = 0.0  # 0-10, +1/hour when temp<5°C, -2/hour when >15°C
        self.wet: bool = False  # True after 2 steps in rain outdoors
        self.wet_rain_steps: int = 0  # counter for rain exposure
        self.fatigue: float = 0.0  # 0-10, +1/hour walk, -2/hour wait

    def now(self) -> datetime | None:
        """Return the current simulated UTC time: landed_at + elapsed_hours."""
        if self.landed_at is None:
            return None
        return self.landed_at + timedelta(hours=self.elapsed_hours)

    def to_dict(self) -> dict:
        """Serialize state to a dict (used by save() and journeys.py)."""
        return {
            "save_version": 1,
            "pos": list(self.pos) if self.pos else None,
            "path": self.path[-50:],  # keep last 50 steps (not entire history)
            "landed_at": self.landed_at.isoformat() if self.landed_at else None,
            "elapsed_hours": self.elapsed_hours,
            "mode": self.mode,
            "heading": self.heading,
            "messages": [m if isinstance(m, dict) else {"content": m, "encountered": False} for m in self.messages],
            "place_name": self.place_name,
            "last_text": self.last_text,
            "biome": self.biome,
            "seen_cards": list(self.seen_cards),
            "seen_humanities": list(self.seen_humanities),
            "souvenir": self.souvenir,
            "quotes": self.quotes[-50:],  # keep last 50
            "mishap_seen": self.mishap_seen,
            "mishap_tag": self.mishap_tag,
            "postcards": self.postcards[-20:],  # keep last 20
            "radio_station": self.radio_station,
            "radio_pos": list(self.radio_pos) if self.radio_pos else None,
            "last_env": self.last_env,
            "env_pos": list(self.env_pos) if self.env_pos else None,
            "env_at": self.env_at.isoformat() if self.env_at else None,
            "visit_counts": self.visit_counts,
            "last_surface": self.last_surface,
            "last_elevation": self.last_elevation,
            "steps_since_discovery": self.steps_since_discovery,
            "radio_steps_since": self.radio_steps_since,
            "walk_step_counter": self.walk_step_counter,
            "steps_since_content": self.steps_since_content,
            "narrative": self.narrative,
            "recent_scenes": self.recent_scenes[-10:],  # keep last 10
            "recent_touch_sentences": self.recent_touch_sentences[-10:],  # keep last 10
            "journey_log": self.journey_log[-50:],  # keep last 50 events
            "wilderness_depth_km": self.wilderness_depth_km,
            "seen_people": list(self.seen_people),
            "last_person": self.last_person,
            "last_person_place": self.last_person_place,
            "talk_count": self.talk_count,
            "person_encountered_this_walk": self.person_encountered_this_walk,
            "errand": self.errand,
            "errand_letter_taken_this_journey": self.errand_letter_taken_this_journey,
            "errand_festival_mentioned_this_journey": self.errand_festival_mentioned_this_journey,
            "intent": self.intent,
            "blind": self.blind,
            "blind_clues": self.blind_clues,
            "door_key": self.door_key,
            "drift_seen": self.drift_seen,
            # Card 20: per-journey odometer
            "total_distance_km": self.total_distance_km,
            # Card 50: body state
            "whim": self.whim,
            "whim_steps_since": self.whim_steps_since,
            "hunger": self.hunger,
            "cold": self.cold,
            "wet": self.wet,
            "wet_rain_steps": self.wet_rain_steps,
            "fatigue": self.fatigue,
        }

    @classmethod
    def migrate(cls, data: dict) -> dict:
        """Migrate old save data to current version. Stub for future use."""
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "WorldState":
        """Restore state from a dict (used by load() and journeys.py)."""
        version = data.get("save_version", 0)
        if version == 0:
            pass  # 老档,全兼容
        elif version > 1:
            data = cls.migrate(data)
        s = cls()
        if data.get("pos"):
            s.pos = tuple(data["pos"])
        s.path = data.get("path", [])
        if data.get("landed_at"):
            s.landed_at = datetime.fromisoformat(data["landed_at"])
            if s.landed_at.tzinfo is None:
                s.landed_at = s.landed_at.replace(tzinfo=timezone.utc)
        s.elapsed_hours = data.get("elapsed_hours", 0.0)
        s.mode = data.get("mode", "land")
        s.heading = data.get("heading", 0.0)
        for m in data.get("messages", []):
            s.messages.append(m)
        s.place_name = data.get("place_name")
        s.last_text = data.get("last_text", "")
        s.biome = data.get("biome")
        s.seen_cards = set(data.get("seen_cards", []))
        s.seen_humanities = set(data.get("seen_humanities", []))
        s.souvenir = data.get("souvenir")
        s.quotes = data.get("quotes", [])
        s.mishap_seen = data.get("mishap_seen", [])
        s.mishap_tag = data.get("mishap_tag")
        s.visit_counts = data.get("visit_counts", {})
        s.last_surface = data.get("last_surface")
        s.last_elevation = data.get("last_elevation", 0.0)
        s.steps_since_discovery = data.get("steps_since_discovery", 0)
        s.radio_steps_since = data.get("radio_steps_since", 999)
        s.walk_step_counter = data.get("walk_step_counter", 0)
        s.steps_since_content = data.get("steps_since_content", 0)
        _default_narrative = {
            "direction": None, "distance_walked": 0,
            "last_feature": None, "discoveries": [], "mood": "neutral",
        }
        loaded_narrative = data.get("narrative", {})
        if isinstance(loaded_narrative, dict):
            s.narrative = {**_default_narrative, **loaded_narrative}
        else:
            s.narrative = _default_narrative
        s.recent_scenes = data.get("recent_scenes", [])
        s.recent_touch_sentences = data.get("recent_touch_sentences", [])
        s.journey_log = data.get("journey_log", [])
        s.postcards = data.get("postcards", [])
        s.wilderness_depth_km = data.get("wilderness_depth_km", 0.0)
        s.seen_people = set(data.get("seen_people", []))
        s.last_person = data.get("last_person")
        s.last_person_place = data.get("last_person_place")
        s.talk_count = data.get("talk_count", 0)
        s.person_encountered_this_walk = data.get("person_encountered_this_walk", False)
        s.errand = data.get("errand")
        s.errand_letter_taken_this_journey = data.get("errand_letter_taken_this_journey", False)
        s.errand_festival_mentioned_this_journey = data.get("errand_festival_mentioned_this_journey", False)
        s.blind = data.get("blind", False)
        s.blind_clues = data.get("blind_clues", 0)
        s.door_key = data.get("door_key")
        s.drift_seen = data.get("drift_seen", [])
        # Card 20: per-journey odometer
        s.total_distance_km = data.get("total_distance_km", 0.0)
        # Card 50: body state
        s.whim = data.get("whim")
        s.whim_steps_since = data.get("whim_steps_since", 999)
        s.hunger = data.get("hunger", 0.0)
        s.cold = data.get("cold", 0.0)
        s.wet = data.get("wet", False)
        s.wet_rain_steps = data.get("wet_rain_steps", 0)
        s.fatigue = data.get("fatigue", 0.0)
        s.intent = data.get("intent")
        s.radio_station = data.get("radio_station")
        if data.get("radio_pos"):
            s.radio_pos = tuple(data["radio_pos"])
        s.last_env = data.get("last_env")
        if data.get("env_pos"):
            s.env_pos = tuple(data["env_pos"])
        if data.get("env_at"):
            s.env_at = datetime.fromisoformat(data["env_at"])
            if s.env_at.tzinfo is None:
                s.env_at = s.env_at.replace(tzinfo=timezone.utc)
        return s

    def save(self) -> None:
        """Persist journey to disk (thin wrapper around to_dict)."""
        _SAVE_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix="journey-", suffix=".tmp", dir=_SAVE_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, _SAVE_FILE)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    @classmethod
    def load(cls) -> "WorldState | None":
        """Load saved journey from disk (thin wrapper around from_dict)."""
        if not _SAVE_FILE.exists():
            return None
        try:
            data = json.loads(_SAVE_FILE.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception as exc:
            import logging
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = _SAVE_FILE.with_name(f"journey.json.broken_{ts}")
            try:
                os.replace(str(_SAVE_FILE), str(backup))
            except OSError:
                pass
            msg = f"存档读不出来,已备份到 {backup.name},旅程重新开始"
            logging.getLogger(__name__).warning("%s (%s)", msg, exc)
            print(msg)
            return None

    def clear(self) -> None:
        """Clear saved journey."""
        if _SAVE_FILE.exists():
            _SAVE_FILE.unlink()

    def record_journey_visit(self, place: str) -> int:
        """Record a visit to a place within this journey. Returns visit number."""
        self.visit_counts[place] = self.visit_counts.get(place, 0) + 1
        return self.visit_counts[place]

    def reset_body_state(self) -> None:
        """Reset body state on continue_journey.

        Card 50: '睡了一觉,身体是你的了'.
        Memory/position/collection are continuous; body resets daily.
        """
        self.whim = None
        self.whim_steps_since = 999
        self.hunger = 0.0
        self.cold = 0.0
        self.wet = False
        self.wet_rain_steps = 0
        self.fatigue = 0.0
