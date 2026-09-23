# -*- coding: utf-8 -*-
"""离线 HTML 视觉验收：无头 Chrome 截图（本机没装 agent-browser）。

用法：
  python tools/shot.py <html> --offsets 0,1200,2400 --out _shots
  python tools/shot.py <html> --measure .nature,.splist,.skychart

坑（本机实测）：
- headless 最小窗宽约 500px，设 430 会输出被裁的图，看着像横向溢出，是假象
- 靠锚点跳转截不到中段（scroll-behavior:smooth 来不及滚），
  要截中段就注入 body{position:relative;top:-Npx}
- --dump-dom 会等 load 后执行 JS，可用它读回测量值
"""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

MEASURE_TPL = """
<script>
window.addEventListener('load', function () {
  var out = [];
  var sels = %s;
  for (var i = 0; i < sels.length; i++) {
    var n = document.querySelector(sels[i]);
    if (!n) { out.push(sels[i] + '=MISSING'); continue; }
    var r = n.getBoundingClientRect();
    out.push(sels[i] + '=' + Math.round(r.top + window.scrollY) + ',' + Math.round(r.height));
  }
  document.title = 'MEASURE|' + out.join('|') +
                   '|BODY=' + document.body.scrollHeight;
});
</script>
"""


def _chrome(args, profile):
    return subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--user-data-dir=" + profile] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90)          # 给个上限，别把会话挂死


def measure(html, sels, as_print=False):
    """返回 {选择器: (offsetTop, height)} 和页面总高。"""
    profile = tempfile.mkdtemp(prefix="shot-")
    try:
        tmp = pathlib.Path(profile) / "m.html"
        js = MEASURE_TPL % ("[" + ",".join("'%s'" % s for s in sels) + "]")
        txt = pathlib.Path(html).read_text(encoding="utf-8")
        if as_print:
            txt = txt.replace("@media print{", "@media all{", 1)
        txt = txt.replace("</head>", js + "</head>", 1)
        tmp.write_text(txt, encoding="utf-8")
        p = _chrome(["--window-size=1240,1400", "--virtual-time-budget=4000",
                     "--dump-dom", tmp.as_uri()], profile)
        m = re.search(r"<title>MEASURE\|(.*?)</title>", p.stdout or "", re.S)
        if not m:
            print("测量失败，未拿到 MEASURE。stdout 片段：")
            print((p.stdout or "")[:600])
            print((p.stderr or "")[:600])
            return {}, 0
        parts = m.group(1).split("|")
        body = 0
        boxes = {}
        for part in parts:
            if part.startswith("BODY="):
                body = int(part[5:])
                continue
            k, v = part.split("=", 1)
            if v == "MISSING":
                boxes[k] = None
                continue
            t, h = v.split(",")
            boxes[k] = (int(t), int(h))
        return boxes, body
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def shot(html, offset, out, profile, width=1240, height=1500, dsf=1, as_print=False):
    tmp = pathlib.Path(profile) / ("s%d.html" % offset)
    txt = pathlib.Path(html).read_text(encoding="utf-8")
    if as_print:
        # Chrome 命令行截不了 print 媒体，把打印块改成 @media all 直接在屏幕上看
        txt = txt.replace("@media print{", "@media all{", 1)
    if offset:
        css = ("<style>html{scroll-behavior:auto!important}"
               "body{position:relative;top:-%dpx}</style>" % offset)
        txt = txt.replace("</head>", css + "</head>", 1)
    tmp.write_text(txt, encoding="utf-8")
    out = pathlib.Path(out).resolve()          # Chrome 不吃相对路径，必须绝对
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["--window-size=%d,%d" % (width, height), "--virtual-time-budget=4000"]
    if dsf != 1:
        args.append("--force-device-scale-factor=%s" % dsf)
    args.append("--screenshot=" + str(out))
    args.append(tmp.as_uri())            # 别忘了页面 URL，漏了 Chrome 会挂住
    _chrome(args, profile)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--measure", default="")
    ap.add_argument("--offsets", default="0")
    ap.add_argument("--out", default="_shots")
    ap.add_argument("--width", type=int, default=1240)
    ap.add_argument("--height", type=int, default=1500)
    ap.add_argument("--dsf", type=float, default=1.0, help="设备像素比，2 可看清小字")
    ap.add_argument("--tag", default="off", help="输出文件名前缀")
    ap.add_argument("--print", dest="as_print", action="store_true",
                    help="临时把 @media print 改成 @media all，用来核对打印版式")
    a = ap.parse_args()

    if not pathlib.Path(CHROME).exists():
        print("找不到 Chrome：%s" % CHROME)
        return 1

    profile = tempfile.mkdtemp(prefix="shot-")
    try:
        if a.measure:
            sels = [s for s in a.measure.split(",") if s]
            boxes, body = measure(a.html, sels, a.as_print)
            print("页面总高 %d px" % body)
            for s in sels:
                b = boxes.get(s)
                print("  %-22s %s" % (s, "缺失" if b is None else "top=%d h=%d" % b))

        for off in [int(x) for x in a.offsets.split(",") if x.strip()]:
            p = shot(a.html, off, pathlib.Path(a.out) / ("%s%05d.png" % (a.tag, off)),
                     profile, a.width, a.height, a.dsf, a.as_print)
            print("截图 %s" % p)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
