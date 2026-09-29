#!/usr/bin/env python3
"""把 AI 生图产物处理成 SkillHub 市场展示头像（512x512）。

ImageGen 产出右下角带 "AI生成 WORKBUDDY" 水印，且图形不一定满幅。
本脚本：Chrome 裁掉水印区 + 居中重构图 + 缩放到 512x512，输出 PNG 与 JPG 两份。

产出：
  _shots/icon.png  512x512 PNG
  _shots/icon.jpg  512x512 JPG（体积更小，上传更保险）
"""

import os
import struct
import subprocess
import sys
import tempfile

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# 源图（收翅线条鸟版）
SRC = "generated-images/A_minimalist_flat_app_icon__sq_2026-09-23T09-18-43.png"

CROP_RIGHT = 0.14   # 右侧水印宽度占比
CROP_BOTTOM = 0.13  # 下方水印高度占比
SHIFT_LEFT = -0.04  # 构图水平微调（负值向右移，把鸟推回中心）
SHIFT_VERT = -0.22  # 构图垂直微调（负值向下移）


def _png_size(path):
    d = open(path, "rb").read(33)
    return struct.unpack(">II", d[16:24])


def build_html(src, w, h):
    """按水印比例放大图片，再把水印推出可视区。"""
    scaled_w = round(512 / (1 - CROP_RIGHT))
    scaled_h = round(scaled_w * h / w)
    cut_bottom = round(scaled_h * CROP_BOTTOM)      # 被裁掉的下边像素
    cut_right = round(scaled_w * CROP_RIGHT)        # 被裁掉的右边像素
    left = -round(cut_right * (1 + SHIFT_LEFT))     # 向右推，吃掉右边水印
    top = -round(cut_bottom / 2 * (1 + SHIFT_VERT)) # 上下均分，图形居中
    return """<!doctype html><html><head><meta charset="utf-8"><style>
      *{margin:0;padding:0;box-sizing:border-box}
      html,body{width:512px;height:512px;overflow:hidden;background:#00a1d6}
      .frame{width:512px;height:512px;overflow:hidden;position:relative}
      img{position:absolute;width:%(sw)dpx;height:%(sh)dpx;left:%(left)dpx;top:%(top)dpx}
    </style></head><body>
    <div class="frame"><img src="%(src)s"></div>
    </body></html>""" % {
        "sw": scaled_w, "sh": scaled_h, "left": left, "top": top,
        "src": "file:///" + src.replace("\\", "/").lstrip("/"),
    }


def shoot(html_path, out):
    subprocess.run([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--window-size=512,512",
        "--screenshot=%s" % out,
        "file:///%s" % html_path.replace("\\", "/").lstrip("/"),
    ], check=True, capture_output=True)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(root, SRC.replace("/", os.sep))
    if not os.path.exists(src):
        sys.exit("找不到源图：%s" % src)

    w, h = _png_size(src)
    out_dir = os.path.join(root, "_shots")
    os.makedirs(out_dir, exist_ok=True)

    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    tmp.write(build_html(src, w, h))
    tmp.close()
    html_path = tmp.name.replace("\\", "/").lstrip("/")

    png = os.path.join(out_dir, "icon.png")
    jpg = os.path.join(out_dir, "icon.jpg")
    shoot(html_path, png)
    shoot(html_path, jpg)
    os.unlink(tmp.name)

    for p in (png, jpg):
        kb = os.path.getsize(p) // 1024
        dim = _png_size(p) if p.endswith(".png") else "512x512"
        print("%-10s %4d KB  %s" % (os.path.basename(p), kb, dim))


if __name__ == "__main__":
    main()
