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
import pathlib
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from nowhere import marks as marks_mod
from nowhere import placememory
from nowhere.server import reply_postcard_impl
import nowhere.server as _server

_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"


def _state():
    """当前世界状态(open_door 会换实例,必须动态取)。"""
    return _server._state


# ── Handlers ─────────────────────────────────────────────────────────


async def get_history(_request: Request) -> JSONResponse:
    """落点(地名/坐标/次数) + 走过的 path。"""
    s = _state()
    return JSONResponse({
        "landings": placememory.landings(),
        "path": s.path,
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
    if now is not None:
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
    body = await request.json() if await request.body() else {}
    content = body.get("content", "")
    if not content:
        return JSONResponse({"ok": False, "error": "empty content"}, status_code=400)
    # Mirror send_postcard_impl's 1000-char cap so a malicious client can't
    # fill deque + on-disk JSON with a huge payload.
    if len(content) > 1000:
        return JSONResponse({"ok": False, "error": "too_long"}, status_code=400)
    _state().messages.append({"content": content, "encountered": False})
    return JSONResponse({"ok": True, "queued": len(_state().messages)})


async def get_messages(_request: Request) -> JSONResponse:
    """Return all queued messages."""
    return JSONResponse(list(_state().messages))


async def get_postcards(_request: Request) -> JSONResponse:
    """明信片墙: 落盘文件是真相——任何进程寄的都在,新的在前。"""
    return JSONResponse(placememory.postcards())


async def reply_postcard(request: Request) -> JSONResponse:
    """人在某张明信片下回话。回话进留言池,AI 在路上捡到。"""
    from nowhere.server import reply_postcard_impl

    card_id = int(request.path_params["card_id"])
    body = await request.json() if await request.body() else {}
    content = (body.get("content") or "").strip()
    if not content:
        return JSONResponse({"ok": False, "error": "empty content"}, status_code=400)
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


async def api_open_door(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = await _server.open_door_impl(to=body.get("to"))
    return _json_or_text(r)


async def api_walk(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = await _server.walk_impl(
        direction=body.get("direction", "forward"),
        distance_km=body.get("distance_km", 2.0),
    )
    return _json_or_text(r)


async def api_listen(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = await _server.listen_impl(seconds=body.get("seconds", 10))
    return _json_or_text(r)


async def api_look_around(request: Request) -> JSONResponse:
    r = await _server.look_around_impl()
    return _json_or_text(r)


async def api_ask(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = await _server.ask_impl(topic=body.get("topic", ""))
    return _json_or_text(r)


async def api_send_postcard(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = _server.send_postcard_impl(text=body.get("text", ""))
    return _json_or_text(r)


async def api_where_am_i(request: Request) -> JSONResponse:
    r = _server.where_am_i_impl()
    return _json_or_text(r)


async def api_mark(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = _server.mark_impl(
        name=body.get("name", ""),
        note=body.get("note", ""),
    )
    return _json_or_text(r)


async def api_walk_to(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = await _server.walk_to_impl(place=body.get("place", ""))
    return _json_or_text(r)


async def api_wait(request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    r = await _server.wait_impl(hours=body.get("hours", 1.0))
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
        Route("/mark", api_mark, methods=["POST"]),
        Route("/walk_to", api_walk_to, methods=["POST"]),
        Route("/wait", api_wait, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=_STATIC_DIR), name="static"),
    ],
)
