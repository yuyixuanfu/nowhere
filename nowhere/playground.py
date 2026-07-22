"""
乌有乡试玩 CLI —— 不需要 MCP，不需要 Claude Code，直接 python 跑。

用法:
    python -m nowhere.playground          # 启动试玩
    python -m nowhere.playground --web    # 同时起网页旁观者 (localhost:8077)
"""
import asyncio
import sys
import random
from datetime import datetime, timezone

# Windows GBK 终端兼容
import io
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
if sys.stdin.encoding and sys.stdin.encoding.lower() != "utf-8":
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")


def _parse_walk(arg: str) -> tuple[str, float]:
    """Parse walk argument into (direction, distance_km).

    ``walk N``      → ("N", 2.0)
    ``walk N 3.5``  → ("N", 3.5)
    ``walk``        → ("forward", 2.0)
    """
    if not arg:
        return "forward", 2.0
    parts = arg.split()
    direction = parts[0]
    distance = float(parts[1]) if len(parts) > 1 else 2.0
    return direction, distance


def _parse_listen(arg: str) -> int:
    """Parse listen argument into seconds.

    ``listen``   → 10
    ``listen 5`` → 5
    """
    return int(arg) if arg else 10


async def main():
    start_web = "--web" in sys.argv

    # 导入内部函数（和 server.py 一样的 _impl 链路）
    from nowhere import (
        server, state as state_mod, providers
    )

    _state = state_mod.WorldState
    s = server._state  # 共享状态

    print()
    print("=" * 50)
    print("  🌀 乌有乡 (Nowhere) — 试玩模式")
    print("=" * 50)
    print()
    print("命令:")
    print("  open [地名]    — 开门(随机落地或去指定地名)")
    print("  walk [方向]    — 走路(N/NE/E/SE/S/SW/W/NW/uphill/toward_sea)")
    print("  listen         — 听电台")
    print("  look           — 看看周围有什么")
    print("  ask [话题]     — 问关于这里的事")
    print("  mark [名字]    — 标记当前位置")
    print("  marks          — 列出所有标记")
    print("  postcard [话]  — 寄一张明信片回家")
    print("  walkto [地名]  — 朝一个地方走过去")
    print("  wait [小时]    — 原地待着,让时间流过去")
    print("  where          — 我在哪")
    print("  providers      — 看感官状态")
    print("  quit           — 退出")
    print()

    if start_web:
        print("🌐 网页旁观者层启动中... http://localhost:8077")
        asyncio.create_task(_start_web())
        print()

    while True:
        try:
            # to_thread: 别让 input 阻塞事件循环,网页旁观者要活
            line = (await asyncio.to_thread(input, "🌀 > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not line:
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        try:
            if cmd == "quit":
                print("再见。")
                break

            elif cmd == "open":
                to = arg if arg else None
                r = await server.open_door_impl(to=to)
                _print_result(r)

            elif cmd == "walk":
                direction, distance = _parse_walk(arg)
                r = await server.walk_impl(direction=direction, distance_km=distance)
                _print_result(r)

            elif cmd == "listen":
                seconds = _parse_listen(arg)
                r = await server.listen_impl(seconds=seconds)
                _print_result(r)

            elif cmd == "look":
                r = await server.look_around_impl()
                _print_result(r)

            elif cmd == "ask":
                if not arg:
                    print("  问什么？用法: ask 珠穆朗玛峰")
                    continue
                r = await server.ask_impl(topic=arg)
                _print_result(r)

            elif cmd == "mark":
                if not arg:
                    print("  标记什么？用法: mark 和她来过的地方 [备注]")
                    continue
                name_parts = arg.split(maxsplit=1)
                name = name_parts[0]
                note = name_parts[1] if len(name_parts) > 1 else ""
                r = server.mark_impl(name=name, note=note)
                _print_result(r)

            elif cmd == "marks":
                r = server.marks_impl()
                _print_result(r)

            elif cmd == "postcard":
                if not arg:
                    print("  写什么？用法: postcard 这里的风有沙子的味道")
                    continue
                r = server.send_postcard_impl(arg)
                _print_result(r)

            elif cmd == "walkto":
                if not arg:
                    print("  去哪？用法: walkto 缙云山")
                    continue
                r = await server.walk_to_impl(arg)
                _print_result(r)

            elif cmd == "wait":
                try:
                    hrs = float(arg) if arg else 1.0
                except ValueError:
                    hrs = 1.0
                r = await server.wait_impl(hrs)
                _print_result(r)

            elif cmd == "where":
                r = server.where_am_i_impl()
                _print_result(r)

            elif cmd == "providers":
                from nowhere.providers import provider_status
                status = provider_status()
                print()
                for source, s_val in status.items():
                    icon = {"ok": "🟢", "degraded": "🟡", "down": "🔴"}.get(s_val, "⚪")
                    print(f"  {icon} {source}: {s_val}")
                print()

            else:
                print(f"  未知命令: {cmd}")
                print("  可用: open/walk/listen/look/ask/mark/marks/where/providers/quit")

        except Exception as e:
            print(f"  ❌ 错误: {e}")


def _print_result(r: dict):
    """格式化打印结果"""
    print()
    print("─" * 50)
    print(r.get("text", "(无文本)"))
    print("─" * 50)

    data = r.get("data", {})
    if data:
        # 显示关键数据
        pos = data.get("position")
        if pos and isinstance(pos, (list, tuple)) and len(pos) >= 2:
            print(f"  📍 位置: {pos[0]:.4f}, {pos[1]:.4f}")

        step = data.get("step")
        if step:
            print(f"  👣 步: {'阻断' if step.get('blocked') else '前进'}"
                  f" | 海拔差 {step.get('elevation_delta', 0):+.0f}m"
                  f" | 坡度 {step.get('slope_deg', 0):.1f}°"
                  f" | 地表 {step.get('new_surface', '?')}")

        env = data.get("env")
        if env:
            print(f"  🌡️  {env.get('temp_c', '?')}°C"
                  f" | 风 {env.get('wind_ms', '?')} m/s"
                  f" | {env.get('surface', '?')}"
                  f" | 海拔 {env.get('elevation', '?')}m")

        radio = data.get("radio")
        if radio:
            print(f"  📻 电台: {radio.get('name', '?')}")

        stream = data.get("stream_url")
        if stream:
            playing = data.get("playing")
            if playing:
                print(f"  🔊 正在播放: {stream}")
            else:
                print(f"  🔊 流地址: {stream}")

        providers = data.get("providers")
        if providers:
            degraded = [k for k, v in providers.items() if v != "ok"]
            if degraded:
                print(f"  ⚠️  降级: {', '.join(degraded)}")

    print()


async def _start_web():
    """后台启动 web 服务"""
    import uvicorn
    from nowhere.web import app
    config = uvicorn.Config(app, host="0.0.0.0", port=8077, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
