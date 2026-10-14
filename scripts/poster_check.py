#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""poster_check.py · 生成图体检

    python tools/poster_check.py <图.png> [更多图...]

生图接口会**偶发返回一张近乎空白的图**却仍然报成功。空白图直接交付就是白跑一趟，
所以在交给用户之前先机器判一道：

  · 尺寸：A4 竖幅（宽 ≥ 1000、高 ≥ 1400；底部裁掉 4.5% 水印后高度约 1466）
  · 空白：灰度标准差过低、或采样色数过少 —— 两条都中才算空白

退出码：0 = 可用；1 = 有问题（空白/尺寸不对/读不了）；2 = 环境缺 PIL 没法判。
"""
from __future__ import annotations

import os
import sys

MIN_W, MIN_H = 1000, 1400
BLANK_STD = 8.0
BLANK_COLORS = 300


def measure(path):
    """返回 (宽, 高, 灰度标准差, 采样色数)。"""
    from PIL import Image, ImageStat
    im = Image.open(path).convert("RGB")
    stat = ImageStat.Stat(im)
    std = sum(stat.stddev) / 3.0
    small = im.resize((80, 120))
    colors = len(small.getcolors(80 * 120) or [])
    return im.size[0], im.size[1], std, colors


def check(path):
    """返回 (是否可用, 说明)。"""
    if not os.path.isfile(path):
        return False, "文件不存在"
    try:
        w, h, std, colors = measure(path)
    except Exception as e:                                  # noqa: BLE001
        return False, "读不了：%s" % e
    if w < MIN_W or h < MIN_H:
        return False, "尺寸 %dx%d 偏小（要 ≥ %dx%d）" % (w, h, MIN_W, MIN_H)
    if std < BLANK_STD and colors < BLANK_COLORS:
        return False, ("近乎空白（标准差 %.1f < %.0f，采样色数 %d < %d）—— 生图偶发返回空白，"
                       "重新生成" % (std, BLANK_STD, colors, BLANK_COLORS))
    return True, "%dx%d，标准差 %.1f，采样色数 %d" % (w, h, std, colors)


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    try:
        import PIL                                              # noqa: F401
    except ImportError:
        print("! 环境没有 Pillow，装不了就直接看图判断；本工具跳过", file=sys.stderr)
        return 2
    bad = 0
    for p in argv:
        ok, note = check(p)
        print("%s %s — %s" % ("ok " if ok else "x  ", p, note))
        if not ok:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
