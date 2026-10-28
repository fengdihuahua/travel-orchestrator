#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parity_check.py · Python / Node 双实现一致性测试（M3 的防漂移命门）

同一批 fixtures，两套实现各跑一遍，断言：
  红方 rules     stdout 归一化后逐字节一致、退出码一致
  红方 check     --json 结构一致（去掉 checked_at 时间戳）、退出码一致、文本报告一致
  结构校验       stdout 归一化后逐字节一致、退出码一致
  拆分/组装      同一 trip.json 拆出的分片目录逐字节一致
  渲染           MD/HTML 产物归一化时间戳后逐字节一致、stdout/退出码一致；
                 结构门坏数据两边同 exit 3、stderr 一致
  自然专项       离线确定性路径字节级：CLI 参数错误（exit 2 + stderr）与纯函数
                 （歧义告警/潮汐极值/霞光评分/均值窗/时间移位/JSON 浮点语义）；
                 联网取数为活数据，仅 TO_PARITY_LIVE=1 时做结构级冒烟，
                 不承诺字节级

归一化只做两件无歧义的事：\r\n → \n（Python 在 Windows 控制台输出 CRLF），
JSON 去掉 checked_at（ wall-clock 戳）。除此之外要求逐字节一致——
这条线一松，双实现就开始悄悄漂移。

用法：
    python tools/parity_check.py          # 全量
    python tools/parity_check.py --quick  # 只跑示例方案，不生成坏数据 fixtures

退出码：0 = 全部一致；1 = 存在漂移；2 = 环境缺 node。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
# 优先 node.exe：Windows 上 shutil.which("node") 会先命中 node.CMD 包装器，
# 多行 -e 脚本经 cmd.exe 转义会被吞掉（exit 0 无输出），.exe 没这个问题
NODE = shutil.which("node.exe") or shutil.which("node")

PASS = FAIL = 0

STAMP_RE = re.compile(r"生成于 \d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (" —— " + str(detail)[:200] if detail else ""))


def norm(s):
    return s.replace("\r\n", "\n")


def norm_stamp(s):
    """渲染产物/输出里的墙钟时间戳归一化（两次运行跨分钟不算漂移）。"""
    return STAMP_RE.sub("生成于 <T>", s)


def run(cmd):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, norm(p.stdout), norm(p.stderr)


def py_red(*a):
    return run([PY, "scripts/red_team.py"] + list(a))


def js_red(*a):
    return run([NODE, "js/red_team.mjs"] + list(a))


def py_bt(*a):
    return run([PY, "scripts/build_trip.py"] + list(a))


def js_bt(*a):
    return run([NODE, "js/build_trip.mjs"] + list(a))


def py_rp(*a):
    return run([PY, "scripts/render_plan.py"] + list(a))


def js_rp(*a):
    return run([NODE, "js/render_plan.mjs"] + list(a))


def py_nf(*a):
    return run([PY, "scripts/nature_forecast.py"] + list(a))


def js_nf(*a):
    return run([NODE, "js/nature_forecast.mjs"] + list(a))


# ---------------------------------------------------------------- fixtures

def load_example():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import build_trip as bt
    trip, _label, _n = bt.resolve_trip(os.path.join(ROOT, "examples", "weihai-2d"),
                                       quiet=True)
    return trip


def make_fixtures(tmp):
    """基于示例方案派生一组坏数据 fixtures（单文件形态），每个瞄准特定规则。"""
    base = load_example()
    out = {}

    def put(name, mutate):
        t = copy.deepcopy(base)
        mutate(t)
        p = os.path.join(tmp, name + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(t, f, ensure_ascii=False, indent=2)
        out[name] = p

    def days(t):
        return t["itinerary"]["days"]

    put("budget_over", lambda t: t["itinerary"]["budget"]["categories"].__setitem__(
        0, {**t["itinerary"]["budget"]["categories"][0], "max": 99999}))
    put("time_overlap", lambda t: days(t)[0]["items"].append(
        dict(days(t)[0]["items"][0],
             item_id="dup-time-1",
             time=days(t)[0]["items"][0].get("time", "08:00-09:00"))))
    put("tide_mismatch", lambda t: t["itinerary"]["nature"]["beachcombing"].__setitem__(
        "best_window", "12:00-14:00"))
    put("unknown_with_value", lambda t: t["facts"].append(
        {"fact_id": "fx1", "claim": "某票价", "value": 120, "status": "unknown"}))
    put("duplicate_item_id", lambda t: days(t)[1]["items"].__setitem__(
        0, {**days(t)[1]["items"][0],
            "item_id": days(t)[0]["items"][0]["item_id"]}))

    def stale(t):
        for s in t.get("sources", []):
            if (s.get("kind") or "").lower() in ("api", "official_site", "web_search"):
                s["retrieved_at"] = "2020-01-01"
                return
    put("stale_source", stale)
    put("missing_dates", lambda t: t["request"]["dates"].__delitem__("start"))
    put("bad_weekday", lambda t: days(t)[0].__setitem__("weekday", "周三"))
    # 渲染结构门：整个 itinerary 缺失必触发 TripDataError（exit 3）
    put("no_itinerary", lambda t: t.__delitem__("itinerary"))
    return out


# ---------------------------------------------------------------- 用例

def case_rules():
    print("\n== rules 清单 ==")
    rc_p, out_p, _ = py_red("rules")
    rc_j, out_j, _ = js_red("rules")
    check("rules 退出码一致", rc_p == rc_j, "%s / %s" % (rc_p, rc_j))
    check("rules 输出逐字节一致", out_p == out_j)


def case_check(path, label):
    rc_p, out_p, err_p = py_red("check", path, "--json")
    rc_j, out_j, err_j = js_red("check", path, "--json")
    check(label + "：退出码一致", rc_p == rc_j, "%s / %s" % (rc_p, rc_j))
    # 目录形态下 stdout 首行是「数据来源：parts/（N 个分片）」，JSON 从第一个 { 开始
    jp = _loads_json(out_p)
    jj = _loads_json(out_j)
    if jp is None or jj is None:
        check(label + "：JSON 可解析", False,
              "py=%r js=%r" % (out_p[:80], out_j[:80]))
        return
    jp.pop("checked_at", None)
    jj.pop("checked_at", None)
    check(label + "：JSON 报告一致", jp == jj,
          "" if jp == jj else _diff_json(jp, jj))
    # 文本报告也要一致（红方人机两读）
    rc_p2, out_p2, _ = py_red("check", path)
    rc_j2, out_j2, _ = js_red("check", path)
    check(label + "：文本报告一致", out_p2 == out_j2 and rc_p2 == rc_j2,
          "" if out_p2 == out_j2 else _diff_text(out_p2, out_j2))


def canon_json(o):
    """规范化比较：int/float 统一按值（JS 无 float/int 之分），dict 按键排序。"""
    if isinstance(o, bool):
        return ("b", o)
    if isinstance(o, (int, float)):
        return ("n", float(o))
    if isinstance(o, str):
        return ("s", o)
    if o is None:
        return ("z",)
    if isinstance(o, list):
        return ("l", tuple(canon_json(x) for x in o))
    if isinstance(o, dict):
        return ("d", tuple((k, canon_json(v)) for k, v in sorted(o.items())))
    return ("?", str(o))


def _loads_json(s):
    i = s.find("{")
    if i < 0:
        return None
    try:
        return json.loads(s[i:])
    except ValueError:
        return None


def _diff_json(a, b):
    sa = json.dumps(a, ensure_ascii=False, indent=2).splitlines()
    sb = json.dumps(b, ensure_ascii=False, indent=2).splitlines()
    return _diff_text("\n".join(sa), "\n".join(sb))


def _diff_text(a, b):
    import difflib
    d = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm=""))
    return "\n".join(d[:24])


def case_validate(path, label):
    rc_p, out_p, _ = py_bt("validate", path)
    rc_j, out_j, _ = js_bt("validate", path)
    check(label + "：validate 退出码一致", rc_p == rc_j, "%s / %s" % (rc_p, rc_j))
    check(label + "：validate 输出一致", out_p == out_j,
          "" if out_p == out_j else _diff_text(out_p, out_j))


def case_split_assemble(tmp):
    """同一数据：py 拆分 vs js 拆分，分片逐字节比；再组装回来与原文等值。"""
    print("\n== split / assemble 分片字节一致 ==")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import build_trip as bt
    trip, _l, _n = bt.resolve_trip(os.path.join(ROOT, "examples", "weihai-2d"),
                                   quiet=True)
    src = os.path.join(tmp, "src_trip.json")
    with open(src, "w", encoding="utf-8") as f:
        json.dump(trip, f, ensure_ascii=False, indent=2)
    py_dir = os.path.join(tmp, "split_py")
    js_dir = os.path.join(tmp, "split_js")
    rc_p, out_p, err_p = py_bt("split", src, "--trip-dir", py_dir)
    rc_j, out_j, err_j = js_bt("split", src, "--trip-dir", js_dir)
    check("split 退出码一致", rc_p == rc_j, "%s / %s | %s" % (rc_p, rc_j, err_j.strip()))
    # 输出里的路径不同（py_dir/js_dir），只比分片内容
    py_files = sorted(os.listdir(os.path.join(py_dir, "parts")))
    js_files = sorted(os.listdir(os.path.join(js_dir, "parts")))
    check("split 分片清单一致", py_files == js_files,
          "\n".join(py_files) + " // " + "\n".join(js_files))
    same = True
    for name in py_files:
        with open(os.path.join(py_dir, "parts", name), encoding="utf-8") as f:
            a = f.read()
        with open(os.path.join(js_dir, "parts", name), encoding="utf-8") as f:
            b = f.read()
        if a != b:
            # JS 只有 number 一种数值类型：Python 的 0.0 到 JS 再落盘成 0。
            # 这是 JSON 语义内完全等值的差异（json.load 后两者都是 0/0.0），
            # 真正要防的是"字段丢了/内容变了"，所以按值做规范化比较。
            if canon_json(json.loads(a)) != canon_json(json.loads(b)):
                same = False
                print("    分片语义不一致：" + name)
                print(_diff_text(a, b))
                break
    check("split 分片一致（语义级，数值按值比较）", same)


def case_render(tmp):
    """render_plan 直渲：exit code、stdout（归一化输出目录）、MD/HTML 字节（归一化时间戳）。"""
    print("\n== render 渲染产物字节一致 ==")
    example = os.path.join(ROOT, "examples", "weihai-2d")
    py_dir = os.path.join(tmp, "render_py")
    js_dir = os.path.join(tmp, "render_js")
    rc_p, out_p, err_p = py_rp(example, "--out-dir", py_dir)
    rc_j, out_j, err_j = js_rp(example, "--out-dir", js_dir)
    check("render 退出码一致", rc_p == rc_j, "%s / %s" % (rc_p, rc_j))
    check("render stdout 一致",
          out_p.replace(py_dir, "<OUT>") == out_j.replace(js_dir, "<OUT>"),
          "" if out_p.replace(py_dir, "<OUT>") == out_j.replace(js_dir, "<OUT>")
          else _diff_text(out_p, out_j))
    check("render stderr 一致", err_p == err_j,
          "" if err_p == err_j else _diff_text(err_p, err_j))
    py_files = sorted(os.listdir(py_dir))
    js_files = sorted(os.listdir(js_dir))
    check("render 产物清单一致", py_files == js_files,
          "\n".join(py_files) + " // " + "\n".join(js_files))
    for name in py_files:
        with open(os.path.join(py_dir, name), encoding="utf-8") as f:
            a = norm_stamp(f.read())
        with open(os.path.join(js_dir, name), encoding="utf-8") as f:
            b = norm_stamp(f.read())
        check("render 产物一致 " + name, a == b,
              "" if a == b else _diff_text(a, b))


def case_render_build(tmp):
    """build_trip render 子命令：同样三件套（目录输入、自带 validate + skip 结构门）。"""
    print("\n== build_trip render 子命令一致 ==")
    example = os.path.join(ROOT, "examples", "weihai-2d")
    py_dir = os.path.join(tmp, "btrender_py")
    js_dir = os.path.join(tmp, "btrender_js")
    rc_p, out_p, err_p = py_bt("render", example, "--out-dir", py_dir)
    rc_j, out_j, err_j = js_bt("render", example, "--out-dir", js_dir)
    check("build_trip render 退出码一致", rc_p == rc_j, "%s / %s" % (rc_p, rc_j))
    check("build_trip render stdout 一致",
          out_p.replace(py_dir, "<OUT>") == out_j.replace(js_dir, "<OUT>"),
          "" if out_p.replace(py_dir, "<OUT>") == out_j.replace(js_dir, "<OUT>")
          else _diff_text(out_p, out_j))
    check("build_trip render stderr 一致", err_p == err_j,
          "" if err_p == err_j else _diff_text(err_p, err_j))
    py_files = sorted(os.listdir(py_dir))
    js_files = sorted(os.listdir(js_dir))
    check("build_trip render 产物清单一致", py_files == js_files,
          "\n".join(py_files) + " // " + "\n".join(js_files))
    for name in py_files:
        with open(os.path.join(py_dir, name), encoding="utf-8") as f:
            a = norm_stamp(f.read())
        with open(os.path.join(js_dir, name), encoding="utf-8") as f:
            b = norm_stamp(f.read())
        check("build_trip render 产物一致 " + name, a == b,
              "" if a == b else _diff_text(a, b))


def case_render_gate(path):
    """结构门：坏数据走渲染必须两边同 exit 3、同 stderr。"""
    rc_p, _out_p, err_p = py_rp(path)
    rc_j, _out_j, err_j = js_rp(path)
    check("渲染结构门退出码=3", rc_p == 3 and rc_j == 3, "%s / %s" % (rc_p, rc_j))
    check("渲染结构门 stderr 一致", err_p == err_j,
          "" if err_p == err_j else _diff_text(err_p, err_j))


# ---------------------------------------------------------------- 自然专项（M3c）

# 纯函数差分的 JS 探针：数据全来自 Python 落盘的 fixtures 文件（两侧同一份输入，
# 杜绝两边各自造数据造成的假一致/假分歧）。
_NF_JS_PROBE = r"""
import { readFileSync } from "node:fs";
// node <probe.mjs> nf fmt fx：argv = [node, 脚本, ...三参数]
const [nfPath, fmtPath, fxPath] = process.argv.slice(2);
const nf = await import(nfPath);
const { pyF, pyJsonLoads, pyJsonDumps } = await import(fmtPath);
const fx = JSON.parse(readFileSync(fxPath, "utf8"));
const aw = (t, v, c) => { const r = nf.avgWindow(t, v, c); return r === null ? null : pyF(r); };
const cases = {
  place_ambiguity: [nf.placeAmbiguity("大同", fx.dup), nf.placeAmbiguity("威海", fx.single)],
  find_extremes: [nf.findExtremes(fx.times, fx.heights),
                  nf.findExtremes(fx.times2, fx.heights2),
                  nf.findExtremes([], [])],
  score_glow: fx.glow_args.map((a) => nf.scoreGlow(...a)),
  avg_window: fx.avg_centers.map((c) => aw(fx.wx_times, fx.wx_vals, c)),
  shift_hour: fx.shift_args.map(([t, o]) => nf.shiftHour(t, o)),
  json_roundtrip: pyJsonLoads(fx.roundtrip_text),
};
process.stdout.write(pyJsonDumps(cases, 2) + "\n");
"""


def case_nature_forecast(tmp):
    """nature_forecast 离线确定性 parity：CLI 错误路径 + 纯函数逐字节。"""
    print("\n== nature_forecast 离线确定性一致 ==")
    # --- CLI 错误路径（不触网，exit 2 + stderr 逐字节）---
    cli_cases = [
        ("经纬度越界", ["sun", "--lat", "91", "--lng", "0"]),
        ("城市名非法字符", ["glow", "--city", "威;海"]),
        ("日期已过去", ["tide", "--lat", "37.5", "--lng", "122.1", "--date", "2020-01-01"]),
        ("缺地点参数", ["astro"]),
        ("日期格式错", ["sun", "--lat", "37.5", "--lng", "122.1", "--date", "bad"]),
        ("超出预报窗口", ["tide", "--lat", "37.5", "--lng", "122.1", "--date", "2099-01-01"]),
    ]
    for label, args in cli_cases:
        rc_p, out_p, err_p = py_nf(*args)
        rc_j, out_j, err_j = js_nf(*args)
        check("nf " + label + " 退出码一致", rc_p == rc_j, "%s / %s" % (rc_p, rc_j))
        check("nf " + label + " stderr 一致", err_p == err_j and out_p == out_j,
              "" if err_p == err_j and out_p == out_j
              else "py=%r js=%r" % (err_p, err_j))

    # --- 纯函数差分（fixtures 由 Python 生成，JS 读同一份）---
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import math
    import nature_forecast as nf
    times, heights = [], []
    for d in range(1, 4):
        for hh in range(24):
            times.append("2026-10-%02dT%02d:00" % (d, hh))
            if d == 3 and hh >= 6:
                heights.append(None)
            elif d == 1 and hh == 12:
                heights.append(heights[-1])
            else:
                heights.append(round(1.2 * math.sin((hh + d * 3) / 3.7)
                                     + 0.3 * math.cos(hh / 2.1), 3))
    fx = {
        "dup": [{"name": "大同", "admin1": "山西省", "latitude": 40.09, "longitude": 113.29},
                {"name": "大同", "admin1": "黑龙江省", "latitude": 46.0, "longitude": 124.8},
                {"name": "大同", "country": "越南", "latitude": 21.0, "longitude": 105.8}],
        "single": [{"name": "威海", "admin1": "山东省", "latitude": 37.5, "longitude": 122.1}],
        "times": times,
        "heights": heights,
        "times2": ["2026-10-01T00:00", "2026-10-01T01:00", "2026-10-01T02:00"],
        "heights2": [0.5, None, 0.7],
        "glow_args": [[10.0, 40.0, 60.0, 20.0, 60.0, 0.25],
                      [50.0, 5.0, 10.0, 5.0, 95.0, 0.7],
                      [None, None, None, None, None, None],
                      [100.0, 80.0, 90.0, 10.0, 92.0, 0.65]],
        "wx_times": ["2026-10-01T%02d:00" % h for h in range(24)],
        "wx_vals": [10.0, 20.0, 35.5, None, 60.0, 80.0, 100.0, 15.0] + [42.0] * 16,
        "avg_centers": ["2026-10-01T03:00Z", "2026-10-01T03:30+02:00",
                        "2026-10-01T05:00", "2030-01-01T00:00Z"],
        "shift_args": [["2026-10-01T12:00", 0.5], ["2026-10-01T00:30", -1.25],
                       ["2026-10-01T23:30", 1.0], ["2026-12-31T23:00", 2.0]],
        "roundtrip_text": json.dumps(
            {"int": 12, "float": 12.0, "exp": 1e3, "neg": -0.0,
             "s_cn": "中文", "empty_obj": {}, "empty_arr": [],
             "nested": {"a": [1, 2.0, None, True, False]}, "null_v": None},
            ensure_ascii=False),
    }
    fx_path = os.path.join(tmp, "nf_fixtures.json")
    with open(fx_path, "w", encoding="utf-8") as f:
        json.dump(fx, f, ensure_ascii=False)

    expected = {
        "place_ambiguity": [nf.place_ambiguity("大同", fx["dup"]),
                            nf.place_ambiguity("威海", fx["single"])],
        "find_extremes": [nf.find_extremes(fx["times"], fx["heights"]),
                          nf.find_extremes(fx["times2"], fx["heights2"]),
                          nf.find_extremes([], [])],
        "score_glow": [nf.score_glow(*a) for a in fx["glow_args"]],
        "avg_window": [nf._avg_window(fx["wx_times"], fx["wx_vals"], c)
                       for c in fx["avg_centers"]],
        "shift_hour": [nf.shift_hour(t, o) for t, o in fx["shift_args"]],
        "json_roundtrip": json.loads(fx["roundtrip_text"]),
    }
    exp_text = json.dumps(expected, ensure_ascii=False, indent=2) + "\n"

    nf_mod = "file:///" + os.path.abspath("js/nature_forecast.mjs").replace("\\", "/")
    fmt_mod = "file:///" + os.path.abspath("js/pyfmt.mjs").replace("\\", "/")
    # 探针落成临时 .mjs 再跑：多行 -e 脚本在 Windows 的 node.CMD 包装器下会被静默吞掉
    probe_path = os.path.join(tmp, "nf_probe.mjs")
    with open(probe_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_NF_JS_PROBE)
    rc_j, out_j, err_j = run([NODE, probe_path, nf_mod, fmt_mod, fx_path])
    check("nf 纯函数探针 exit 0", rc_j == 0, err_j.strip()[:200])
    check("nf 纯函数逐字节一致", out_j == norm(exp_text),
          "" if out_j == norm(exp_text) else _diff_text(norm(exp_text), out_j))

    # --- 联网冒烟（仅 TO_PARITY_LIVE=1）：活数据不承诺字节级，只看退出码与骨架 ---
    if os.environ.get("TO_PARITY_LIVE") == "1":
        print("\n== nature_forecast 联网冒烟（结构级，非字节级）==")
        for sub, args in [("tide", ["--lat", "37.5", "--lng", "122.1"]),
                          ("sun", ["--lat", "37.5", "--lng", "122.1", "--date", "2026-10-20"]),
                          ("astro", ["--lat", "37.5", "--lng", "122.1",
                                     "--at", "2026-10-20T12:00:00Z"]),
                          ("glow", ["--lat", "37.5", "--lng", "122.1",
                                    "--date", "2026-10-20", "--event", "set"])]:
            rc_p, out_p, _ = py_nf(sub, *args)
            rc_j, out_j, _ = js_nf(sub, *args)
            check("nf live " + sub + " 退出码一致", rc_p == rc_j, "%s / %s" % (rc_p, rc_j))
            head_p = norm_stamp(out_p).splitlines()[:2]
            head_j = norm_stamp(out_j).splitlines()[:2]
            check("nf live " + sub + " 骨架一致", head_p == head_j,
                  "\n".join(head_p) + " // " + "\n".join(head_j))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    quick = "--quick" in (argv or sys.argv[1:])
    if not NODE:
        print("x 环境里没有 node，跳过 parity（这不等于通过）", file=sys.stderr)
        return 2

    tmp = tempfile.mkdtemp(prefix="to-parity-")
    try:
        example = os.path.join(ROOT, "examples", "weihai-2d")
        case_rules()
        case_check(example, "示例方案")
        case_validate(example, "示例方案")
        case_render(tmp)
        case_render_build(tmp)
        case_nature_forecast(tmp)
        if not quick:
            fixtures = make_fixtures(tmp)
            for name, path in fixtures.items():
                case_check(path, "坏数据/" + name)
            case_validate(os.path.join(tmp, "budget_over.json"), "坏数据/budget_over")
            case_render_gate(os.path.join(tmp, "no_itinerary.json"))
            case_split_assemble(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("parity 共 %d 项，通过 %d，失败 %d" % (PASS + FAIL, PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
