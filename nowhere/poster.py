"""明信片正面海报——按坐标生成真实路网海报(maptoposter vendor)。

可选增强: 依赖 osmnx/geopandas(重,联网拉 OSM)。没有就安静缺席,
前端用 SVG 兜底——核心游戏永不被它拖累。
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "tools" / "maptoposter" / "create_map_poster.py"
OUT_DIR = pathlib.Path(__file__).resolve().parent / "static" / "postcards"

_available: bool | None = None


def available() -> bool:
    """osmnx 在 + vendor 脚本在 + 主题在,才算能用。"""
    global _available
    if _available is None:
        try:
            import osmnx  # noqa: F401

            _available = (
                _SCRIPT.exists()
                and (_SCRIPT.parent / "themes" / "nowhere_paper.json").exists()
            )
        except Exception:
            _available = False
    return _available


async def generate(
    lat: float,
    lon: float,
    place: str,
    out_path: pathlib.Path,
    distance: int = 8000,
) -> bool:
    """按坐标生成纸感海报。成功 True,任何失败都 False(不炸主流程)。"""
    if not available():
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    label = place or "Nowhere"
    cmd = [
        sys.executable, "-X", "utf8", str(_SCRIPT),
        "--city", label, "--country", label,  # country 不能空串,展示名另给
        "--latitude", str(lat), "--longitude", str(lon),
        "--distance", str(distance),
        "--theme", "nowhere_paper",
        "--display-city", label,
        "--width", "6", "--height", "4",
        "--output", str(out_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_SCRIPT.parent),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180)
        return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 10_000
    except Exception:
        # 失败清理半成品
        try:
            if out_path.exists():
                out_path.unlink()
        except OSError:
            pass
        return False


def blank(out_path: pathlib.Path, place: str, lat: float, lon: float, surface: str = "") -> bool:
    """无路荒野的正面: 没有路,就画那里的样子。

    matplotlib 线稿,和路网海报一个画风(纸底墨线楷体)。
    沙漠=流沙曲线,冰雪=冰川等高线+裂隙,其他=等高线丘陵。
    不联网,必成。
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib.font_manager import FontProperties
    except ImportError:
        return False
    try:
        import sys as _sys

        font_path = None
        candidates = (
            [r"C:\Windows\Fonts\simkai.ttf", r"C:\Windows\Fonts\kaiu.ttf", r"C:\Windows\Fonts\simsun.ttc"]
            if _sys.platform == "win32"
            else ["/System/Library/Fonts/Supplemental/Songti.ttc"]
            if _sys.platform == "darwin"
            else ["/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"]
        )
        for p in candidates:
            if pathlib.Path(p).exists():
                font_path = p
                break
        f_big = FontProperties(fname=font_path, size=34) if font_path else FontProperties(size=34)
        f_small = FontProperties(fname=font_path, size=13) if font_path else FontProperties(size=13)

        seed = abs(hash(place)) % (2**32)
        rng = np.random.default_rng(seed)
        ink = "#4a3b2c"
        paper = "#f5f0e6"

        fig, ax = plt.subplots(figsize=(6, 4), dpi=200)
        fig.patch.set_facecolor(paper)
        ax.set_facecolor(paper)
        ax.set_xlim(0, 300)
        ax.set_ylim(0, 190)
        ax.axis("off")

        def flow_lines(n, y0, y1, amp, freq, rough, lw=0.7, alpha=0.75):
            xs = np.linspace(0, 300, 400)
            for i in range(n):
                ybase = y0 + (y1 - y0) * i / max(n - 1, 1)
                phase = rng.uniform(0, 2 * np.pi)
                drift = rng.uniform(-0.4, 0.4)
                y = (
                    ybase
                    + amp * np.sin(xs / 300 * freq * 2 * np.pi + phase)
                    + rough * np.sin(xs / 300 * freq * 7.3 * np.pi + phase * 2.7)
                    + drift * (xs / 300 - 0.5) * amp
                )
                ax.plot(xs, y, color=ink, lw=lw, alpha=alpha, solid_capstyle="round")

        if surface in ("snow", "ice"):
            # 冰川: 远处山脊 + 等高线 + 裂隙
            xs = np.linspace(0, 300, 300)
            ridge = 150 + 18 * np.sin(xs / 300 * 2.1 * np.pi + rng.uniform(0, 3)) + 8 * np.sin(xs / 300 * 6.7 * np.pi)
            ax.fill_between(xs, 190, ridge, color=paper, zorder=2)
            ax.plot(xs, ridge, color=ink, lw=1.2, zorder=3)
            for i in range(1, 7):
                ax.plot(xs, ridge - i * 9 - 4 * np.sin(xs / 40 + i), color=ink, lw=0.5, alpha=0.55, zorder=3)
            for _ in range(7):  # 裂隙
                x0 = rng.uniform(40, 260)
                y0 = rng.uniform(50, 110)
                ax.plot([x0, x0 + rng.uniform(-14, 14)], [y0, y0 - rng.uniform(8, 26)],
                        color=ink, lw=0.6, alpha=0.6, zorder=3)
        elif surface in ("sand", "bare"):
            # 沙丘: 流沙曲线一层一层
            flow_lines(16, 8, 148, amp=7, freq=2.2, rough=1.6)
        else:
            # 丘陵/旷野: 等高线团
            for cx, cy, rr in [(80, 70, 52), (215, 60, 62), (150, 30, 40)]:
                for i in range(7):
                    r = rr - i * (rr / 8)
                    t = np.linspace(0, 2 * np.pi, 200)
                    wob = 1 + 0.12 * np.sin(t * 3 + cx) + 0.06 * np.sin(t * 7 + cy)
                    ax.plot(cx + r * wob * np.cos(t), cy + r * 0.55 * wob * np.sin(t),
                            color=ink, lw=0.55, alpha=0.6)

        # 纸框
        for lw_, m in ((2.2, 4), (0.6, 7)):
            ax.add_patch(plt.Rectangle((m, m), 300 - 2 * m, 190 - 2 * m,
                                       fill=False, edgecolor=ink, lw=lw_, zorder=5))
        label = place or "Nowhere"
        paper_box = dict(facecolor=paper, edgecolor="none", pad=5)
        ax.text(150, 168, label, ha="center", va="center", color=ink,
                fontproperties=f_big, zorder=6, bbox=paper_box)
        coord = f"{abs(lat):.2f}{'N' if lat >= 0 else 'S'}  {abs(lon):.2f}{'E' if lon >= 0 else 'W'}"
        ax.text(150, 152, coord, ha="center", va="center", color=ink,
                fontproperties=f_small, alpha=0.85, zorder=6, bbox=paper_box)
        ax.text(150, 14, "此处无路可画 · 乌有乡", ha="center", va="center", color=ink,
                fontproperties=f_small, alpha=0.5, zorder=6)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, facecolor=paper, bbox_inches="tight", pad_inches=0)
        plt.close(fig)
        return True
    except Exception:
        return False


def cleanup_orphans(keep_ids: set[int]) -> None:
    """明信片只留最近 20 张,图也跟着收。"""
    if not OUT_DIR.exists():
        return
    for p in OUT_DIR.glob("card_*.png"):
        try:
            cid = int(p.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        if cid not in keep_ids:
            try:
                p.unlink()
            except OSError:
                pass
