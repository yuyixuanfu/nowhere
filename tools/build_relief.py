"""一次性构建: Natural Earth 50m 物理地形晕渲 → relief.jpg(纸感化)。

只在换数据源时手动跑: python tools/build_relief.py
生成物提交进仓库,运行时零网络。

源: HYP_50M_SR(晕渲+分高度设色),重染成乌有乡纸感(去饱和+暖茶调),
等距圆柱投影——和前端 proj(lat,lon) 严丝合缝。
"""

from __future__ import annotations

import io
import pathlib
import urllib.request
import zipfile

from PIL import Image, ImageEnhance

URL = "https://naciscdn.org/naturalearth/50m/raster/HYP_50M_SR.zip"
OUT = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "static" / "relief.jpg"
CACHE = pathlib.Path(__file__).resolve().parent / ".cache" / "HYP_50M_SR.zip"
SIZE = (2000, 1000)


def main() -> None:
    if CACHE.exists():
        raw = CACHE.read_bytes()
    else:
        raw = urllib.request.urlopen(URL, timeout=600).read()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(raw)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    tif_name = [n for n in zf.namelist() if n.lower().endswith(".tif")][0]
    img = Image.open(io.BytesIO(zf.read(tif_name))).convert("RGB")
    img = img.resize(SIZE, Image.LANCZOS)

    # 纸感重染: 保地形对比,暖茶调
    img = ImageEnhance.Color(img).enhance(0.5)
    img = ImageEnhance.Brightness(img).enhance(1.04)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    warm = Image.new("RGB", img.size, (243, 233, 208))
    img = Image.blend(img, warm, 0.22)

    OUT.write_bytes(b"")
    img.save(OUT, "JPEG", quality=82)
    print(f"{img.size} -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
