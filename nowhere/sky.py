"""Offline sky calculations: sun, moon, planets, Milky Way, constellations.

Uses ``pyephem`` which bundles its own JPL ephemeris — zero network requests.
"""

from __future__ import annotations

import math
import random as _random
from datetime import datetime, timedelta, timezone

import ephem

# ── constants ───────────────────────────────────────────────────────
_MILKY_WAY_CORE_RA = "17:45:40"  # Sgr A* J2000
_MILKY_WAY_CORE_DEC = "-29:00:28"
_MILKY_WAY_ALT_THRESHOLD = 20.0  # degrees

_PLANETS = [
    ("Mercury", ephem.Mercury),
    ("Venus", ephem.Venus),
    ("Mars", ephem.Mars),
    ("Jupiter", ephem.Jupiter),
    ("Saturn", ephem.Saturn),
]


# ── helpers ─────────────────────────────────────────────────────────

def _make_observer(lat: float, lon: float, dt: datetime) -> ephem.Observer:
    """Build an ephem Observer from decimal lat/lon and a tz-aware datetime.

    naive datetime 一律按 UTC 解释(本文件所有公开函数同此约定)。
    """
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    # ephem expects UTC as "YYYY/MM/DD HH:MM:SS"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    obs.date = utc.strftime("%Y/%m/%d %H:%M:%S")
    return obs


def _to_iso(dt_ephem) -> str | None:
    """Convert an ephem Julian date to an ISO-8601 UTC string."""
    if dt_ephem is None:
        return None
    py_dt = ephem.Date(dt_ephem).datetime()
    py_dt = py_dt.replace(tzinfo=timezone.utc)
    return py_dt.isoformat()



def _phase_label(sun_alt: float) -> str:
    if sun_alt > 0:
        return "day"
    if sun_alt > -6:
        return "civil"
    if sun_alt > -12:
        return "nautical"
    return "night"


# ── public API ──────────────────────────────────────────────────────

def sun_moon(lat: float, lon: float, dt: datetime) -> dict:
    """Return sun & moon info at *lat*, *lon* for tz-aware *dt* (UTC).

    Keys: sun_alt, phase, sunrise, sunset, moon_phase, moon_alt.
    """
    obs = _make_observer(lat, lon, dt)

    # ── sun ─────────────────────────────────────────────────────────
    sun = ephem.Sun(obs)
    sun_alt = float(sun.alt) * 180.0 / math.pi

    # sunrise / sunset for the local calendar day: 围绕地方时正午(≈UTC
    # 正午 − 经度/15 小时)重建 observer 后取 previous_rising/next_setting,
    # 保证两个时刻同属 dt 的那一日; 直接对 dt 取最近事件会在午夜附近
    # 一个来自昨日、一个来自明日
    sunrise_iso: str | None = None
    sunset_iso: str | None = None
    try:
        noon_obs = ephem.Observer()
        noon_obs.lat = str(lat)
        noon_obs.lon = str(lon)
        utc_dt = dt.astimezone(timezone.utc)
        noon = utc_dt + timedelta(hours=(12.0 - lon / 15.0))
        noon_obs.date = noon.strftime("%Y/%m/%d %H:%M:%S")
        sunrise_iso = _to_iso(noon_obs.previous_rising(ephem.Sun()))
        sunset_iso = _to_iso(noon_obs.next_setting(ephem.Sun()))
    except (ephem.NeverUpError, ephem.AlwaysUpError):
        pass  # intentionally ignored: polar regions where sun never rises/sets is normal

    # ── moon ────────────────────────────────────────────────────────
    moon = ephem.Moon(obs)
    moon_phase = moon.phase / 100.0  # 0=new, 1=full
    moon_alt = float(moon.alt) * 180.0 / math.pi

    return {
        "sun_alt": sun_alt,
        "phase": _phase_label(sun_alt),
        "sunrise": sunrise_iso,
        "sunset": sunset_iso,
        "moon_phase": moon_phase,
        "moon_alt": moon_alt,
    }


def visible_sky(lat: float, lon: float, dt: datetime, rng: _random.Random | None = None) -> dict:
    """Return visible-sky info at *lat*, *lon* for tz-aware *dt* (UTC).

    Keys: planets, moon_phase, moon_alt, milky_way_core_up, aurora (dict | None).
    """
    obs = _make_observer(lat, lon, dt)

    # ── planets (Mercury–Saturn, alt > 0, sorted by brightness) ────
    planets: list[dict] = []
    for name, factory in _PLANETS:
        body = factory()
        body.compute(obs)
        alt = float(body.alt) * 180.0 / math.pi
        if alt > 0:
            planets.append({"name": name, "alt": alt, "mag": body.mag})
    planets.sort(key=lambda p: p["mag"])

    # ── moon ────────────────────────────────────────────────────────
    moon = ephem.Moon(obs)
    moon_phase = moon.phase / 100.0
    moon_alt = float(moon.alt) * 180.0 / math.pi

    # ── Milky Way core ──────────────────────────────────────────────
    sgra = ephem.FixedBody()
    sgra._ra = _MILKY_WAY_CORE_RA
    sgra._dec = _MILKY_WAY_CORE_DEC
    sgra.compute(obs)
    milky_way_core_up = float(sgra.alt) * 180.0 / math.pi > _MILKY_WAY_ALT_THRESHOLD

    # ── aurora ───────────────────────────────────────────────────────
    # Real NOAA OVATION forecast first.  It gives per-coordinate aurora
    # power, so Tromso and Fairbanks on the same night can differ.  Only
    # when the forecast is unreachable do we fall back to the latitude
    # heuristic below (offline / airplane mode must still work).
    aurora_info: dict | None = None
    abs_lat = abs(lat)
    if 50 <= abs_lat <= 90:
        try:
            from nowhere import aurora as _aurora_mod

            real = _aurora_mod.forecast(lat, lon)
        except Exception:  # noqa: BLE001 - forecast must never break the sky
            real = None
        if real is not None:
            aurora_info = real
            return _finish_sky(
                planets, moon_phase, moon_alt, milky_way_core_up, aurora_info
            )
        if _aurora_mod_has_data():
            # Forecast is live and says nothing is happening here.  Do not
            # invent an aurora just because we are far north.
            return _finish_sky(
                planets, moon_phase, moon_alt, milky_way_core_up, None
            )
    if 60 <= abs_lat <= 90:
        # Magnetic latitude approximation: auroral oval ~60-80° geographic
        # Probability increases toward the sweet spot ~65-75°
        if abs_lat < 65:
            base_prob = 0.15
        elif abs_lat < 72:
            base_prob = 0.40
        elif abs_lat < 80:
            base_prob = 0.55
        else:
            base_prob = 0.25  # near pole, less activity
        # Moonlight suppresses faint aurora visibility
        if moon_phase > 0.7 and moon_alt > 0:
            base_prob *= 0.4
        _rng = rng or _random.Random()
        if _rng.random() < base_prob:
            # Color: green is most common, purple/red for high activity
            color_roll = _rng.random()
            if color_roll < 0.65:
                color = "green"
            elif color_roll < 0.85:
                color = "green_purple"
            else:
                color = "purple_red"
            # Shape: arcs most common, curtains at higher activity
            shape_roll = _rng.random()
            if shape_roll < 0.45:
                shape = "arc"
            elif shape_roll < 0.75:
                shape = "curtain"
            elif shape_roll < 0.90:
                shape = "corona"
            else:
                shape = "diffuse"
            # Intensity 1-5 (Kp-like, affects brightness and movement)
            intensity = min(5, max(1, round(_rng.triangular(1, 5, 2.5))))
            aurora_info = {
                "color": color,
                "shape": shape,
                "intensity": intensity,
            }

    return _finish_sky(
        planets, moon_phase, moon_alt, milky_way_core_up, aurora_info
    )


def _finish_sky(
    planets: list[dict],
    moon_phase: float,
    moon_alt: float,
    milky_way_core_up: bool,
    aurora_info: dict | None,
) -> dict:
    """Assemble the visible_sky payload (single exit point)."""
    return {
        "planets": planets,
        "moon_phase": moon_phase,
        "moon_alt": moon_alt,
        "milky_way_core_up": milky_way_core_up,
        "aurora": aurora_info,
    }


def _aurora_mod_has_data() -> bool:
    """True when a real OVATION grid is loaded (so silence means silence)."""
    try:
        from nowhere import aurora as _aurora_mod

        return _aurora_mod.available()
    except Exception:  # noqa: BLE001
        return False
