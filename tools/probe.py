# -*- coding: utf-8 -*-
"""列出某个容器内所有元素的盒模型 + 计算样式，用于定位「看着不对」的元素。

用法：python tools/probe.py <html> --root ".nature" --filter x=700,900
"""
import argparse
import html as _h
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

JS = """
<script>
window.addEventListener('load', function () {
 try {
  var root = document.querySelector(%(root)s);
  if (!root) { document.title = 'M|ROOT MISSING|'; return; }
  var rows = [], k = 0;
  var all = root.querySelectorAll('*');
  for (var i = 0; i < all.length; i++) {
    var n = all[i];
    if (n.tagName !== 'svg' && n.closest('svg')) continue;   // SVG 内部整体跳过
    var r = n.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    var cs = getComputedStyle(n);
    var cls = (typeof n.className === 'string') ? n.className : '';
    rows.push([k++, n.tagName, cls,
               Math.round(r.left), Math.round(r.top + window.scrollY),
               Math.round(r.width), Math.round(r.height),
               cs.display, cs.position, (cs.backgroundImage || '').slice(0, 24),
               (n.textContent || '').replace(/[\\r\\n\\t ]+/g, ' ').trim().slice(0, 26)]);
  }
  window.__rows = rows;
  document.title = 'M|' + rows.length + ' rows ok';
  document.body.setAttribute('data-rows', JSON.stringify(rows));
 } catch (e) { document.title = 'M|ERR ' + e.message; }
});
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--root", default="body")
    ap.add_argument("--filter", default="")
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()

    # 注意：选择器必须带引号，否则 querySelector(.foo) 是语法错误，
    # 整个 <script> 块解析失败 → title 不变 → 看着像「脚本没跑」
    root = json.dumps(a.root)
    prof = tempfile.mkdtemp(prefix="probe-")
    try:
        tmp = pathlib.Path(prof) / "p.html"
        tmp.write_text(pathlib.Path(a.html).read_text(encoding="utf-8")
                       .replace("</head>", (JS % {"root": root}) + "</head>", 1),
                       encoding="utf-8")
        p = subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
             "--window-size=1240,1400", "--virtual-time-budget=4000", "--dump-dom",
             "--user-data-dir=" + prof, tmp.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r'<title>M\|([^<]*)</title>', p.stdout or "", re.S)
        if not m:
            print("probe 失败（没拿到 title）")
            print((p.stdout or "")[:400])
            return 1
        if not m.group(1).endswith("rows ok"):
            print("probe 失败：%s" % m.group(1))
            return 1

        dm = re.search(r'data-rows="(.*?)"\s*>', p.stdout or "", re.S)
        if not dm:
            print("probe 失败（没拿到 data-rows）")
            return 1
        rows = json.loads(_h.unescape(dm.group(1)))

        flt = None
        if a.filter:
            k, v = a.filter.split("=")
            lo, hi = [float(x) for x in v.split(",")]
            flt = (k, lo, hi)

        n = 0
        print("%-4s %-10s %-26s %6s %6s %6s %6s  %-14s %-9s %s"
              % ("#", "tag", "class", "left", "top", "w", "h", "display", "bg", "text"))
        for f in rows:
            if flt:
                idx = {"x": 3, "y": 4, "w": 5, "h": 6, "b": 4}[flt[0]]
                val = float(f[idx])
                if idx == 4:
                    val = val + float(f[6])
                if not (flt[1] <= val <= flt[2]):
                    continue
            print("%-4s %-10s %-26s %6s %6s %6s %6s  %-14s %-9s %s"
                  % (f[0], f[1], str(f[2])[:26], f[3], f[4], f[5], f[6],
                     f[7], f[8], f[10]))
            n += 1
            if n >= a.limit:
                break
        print("-- 共 %d 条（容器内合计 %d）--" % (n, len(rows)))
        return 0
    finally:
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
