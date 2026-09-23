#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_trip.py — 行程数据分片存取与校验工具 (v1.1.0)
归属 skill: travel-orchestrator

为什么需要它
------------
一份完整方案的 trip.json 通常 20-40 KB。这个体量**一次写入很容易失败**
（被截断、参数丢失、写一半），现场表现就是"生成时总是报错"，
然后只能临时手写脚本把 JSON 拼起来——上一版就是这样的，
这类临时脚本既没校验也不可复用。

所以这里把"分段写小文件 + 组装 + 校验"变成正式流程：

  parts/01_request.json        基本信息（part 之间按文件名排序合并）
  parts/02_weather.json
  parts/03_nature.json
  parts/10_days_D1.json        逐日行程可以一天一个文件
  parts/10_days_D2.json
  parts/20_food.json
  parts/30_budget.json
  ...

每个分片只有几 KB，写入不会失败；合并规则是**深合并**，
列表默认覆盖；键名以 `+` 结尾表示"追加到已有列表"，例如：

  parts/10_days_D1.json   {"itinerary": {"days+": [ ...D1... ]}}
  parts/10_days_D2.json   {"itinerary": {"days+": [ ...D2... ]}}
  → 合并成 {"itinerary": {"days": [D1, D2]}}

设计取向
--------
**数据以分片为常态，不落中间大文件。** 渲染器、红方复核、校验都直接读
`<trip_dir>/`，内部按文件名顺序合并 `parts/*.json`。这样就不存在
「合并产物与分片不同步」和「组装到一半写坏」这两个环节——而它们正是
以前「生成慢、一生成就报错」的主要来源。

只有确实需要一个独立大 JSON（交付 / 存档 / 喂给别的工具）时才 `assemble`，
或 `render ... --write-json` 顺手落一份。产物不是数据源。

用法
----
  # 生成骨架，避免从零手写大 JSON
  python build_trip.py skeleton travel-plan/2026-10-01-weihai-2d \
      --trip-id 2026-10-01-weihai-2d --start 2026-10-01 --days 2

  # 合并 + 校验 + 渲染（读目录，不写 trip.json）
  python build_trip.py render travel-plan/2026-10-01-weihai-2d

  # 已有的大 trip.json 拆成分片（迁移用，会自检等值）
  python build_trip.py split travel-plan/2026-10-01-weihai-2d/trip.json

  # 只校验：传目录读 parts/，传文件读文件
  python build_trip.py validate travel-plan/2026-10-01-weihai-2d

  # 需要一个独立大 JSON 时才组装
  python build_trip.py assemble travel-plan/2026-10-01-weihai-2d

  # 查看渲染器需要的最小结构说明
  python build_trip.py schema
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta

VERSION = "1.1.0"

# 渲染器/红方复核都依赖的最小结构（缺了必然报错，所以先在这里拦住）
REQUIRED_TOP = {
    "trip_id": str,
    "plan_version": int,
    "status": str,
    "request": dict,
    "itinerary": dict,
}
REQUIRED_ITINERARY = {
    "days": list,
    "food": list,
    "budget": dict,
    "checklist": list,
}
# 有则更好，没有也不阻塞（红方会给 info 级提示）
# 注意：weather / nature 是嵌在 itinerary 里的，不在顶层
OPTIONAL_TOP = ("pitfalls", "facts", "sources", "red_team", "status_note", "title")

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 合并后的顶层键序。分片按文件名排序合并，键的插入顺序会随分片内容变化；
# 统一按这个顺序重排，产出才稳定、好 diff。
TOP_ORDER = ("trip_id", "plan_version", "status", "status_note", "request",
             "itinerary", "pitfalls", "facts", "sources", "red_team", "decisions",
             "title")


def order_top(trip):
    """按 TOP_ORDER 重排顶层键，其余键保持原相对顺序跟在后面。"""
    if not isinstance(trip, dict):
        return trip
    out = {}
    for k in TOP_ORDER:
        if k in trip:
            out[k] = trip[k]
    for k, v in trip.items():
        if k not in out:
            out[k] = v
    return out


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def die(msg, hints=None):
    print("x " + msg, file=sys.stderr)
    for h in hints or []:
        print("  -> " + h, file=sys.stderr)
    sys.exit(2)


def deep_merge(base, patch, path=""):
    """深合并 patch 到 base。

    - 嵌套 dict 递归合并
    - 键名以 '+' 结尾 → 追加到同名列表（`days+` 追加到 `days`）
    - 其他列表 / 标量 → 覆盖
    """
    if not isinstance(patch, dict):
        return patch
    if not isinstance(base, dict):
        base = {}
    for key, val in patch.items():
        append = isinstance(key, str) and key.endswith("+")
        real = key[:-1] if append else key
        if append:
            cur = base.get(real)
            if not isinstance(cur, list):
                cur = []
                base[real] = cur
            if isinstance(val, list):
                cur.extend(val)
            else:
                cur.append(val)
            continue
        if isinstance(val, dict) and isinstance(base.get(real), dict):
            deep_merge(base[real], val, path + "/" + str(real))
        else:
            base[real] = val
    return base


def load_parts(parts_dir, quiet=False):
    """按文件名顺序读取 parts/*.json，逐个深合并。"""
    if not os.path.isdir(parts_dir):
        die("分片目录不存在：" + parts_dir,
            ["先用 `skeleton` 生成骨架，或把分片 json 放进该目录",
             "也可以直接用 validate 校验已有的 trip.json"])
    files = sorted(f for f in os.listdir(parts_dir)
                   if f.endswith(".json") and not f.startswith("_"))
    if not files:
        die("分片目录里没有 .json 文件：" + parts_dir)
    merged = {}
    for name in files:
        p = os.path.join(parts_dir, name)
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as e:
            die("分片 " + name + " 不是合法 JSON：" + str(e),
                ["第 " + str(e.lineno) + " 行第 " + str(e.colno) + " 列",
                 "常见原因：字符串里有多余的逗号、单引号、或内容被截断"])
        except OSError as e:
            die("无法读取分片 " + name + "：" + str(e))
        if not isinstance(data, dict):
            die("分片 " + name + " 顶层必须是 JSON 对象（{}），当前是 "
                + type(data).__name__)
        deep_merge(merged, data)
        if not quiet:
            print("  + 合并 " + name + " (" + str(os.path.getsize(p)) + " B)")
    return merged, files


def _load_json_file(path):
    """读一个 json 文件，出错时给可定位的提示而不是裸 traceback。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        die("不是合法 JSON：" + str(path) + " " + str(e),
            ["第 " + str(e.lineno) + " 行第 " + str(e.colno) + " 列",
             "多半是写入被截断或引号不匹配；改用分片存放可避免"])
    except OSError as e:
        die("无法读取：" + str(path) + " " + str(e))


def parts_dir_of(path):
    """给「方案目录」或「目录里的某个文件」，推出分片目录。"""
    p = os.path.abspath(path)
    if os.path.isdir(p):
        inner = os.path.join(p, "parts")
        return inner if os.path.isdir(inner) else p
    return os.path.join(os.path.dirname(p), "parts")


def resolve_trip(path, quiet=False):
    """统一的取数入口（渲染器、红方复核、本工具自己都走这里）。

    传目录 → 按名序合并该目录下的 parts/*.json（就地，不落盘）
    传文件 → 直接读该文件

    返回 (行程字典, 数据来源描述, 分片数)。
    """
    p = os.path.abspath(path)
    if os.path.isfile(p):
        return order_top(_load_json_file(p)), os.path.basename(p), 0
    if not os.path.isdir(p):
        die("找不到方案目录或 trip.json：" + path,
            ["方案目录一般形如 travel-plan/<trip_id>/，里面有 parts/ 或 trip.json",
             "也可以直接把 trip.json 的路径传进来"])
    pd = parts_dir_of(p)
    merged, files = load_parts(pd, quiet=quiet)
    # 标签跟着实际形态走：分片在 parts/ 子目录里就写 parts/，平铺在方案目录里就写目录名
    label = os.path.basename(pd) + "/（" + str(len(files)) + " 个分片）"
    return order_top(merged), label, len(files)


def _write_trip(trip, out):
    """写一份独立 trip.json：先备份旧的，写完读回验证真的能解析。"""
    if os.path.exists(out):
        shutil.copy2(out, out + ".bak")
        print("  已备份旧文件 -> " + os.path.basename(out) + ".bak")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(trip, fh, ensure_ascii=False, indent=2)
    with open(out, encoding="utf-8") as fh:
        json.load(fh)
    print("  已写入 " + out + " (" + str(os.path.getsize(out)) + " B)")


def split_trip(trip):
    """把一份完整行程切成小分片：[(文件名, 数据), ...]，顺序即合并顺序。

    分法固定，保证 load_parts 合并回来与输入完全等值（split 命令会自检，
    不等值就报错，绝不静默丢数据）。
    """
    out = []
    head = {}
    for k in ("trip_id", "plan_version", "status", "status_note", "title"):
        if k in trip:
            head[k] = trip[k]
    if "request" in trip:
        head["request"] = trip["request"]
    out.append(("01_request.json", head))

    itin = trip.get("itinerary") if isinstance(trip.get("itinerary"), dict) else {}
    used = set()
    for idx, key in ((2, "weather"), (3, "nature")):
        if key in itin:
            out.append(("%02d_%s.json" % (idx, key), {"itinerary": {key: itin[key]}}))
            used.add(key)
    days = itin.get("days")
    if isinstance(days, list) and days:
        for i, day in enumerate(days):
            out.append(("%02d_days_D%d.json" % (10 + i, i + 1),
                        {"itinerary": {"days+": [day]}}))
    elif "days" in itin:
        out.append(("10_days.json", {"itinerary": {"days": []}}))
    used.add("days")
    n = 20
    for key in ("food", "budget", "checklist"):
        if key in itin:
            out.append(("%02d_%s.json" % (n, key), {"itinerary": {key: itin[key]}}))
            used.add(key)
        n += 1
    rest_itin = {k: v for k, v in itin.items() if k not in used}
    if rest_itin:
        out.append(("%02d_itinerary_other.json" % n, {"itinerary": rest_itin}))
        n += 1
    skip = ("trip_id", "plan_version", "status", "status_note", "title",
            "request", "itinerary")
    rest_top = {k: v for k, v in trip.items() if k not in skip}
    for key in ("pitfalls", "facts", "sources", "red_team", "decisions"):
        if key in rest_top:
            out.append(("%02d_%s.json" % (n, key), {key: rest_top.pop(key)}))
            n += 1
    if rest_top:
        out.append(("%02d_rest.json" % n, rest_top))
    return out


def validate(trip, label="trip.json", quiet=False):
    """结构校验。返回 (是否通过, 错误列表, 警告列表)。"""
    errors, warns = [], []
    if not isinstance(trip, dict):
        return False, ["顶层不是 JSON 对象"], []
    for key, typ in REQUIRED_TOP.items():
        if key not in trip:
            errors.append("缺少顶层字段 " + key)
        elif not isinstance(trip[key], typ):
            errors.append("字段 " + key + " 应为 " + typ.__name__ + "，实际是 "
                          + type(trip[key]).__name__)
    itin = trip.get("itinerary") if isinstance(trip.get("itinerary"), dict) else {}
    # request 子结构：字段名与 render_plan / red_team 的约定必须一致，
    # 不一致会出现"生成成功但一渲染/一复核就阻断"的情况，所以在这里提前拦住
    req = trip.get("request") if isinstance(trip.get("request"), dict) else {}
    if isinstance(trip.get("request"), dict):
        dates = req.get("dates") if isinstance(req.get("dates"), dict) else {}
        if not dates.get("start") or not dates.get("end"):
            errors.append("request.dates 缺少 start / end（渲染与红方复核都依赖它）")
        else:
            for k in ("start", "end"):
                try:
                    datetime.strptime(dates[k], "%Y-%m-%d")
                except (ValueError, TypeError):
                    errors.append("request.dates." + k + " 格式应为 YYYY-MM-DD，当前 "
                                  + str(dates[k]))
        if not req.get("companions"):
            errors.append("request.companions 为空（红方判定为阻断项）")
        elif not isinstance(req["companions"], str):
            warns.append("request.companions 建议用字符串（如「2 成人」），当前是 "
                         + type(req["companions"]).__name__)
        if not isinstance(req.get("budget"), dict):
            warns.append("request.budget 缺失或不是对象（含 amount_max/currency/mode）")
        elif req["budget"].get("amount_max") is None:
            warns.append("request.budget.amount_max 为 null，预算超支检查（RT-S06）将跳过")
        for k in ("origin", "destination"):
            if not isinstance(req.get(k), dict) or not (req[k] or {}).get("name"):
                warns.append("request." + k + ".name 为空，封面会显示「未定」")
    for key, typ in REQUIRED_ITINERARY.items():
        if key not in itin:
            errors.append("itinerary 缺少字段 " + key)
        elif not isinstance(itin[key], typ):
            errors.append("itinerary." + key + " 应为 " + typ.__name__ + "，实际是 "
                          + type(itin[key]).__name__)
    # 逐日行程
    days = itin.get("days") if isinstance(itin.get("days"), list) else []
    if isinstance(itin.get("days"), list) and not days:
        errors.append("itinerary.days 为空，方案至少要有一天")
    seen_ids = set()
    for i, d in enumerate(days):
        if not isinstance(d, dict):
            errors.append("itinerary.days[" + str(i) + "] 不是对象")
            continue
        did = d.get("day_id") or ("#" + str(i + 1))
        if not d.get("date"):
            errors.append(did + " 缺 date")
        else:
            try:
                real = WEEKDAY_CN[datetime.strptime(d["date"], "%Y-%m-%d").weekday()]
                if d.get("weekday") and real not in str(d["weekday"]):
                    warns.append(did + " weekday 写的是 " + str(d["weekday"])
                                 + "，实际是 " + real)
            except (ValueError, TypeError):
                errors.append(did + " date 格式应为 YYYY-MM-DD，当前 " + str(d.get("date")))
        if not isinstance(d.get("items"), list) or not d.get("items"):
            warns.append(did + " 没有 items（行程条目），渲染出来会是空白天")
        for j, it in enumerate(d.get("items") or []):
            if not isinstance(it, dict):
                errors.append(did + " items[" + str(j) + "] 不是对象")
                continue
            iid = it.get("item_id")
            if not iid:
                warns.append(did + " items[" + str(j) + "] 缺 item_id")
            elif iid in seen_ids:
                errors.append("item_id 重复：" + str(iid))
            else:
                seen_ids.add(iid)
            if not it.get("title"):
                warns.append(did + " items[" + str(j) + "] 缺 title")
    # 预算：真实结构是 {"currency","categories":[{name,min,max}],"total":{...}}
    if isinstance(itin.get("budget"), dict):
        b = itin["budget"]
        if "categories" not in b and "total" not in b:
            warns.append("itinerary.budget 既没有 categories 也没有 total")
        for k, c in enumerate(b.get("categories") or []):
            if not isinstance(c, dict):
                errors.append("budget.categories[" + str(k) + "] 不是对象")
                continue
            if c.get("min") is None or c.get("max") is None:
                warns.append("预算分项「" + str(c.get("name")) + "」缺 min/max")
            elif isinstance(c.get("min"), (int, float)) and isinstance(c.get("max"), (int, float)) \
                    and c["min"] > c["max"]:
                errors.append("预算分项「" + str(c.get("name")) + "」min 大于 max")
    # 天气：真实结构是 {"kind","summary","entries":[...]}（也接受直接给列表）
    wx = itin.get("weather")
    if isinstance(wx, dict):
        if not isinstance(wx.get("entries"), list):
            warns.append("itinerary.weather 缺少 entries 列表")
    elif wx is not None and not isinstance(wx, list):
        errors.append("itinerary.weather 应为对象（含 entries）或列表，实际是 "
                      + type(wx).__name__)
    missing_opt = [k for k in OPTIONAL_TOP if k not in trip]
    if missing_opt:
        warns.append("可选字段未填（不阻塞，但会影响交付完整度）："
                     + "、".join(missing_opt))
    if not quiet:
        print("-" * 60)
        print("校验 " + label + "：" + ("通过" if not errors else "不通过"))
        if errors:
            print("  错误 " + str(len(errors)) + " 项：")
            for e in errors[:20]:
                print("    ! " + e)
            if len(errors) > 20:
                print("    ... 其余 " + str(len(errors) - 20) + " 项省略")
        if warns:
            print("  提示 " + str(len(warns)) + " 项：")
            for w in warns[:10]:
                print("    - " + w)
            if len(warns) > 10:
                print("    ... 其余 " + str(len(warns) - 10) + " 项省略")
    return not errors, errors, warns


def cmd_assemble(args):
    trip_dir = os.path.abspath(args.trip_dir)
    parts_dir = os.path.abspath(args.parts or parts_dir_of(trip_dir))
    out = os.path.abspath(args.out or os.path.join(trip_dir, "trip.json"))
    print("组装 trip.json（只在需要一份独立大 JSON 时才用）")
    print("  分片目录: " + parts_dir)
    merged, files = load_parts(parts_dir)
    merged = order_top(merged)
    ok, errors, _ = validate(merged, os.path.basename(out), quiet=False)
    # 组装成功后先备份旧文件，再落盘
    if args.dry_run:
        print("  [dry-run] 未写入。" + ("结构校验通过。" if ok else "结构校验未通过，见上。"))
        return 0 if ok else 1
    if not ok and not args.force:
        die("校验未通过，已放弃写入（避免产出坏数据）。",
            ["修正上列错误后重跑",
             "确实要强行写入：加 --force（不推荐，渲染阶段还会再报一次）"])
    if os.path.exists(out):
        bak = out + ".bak"
        shutil.copy2(out, bak)
        print("  已备份旧文件 -> " + os.path.basename(bak))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    # 读回来验证真的能解析
    with open(out, encoding="utf-8") as fh:
        json.load(fh)
    print("  已写入 " + out + " (" + str(os.path.getsize(out)) + " B, "
          + str(len(files)) + " 个分片)")
    print("  注意：这份 trip.json 是产物不是数据源，改数据改 parts/。")
    print("  下一步：python scripts/build_trip.py render \"" + trip_dir + "\"")
    return 0


def cmd_render(args):
    """合并 + 校验 + 渲染。默认不落中间 trip.json。"""
    src = os.path.abspath(args.trip_dir)
    trip, label, _ = resolve_trip(src, quiet=False)
    print("-" * 60)
    print("渲染自 " + label)
    ok, errors, _ = validate(trip, label, quiet=False)
    if not ok and not args.force:
        die("结构校验未通过，已放弃渲染（避免产出看着成功的废交付物）。",
            ["先修正上列错误后重跑",
             "确实要强行渲染：加 --force（不推荐，红方复核还会再拦一次）"])
    if args.write_json:
        base = src if os.path.isdir(src) else os.path.dirname(src)
        _write_trip(trip, os.path.abspath(args.out or os.path.join(base, "trip.json")))
    import render_plan
    r = render_plan.write_outputs(src, args.out_dir)
    print("ok 已生成 v" + str(r["plan_version"]) + "：")
    print("  MD  : " + r["md"])
    print("  HTML: " + r["html"])
    return 0


def cmd_split(args):
    """把一份大 trip.json 拆成 parts/*.json（迁移用）。"""
    src = os.path.abspath(args.trip_json)
    trip, label, _ = resolve_trip(src)
    trip_dir = os.path.abspath(args.trip_dir or os.path.dirname(src))
    parts = os.path.abspath(args.parts or os.path.join(trip_dir, "parts"))
    pieces = split_trip(trip)
    sizes = [len(json.dumps(o, ensure_ascii=False)) for _, o in pieces]
    print("拆分 " + label + " -> " + parts)
    print("  " + str(len(pieces)) + " 个分片，正文合计 " + str(sum(sizes))
          + " 字符，最大一片 " + str(max(sizes)) + " 字符")
    if args.dry_run:
        for name, _ in pieces:
            print("  [dry-run] " + name)
        return 0
    os.makedirs(parts, exist_ok=True)
    for name, obj in pieces:
        p = os.path.join(parts, name)
        if os.path.exists(p) and not args.force:
            die("分片已存在：" + name + "（确认要覆盖就加 --force）")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        print("  + " + name + " (" + str(os.path.getsize(p)) + " B)")
    # 自检：合并回来必须与原文完全等值，否则这次拆分就是丢数据
    merged, _ = load_parts(parts, quiet=True)
    same = (json.dumps(merged, ensure_ascii=False, sort_keys=True)
            == json.dumps(trip, ensure_ascii=False, sort_keys=True))
    if not same:
        die("拆分自检失败：合并结果与原文不等值，请勿使用刚写入的分片",
            ["这是一个 bug，请保留 parts/ 目录与原始 trip.json 反馈"])
    print("  自检通过：合并结果与原文完全等值")
    ok, errors, _ = validate(merged, label, quiet=True)
    print("  结构校验：" + ("通过" if ok else "未通过（" + str(len(errors)) + " 项）"))
    print()
    print("下一步：确认无误后可删掉 " + os.path.basename(src)
          + "，之后统一用 `render " + trip_dir + "`。")
    return 0 if ok else 1


def cmd_validate(args):
    if not os.path.exists(args.trip_json):
        die("路径不存在：" + str(args.trip_json),
            ["传方案目录（读其中的 parts/）或直接传 trip.json 路径",
             "方案目录一般是 travel-plan/<trip_id>/"])
    trip, label, n = resolve_trip(args.trip_json, quiet=True)
    if n:
        print("数据来源：" + label)
        print()
    # 目录形态下 label 已经带了分片数，这里只补一个可读的来源行
    print("校验对象：" + os.path.abspath(args.trip_json))
    ok, _, _ = validate(trip, label)
    return 0 if ok else 1


def cmd_schema(args):
    print("渲染器需要的最小结构（缺一项就会报错）—— 与 render_plan.py 实际读取一致")
    print(json.dumps({
        "trip_id": "字符串，唯一标识",
        "plan_version": "整数，从 1 开始",
        "status": "draft | conditional | executable_as_of_check",
        "status_note": "方案状态的说明",
        "request": {"origin": {"name": "出发城市"},
                    "destination": {"name": "目的地"},
                    "dates": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
                    "companions": "字符串（如 2 成人），不是数组",
                    "budget": {"amount_max": 0, "currency": "CNY", "mode": "total"},
                    "pace": "节奏描述",
                    "interests": {"must": [{"label": "赶海"}],
                                  "prefer": ["观星", "日出"]}},
        "itinerary": {
            "weather": {"kind": "forecast", "summary": "整段概述",
                        "entries": [{"date": "YYYY-MM-DD", "summary": "晴 16-22℃"}]},
            "nature": {"birding": {}, "stargazing": {}, "sunrise": {}, "beachcombing": {}},
            "days": [{"day_id": "D1", "date": "YYYY-MM-DD", "weekday": "周一",
                      "title": "当日主题", "alternatives": "",
                      "items": [{"item_id": "d1-1（全局唯一）", "time": "08:00-10:00",
                                 "title": "", "kind": "nature|transport_major|meal|sight",
                                 "place": "", "booking_status": "not_required",
                                 "notes": ""}]}],
            "food": [{"name": "", "note": ""}],
            "budget": {"currency": "CNY",
                       "categories": [{"name": "住宿", "min": 0, "max": 0}],
                       "total": {"min": 0, "max": 0}},
            "checklist": [{"text": "", "due": ""}],
        },
        "pitfalls": [{"title": "", "detail": "", "avoid": ""}],
        "facts": [{"fact_id": "f1", "claim": "一句话结论", "value": "具体值",
                   "status": "verified|estimated|unverified|unknown", "source_ids": ["s1"]}],
        "sources": [{"source_id": "s1", "name": "", "kind": "api|official_site|web_search",
                     "url": "", "retrieved_at": "YYYY-MM-DD"}],
        "red_team": {"verdict": "", "findings": []},
    }, ensure_ascii=False, indent=2))
    print()
    print("可选字段：" + "、".join(OPTIONAL_TOP))
    print("注意：item_id 必须全局唯一，且预算分项 min 不得大于 max —— 这两项是阻断级。")
    return 0


def cmd_skeleton(args):
    trip_dir = os.path.abspath(args.trip_dir)
    parts = os.path.join(trip_dir, "parts")
    os.makedirs(parts, exist_ok=True)
    start = datetime.strptime(args.start, "%Y-%m-%d")
    days = max(1, min(int(args.days), 30))

    def dump(name, obj):
        p = os.path.join(parts, name)
        if os.path.exists(p) and not args.force:
            print("  跳过已存在: " + name)
            return
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        print("  生成: " + name)

    print("生成骨架 -> " + parts)
    dump("01_request.json", {
        "trip_id": args.trip_id,
        "plan_version": 1,
        "status": "draft",
        "status_note": "骨架阶段，待补实时数据后改为 conditional / executable_as_of_check",
        # request 的字段名必须与 render_plan.py / red_team.py 完全一致，
        # 这里写错是最常见的"能生成但一渲染就报错"的原因
        "request": {
            "origin": {"name": ""},
            "destination": {"name": ""},
            "dates": {"start": args.start,
                      "end": (start + timedelta(days=days - 1)).strftime("%Y-%m-%d")},
            "companions": "（人数与关系，如 2 成人）",
            "budget": {"amount_max": None, "currency": "CNY", "mode": "total"},
            "pace": "",
            "interests": {"must": [], "prefer": []},
        },
        "itinerary": {"days+": [], "food": [],
                      "budget": {"currency": "CNY", "categories": [], "total": {}},
                      "checklist": [],
                      "weather": {"kind": "forecast", "summary": "", "entries": []},
                      "nature": {}},
    })
    for i in range(days):
        d = start + timedelta(days=i)
        dump("%02d_days_D%d.json" % (10 + i, i + 1), {
            "itinerary": {"days+": [{
                "day_id": "D" + str(i + 1),
                "date": d.strftime("%Y-%m-%d"),
                "weekday": WEEKDAY_CN[d.weekday()],
                "theme": "",
                "items": [],
                "stay": "",
            }]}
        })
    dump("%02d_food.json" % (20 + days), {"itinerary": {"food": []}})
    dump("%02d_budget.json" % (21 + days), {
        "itinerary": {"budget": {"currency": "CNY", "categories": [],
                                 "total": {"min": None, "max": None}}}
    })
    dump("%02d_checklist.json" % (22 + days), {"itinerary": {"checklist": []}})
    dump("%02d_weather.json" % (23 + days), {
        "itinerary": {"weather": {"kind": "forecast", "summary": "",
                                  "entries": [{"date": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
                                               "summary": ""} for i in range(days)]}}
    })
    dump("%02d_nature.json" % (24 + days), {"nature": {}})
    dump("%02d_pitfalls.json" % (25 + days), {"pitfalls": []})
    dump("%02d_facts.json" % (26 + days), {"facts": [], "sources": []})
    print()
    print("下一步：逐个填 parts/*.json（每个都很小，写起来不会失败），")
    print("        然后 python scripts/build_trip.py render \"" + trip_dir + "\"")
    print("        （渲染器直接读目录合并，不需要先组装 trip.json）")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description="行程数据分片存取与校验 v" + VERSION,
        epilog="推荐流程：skeleton 生成骨架 -> 逐个填 parts -> render 一条龙渲染（直接读目录）")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("assemble", help="把 parts/*.json 合并成 trip.json 并校验")
    a.add_argument("trip_dir", help="方案目录，如 travel-plan/2026-10-01-weihai-2d")
    a.add_argument("--parts", default=None, help="分片目录，默认 <trip_dir>/parts")
    a.add_argument("--out", default=None, help="输出路径，默认 <trip_dir>/trip.json")
    a.add_argument("--force", action="store_true", help="校验不通过也写入（不推荐）")
    a.add_argument("--dry-run", action="store_true", help="只校验，不写文件")

    v = sub.add_parser("validate", help="校验结构（方案目录或 trip.json 都行）")
    v.add_argument("trip_json", help="方案目录（读其中的 parts/）或 trip.json 路径")

    r = sub.add_parser("render", help="读目录合并 + 校验 + 渲染 MD/HTML")
    r.add_argument("trip_dir", help="方案目录（内含 parts/），也可传 trip.json")
    r.add_argument("--out-dir", default=None, help="产物输出目录，默认与数据同目录")
    r.add_argument("--write-json", action="store_true",
                   help="顺手落一份独立 trip.json（默认不落）")
    r.add_argument("--out", default=None, help="--write-json 的目标路径")
    r.add_argument("--force", action="store_true",
                   help="结构校验不通过也渲染（不推荐）")

    sp = sub.add_parser("split", help="把大 trip.json 拆成 parts/*.json")
    sp.add_argument("trip_json", help="要拆的 trip.json")
    sp.add_argument("--trip-dir", default=None,
                    help="分片所属方案目录，默认取该文件所在目录")
    sp.add_argument("--parts", default=None, help="分片目录，默认 <trip_dir>/parts")
    sp.add_argument("--force", action="store_true", help="覆盖已存在的分片")
    sp.add_argument("--dry-run", action="store_true", help="只列出将要生成的分片")

    sub.add_parser("schema", help="打印渲染器需要的最小结构说明")

    s = sub.add_parser("skeleton", help="生成分片骨架")
    s.add_argument("trip_dir", help="方案目录")
    s.add_argument("--trip-id", default=None, help="trip_id，默认取目录名")
    s.add_argument("--start", required=True, help="出发日期 YYYY-MM-DD")
    s.add_argument("--days", type=int, default=2, help="天数，默认 2")
    s.add_argument("--force", action="store_true", help="覆盖已存在的分片")
    return p


def main(argv=None):
    configure_console()
    args = build_parser().parse_args(argv)
    if args.cmd == "skeleton" and not args.trip_id:
        args.trip_id = os.path.basename(os.path.abspath(args.trip_dir))
    handlers = {"assemble": cmd_assemble, "validate": cmd_validate,
                "schema": cmd_schema, "skeleton": cmd_skeleton,
                "render": cmd_render, "split": cmd_split}
    try:
        rc = handlers[args.cmd](args)
        return rc if isinstance(rc, int) else 0
    except KeyboardInterrupt:
        print("x 已中断", file=sys.stderr)
        return 130
    except Exception as e:                      # 兜底：永不抛裸 traceback
        print("x 未预期的错误：" + type(e).__name__ + ": " + str(e), file=sys.stderr)
        print("  请把上面这行贴出来以便定位。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
