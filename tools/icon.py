#!/usr/bin/env python3
"""把 AI 生图产物处理成市场展示头像（512x512）。

ImageGen 出图右下角带 "AI生成 WORKBUDDY" 水印。本脚本切掉水印带、取居中正方形、
缩到 512，输出 PNG 与 JPG。纯 PIL，不依赖浏览器。

用法：
    python tools/icon.py                                  # 默认一套
    python tools/icon.py --src <源图> --name icon-v2      # 出第二个版本

默认：源图 _shots/avatar-src.png → 产出 _shots/icon.png、_shots/icon.jpg
      市场头像上传用 icon.png；--name 换个前缀就是另一版，两版并存不互相覆盖。

源图不是方图时会切掉底部水印带再取居中方块；--bottom 调水印带占比，
--bias 上下微调取景（0 居中，正数往下取）。
"""

import argparse
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(HERE, "_shots")

CROP_BOTTOM = 0.09          # 底部水印带占比
SIZE = 512


def build(src, name="icon", bottom=CROP_BOTTOM, bias=0.0, size=SIZE, quiet=False):
    """切水印 → 取居中方块（可按 bias 上下微调）→ 缩到 size。返回 (png, jpg)。"""
    if not os.path.exists(src):
        raise FileNotFoundError("找不到源图：%s" % src)
    im = Image.open(src).convert("RGB")
    w, h = im.size
    im = im.crop((0, 0, w, max(1, int(h * (1 - bottom)))))
    w, h = im.size
    s = min(w, h)
    top = int((h - s) * (0.5 + bias))
    top = max(0, min(top, h - s))
    left = (w - s) // 2
    im = im.crop((left, top, left + s, top + s))
    im = im.resize((size, size), Image.LANCZOS)
    os.makedirs(SHOTS, exist_ok=True)
    png = os.path.join(SHOTS, name + ".png")
    jpg = os.path.join(SHOTS, name + ".jpg")
    im.save(png, "PNG", optimize=True)
    im.save(jpg, "JPEG", quality=92, optimize=True)
    if not quiet:
        for p in (png, jpg):
            print("%-12s %dx%d  %.0f KB" % (os.path.basename(p), size, size,
                                            os.path.getsize(p) / 1024))
    return png, jpg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(SHOTS, "avatar-src.png"))
    ap.add_argument("--name", default="icon", help="输出前缀，默认 icon")
    ap.add_argument("--bottom", type=float, default=CROP_BOTTOM, help="底部水印带占比")
    ap.add_argument("--bias", type=float, default=0.0, help="取景上下微调，正数往下")
    a = ap.parse_args()
    try:
        build(a.src, a.name, a.bottom, a.bias)
    except FileNotFoundError as e:
        print(e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
