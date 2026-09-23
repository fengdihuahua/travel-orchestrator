#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doctor.py — travel-orchestrator 环境与依赖自检 (v1.0.0)

用途：一条命令回答"为什么我这边跑不起来"，把 90% 的报错挡在门口。
检查项：Python 版本、标准库、脚本能否编译、目录与写权限、各接口连通性、eBird Token。

用法：
  python scripts/doctor.py
  python scripts/doctor.py --quick     # 跳过网络检查，只查本地
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import ssl

VERSION = "1.0.0"
TIMEOUT = 12
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "travel-orchestrator-doctor/" + VERSION}

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)


def configure_console():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def dwidth(s):
    """显示宽度：中文/全角算 2 列，用于对齐输出。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(s))


def pad(s, width):
    return str(s) + " " * max(0, width - dwidth(s))


def probe(name, url, need_key=True, expect_json=True):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            body = r.read(65536)
        if expect_json:
            head = body.lstrip()[:1]
            if head in (b"{", b"["):
                note = "OK（JSON）"
            elif head == b"<":
                note = "OK（返回 HTML，接口可能变更）"
            else:
                note = "OK（非 JSON 响应）"
        else:
            note = "OK（HTTP " + str(r.status) + "）"
        return True, note
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and need_key:
            return True, "端点在，需要 Key（HTTP " + str(e.code) + "）"
        return False, "HTTP " + str(e.code)
    except Exception as e:
        return False, str(e)[:60]


def main(argv=None):
    configure_console()
    ap = argparse.ArgumentParser(description="travel-orchestrator 自检 v" + VERSION)
    ap.add_argument("--quick", action="store_true", help="跳过网络检查")
    args = ap.parse_args(argv)

    problems, warnings = [], []
    print("=" * 66)
    print("travel-orchestrator 环境自检 v" + VERSION)
    print("=" * 66)

    # ---- 1. Python 与标准库
    print("\n[1] Python 运行时")
    v = sys.version_info
    print("    解释器: " + sys.executable)
    print("    版本  : " + sys.version.split()[0])
    if v < (3, 8):
        problems.append("Python 版本过低（需 3.8+），当前 " + sys.version.split()[0])
        print("    [FAIL] 需要 Python 3.8 以上")
    else:
        print("    [OK]   版本满足要求（需要 3.8+）")
    missing = []
    for mod in ("json", "urllib.request", "datetime", "calendar", "argparse", "re"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        problems.append("标准库缺失：" + "、".join(missing) + "（Python 安装不完整）")
        print("    [FAIL] 缺少标准库: " + "、".join(missing))
    else:
        print("    [OK]   所需标准库齐全（本工具只用标准库，无需 pip install）")

    # ---- 2. 脚本能否编译
    print("\n[2] 脚本完整性")
    import py_compile
    scripts = ["nature_forecast.py", "render_plan.py", "red_team.py", "build_trip.py"]
    for s in scripts:
        p = os.path.join(HERE, s)
        if not os.path.exists(p):
            problems.append("缺少脚本 " + s + "，skill 安装不完整")
            print("    [FAIL] " + s + " 不存在")
            continue
        try:
            py_compile.compile(p, doraise=True, cfile=os.path.join(
                os.environ.get("TEMP", "/tmp"), s + ".pyc"))
            print("    [OK]   " + pad(s, 22) + " " + str(os.path.getsize(p)) + " B")
        except Exception as e:
            problems.append(s + " 编译失败：" + str(e)[:80])
            print("    [FAIL] " + s + " 编译失败: " + str(e)[:60])

    # ---- 3. 目录与写权限
    print("\n[3] 目录与写权限")
    print("    skill 根目录: " + SKILL_ROOT)
    for sub in ("scripts", "references"):
        d = os.path.join(SKILL_ROOT, sub)
        print("    [%s] %s" % ("OK  " if os.path.isdir(d) else "FAIL", sub + "/"))
        if not os.path.isdir(d):
            problems.append("缺少目录 " + sub + "/")
    tmp = os.environ.get("TEMP") or os.path.join(SKILL_ROOT, ".writable_test")
    try:
        t = os.path.join(tmp, "_to_doctor_test")
        with open(t, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(t)
        print("    [OK]   可写临时目录: " + tmp)
    except Exception as e:
        problems.append("临时目录不可写：" + str(e)[:60])
        print("    [FAIL] 临时目录不可写: " + str(e)[:60])

    # ---- 4. eBird Token
    print("\n[4] eBird Token（可选）")
    tok = None
    for k in ("EBIRD_API_TOKEN", "EBIRD_TOKEN"):
        if os.environ.get(k, "").strip():
            tok = os.environ[k].strip()
            print("    [OK]   从环境变量 " + k + " 读到 Token（" + tok[:4] + "****）")
            break
    if not tok:
        print("    [ -- ] 未配置。不影响使用：观鸟默认走免注册的 iNaturalist。")
        print("           需要珍稀鸟种提醒/官方热点库时，申请免费 Token：")
        print("           https://ebird.org/api/keygen")

    # ---- 5. 网络连通性
    if args.quick:
        print("\n[5] 网络连通性：已按 --quick 跳过")
    else:
        print("\n[5] 数据接口连通性")
        lat, lng = 37.5091, 122.1136
        checks = [
            ("日出日落", "https://api.sunrise-sunset.org/json?lat=%s&lng=%s&formatted=0" % (lat, lng), True),
            ("观星", "https://www.cyclecalcs.com/v2/today?lat=%s&lon=%s" % (lat, lng), True),
            ("潮汐", "https://marine-api.open-meteo.com/v1/marine?latitude=%s&longitude=%s&hourly=sea_level_height_msl&forecast_days=1" % (lat, lng), True),
            ("气象", "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=cloud_cover&forecast_days=1" % (lat, lng), True),
            ("气溶胶", "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=%s&longitude=%s&hourly=aerosol_optical_depth&forecast_days=1" % (lat, lng), True),
            ("地理编码", "https://geocoding-api.open-meteo.com/v1/search?name=weihai&count=1", True),
            ("观鸟-观测", "https://api.inaturalist.org/v1/observations?taxon_id=3&lat=%s&lng=%s&radius=25&per_page=1" % (lat, lng), True),
            ("观鸟-物种", "https://api.inaturalist.org/v1/taxa/autocomplete?q=%E9%BB%91%E5%B0%BE%E9%B8%A5", True),
            ("观鸟-备份", "https://api.gbif.org/v1/occurrence/search?taxonKey=212&year=2025,2026&limit=1", True),
        ]
        if tok:
            checks.append(("eBird", "https://api.ebird.org/v2/data/obs/geo/recent?lat=%s&lng=%s&dist=25&back=7" % (lat, lng), True))
        for name, url, need in checks:
            ok, note = probe(name, url, need_key=need)
            print("    [%s] %s %s" % ("OK  " if ok else "FAIL", pad(name, 16), note))
            if not ok:
                warnings.append(name + " 接口不通：" + note)

    # ---- 汇总
    print("\n" + "=" * 66)
    if problems:
        print("发现 " + str(len(problems)) + " 个必须解决的问题：")
        for p in problems:
            print("  ! " + p)
    else:
        print("本地环境检查全部通过。")
    if warnings:
        print("网络告警 " + str(len(warnings)) + " 项（多为临时网络问题，重试或换网络）：")
        for w in warnings:
            print("  - " + w)
    print()
    if problems:
        print("结论：环境有问题，先按上面的 ! 项修，再跑渲染。")
        return 1
    if warnings:
        print("结论：环境可用。个别接口不通时会自动降级到 web 搜索，不阻塞交付。")
        return 0
    print("结论：一切正常。可以开始用了：")
    print("  python scripts/build_trip.py skeleton <方案目录> --start 2026-10-01 --days 2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
