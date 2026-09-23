# -*- coding: utf-8 -*-
"""v4.6.0 交付前一把过：编译 → 测试 → 红方 → 渲染 → 一致性 → 示例产物同步。

为什么渲染到临时目录
--------------------
渲染产物头部带「生成于 <日期 时间>」，每跑一次就变一次字节。早先这一步是
**就地**重渲染 `examples/`，于是验收刚跑完，仓库里的示例产物、安装目录、上架
zip 三方立刻互相不一致，每次提交都多一条无意义的 diff（"生成于" 那行）。

现在渲染到临时目录：用得着的产物一个字节都不动。示例产物有没有跟上渲染器，
交给第 6 步判定（忽略生成时间后逐字节比），要刷新就显式加 `--refresh`。

用法：
    python tools/verify_all.py              # 常规验收，不动仓库里任何产物
    python tools/verify_all.py --refresh    # 顺手把 examples/ 的示例产物刷成当前渲染器输出
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
EXAMPLE = ROOT / "examples" / "weihai-2d"
# 产物头部的时间戳，比对时归一化掉
STAMP = re.compile(r"生成于\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}")


def run(title, args, tail=16):
    p = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print("=" * 60)
    print("%s  (exit=%d)" % (title, p.returncode))
    print("=" * 60)
    out = (p.stdout or "").strip().splitlines()
    err = (p.stderr or "").strip().splitlines()
    for line in out[-tail:]:
        print(line)
    if p.returncode != 0 and err:
        print("-- stderr --")
        for line in err[-10:]:
            print(line)
    return p.returncode


def normalize(path):
    return STAMP.sub("生成于 T", path.read_text(encoding="utf-8"))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    refresh = "--refresh" in argv

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="travel-orch-verify-"))
    bad = 0
    try:
        bad += run("1. 编译", ["-m", "py_compile", "scripts/render_plan.py",
                               "scripts/nature_widgets.py", "scripts/red_team.py",
                               "scripts/nature_forecast.py", "scripts/build_trip.py"],
                   tail=4)
        bad += run("2. 回归测试", ["tests/run_tests.py"], tail=6)
        bad += run("3. 红方复核（示例方案，直接读目录）",
                   ["scripts/red_team.py", "check", "examples/weihai-2d"], tail=20)
        bad += run("4. 渲染示例方案（分片目录 → MD + HTML，出到临时目录）",
                   ["scripts/build_trip.py", "render", "examples/weihai-2d",
                    "--out-dir", str(tmp)], tail=10)
        bad += run("5. 产物一致性（署名 / 纯离线单文件 / 关键数字可追）",
                   ["scripts/render_plan.py", "examples/weihai-2d",
                    "--check", "--out-dir", str(tmp)], tail=14)

        # 6. 仓库里的示例产物是否跟得上当前渲染器（忽略生成时间逐字节比）
        print("=" * 60)
        print("6. 示例产物与当前渲染器同步  (--refresh 可刷新)")
        print("=" * 60)
        fresh = sorted(p for p in tmp.iterdir() if p.suffix in (".md", ".html"))
        if not fresh:
            print("x 临时目录里没有产物，前一步大概是失败了")
            bad += 1
        else:
            drift = []
            for src in fresh:
                dst = EXAMPLE / src.name
                if not dst.exists():
                    drift.append(src.name + "（仓库里没有）")
                elif normalize(src) != normalize(dst):
                    drift.append(src.name)
            if drift:
                for name in drift:
                    print("x " + name + " 与当前渲染器输出不一致")
                if refresh:
                    for src in fresh:
                        shutil.copyfile(src, EXAMPLE / src.name)
                    print("已按 --refresh 刷新 examples/：" + "、".join(p.name for p in fresh))
                else:
                    print("  跑 `python tools/verify_all.py --refresh` 刷新")
                    print("  （通常意味着渲染逻辑改了，示例产物该重新生成）")
                    bad += 1
            else:
                print("一致（忽略生成时间）：" + "、".join(p.name for p in fresh)
                      + "，共 %d 个" % len(fresh))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(">>> 汇总：%s" % ("全部通过" if bad == 0 else "%d 步失败" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
