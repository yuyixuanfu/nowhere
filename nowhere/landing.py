"""Landing spot pool — picks a random biome coordinate with jitter."""

from __future__ import annotations

import json
import pathlib
import random

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
_POOL_PATH = _DATA_DIR / "pool.json"

_pool: list[dict] | None = None


def _load_pool() -> list[dict]:
    global _pool
    if _pool is None:
        with open(_POOL_PATH, encoding="utf-8") as f:
            _pool = json.load(f)
    return _pool


def random_spot(rng: random.Random) -> dict:
    """Pick a random landing spot from pool.json and add +/-0.1deg jitter.

    抖动收紧到 0.1°(约 11km): 池里的点是手核的真实海拔/地表,
    terrain 在 0.15° 半径内优先用池值,抖太远就吃不到真值了。
    Returns {"lat", "lon", "biome", "name_hint"}.
    """
    pool = _load_pool()
    spot = rng.choice(pool)
    return {
        "lat": spot["lat"] + rng.uniform(-0.1, 0.1),
        "lon": spot["lon"] + rng.uniform(-0.1, 0.1),
        "biome": spot["biome"],
        "name_hint": spot["name_hint"],
    }
