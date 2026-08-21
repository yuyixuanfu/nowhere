"""Web observer layer -- a quiet window into the nowhere walk.

Endpoints
---------
GET  /           -> static/index.html
GET  /state      -> JSON snapshot of position, path, mode, time, last_text, radio, env
POST /message    -> enqueue a human message into state.messages
GET  /messages   -> list of {"content", "encountered"} dicts
GET  /history    -> landings(地名/坐标/次数) + path
GET  /marks      -> 全部标记
GET  /sightings  -> 动物目击编录
GET  /postcards  -> 明信片列表
POST /postcard/{id}/reply -> 人回明信片
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from nowhere import marks as marks_mod
from nowhere import placememory
from nowhere.server import reply_postcard_impl
import nowhere.server as _server

# ── Injection guard ──────────────────────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"忽略.{0,10}(之前|以上|先前|前面|所有)",
        r"无视.{0,10}(之前|以上|先前|前面|所有)",
        r"ignore\s+(previous|all|above|prior)\s+(instructions?|prompts?|rules?)",
        r"ignore\s+(everything|all)\s+(above|before|prior)",
        r"以上.{0,6}(指令|命令|规则|指示|提示词)",
        r"system\s*prompt",
        r"你现在是",
        r"new\s+instructions",
        r"disregard\s+(previous|all|above|prior)",
        r"forget\s+(everything|all|your)\s+(instructions?|rules?|prompts?)",
        r"你(现在)?的角色是",
        r"你是一个(?!什么)",
        r"act\s+as\s+(?:a\s+)?(?:different|new|another)",
        r"override\s+(?:previous|all|your)\s+(?:instructions?|rules?)",
    ]
]

_MSG_MAX_LEN = 200
_REPLY_MAX_LEN = 300


def _strip_control_chars(text: str) -> str:
    """Remove control characters (\\x00-\\x1f) but keep newlines."""
    return re.sub(r"[\x00-\x09\x0b-\x1f]", "", text)


def _check_injection(text: str) -> bool:
    """Return True if text matches any injection pattern."""
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return True
    return False

_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"


def _state():
    """当前世界状态(open_door 会换实例,必须动态取)。"""
    return _server._state


# ── Handlers ─────────────────────────────────────────────────────────


async def get_history(_request: Request) -> JSONResponse:
    """落点、当前路径和持久化的旅行足迹。"""
    s = _state()
    return JSONResponse({
        "landings": placememory.landings(),
        "path": s.path,
        "footprints": placememory.journey_footprints(),
    })


async def get_marks(_request: Request) -> JSONResponse:
    """全部标记。"""
    return JSONResponse(marks_mod.all())


async def get_sightings(_request: Request) -> JSONResponse:
    """动物目击编录。"""
    return JSONResponse(placememory.sightings())


async def index(_request: Request):
    """Serve the single-page observer UI."""
    return FileResponse(_STATIC_DIR / "index.html")


async def state(_request: Request) -> JSONResponse:
    """Return a JSON snapshot of the current world state."""
    s = _state()

    pos: list[float] | None = None
    if s.pos is not None:
        pos = [s.pos[0], s.pos[1]]

    local_time: str | None = None
    now = s.now()
    if now is not None and pos is not None:
        try:
            tz_name = _server._tf.timezone_at(lat=pos[0], lng=pos[1])
            if tz_name:
                local_dt = now.astimezone(ZoneInfo(tz_name))
                local_time = local_dt.isoformat()
            else:
                local_time = now.isoformat()
        except Exception:
            local_time = now.isoformat()
    elif now is not None:
        local_time = now.isoformat()

    # radio from last_env
    radio_info: dict | None = None
    if s.last_env and s.last_env.get("radio"):
        r = s.last_env["radio"]
        radio_info = {"name": r.get("name", ""), "stream_url": r.get("stream_url", "")}

    # env from last_env — both nested ({terrain:{...}}) and top-level
    # ({elevation, surface, ...}) shapes appear in the codebase.
    env_info: dict | None = None
    if s.last_env:
        weather = s.last_env.get("weather", {})
        nested_terrain = s.last_env.get("terrain")
        if isinstance(nested_terrain, dict):
            terrain = nested_terrain
        else:
            # top-level shape — synthesize terrain dict
            terrain = {
                k: s.last_env.get(k)
                for k in ("elevation", "surface")
                if k in s.last_env
            }
        env_info = {
            "elevation": terrain.get("elevation"),
            "temp_c": weather.get("temp_c"),
            "wind_ms": weather.get("wind_ms"),
            "surface": terrain.get("surface"),
        }

    return JSONResponse({
        "pos": pos,
        "path": s.path,
        "mode": s.mode,
        "local_time": local_time,
        "last_text": s.last_text,
        "radio": radio_info,
        "env": env_info,
    })


async def post_message(request: Request) -> JSONResponse:
    """Enqueue a human message into state.messages."""
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    content = body.get("content", "")
    if not isinstance(content, str):
        return _bad_request("bad_content")
    content = content.strip()
    if not content:
        return JSONResponse({"ok": False, "error": "empty content"}, status_code=400)
    # -- Injection guard --
    content = _strip_control_chars(content)
    if _check_injection(content):
        return _bad_request("rejected")
    content = content[:_MSG_MAX_LEN]
    state = _state()
    state.messages.append({"content": content, "encountered": False})
    state.save()
    return JSONResponse({"ok": True, "queued": len(state.messages)})


async def get_messages(_request: Request) -> JSONResponse:
    """Return all queued messages."""
    return JSONResponse(list(_state().messages))


async def get_postcards(_request: Request) -> JSONResponse:
    """明信片墙: 落盘文件是真相——任何进程寄的都在,新的在前。"""
    return JSONResponse(placememory.postcards())


async def reply_postcard(request: Request) -> JSONResponse:
    """人在某张明信片下回话。回话进留言池,AI 在路上捡到。"""
    card_id = int(request.path_params["card_id"])
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    content = body.get("content", "")
    if not isinstance(content, str):
        return _bad_request("bad_content")
    content = content.strip()
    if not content:
        return JSONResponse({"ok": False, "error": "empty content"}, status_code=400)
    # -- Injection guard --
    content = _strip_control_chars(content)
    if _check_injection(content):
        return _bad_request("rejected")
    content = content[:_REPLY_MAX_LEN]
    result = reply_postcard_impl(card_id, content)
    return JSONResponse(result, status_code=200 if result["ok"] else 404)


async def delete_postcard(request: Request) -> JSONResponse:
    """撕掉一张明信片(测试卡/废卡别留墙上)。"""
    card_id = int(request.path_params["card_id"])
    ok = placememory.delete_postcard(card_id)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 404)


# ── Tool API endpoints ──────────────────────────────────────────────────


def _json_or_text(d: dict) -> JSONResponse:
    """Wrap a tool result dict as JSON, ensuring text is included."""
    return JSONResponse(d)


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=400)


async def _body(request: Request) -> dict | JSONResponse:
    if not await request.body():
        return {}
    try:
        body = await request.json()
    except Exception:
        return _bad_request("invalid_json")
    if not isinstance(body, dict):
        return _bad_request("object_required")
    return body


def _number(body: dict, key: str, default: float) -> float | JSONResponse:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _bad_request(f"bad_{key}")
    if not math.isfinite(value):
        return _bad_request(f"bad_{key}")
    return float(value)


async def api_open_door(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    to = body.get("to")
    if to is not None and not isinstance(to, str):
        return _bad_request("bad_to")
    traveler_name = body.get("traveler_name")
    if traveler_name is not None and not isinstance(traveler_name, str):
        return _bad_request("bad_traveler_name")
    r = await _server.open_door_impl(to=to, traveler_name=traveler_name)
    return _json_or_text(r)


async def api_walk(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    direction = body.get("direction", "forward")
    if not isinstance(direction, str):
        return _bad_request("bad_direction")
    distance = _number(body, "distance_km", 2.0)
    if isinstance(distance, JSONResponse):
        return distance
    r = await _server.walk_impl(direction=direction, distance_km=distance)
    return _json_or_text(r)


async def api_listen(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    seconds = _number(body, "seconds", 10)
    if isinstance(seconds, JSONResponse):
        return seconds
    r = await _server.listen_impl(seconds=int(seconds))
    return _json_or_text(r)


async def api_look_around(request: Request) -> JSONResponse:
    r = await _server.look_around_impl()
    return _json_or_text(r)


async def api_ask(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    topic = body.get("topic", "")
    if not isinstance(topic, str):
        return _bad_request("bad_topic")
    r = await _server.ask_impl(topic=topic)
    return _json_or_text(r)


async def api_send_postcard(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    text = body.get("text", "")
    if not isinstance(text, str):
        return _bad_request("bad_text")
    r = _server.send_postcard_impl(text=text)
    return _json_or_text(r)


async def api_where_am_i(request: Request) -> JSONResponse:
    r = _server.where_am_i_impl()
    return _json_or_text(r)


async def api_continue(request: Request) -> JSONResponse:
    if await request.body():
        body = await _body(request)
        if isinstance(body, JSONResponse):
            return body
        if body:
            return _bad_request("no_arguments_allowed")
    r = await _server.continue_journey()
    return _json_or_text(r)


async def api_mark(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    name = body.get("name", "")
    note = body.get("note", "")
    if not isinstance(name, str):
        return _bad_request("bad_name")
    if not isinstance(note, str):
        return _bad_request("bad_note")
    name = name.strip()
    if not name:
        return _bad_request("empty_name")
    if len(name) > 200 or len(note) > 1000:
        return _bad_request("too_long")
    r = _server.mark_impl(name=name, note=note)
    return _json_or_text(r)


async def api_walk_to(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    place = body.get("place", "")
    if not isinstance(place, str):
        return _bad_request("bad_place")
    place = place.strip()
    if not place:
        return _bad_request("empty_place")
    if len(place) > 200:
        return _bad_request("too_long")
    r = await _server.walk_to_impl(place=place)
    return _json_or_text(r)


async def api_wait(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    hours = _number(body, "hours", 1.0)
    if isinstance(hours, JSONResponse):
        return hours
    r = await _server.wait_impl(hours=hours)
    return _json_or_text(r)


# ── App ───────────────────────────────────────────────────────────────

app = Starlette(
    routes=[
        Route("/", index),
        # observer endpoints
        Route("/state", state),
        Route("/message", post_message, methods=["POST"]),
        Route("/messages", get_messages),
        Route("/postcards", get_postcards),
        Route("/postcard/{card_id:int}/reply", reply_postcard, methods=["POST"]),
        Route("/postcard/{card_id:int}", delete_postcard, methods=["DELETE"]),
        Route("/history", get_history),
        Route("/marks", get_marks),
        Route("/sightings", get_sightings),
        # tool API endpoints
        Route("/open_door", api_open_door, methods=["POST"]),
        Route("/walk", api_walk, methods=["POST"]),
        Route("/listen", api_listen, methods=["POST"]),
        Route("/look_around", api_look_around, methods=["POST"]),
        Route("/ask", api_ask, methods=["POST"]),
        Route("/postcard", api_send_postcard, methods=["POST"]),
        Route("/where_am_i", api_where_am_i, methods=["POST"]),
        Route("/continue", api_continue, methods=["POST"]),
        Route("/mark", api_mark, methods=["POST"]),
        Route("/walk_to", api_walk_to, methods=["POST"]),
        Route("/wait", api_wait, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=_STATIC_DIR), name="static"),
    ],
)
