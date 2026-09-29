#!/usr/bin/env python3
"""把 AI 生的鸟图抠掉蓝底 → 透明 PNG，供合成使用。

纯标准库实现：读 PNG → 解析像素 → 把接近背景蓝的像素设为全透明 → 写 PNG。
只在有 numpy 时走快路径；没有则用纯 Python 逐像素处理（512px 图可接受）。
"""

import os
import struct
import sys
import zlib

SRC = "generated-images/A_minimalist_flat_app_icon__sq_2026-09-23T09-18-43.png"
OUT = "_shots/_bird_cut.png"

# 背景蓝 #00a1d6 的容差（取大一些，把边缘抗锯齿的过渡像素一并吃掉）
BG = (0, 161, 214)
TOL = 90
# 水印区（右下角比例）直接判为背景，避免水印文字被当成前景保留
WM_RIGHT = 0.26
WM_BOTTOM = 0.16


def read_png(path):
    """极简 PNG 解码，支持 8bit RGB / RGBA，非隔行。"""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    pos, idat, w, h, bd, ct = 8, b"", 0, 0, 0, 0
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln
    assert bd == 8 and ct in (2, 6), "仅支持 8bit RGB/RGBA，实际 bd=%d ct=%d" % (bd, ct)
    nch = 3 if ct == 2 else 4
    raw = zlib.decompress(idat)

    # 反滤波
    stride = w * nch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if f == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, nch, out


def write_png(path, w, h, rgba):
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(typ, payload):
        return (struct.pack(">I", len(payload)) + typ + payload
                + struct.pack(">I", zlib.crc32(typ + payload) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    open(path, "wb").write(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(root, SRC.replace("/", os.sep))
    if not os.path.exists(src):
        sys.exit("找不到源图：%s" % src)

    w, h, nch, px = read_png(src)
    rgba = bytearray(w * h * 4)
    kept = 0
    wm_x = w * (1 - WM_RIGHT)   # 水印区左边界
    wm_y = h * (1 - WM_BOTTOM)  # 水印区上边界
    for i in range(w * h):
        x, y = i % w, i // w
        r, g, b = px[i * nch], px[i * nch + 1], px[i * nch + 2]
        # 水印区内的像素一律视为背景
        in_wm = x >= wm_x and y >= wm_y
        d = max(abs(r - BG[0]), abs(g - BG[1]), abs(b - BG[2]))
        if in_wm or d <= TOL:
            a = 0
        elif d >= TOL + 40:
            a = 255
        else:
            # 软过渡：容差带内线性提升不透明度，消除硬锯齿
            a = round((d - TOL) * 255.0 / 40)
        if a:
            kept += 1
        rgba[i * 4:i * 4 + 4] = bytes((r, g, b, a))

    out = os.path.join(root, OUT.replace("/", os.sep))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_png(out, w, h, rgba)
    print("抠图完成：%dx%d，保留像素 %.1f%% → %s"
          % (w, h, kept * 100.0 / (w * h), OUT))


if __name__ == "__main__":
    main()
