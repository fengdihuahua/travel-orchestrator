#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_plan.py · travel-orchestrator 交付渲染器（只用标准库，完全自包含）

从同一份行程数据（travel-plan/<trip_id>/trip.json）渲染两种交付物：
  1. Markdown 完整攻略（.md）
  2. 单文件 HTML 手账（.html，双击可开、离线可用、无 CDN、无脚本）

用法：
  python render_plan.py travel-plan/<trip_id>/trip.json
  python render_plan.py <trip.json> --out-dir <输出目录>
  python render_plan.py <trip.json> --check   # 只检查产物一致性

trip.json 结构（本 skill 的单一业务数据源）：
  {
    "trip_id": "...", "plan_version": 1,
    "status": "draft|conditional|executable_as_of_check", "status_note": "...",
    "request": {origin, dates, companions, budget, pace, interests,
                why, constraints[], taste{}, risk{}, nature_caps{}},
    # decisions 是 S1 访谈的过程记录：它只用于 S10 改方案时回看"当初是怎么定的"，
    # 与红方复核同理，不渲染进交付物 —— 读者要的是行程，不是问答流水
    "decisions": [{decision_id, question, choice, reason, pushed_back}],
    "itinerary": {
      "weather": {kind, summary, entries[]},
      "nature": {birding{}, stargazing{}, sunrise{}, beachcombing{}, glow{}},
      "days": [{day_id, date, weekday, title, items[], alternatives[]}],
      "food": [], "budget": {currency, categories[]}, "checklist": []
    },
    "pitfalls": [], "facts": [], "sources": [], "red_team": {rounds: []}
  }
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
from datetime import datetime
from urllib.parse import quote

import nature_widgets as nw  # 自然专项的交互件（星图/潮汐点选图等）

VERSION = "3.8.0"
ATTR_LINE = "由 旅行规划总编排（travel-orchestrator）生成"

STATUS_LABEL = {
    "draft": "草案",
    "conditional": "有条件可用",
    "executable_as_of_check": "已核查可用",
}
# 状态色：白字压在封面的粉蓝渐变上，要够深才读得清
PALETTE = {
    "draft": "#d6226b",
    "conditional": "#d6226b",
    "executable_as_of_check": "#0f7a5f",
}

KIND_LABEL = {
    "transport_major": "大交通",
    "transport": "交通",
    "visit": "景点",
    "meal": "用餐",
    "rest": "休整",
    "nature": "自然观察",
    "checkin": "入住",
    "shop": "采买",
}
KIND_CLASS = {
    "transport_major": "k-transport",
    "transport": "k-transport",
    "meal": "k-meal",
    "nature": "k-nature",
    "rest": "k-rest",
    "checkin": "k-rest",
    "shop": "k-meal",
    "visit": "k-visit",
}


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def e(v):
    return html.escape(str(v if v is not None else ""))


def canonical_hash(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


class TripDataError(ValueError):
    """行程数据本身有问题（结构缺失等）。这是渲染前的质量门，不是渲染故障。"""


def load_trip(path: str) -> dict:
    """读一份 trip.json（文件）。目录请走 read_trip_source。"""
    with open(path, "r", encoding="utf-8") as f:
        trip = json.load(f)
    if not isinstance(trip, dict):
        raise ValueError("行程数据不是对象")
    return trip


def structure_problems(trip: dict, origin: dict | None = None) -> list:
    """渲染前的结构门。

    渲染器对坏数据太宽容：字段缺了只会渲染成空白，命令照样返回 0，
    现场看着像成功、交付出去才发现是废纸。所以先借 build_trip 的规则拦一道。

    origin 是「字段 → 分片文件」映射（build_trip.load_parts 记录），传进来
    报错就能直接说清改哪个文件，省掉在十几个分片里盲翻的那几轮。
    """
    try:
        import build_trip as bt
    except ImportError:
        return []
    _ok, errors, _warns = bt.validate(trip, "trip.json", quiet=True, origin=origin)
    return list(errors)


def read_trip_source(path: str, with_origin: bool = False):
    """统一的取数入口：目录 → 就地合并 parts/；文件 → 读该文件。

    数据以分片为常态，所以这里不落中间大文件。
    with_origin=True 时额外返回「字段 → 分片文件」映射（四个返回值）。
    """
    p = os.path.abspath(path)
    try:
        import build_trip as bt
    except ImportError:
        bt = None
    origin = {}
    if bt is not None:
        if with_origin:
            trip, label, _n, origin = bt.resolve_trip(p, quiet=True, with_origin=True)
        else:
            trip, label, _n = bt.resolve_trip(p, quiet=True)
    elif os.path.isdir(p):
        raise TripDataError("找不到 build_trip.py，无法读取分片目录：" + p)
    else:
        trip, label = load_trip(p), os.path.basename(p)
    if not isinstance(trip, dict):
        raise ValueError("行程数据不是对象")
    src_dir = p if os.path.isdir(p) else os.path.dirname(p)
    if with_origin:
        return trip, label, src_dir, origin
    return trip, label, src_dir


def g(d, *keys, default=None):
    """安全取嵌套值：g(trip, 'request', 'dates', 'start')"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _dict(v):
    """不是 dict 就返回 {}。

    给 `request` / `itinerary` / `budget` 这类嵌套块用：写成字符串或数组时
    `x.get(...)` 会直接 AttributeError，整份交付物崩掉。一个块写错，
    最多少渲染一块，不该连累别的。
    """
    return v if isinstance(v, dict) else {}


def _objs(v):
    """把 `[{...}, ...]` 里不是对象的元素滤掉，只留 dict。

    校验层（build_trip.validate）会拦住这类错误，但渲染器不能假设"上游一定校验过"：
    直接 `x.get(...)` 会抛 AttributeError，用户看到的是 traceback 而不是
    「哪个字段写错了」。这里统一挡一道：坏数据最多少显示一条，不会让整份交付物崩掉。
    """
    return [x for x in (v or []) if isinstance(x, dict)]


def _geo(i):
    """取条目的 `geo`，不是对象就当成没填（字符串/数组写法曾经直接把渲染器打崩）。"""
    geo = i.get("geo")
    return geo if isinstance(geo, dict) else {}


def _tide_row(p):
    """潮汐曲线上的一个点 → `(时刻原文, 潮位) | (None, None)`。

    对象 `{t,h}` 和二元数组 `[time, h]` 两种写法都认；潮位统一过 nw.num，
    免得 `float("nan")` 在表格里印出 "+nan m"。
    """
    if isinstance(p, dict):
        tm = p.get("t") or p.get("time")
        h = p.get("h")
        if h is None:
            h = p.get("height_m")
    elif isinstance(p, (list, tuple)) and len(p) >= 2:
        tm, h = p[0], p[1]
    else:
        return None, None
    return tm, nw.num(h)


def _as_items(v):
    """把「列表 / 单个值」统一成列表。

    字段本该是 `["a", "b"]` 却写成 `"a"` 是常见手滑：`"；".join("abc")`
    会把一个词拆成三个字，不报错、静默出错。这里是统一收口。
    """
    if v in (None, ""):
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _texts(v):
    """`_as_items` 再转成字符串并丢掉空值，专给 `"、".join(...)` 用。"""
    return [str(x) for x in _as_items(v) if x not in (None, "")]


def _labels(v, *keys):
    """兴趣标签 → 字符串列表。元素是对象就取 `keys` 里第一个非空字段，是字符串就直接用。

    容两件事：元素写成字符串（结构上不合规，但意思读得懂）、字段名写成别名。
    以前直接 `x.get("label")`，元素是字符串就 AttributeError，一份交付物全崩。
    """
    out = []
    for x in (v or []):
        t = next((x.get(k) for k in keys if isinstance(x, dict) and x.get(k)), None) \
            if isinstance(x, dict) else x
        t = str(t or "").strip()
        if t:
            out.append(t)
    return out


def money(v, cur="CNY"):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:g} {cur}"
    return "待确认"


# ====================================================================== Markdown
def md_from_trip(trip: dict) -> str:
    req = _dict(trip.get("request"))
    it = _dict(trip.get("itinerary"))
    ver = trip.get("plan_version", 1)
    status = trip.get("status", "draft")
    L = []

    L.append(f"# {trip.get('trip_id', '旅行方案')}")
    L.append("")
    L.append(f"> 方案状态：**{STATUS_LABEL.get(status, status)}**（`{status}`）"
             + (f" · {trip['status_note']}" if trip.get("status_note") else ""))
    L.append(f"> 版本 v{ver} · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    L.append("")
    L.append("**使用须知**")
    L.append("")
    # 「注意什么」跟着方案里实际有的专项走：没有赶海就别提潮汐，
    # 否则正文找不到潮汐内容、开头却让人去复核潮汐表，读起来像模板没换。
    nature = it.get("nature") or {}
    changing = ["时间", "价格", "预约"]
    if nature.get("beachcombing"):
        changing.append("潮汐")
    if nature.get("stargazing"):
        changing.append("月相")
    if nature.get("sunrise"):
        changing.append("云量")
    changing.append("天气")
    focus = ["景区预约"]
    for key, label in (("beachcombing", "赶海潮汐"), ("sunrise", "日出日落"),
                       ("stargazing", "月相与云量"), ("birding", "鸟况")):
        if nature.get(key):
            focus.append(label)
    L.append("方案里的" + "、".join(changing) + "都可能变。出发前请对照官方渠道复核一遍，"
             "尤其是" + "、".join(focus[:4]) + "。查不到的信息这里会写「未查到」，"
             "不会用估计值凑数。")
    L.append("")

    # ---- 一页速览
    L.append("## 一页速览")
    L.append("")
    origin = g(req, "origin", "name") or "未定"
    ds, de = g(req, "dates", "start") or "?", g(req, "dates", "end") or "?"
    budget = _dict(req.get("budget"))
    L.append(f"| 项目 | 内容 |")
    L.append(f"|---|---|")
    L.append(f"| 出发地 | {origin} |")
    L.append(f"| 日期 | {ds} → {de} |")
    L.append(f"| 同行 | {req.get('companions') or '未定'} |")
    L.append(f"| 预算 | {money(budget.get('amount_max'), budget.get('currency', 'CNY'))}"
             f"（{'总额' if budget.get('mode') == 'total' else '人均' if budget.get('mode') == 'per_person' else '口径未定'}） |")
    L.append(f"| 节奏 | {req.get('pace') or '未定'} |")
    interests = _dict(req.get("interests"))
    must = "、".join(_labels(interests.get("must"), "label", "name"))
    prefer = "、".join(_labels(interests.get("prefer")))
    if must or prefer:
        L.append(f"| 专项偏好 | {_pref_text(must, prefer)} |")
    why = str(req.get("why") or "").strip()
    if why:
        L.append(f"| 这趟的主题 | {why} |")
    cons = [str(c).strip() for c in (req.get("constraints") or []) if str(c).strip()]
    if cons:
        L.append(f"| 硬约束 | {'；'.join(cons)} |")
    L.append("")

    # ---- 天气研判
    weather = it.get("weather") if isinstance(it.get("weather"), dict) else {}
    L.append("## 天气研判")
    L.append("")
    wx_entries = _objs(weather.get("entries"))
    if weather.get("kind") == "forecast" and wx_entries:
        for w in wx_entries:
            src = f"（{w['source_id']}）" if w.get("source_id") else ""
            L.append(f"- {w.get('date', '')}：{w.get('summary', '')}{src}")
    else:
        L.append(f"- {weather.get('summary') or '规划期气候参考。出发前请查逐日预报。'}")
    L.append("")

    # ---- 自然观察专项
    nature = it.get("nature") if isinstance(it.get("nature"), dict) else {}
    if nature:
        L.append("## 自然观察专项")
        L.append("")
        b = _sub(nature, "birding")
        if b:
            L.append("### 观鸟")
            L.append("")
            species = b.get("species") or []
            rich = [x for x in species if isinstance(x, dict)]
            if rich:
                L.append("| 鸟种 | 学名 | 保护等级 | 本地最佳月份 | 图鉴 |")
                L.append("|---|---|---|---|---|")
                for x in rich:
                    ranks = x.get("status") or x.get("保护等级") or []
                    if isinstance(ranks, str):
                        ranks = [ranks]
                    rk = "；".join(
                        (r.get("rank", "") + ("（%s）" % r["source"] if r.get("source") else ""))
                        if isinstance(r, dict) else str(r) for r in ranks) or "未查到"
                    months = x.get("best_months") or x.get("最佳月份") or "未查到"
                    if isinstance(months, (list, tuple)):
                        months = "、".join(str(m) for m in months)
                    url = x.get("taxa_url") or x.get("species_url") or ""
                    L.append("| %s | %s | %s | %s | %s |" % (
                        x.get("name", ""), x.get("sci") or "—", rk, months,
                        ("[图鉴](%s)" % url) if url else "—"))
                L.append("")
                for x in rich:
                    if x.get("intro"):
                        src = ("（来源：%s）" % x["intro_source"]) if x.get("intro_source") else ""
                        L.append("- **%s**：%s%s" % (x.get("name", ""), x["intro"], src))
            plain = [str(x) for x in species if not isinstance(x, dict)]
            if plain:
                L.append("- 近期可见鸟种：%s" % "、".join(plain))
                L.append("  - 查图：[iNaturalist](https://www.inaturalist.org/search?q=%s)｜"
                         "[图片搜索](https://image.baidu.com/search/index?tn=baiduimage&word=%s)"
                         % (plain[0], plain[0]))
            if b.get("hotspots"):
                L.append("- 观察点：%s" % _md_places(b["hotspots"]))
            L.append("- 最佳时段：%s（正午鸟少，别硬等）" % (b.get("best_time") or "日出后 2 小时"))
            if b.get("notes"):
                L.append("- %s" % b["notes"])
            L.append("")
        s = _sub(nature, "stargazing")
        if s:
            L.append("### 观星")
            L.append("")
            if s.get("moon_phase"):
                L.append("- 月相：%s" % s["moon_phase"])
            if s.get("dark_window"):
                L.append("- 暗夜窗口：%s" % s["dark_window"])
            if s.get("planets"):
                pl = [str(p.get("name") or "") if isinstance(p, dict) else str(p)
                      for p in _as_items(s["planets"])]
                L.append("- 可见行星：%s" % ("、".join(x for x in pl if x) or "—"))
            if s.get("rating"):
                L.append("- 观星条件：%s" % s["rating"])
            if s.get("events"):
                L.append("- 近期天文事件：%s" % "；".join(_texts(s["events"])))
            for t in _objs(s.get("targets")):
                if isinstance(t, dict):
                    L.append("- 目标：%s%s%s" % (
                        t.get("name", ""),
                        "（%s）" % t["type"] if t.get("type") else "",
                        " —— %s" % (t.get("note") or t.get("how")) if (t.get("note") or t.get("how")) else ""))
            cfg = s.get("chart") if isinstance(s.get("chart"), dict) else {}
            if cfg:
                L.append("- 星图：%s 内嵌示意星图（按 %.2f°N %.2f°E、%s 当地时刻算地平坐标，"
                         "只画地平线以上的亮星；未做大气折射与光污染修正）。"
                         "精确星图见 [Stellarium Web](https://stellarium-web.org/)。"
                         % (_md_places(s.get("spots")) if s.get("spots") else "见攻略页",
                            nw.num(cfg.get("lat")) or 0.0, nw.num(cfg.get("lng")) or 0.0,
                            cfg.get("at") or ""))
            L.append("")
        r = _sub(nature, "sunrise")
        gl = _sub(nature, "glow")
        if r:
            L.append("### 日出日落")
            L.append("")
            if r.get("sunrise") or r.get("sunset"):
                L.append("- 日出 %s｜日落 %s" % (r.get("sunrise") or "未查到",
                                                r.get("sunset") or "未查到"))
            if r.get("golden_hour"):
                L.append("- 黄金时刻：%s" % r["golden_hour"])
            if r.get("blue_hour"):
                L.append("- 蓝调时刻：%s" % r["blue_hour"])
            if r.get("best_spot"):
                L.append("- 观测点：%s" % _md_places(r["best_spot"]))
            if gl.get("score") is not None:
                L.append("- 霞光（火烧云）指数：%s/3 %s"
                         % (gl.get("score"), gl.get("level") or ""))
                L.append("  - 估算依据：%s" % "；".join(_texts(gl.get("reasons"))))
                L.append("  - 这是按云量分层、通透度、水汽算出的参考值，不是官方预报。")
            L.append("")
        elif gl:
            L.append("### 朝晚霞")
            L.append("")
            if gl.get("rating"):
                L.append("- 预报：%s" % gl["rating"])
            L.append("- 说明：这是按云量、通透度、水汽、气溶胶算出的参考值，不是官方预报。")
            L.append("")
        t = _sub(nature, "beachcombing")
        if t:
            L.append("### 赶海")
            L.append("")
            if t.get("tide_summary"):
                L.append("- 潮汐：%s" % t["tide_summary"])
            if t.get("best_window"):
                L.append("- 赶海窗口：%s" % t["best_window"])
            if t.get("tide_phase"):
                L.append("- 潮期：%s" % t["tide_phase"])
            if t.get("spots"):
                L.append("- 地点：%s" % _md_places(t["spots"]))
            if t.get("safety"):
                L.append("- ⚠️ %s" % t["safety"])
            curve = t.get("curve") if isinstance(t.get("curve"), list) else []
            if len(curve) >= 3:
                L.append("- 逐时潮位（%s，点选看每小时能不能下滩见攻略页）：" % (t.get("unit") or "潮位"))
                L.append("")
                L.append("| 时刻 | 潮位 |")
                L.append("|---|---|")
                for p in curve:
                    tm, h = _tide_row(p)
                    if h is None:
                        continue
                    L.append("| %s | %+.2f m |" % (str(tm)[-5:], h))
                for e_ in _objs(t.get("extremes")):
                    if e_.get("type"):
                        h = nw.num(e_.get("height_m"))
                        if h is None:
                            h = nw.num(e_.get("h"))
                        if h is None:
                            continue
                        L.append("| %s | %+.2f m（%s） |"
                                 % (str(e_.get("time"))[-5:], h, e_["type"]))
                L.append("")
            L.append("")

    # ---- 逐日行程
    L.append("## 逐日行程")
    L.append("")
    L.extend(_md_route(it))
    for d in _objs(it.get("days")):
        head = f"### {d.get('day_id', '')} · {d.get('date', '')}"
        if d.get("weekday"):
            head += f"（{d['weekday']}）"
        if d.get("title"):
            head += f" — {d['title']}"
        L.append(head)
        L.append("")
        for i in _objs(d.get("items")):
            if _blank_item(i):
                continue
            kind = KIND_LABEL.get(i.get("kind"), "")
            tag = f"`{kind}` " if kind else ""
            place = f" @ [{i['place']}]({_map_url(i['place'])})" if i.get("place") else ""
            st = i.get("booking_status")
            st_txt = {"confirmed": "已订", "not_required": "无需预约",
                      "todo": "待预约", "unavailable": "约不到"}.get(st, st or "")
            st_txt = f"（{st_txt}）" if st_txt else ""
            L.append(f"- **{i.get('time') or ''}** {tag}{i.get('title', '')}{place}{st_txt}")
            if i.get("notes"):
                L.append(f"  - {i['notes']}")
        for a in _objs(d.get("alternatives")):
            L.append(f"- *备选*：{a.get('title', '')}｜触发条件：{a.get('trigger', '未写')}")
        L.append("")

    # ---- 美食推荐
    food = _objs(it.get("food"))
    if food:
        L.append("## 美食推荐")
        L.append("")
        L.append("| 店/摊 | 人均 | 为什么去 | 备注 |")
        L.append("|---|---|---|---|")
        for f_ in food:
            nm = str(f_.get("name", ""))
            kw = f_.get("search") or nm
            bks = nw.booking_links(kw, f_.get("booking_kind") or "food")
            cell = nm + ("（" + " / ".join(f"[{t}]({u})" for t, u in bks) + "）" if bks else "")
            L.append(f"| {cell} | {f_.get('price', '未查到')} | "
                     f"{f_.get('why', '')} | {f_.get('note', '') or '—'} |")
        L.append("")
        L.append("> 人均是平台实时价，会变；点平台入口看当天报价，本方案不写死价格。")
        L.append("")

    # ---- 预算明细
    budget_it = it.get("budget") if isinstance(it.get("budget"), dict) else {}
    cats = _objs(budget_it.get("categories"))
    if cats:
        cur = budget_it.get("currency", "CNY")
        L.append("## 预算明细")
        L.append("")
        L.append(f"| 分项 | 区间（{cur}） |")
        L.append("|---|---|")
        lo_sum = hi_sum = 0.0
        for c in cats:
            # 走 nw.num：nan / inf / 布尔 / "80 元" 这类值都不该进算术与表格
            lo, hi = nw.num(c.get("min")), nw.num(c.get("max"))
            if lo is not None and hi is not None:
                lo_sum += lo
                hi_sum += hi
                L.append(f"| {c.get('name', '')} | {lo:g} – {hi:g} |")
            else:
                L.append(f"| {c.get('name', '')} | 未查到 |")
        if lo_sum or hi_sum:
            L.append(f"| **合计** | **{lo_sum:g} – {hi_sum:g}** |")
        cap = nw.num(g(req, "budget", "amount_max"))
        if cap is not None and hi_sum > cap:
            L.append("")
            L.append(f"> 注意：分项上限合计 {hi_sum:g}，比预算 {cap:g} 高出 {hi_sum - cap:g}。"
                     "要么压住宿和餐饮，要么把预算上调。")
        L.append("")

    # ---- 待办
    checklist = _objs(it.get("checklist"))
    if checklist:
        L.append("## 出发前待办")
        L.append("")
        for c in checklist:
            mark = "x" if c.get("status") == "done" else " "
            prio = "【必办】" if c.get("priority") == "must" else "【可选】"
            dl = f"，截止 {c['deadline']}" if c.get("deadline") else ""
            L.append(f"- [{mark}] {prio}{c.get('task', '')}{dl}")
            if c.get("if_unresolved"):
                L.append(f"  - 没办成怎么办：{c['if_unresolved']}")
        L.append("")

    # ---- 踩坑指南
    pitfalls = _objs(trip.get("pitfalls"))
    if pitfalls:
        L.append("## 踩坑指南")
        L.append("")
        for p in pitfalls:
            level = {"high": "🔴", "medium": "🟡"}.get(p.get("level"), "⚠️")
            L.append(f"- {level} **{p.get('title', '')}**")
            L.append(f"  - {p.get('detail', '')}")
            if p.get("when"):
                L.append(f"  - 什么时候会遇到：{p['when']}")
            if p.get("how"):
                L.append(f"  - 怎么绕：{p['how']}")
        L.append("")

    # 红方复核（scripts/red_team.py）是生成过程中的质量门，属于过程记录，不写进交付物。
    # 复核后仍未解决、且用户需要知道的风险，应改写进上面的「踩坑指南」。

    # ---- 依据索引
    facts = _objs(trip.get("facts"))
    sources = _objs(trip.get("sources"))
    if facts or sources:
        L.append("## 依据索引")
        L.append("")
        L.append("方案里每条关键数据都能追到下面某一项。追不到的都标了「估算」或「未查到」。")
        L.append("")
        if facts:
            L.append("| 编号 | 结论 | 状态 | 来源 | 取数日期 |")
            L.append("|---|---|---|---|---|")
            smap = {s.get("source_id"): s for s in sources}
            for f_ in facts:
                st = {"verified": "已核实", "estimated": "估算", "unverified": "未核实",
                      "unknown": "未查到"}.get(f_.get("status"), f_.get("status", ""))
                sids = f_.get("source_ids") or []
                names = "、".join((smap.get(sid, {}) or {}).get("name", sid) for sid in sids) or "—"
                dates = "、".join((smap.get(sid, {}) or {}).get("retrieved_at", "?") for sid in sids) or "—"
                claim = f_.get("claim") or f_.get("conclusion") or ""
                L.append(f"| {f_.get('fact_id', '')} | {claim} | {st} | {names} | {dates} |")
            L.append("")
        if sources:
            L.append("**数据来源**")
            L.append("")
            for s in sources:
                url = f" <{s['url']}>" if s.get("url") else ""
                L.append(f"- `{s.get('source_id', '')}` {s.get('name', '')}"
                         f"（{s.get('kind', '未标注类型')}，取数 {s.get('retrieved_at', '?')}）{url}")
            L.append("")

    L.append("---")
    L.append("")
    L.append(f"*{ATTR_LINE} · v{ver}*")
    return "\n".join(L)


# ====================================================================== HTML
# 样式表放在 assets/handbook.css，渲染时**内联**进 HTML，
# 所以产物仍然是纯离线单文件（无 <link>、无 @import、无远程资源）。
# 抽成独立文件是为了能整份替换样式，而不必在脚本里做大段字符串编辑。
FALLBACK_CSS = """
/* 兜底样式：assets/handbook.css 读不到时用。只保排版能看，不做动效。 */
:root{--bb-pink:#fb7299;--bb-pink-deep:#e5457a;--bb-pink-soft:#ffe9f1;
--bb-blue:#00a1d6;--bb-blue-deep:#0083ad;--bb-blue-soft:#e3f6fd;--bb-mint:#2bd6b5;
--bb-lilac:#b48cf2;--bb-gold:#ffb01a;--ink:#241f2b;--ink-2:#575060;--ink-3:#8b8495;
--bg:#fdfaff;--surface:#fff;--stroke:#ece4f0;--stroke-2:#d6cbe0;
--font-sans:'Varela Round','Quicksand','Yuanti SC','YouYuan','PingFang SC','Microsoft YaHei',sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);line-height:1.78;font-size:15px;
font-family:var(--font-sans)}
.wrap{max-width:940px;margin:0 auto;padding:0 18px 80px}
.cover{margin:0 0 26px;padding:44px 32px;color:#fff;
background:linear-gradient(118deg,#fb7299,#c9a6f5 62%,#00a1d6)}
.cover h1{margin:0 0 10px;font-size:30px;font-weight:900}
.cover .sub{font-size:13px;color:rgba(255,255,255,.94);margin-bottom:16px}
.cover .facts{display:flex;flex-wrap:wrap;gap:7px}
.cover .facts span{background:#fff;color:var(--ink);border:2px solid #fff;
border-radius:999px;padding:3px 13px;font-size:13px;font-weight:700;
box-shadow:2px 2px 0 rgba(0,131,173,.3)}
.badge{display:inline-block;padding:3px 12px;border-radius:999px;font-size:12.5px;
font-weight:800;color:#fff;margin-left:10px}
h2{display:flex;align-items:center;gap:11px;font-size:22px;margin:44px 0 16px;font-weight:900}
h2::after{content:'';flex:1;height:3px;border-radius:2px;
background:linear-gradient(90deg,rgba(251,114,153,.6),transparent)}
h2 .ico{width:30px;height:30px;display:grid;place-items:center;color:#fff;
background:var(--bb-pink);border-radius:10px}
h2 .ico svg{width:18px;height:18px}
h3{font-size:16.5px;margin:24px 0 8px;font-weight:800}
.card{background:var(--surface);border:1.5px solid var(--stroke);border-radius:16px;
padding:16px 18px;margin:12px 0;box-shadow:3px 3px 0 var(--bb-blue-soft)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:11px}
.grid.g-glance{grid-template-columns:repeat(4,minmax(0,1fr))}
.grid.g-glance .cell.half{grid-column:span 2}
.grid.g-glance .cell.wide{grid-column:1/-1}
.grid .cell{background:var(--surface);border:1.5px solid var(--stroke);border-radius:14px;
padding:12px 14px;box-shadow:2px 2px 0 var(--bb-blue-soft)}
.grid .cell b{display:block;font-size:11.5px;color:var(--ink-3)}
.grid .cell span{font-size:15px;font-weight:700;overflow-wrap:break-word;
word-break:normal;line-break:strict}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-style:normal;display:inline-block;padding:2px 10px;border-radius:999px;
font-size:12.5px;font-weight:700;color:var(--ink-2);background:#f7f4fa;
border:1px solid var(--stroke-2)}
.bks{display:inline-flex;flex-wrap:wrap;gap:5px;margin-left:8px;vertical-align:middle}
.bk{display:inline-block;font-size:11.5px;font-weight:800;padding:1px 9px;border-radius:999px;
color:var(--bb-pink-deep);background:#fff5f9;border:1px solid var(--bb-pink-soft);
text-decoration:none;white-space:nowrap}
.bk::before{content:'↗';margin-right:3px;font-size:10px}
.exlog{margin-top:7px;font-size:12px;color:var(--ink-3)}
@media (max-width:820px){.grid.g-glance{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:480px){
  .grid.g-glance{grid-template-columns:1fr}
  .grid.g-glance .cell.half{grid-column:1/-1}
  .grid{gap:9px}
  .grid .cell{padding:11px 13px}
  .grid .cell span{font-size:14px}
}
.route{background:var(--surface);border:1.5px solid var(--stroke);border-radius:16px;
padding:13px 15px;margin:0 0 14px;box-shadow:3px 3px 0 var(--bb-blue-soft)}
.route .rhead{font-weight:900;font-size:14px;color:var(--ink-2);margin-bottom:7px}
.route .rhead .rsub{font-size:12px;font-weight:600;color:var(--ink-3)}
.rmap{margin:2px 0 9px;border:1.5px dashed var(--stroke-2);border-radius:12px;
background:#faf9fd;padding:6px 8px 4px}
.rmap svg{display:block;width:100%;height:auto;max-height:270px}
.rgrid line{stroke:#ece7f2;stroke-width:1}
.rpath{fill:none;stroke:var(--bb-blue);stroke-width:2;stroke-dasharray:6 5;opacity:.75}
.rmap circle{stroke:#fff;stroke-width:2;paint-order:stroke}
.rmapcap{margin-top:2px;font-size:11.5px;color:var(--ink-3);text-align:center}
.rline{display:flex;gap:9px;align-items:flex-start;padding:5px 0;flex-wrap:wrap}
.rline>b{flex:0 0 auto;font-size:12.5px;font-weight:900;color:#fff;background:var(--bb-pink);
padding:2px 10px;border-radius:999px}
.rstops{display:flex;flex-wrap:wrap;align-items:center;gap:5px;min-width:0}
.rnode{display:inline-flex;align-items:center;gap:5px;font-size:13px;font-weight:700;
color:var(--ink);text-decoration:none;background:#f7f4fa;border:1.5px solid var(--stroke);
border-radius:999px;padding:2px 11px 2px 4px}
.rnode i{font-style:normal;font-size:11px;font-weight:900;color:#fff;background:var(--bb-blue);
width:17px;height:17px;border-radius:50%;display:grid;place-items:center}
.rarrow{color:var(--bb-pink);font-weight:900}
.rfoot{margin-top:7px;font-size:12px;color:var(--ink-3);display:flex;flex-wrap:wrap;
gap:9px;align-items:center}
.rfoot a{color:var(--bb-blue-deep);font-weight:800;text-decoration:none}
.rfoot .rbtn{display:inline-flex;align-items:center;background:var(--bb-blue);color:#fff;
border:1.5px solid var(--bb-blue);border-radius:999px;padding:3px 13px;font-size:12.5px}
.rfoot .rbtn.ghost{background:#fff;color:var(--bb-pink-deep);border-color:var(--bb-pink)}
.rfoot>span{flex:1 1 100%;line-height:1.6}
@media (max-width:480px){.rmap svg{max-height:200px}}
.hl{font-weight:800}
.tl{position:relative;padding-left:26px;border-left:2px dashed rgba(0,161,214,.4)}
.slot{position:relative;margin:0 0 8px;padding:8px 11px;border-radius:13px}
.slot::before{content:'';position:absolute;left:-33px;top:15px;width:11px;height:11px;
border-radius:4px;background:var(--bb-pink);box-shadow:0 0 0 3px var(--bg)}
.slot .t{font-weight:800;color:var(--bb-blue-deep);font-size:13.5px}
.slot .ti{font-weight:800}
.slot .pl{color:var(--ink-2);font-size:13px}
.slot .nt{color:var(--ink-2);font-size:13px;margin-top:3px}
.tag{display:inline-block;font-size:11px;font-weight:800;padding:1px 9px;
border-radius:999px;margin-left:7px;border:1px solid currentColor;background:#f7f4fa}
.map{color:var(--bb-blue-deep);font-weight:700;text-decoration:none;
border-bottom:1.5px dotted rgba(0,131,173,.55)}
.alt{background:#fff6fa;border:1.5px dashed rgba(251,114,153,.4);border-radius:14px;
padding:10px 14px;font-size:13px;margin:8px 0 0}
.daynav{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.daynav a{display:inline-flex;gap:7px;padding:6px 13px;border-radius:999px;background:#fff;
border:1.5px solid var(--stroke);font-size:13px;font-weight:700;color:var(--ink-2);
text-decoration:none;box-shadow:2px 2px 0 var(--bb-blue-soft)}
.day{border:1.5px solid var(--stroke);border-radius:17px;padding:0 18px;margin:0 0 14px;
background:#fff;box-shadow:3px 3px 0 var(--bb-blue-soft)}
.day>summary{display:flex;align-items:center;gap:10px;padding:14px 0;cursor:pointer;
list-style:none;font-weight:800}
.day>summary::-webkit-details-marker{display:none}
.day .dnum{font-weight:800;font-size:12.5px;color:#fff;background:var(--bb-pink);
padding:2px 11px;border-radius:999px;border:2px solid #fff}
.day .dmeta{flex:1;font-size:15px}
.day .dmeta em{font-style:normal;font-weight:800;color:var(--bb-blue-deep);margin-right:7px}
.day .dcnt{font-size:11.5px;font-weight:800;color:var(--ink-3)}
.day .dbody{padding-bottom:14px}
.nature{display:grid;grid-template-columns:1fr;gap:14px}
.nature .card{margin:0;padding:0;overflow:hidden;display:grid;
grid-template-columns:216px minmax(0,1fr)}
.nature .scene{position:relative;overflow:hidden;min-height:166px;
border-right:1.5px solid var(--stroke)}
.nature .scene .label{position:absolute;left:13px;bottom:26px;font-weight:900;
font-size:16px;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,.4)}
.nature .body{padding:16px 18px}
.nature .dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
gap:11px 22px;margin:0}
.nature .kv{border-left:3px solid var(--bb-pink);padding-left:11px}
.nature .kv:nth-child(4n+2){border-left-color:var(--bb-blue)}
.nature .kv:nth-child(4n+3){border-left-color:var(--bb-mint)}
.nature .kv:nth-child(4n){border-left-color:var(--bb-lilac)}
.nature .kv dt{font-size:11.5px;color:var(--ink-3);font-weight:800}
.nature .kv dd{margin:1px 0 0;font-size:14px}
.scene-star{background:#333c74}.scene-sea{background:#4dbde4}.scene-bird{background:#a8e0d2}
.scene-sun{background:#ffc98f}.scene-glow{background:#ffb0a8}
.star,.wave,.bird,.reeds,.sun,.ray,.cloudbar,.glowband,.seashell,.moonp,.star-halo{display:none}
.todo{display:flex;gap:11px;align-items:flex-start;padding:9px 11px;border-radius:13px}
.todo .mark{width:19px;height:19px;margin:3px 0 0;border:1.8px solid var(--stroke-2);
border-radius:6px;display:grid;place-items:center;font-size:12px;font-weight:900;color:#fff}
.todo.is-done .mark{background:var(--bb-blue);border-color:var(--bb-blue)}
.todo .ti{font-weight:800}
.todo.is-done .ti{color:var(--ink-3);text-decoration:line-through}
.todo .nt{color:var(--ink-2);font-size:13px;margin-top:3px}
.todo-sum{font-size:12.5px;font-weight:800;color:var(--ink-2);padding:2px 11px 10px;
border-bottom:1.5px dashed var(--stroke);margin-bottom:6px}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--stroke)}
th{font-size:12.5px;background:#fbf7fd}
td.num,th.num{text-align:right;font-weight:700}
.bar{position:relative;height:8px;background:#f2eef6;border-radius:5px;margin-top:6px}
.bar i{position:absolute;height:100%;border-radius:5px;
background:linear-gradient(90deg,var(--bb-pink),var(--bb-blue))}
details{background:#fff;border:1.5px solid var(--stroke);border-radius:15px;padding:0 16px;
margin:12px 0}
summary{cursor:pointer;padding:13px 0;font-weight:800;font-size:14.5px;list-style:none}
details .body{padding-bottom:14px}
.srcs{font-size:12.5px;color:var(--ink-2)}
.srcs a,.src-link{color:var(--bb-blue-deep);word-break:break-all}
.warn{background:#fff7f2;border:1.5px solid #ffdcc8;border-left:5px solid #ff7a45;
border-radius:6px 15px 15px 6px;padding:13px 17px;margin:10px 0}
.warn b{color:#c2410c;font-size:16px}.warn .when{color:#7c5a4a;font-size:13px}
.note{font-size:13px;background:#f5fbff;border:1.5px dashed rgba(0,161,214,.4);
border-radius:13px;padding:10px 14px;margin:10px 0}
.legend{font-size:12.5px;color:var(--ink-3)}
footer{margin-top:50px;padding-top:16px;border-top:1.5px dashed var(--stroke-2);
color:var(--ink-3);font-size:12.5px;text-align:center}
.totop{position:fixed;right:18px;bottom:18px;width:46px;height:46px;border-radius:15px;
background:var(--bb-pink);border:2px solid #fff;color:#fff;text-decoration:none;
display:flex;align-items:center;justify-content:center;font-size:18px}
.bling,.danmaku{display:none}
@media print{.toc,.totop,.daynav{display:none}.card,details,.day,.warn{break-inside:avoid}
details .body,.day .dbody{display:block}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""



_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# 只认真正的远程引用（@import 远程地址 / url(http…)）；
# 注释里提到关键字、或内联 data: 图形都不算，别误伤。
_CSS_REMOTE = re.compile(r"@import\s+[\"']?\s*(?:https?:)?//|url\(\s*[\"']?\s*(?:https?:)?//",
                         re.I)


def _strip_css_comments(css):
    """去掉样式注释再判断，避免注释里提到某个关键字就被误判成远程引用。"""
    return _CSS_COMMENT.sub("", css)


def load_css():
    """读 assets/handbook.css；读不到或含远程引用时退回内置兜底样式，保证渲染永不因样式失败。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "handbook.css")
    try:
        with open(path, encoding="utf-8") as f:
            css = f.read()
        if css.strip():
            if _CSS_REMOTE.search(_strip_css_comments(css)):
                print("[警告] assets/handbook.css 含远程引用，已改用内置兜底样式",
                      file=sys.stderr)
            else:
                return css
    except OSError:
        pass
    return FALLBACK_CSS


def _clip(text, limit):
    """弹幕条专用：在自然断点截断，别把括号和分号切一半。"""
    for sep in ("（", "；", "，"):
        head = text.split(sep)[0]
        if len(head) >= 6:
            text = head
            break
    return text if len(text) <= limit else text[:limit] + "…"


def _danmaku(trip):
    """封面底部的弹幕条：把方案里最该先看到的事实飘一遍。

    纯装饰，但只放真实数据 —— 拿不到就不飘这一条，不编。
    """
    req = _dict(trip.get("request"))
    it = _dict(trip.get("itinerary"))
    nat = it.get("nature") or {}
    pills = []
    dest = g(req, "destination", "name") or trip.get("title") or ""
    org = g(req, "origin", "name") or ""
    if dest and org:
        pills.append(org + " → " + dest)
    elif dest:
        pills.append(dest + " 行程")
    elif org:
        pills.append("从" + org + "出发")
    days = len(it.get("days") or [])
    if days:
        pills.append(str(days) + " 天")
    sunrise = (nat.get("sunrise") or {}).get("sunrise")
    if sunrise:
        pills.append("日出 " + str(sunrise))
    tide = (nat.get("beachcombing") or {}).get("tide_summary")
    if tide:
        pills.append(_clip(str(tide), 22))
    moon = (nat.get("stargazing") or {}).get("moon_phase")
    if moon:
        pills.append("月相 " + _clip(str(moon), 18))
    for key, head in (("birding", "观鸟"), ("glow", "朝晚霞")):
        if nat.get(key) and len(pills) < 5:
            pills.append(head)
    pills = pills[:5]
    if not pills:
        return ""
    return ('<div class="danmaku" aria-hidden="true">'
            + "".join("<i>" + e(p) + "</i>" for p in pills) + "</div>")


def _cover(trip):
    req = _dict(trip.get("request"))
    status = trip.get("status", "draft")
    ds = g(req, "dates", "start") or "?"
    de = g(req, "dates", "end") or "?"
    days = len(g(trip, "itinerary", "days", default=[]) or [])
    # 封面只放"定位信息"：日期、天数、往返、同行。
    # 专项标签曾经也贴在这里，但速览的「专项偏好」与下面的专项节都会再说一遍 ——
    # 一个事实只在页面上有一个家，重复就是噪音（尤其手机上，chips 一多就换两行）。
    chips = [f"{ds} → {de}", f"{days} 天", _route_text(req, trip),
             req.get("companions") or "同行未定"]
    chips_html = "".join(f"<span>{e(c)}</span>" for c in chips if c and c != "未定")
    note = f'<div class="note">{e(trip["status_note"])}</div>' if trip.get("status_note") else ""
    return (
        f'<header class="cover">{COVER_SPARKS}{COVER_STICKERS}'
        f'<h1>{e(trip.get("title") or trip.get("trip_id", "旅行方案"))}'
        f'<span class="badge" style="background:{PALETTE.get(status, "#d6226b")}">'
        f'{e(STATUS_LABEL.get(status, status))}</span></h1>'
        f'<div class="sub">v{trip.get("plan_version", 1)} · 生成于 '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M")} · 时间价格预约以官方为准</div>'
        f'<div class="facts">{chips_html}</div>{note}'
        f'{_danmaku(trip)}</header>'
    )


def origin_txt(req):
    o = g(req, "origin", "name")
    return f"从{o}出发" if o else ""


def _route_text(req, trip):
    """往返合成一格：目的地和出发地都是只有地名的短值，分开占两格会把网格排歪。"""
    dest = g(req, "destination", "name") or trip.get("title") or ""
    org = g(req, "origin", "name") or ""
    if org and dest:
        return f"{org} → {dest}"
    return dest or org or "未定"


def _pref_text(must, prefer):
    """专项偏好的统一口径。

    原来分「必去专项」「优先专项」两格，两格都写着"专项"，读者分不出区别 ——
    并成一格，把 must / prefer 的差别直接写在值里。
    """
    parts = []
    if must:
        parts.append("必须玩到：" + must)
    if prefer:
        parts.append("能做则做：" + prefer)
    return "　·　".join(parts) or "未定"


def _glance(trip):
    """速览：固定 4 列的网格（`.g-glance`），每格按 span 拼，行行填满。

    分工（一个事实在页面上只有一个家，重复就是噪音）：
      封面管定位：日期 / 天数 / 往返 / 同行
      这里管决策：主题（有则整行）→ 预算 / 节奏 / 专项偏好（合成一行）→ 硬约束（整行）
    宽度按格数动态分配（三格 1+1+2、两格 2+2、一格整行），所以哪几项有值
    都不会留下空格子；窄屏由 CSS 降到 2 列 / 1 列。
    """
    req = _dict(trip.get("request"))
    b = _dict(req.get("budget"))
    mode = {"total": "总额", "per_person": "人均"}.get(b.get("mode"), "口径未定")
    interests = _dict(req.get("interests"))
    must = "、".join(_labels(interests.get("must"), "label", "name"))
    prefer = "、".join(_labels(interests.get("prefer")))

    cells = []
    why = str(req.get("why") or "").strip()
    if why:
        cells.append(("这趟的主题", e(why), "wide"))

    mid = [("预算", f"{money(b.get('amount_max'), b.get('currency', 'CNY'))}（{mode}）")]
    pace = str(req.get("pace") or "").strip()
    if pace:
        mid.append(("节奏", pace))
    pref = _pref_text(must, prefer)
    if pref != "未定":
        mid.append(("专项偏好", pref))
    span_of = {1: "", 2: "half", 4: "wide"}
    widths = {3: [1, 1, 2], 2: [2, 2], 1: [4]}[len(mid)]
    cells.extend((k, e(v), span_of[w]) for (k, v), w in zip(mid, widths))

    # 硬约束拆成一串小胶囊：手机上不再是一条读不完的分号长句
    cons = [str(c).strip() for c in (req.get("constraints") or []) if str(c).strip()]
    if cons:
        chips = "".join(f'<i class="chip">{e(c)}</i>' for c in cons)
        cells.append(("硬约束", f'<span class="chips">{chips}</span>', "wide"))

    if not cells:
        return ""
    inner = "".join(
        f'<div class="cell{(" " + sp) if sp else ""}"><b>{e(k)}</b>'
        f"<span>{v}</span></div>" for k, v, sp in cells)
    return h2("glance", "一页速览") + f'<div class="grid g-glance">{inner}</div>'


def _weather(it):
    w = it.get("weather") if isinstance(it.get("weather"), dict) else {}
    if not w:
        return ""
    out = [h2("weather", "天气研判")]
    entries = _objs(w.get("entries"))
    if w.get("kind") == "forecast" and entries:
        cells = []
        for x in entries:
            src = f'<b style="display:block;font-size:12px;color:var(--ink-3)">{e(x.get("source_id", ""))}</b>' if x.get("source_id") else ""
            cells.append(f'<div class="cell"><b>{e(x.get("date", ""))}</b>'
                         f'<span>{e(x.get("summary", ""))}</span>{src}</div>')
        out.append(f'<div class="grid">{"".join(cells)}</div>')
    else:
        out.append(f'<div class="card">{e(w.get("summary") or "规划期气候参考，出发前请查逐日预报。")}</div>')
    return "".join(out)


# 主题动效头图：纯 CSS 动画，没有任何脚本（见 references/design-system.md §6）
MOTIF = {
    # 观星：八颗错峰闪烁的星星 + 缓缓浮动的月亮 + 一处十字星芒
    "star": ('<span class="star s1"></span><span class="star s2"></span>'
             '<span class="star s3"></span><span class="star s4"></span>'
             '<span class="star s5"></span><span class="star s6"></span>'
             '<span class="star s7"></span><span class="star s8"></span>'
             '<span class="star-halo"></span><span class="moonp"></span>'),
    # 赶海：三层海浪不同速度横向漂移 + 一只随浪起伏的贝壳
    "sea": ('<span class="wave w1"></span><span class="wave w2"></span>'
            '<span class="wave w3"></span><span class="seashell"></span>'),
    # 观鸟：两只小鸟扇翅横穿 + 三丛随风摇摆的芦苇
    "bird": ('<span class="reeds"></span><span class="reeds r2"></span>'
             '<span class="reeds r3"></span>'
             '<span class="bird"><span class="wing wl"></span><span class="wing wr"></span>'
             '<span class="bird-body"></span></span>'
             '<span class="bird b2"><span class="wing wl"></span><span class="wing wr"></span>'
             '<span class="bird-body"></span></span>'),
    # 日出：太阳在水平线附近呼吸式升降 + 旋转的光芒 + 两片缓慢飘过的云
    "sun": ('<span class="ray"></span><span class="sun"></span>'
            '<span class="cloudbar c1"></span><span class="cloudbar c2"></span>'),
    # 朝晚霞：三条霞光带通透移
    "glow": ('<span class="glowband g1"></span><span class="glowband g2"></span>'
             '<span class="glowband g3"></span>'),
}

NATURE_THEME = {
    "birding": ("bird", "观鸟"),
    "stargazing": ("star", "观星"),
    "sunrise": ("sun", "日出日落"),
    "beachcombing": ("sea", "赶海"),
    "glow": ("glow", "朝晚霞"),
}


def _scene(theme, label):
    """带主题动效的卡片头图。"""
    return (f'<div class="scene scene-{theme}">{MOTIF.get(theme, "")}'
            f'<span class="label">{e(label)}</span></div>')


def _sub(d, key):
    """取 `nature` 下的一个子块，不是对象就当没填（返回 {}）。

    四个专项历史上都写成 `n.get(k) or {}`，数据里把某个专项写成字符串/数组时
    会直接 AttributeError 崩掉整份交付物——一个专项写错不该拖垮另外三个。
    """
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _md_places(value):
    """地点字段 → Markdown 链接串。列表、字符串两种写法都认。"""
    if not value:
        return "未查到"
    items = value if isinstance(value, (list, tuple)) else nw._split_places(value)
    out = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or ""
            note = item.get("note") or ""
        else:
            name, note = str(item), ""
        if not name:
            continue
        link = "[%s](%s)" % (name, _map_url(nw.search_keyword(name)))
        out.append(link + ("（%s）" % note if note else ""))
    return "、".join(out) or "未查到"


def _nature(it):
    """自然观察专项：四项数据一行一张全宽卡。
    卡里除了要点，还挂上能点的东西——鸟种卡、星图、天光条 + 霞光指数、潮汐点选图。"""
    n = it.get("nature") if isinstance(it.get("nature"), dict) else {}
    if not n:
        return ""
    cards = []

    b = _sub(n, "birding")
    if b:
        rows = []
        if b.get("hotspots"):
            rows.append(("观察点", nw.place_links(b["hotspots"]), True, True))
        rows.append(("最佳时段", b.get("best_time") or "日出后 2 小时"))
        if b.get("notes"):
            rows.append(("注意", b["notes"]))
        cards.append(_nature_card("观鸟", rows, "birding",
                                  extra=nw.species_list(b.get("species")), extra_first=True))

    s = _sub(n, "stargazing")
    if s:
        rows = []
        if s.get("moon_phase"):
            rows.append(("月相", s["moon_phase"]))
        if s.get("dark_window"):
            rows.append(("暗夜窗口", s["dark_window"]))
        if s.get("planets"):
            pl = [str(p.get("name") or "") if isinstance(p, dict) else str(p)
                  for p in _as_items(s["planets"])]
            pl = [x for x in pl if x]
            rows.append(("可见行星", "、".join(pl) if pl else "—"))
        if s.get("rating"):
            rows.append(("观星条件", s["rating"]))
        if s.get("events"):
            rows.append(("天文事件", "；".join(_texts(s["events"]))))
        if s.get("spots"):
            rows.append(("观测点", nw.place_links(s["spots"]), True, True))
        cards.append(_nature_card("观星", rows, "stargazing", extra=nw.star_section(s)))

    r = _sub(n, "sunrise")
    gl = _sub(n, "glow")
    if r:
        rows = []
        if r.get("sunrise") or r.get("sunset"):
            rows.append(("时刻", "日出 %s · 日落 %s"
                         % (r.get("sunrise") or "未查到", r.get("sunset") or "未查到")))
        if r.get("golden_hour"):
            rows.append(("黄金时刻", r["golden_hour"]))
        if r.get("blue_hour"):
            rows.append(("蓝调时刻", r["blue_hour"]))
        if r.get("best_spot"):
            rows.append(("观测点", nw.place_links(r["best_spot"]), True, True))
        # 霞光（火烧云）本来就长在日出日落上，合成一张卡看，别单列一节让人两头对时间
        cards.append(_nature_card("日出日落", rows, "sunrise",
                                  extra=nw.sun_section(r, gl)))
    elif gl:
        rows = []
        if gl.get("rating"):
            rows.append(("预报", gl["rating"]))
        cards.append(_nature_card("朝晚霞", rows, "glow", extra=nw.glow_meter(gl)))

    t = _sub(n, "beachcombing")
    if t:
        rows = []
        if t.get("tide_summary"):
            rows.append(("潮汐", t["tide_summary"]))
        if t.get("best_window"):
            rows.append(("赶海窗口", t["best_window"]))
        if t.get("tide_phase"):
            rows.append(("潮期", t["tide_phase"]))
        if t.get("spots"):
            rows.append(("地点", nw.place_links(t["spots"]), True, True))
        if t.get("safety"):
            rows.append(("安全", t["safety"]))
        cards.append(_nature_card("赶海", rows, "beachcombing",
                                  extra=nw.tide_section(t, "beachcombing")))

    if not cards:
        return ""
    return h2("nature", "自然观察专项") + '<div class="nature">' + "".join(cards) + "</div>"


def _nature_card(title, rows, key, extra="", extra_first=False):
    """一个专项一整行。rows 里第三项为 True 表示该值是 HTML（地点链接这类）。

    带交互件的卡走通栏布局（场景动效在顶部），没交互件的仍是左边竖条 —— 见
    handbook.css 里 .nature .card.tall 那段。

    rows 每项是 (标签, 值)、(标签, 值, 值是HTML)、(标签, 值, 值HTML, 整行通栏)。
    地点串一律走通栏——「地名（备注）」挤在 1/3 列里会被拆得七零八落。
    """
    theme, label = NATURE_THEME.get(key, ("glow", title))
    kvs = []
    for row in rows:
        k, v = row[0], row[1]
        raw = len(row) > 2 and bool(row[2])
        wide = len(row) > 3 and bool(row[3])
        kvs.append('<div class="kv' + (" wide" if wide else "") + '"><dt>' + e(k)
                   + "</dt><dd>" + (v if raw else e(v)) + "</dd></div>")
    stk = sticker_svg(NATURE_STICKER.get(key, "sparkle"))
    body = ('<dl class="dl">' + "".join(kvs) + "</dl>") if kvs else ""
    if extra:
        body = (extra + body) if extra_first else (body + extra)
    cls = "card tall" if extra else "card"
    return ('<div class="' + cls + '"><div class="scene scene-' + theme + '">'
            + MOTIF.get(theme, "")
            + '<span class="label">' + e(label) + "</span>"
            + '<i class="stk" aria-hidden="true">' + stk + "</i></div>"
            + '<div class="body">' + body + "</div></div>")


def _short_date(value):
    """2026-10-01 → 10-01；格式不对就原样返回。"""
    s = str(value or "")
    m = re.match(r"\d{4}-(\d{2}-\d{2})", s)
    return m.group(1) if m else s


def _day_anchor(day, idx):
    """给每天一个能当锚点用的 id。"""
    raw = str(day.get("day_id") or ("D" + str(idx)))
    return "day-" + re.sub(r"[^0-9A-Za-z_-]", "-", raw)


def _map_url(place):
    """地点 → 地图搜索链接。只是拼字符串，页面本身不联网。"""
    return nw.map_url(place)


def _maplink(place):
    return nw.map_link(place)


def _blank_item(i):
    """字段名对不上（或整条为空）的条目不该渲染成一个空白行。

    生成侧最容易出的问题是字段名写错：条目里一个字段都认不出来时，
    旧渲染器照样输出一个空 slot，页面上就是"无缘无故的空白行"。
    这里统一判空，宁可少一行，也不要留一行空白让读者以为页面坏了。
    """
    return not any(str(i.get(k) or "").strip()
                   for k in ("title", "time", "place", "notes", "kind"))


ROUTE_COLORS = ("#fb7299", "#00a1d6", "#b48cf2", "#2bd6b5", "#ffb01a", "#ff9f7a")


def _spread(xy, radius=16.0):
    """完全重合的点（早上和晚上都在同一个镇）会叠成一坨，序号糊在一起。

    把重合的一组在一个小圆上摊开：偏移量按 16px 算，换算到地图上是几十到几百米，
    在一个街区量级，示意图上完全可以接受——总比两个数字叠着看不清强。
    """
    groups = {}
    for k, (x, y) in enumerate(xy):
        groups.setdefault((round(x, 1), round(y, 1)), []).append(k)
    out = list(xy)
    for (cx, cy), idxs in groups.items():
        if len(idxs) < 2:
            continue
        for pos, k in enumerate(idxs):
            ang = 2 * math.pi * pos / len(idxs) - math.pi / 2
            out[k] = (cx + radius * math.cos(ang), cy + radius * math.sin(ang))
    return out


def _routemap(pts, total=None):
    """内联 SVG 动线示意图：把带坐标的停靠点按顺序连起来（离线，不联网）。

    离线单文件放不了真地图（瓦片要联网、要 Key），所以这里画的是**相对方位
    正确、比例不作数**的示意图：谁在东边、谁在西边、顺序怎么走，一眼能看出来。

    `pts` 每项是 (lng, lat, name, day_no, seq)：`seq` 是**全局停靠点序号**，
    也就是下方逐日列表里的标号——两边必须一致，读者才能对着图找行。
    没坐标的点不画，序号就会留缺口，末尾用一句话说明，别让人以为漏了。
    """
    if len(pts) < 2:
        return ""
    W, H = 640, 300
    proj = nw.project_points([(p[0], p[1], p[2]) for p in pts], W, H)
    if len(proj) < 2:
        return ""
    xy = _spread([(p[0], p[1]) for p in proj])
    path = " ".join("%.1f,%.1f" % (p[0], p[1]) for p in xy)
    grid = "".join('<line x1="%d" y1="0" x2="%d" y2="%d"/>' % (x, x, H)
                   for x in range(80, W, 80))
    grid += "".join('<line x1="0" y1="%d" x2="%d" y2="%d"/>' % (y, W, y)
                    for y in range(60, H, 60))
    nodes = []
    for (x, y), p in zip(xy, pts):
        name, n = p[2], p[4]
        c = ROUTE_COLORS[(p[3] - 1) % len(ROUTE_COLORS)]
        nodes.append(
            '<g><title>' + e("%d. %s" % (n, name))
            + '</title><circle cx="%.1f" cy="%.1f" r="11" fill="%s"/>' % (x, y, c)
            + '<text x="%.1f" y="%.1f" text-anchor="middle" dominant-baseline="central" '
              'font-size="11" font-weight="700" fill="#ffffff">%d</text></g>' % (x, y, n)
        )
    cap = "位置示意（非等比）：标号＝下方停靠点序号，颜色按天区分"
    if total and total > len(pts):
        cap += "；另有 %d 个点未录坐标，图中不显示" % (total - len(pts))
    return (
        '<div class="rmap"><svg viewBox="0 0 %d %d" role="img" '
        'aria-label="行程动线示意图" preserveAspectRatio="xMidYMid meet">' % (W, H)
        + '<g class="rgrid">' + grid + '</g>'
        + '<polyline class="rpath" points="' + path + '"/>'
        + "".join(nodes)
        + '</svg><div class="rmapcap">' + e(cap) + '</div></div>'
    )


def _stops(it):
    """全行程的停靠点 + 全局序号 —— 编号规则的**唯一**实现。

    同一天里同名地点只算一次，跨天连续编号。HTML 的动线图、MD 的动线清单、
    逐日列表都读这一份结果，才不会出现「图上第 5 个点」和「MD 第 5 条」指两处。
    以前这条规则在 _flow / _md_route 里各写一遍，改一处忘一处就悄悄错位。

    返回 (rows, total)：rows 是 [(day_id, [(name, seq, lat, lng), ...]), ...]，
    只保留有停靠点的天；total 是最大的那个全局序号。
    """
    rows, seq = [], 0
    for idx, d in enumerate(_objs(it.get("days")), 1):
        did = d.get("day_id") or "D" + str(idx)
        stops = []
        for i in _objs(d.get("items")):
            if _blank_item(i) or not i.get("place"):
                continue
            name = str(i["place"]).strip()
            if not name or any(name == s[0] for s in stops):
                continue
            geo = _geo(i)
            seq += 1
            stops.append((name, seq, nw.num(geo.get("lat")), nw.num(geo.get("lng"))))
        if stops:
            rows.append((did, stops))
    return rows, seq


def _md_route(it):
    """Markdown 版动线一览：全局编号的停靠点清单 + 两个跳外部地图的链接。

    编号来自 _stops()，与 HTML 版共用同一份结果，所以「HTML 上看第 5 个点」
    和「MD 里第 5 条」指的是同一个地方。
    MD 画不了 SVG，要看图请开 HTML 版——HTML 是主交付物。
    """
    rows, _total = _stops(it)
    if not rows:
        return []
    lines, pts = [], []
    for did, stops in rows:
        for name, n, lat, lng in stops:
            if lat is not None and lng is not None:
                pts.append((lng, lat, name))
            lines.append(f"{n}. [{name}]({_map_url(name)})（{did}）")
    out = ["**动线一览**（按行程顺序；编号与下方逐日停靠点一致）", ""]
    out.extend(lines)
    links = [x for x in (f"[在高德看全部点位]({nw.markers_url(pts)})" if pts else "",
                         f"[规划整条路线]({nw.route_url(pts)})" if len(pts) >= 2 else "") if x]
    if links:
        out += ["", " · ".join(links)]
    out.append("")
    return out


def _flow(it):
    """行程动线：一张路线示意图 + 逐日停靠点 + 跳外部地图的按钮。

    页面是离线单文件，内嵌不了真地图。所以这里给三层：
      1. SVG 示意图——顺序与相对方位，离线可看；
      2. 逐日停靠点——每个名字都是可点的地图搜索链接；
      3. 两个跳转按钮——「看全部点位」和「规划整条路线」交给高德网页版，
         有网时点一下就得到真地图和真路径。
    只在停靠点带 `geo: {lat,lng}` 时才画图与给路线按钮；没有坐标就退化成
    纯链接列表，不留空壳。
    """
    rows_data, seq = _stops(it)
    if not rows_data:
        return ""
    rows, pts = [], []
    for day_no, (did, stops) in enumerate(rows_data, 1):
        nodes = []
        for name, n, lat, lng in stops:
            if lat is not None and lng is not None:
                pts.append((lng, lat, name, day_no, n))
            nodes.append(f'<a class="rnode" href="{e(_map_url(name))}" target="_blank" '
                         f'rel="noopener"><i>{n}</i>{e(name)}</a>')
        rows.append('<div class="rline"><b>' + e(did)
                    + '</b><div class="rstops">' + '<span class="rarrow">→</span>'.join(nodes)
                    + "</div></div>")

    head = ('<div class="rhead">动线一览<span class="rsub">'
            '按行程顺序串起来；标号与下方停靠点一致</span></div>')
    body = head + _routemap(pts, seq) + "".join(rows)

    # 跳外部地图：多点标注 + 路径规划。uri.amap.com 的 via 只支持一个途经点，
    # 所以路线链接最多体现三段，真正的完整顺序看上面的示意图与列表。
    links = []
    mk = nw.markers_url(pts)
    if mk:
        links.append('<a class="rbtn" href="' + e(mk) + '" target="_blank" '
                     'rel="noopener">在高德看全部点位</a>')
    rt = nw.route_url(pts)
    if rt:
        links.append('<a class="rbtn ghost" href="' + e(rt) + '" target="_blank" '
                     'rel="noopener">规划整条路线</a>')
    if links:
        tip = "示意图非导航路径，真实路况以地图为准"
        if len(pts) > nw.MARKER_LIMIT:
            tip += f"；「全部点位」只标前 {nw.MARKER_LIMIT} 个，其余请看上方示意图"
        foot = '<div class="rfoot">' + "".join(links) + "<span>" + tip + "</span></div>"
    else:
        foot = ('<div class="rfoot"><span>按行程顺序画的动线；'
                '点位名可点，点开就是地图搜索</span></div>')
    return '<div class="route">' + body + foot + "</div>"


def _days(it):
    days = _objs(it.get("days"))
    if not days:
        return ""
    out = [h2("days", "逐日行程")]
    flow = _flow(it)
    if flow:
        out.append(flow)
    anchors = []
    for idx, d in enumerate(days, 1):
        did = _day_anchor(d, idx)
        anchors.append('<a href="#' + did + '"><b>'
                       + e(d.get("day_id") or "D" + str(idx)) + "</b>"
                       + e(_short_date(d.get("date"))) + "</a>")
    out.append('<div class="daynav">' + "".join(anchors) + "</div>")
    for idx, d in enumerate(days, 1):
        did = _day_anchor(d, idx)
        items = [i for i in _objs(d.get("items")) if not _blank_item(i)]
        wk = f'（{e(d["weekday"])}）' if d.get("weekday") else ""
        out.append(
            '<details class="day" id="' + did + '" open><summary>'
            '<span class="dnum">' + e(d.get("day_id") or "D" + str(idx)) + "</span>"
            '<span class="dmeta"><em>' + e(_short_date(d.get("date"))) + wk + "</em>"
            + e(d.get("title") or "") + "</span>"
            '<span class="dcnt">' + str(len(items)) + " 项</span></summary>"
            '<div class="dbody"><div class="tl">'
        )
        for i in items:
            kind = i.get("kind") or "visit"
            tag = ('<span class="tag ' + KIND_CLASS.get(kind, "k-visit") + '">'
                   + e(KIND_LABEL.get(kind, "")) + "</span>")
            place = ('<span class="pl"> @ ' + _maplink(i["place"]) + "</span>"
                     if i.get("place") else "")
            st = i.get("booking_status")
            st_txt = {"confirmed": "已订", "not_required": "无需预约",
                      "todo": "待预约", "unavailable": "约不到"}.get(st)
            st_html = '<span class="pl">（' + e(st_txt) + "）</span>" if st_txt else ""
            # 要订的东西（门票/住宿/车票）挂上平台搜索入口：方案不写死价格，
            # 点进去看当天报价与余票。只认数据里显式给了 booking 的条目。
            book = i.get("booking") or {}
            chips = ""
            if isinstance(book, dict) and book.get("keyword"):
                chips = nw.link_chips(nw.booking_links(
                    book.get("keyword"), book.get("kind") or "ticket"))
            note = ('<div class="nt">' + e(i["notes"]) + "</div>") if i.get("notes") else ""
            meta = (("<div>" + place + st_html + "</div>") if (place or st_html) else "") + chips
            t_html = ('<div class="t">' + e(i.get("time")) + "</div>") if i.get("time") else ""
            out.append(
                '<div class="slot">' + t_html
                + '<div class="ti">' + e(i.get("title") or "") + tag + "</div>"
                + meta + note + "</div>"
            )
        out.append("</div>")
        for a in _objs(d.get("alternatives")):
            if a.get("title") or a.get("trigger"):
                out.append('<div class="alt"><b>备选</b> ' + e(a.get("title", ""))
                           + "｜触发条件：" + e(a.get("trigger") or "未写") + "</div>")
        out.append("</div></details>")
    return "".join(out)


def _food(it):
    food = [x for x in (it.get("food") or [])
            if isinstance(x, dict) and str(x.get("name") or "").strip()]
    if not food:
        return ""
    rows = ""
    for x in food:
        name = str(x.get("name", ""))
        kw = str(x.get("search") or name).strip()
        chips = nw.link_chips(nw.booking_links(kw, x.get("booking_kind") or "food"))
        rows += (f'<tr><td>{e(name)}{chips}</td>'
                 f'<td class="num">{e(x.get("price") or "未查到")}</td>'
                 f'<td>{e(x.get("why", ""))}</td><td>{e(x.get("note") or "—")}</td></tr>')
    return (h2("food", "美食推荐")
            + '<table><thead><tr><th>店/摊</th><th class="num">人均</th>'
            + '<th>为什么去</th><th>备注</th></tr></thead><tbody>' + rows + '</tbody></table>'
            + '<div class="exlog">人均是平台实时价，会变；点店名旁的入口看当天报价，'
              '本方案不写死价格。</div>')


def _budget(it, req):
    b = it.get("budget") if isinstance(it.get("budget"), dict) else {}
    cats = _objs(b.get("categories"))
    if not cats:
        return ""
    cur = b.get("currency", "CNY")
    # 先全部过 nw.num：预算条是按比例画宽的，一个 nan 就能让 left/width 变成 "nan%"，
    # 条子整根消失还看不出哪条坏；inf 更会让所有条挤成 0 宽。
    rows_data = [(c.get("name", ""), nw.num(c.get("min")), nw.num(c.get("max")))
                 for c in cats]
    caps = [hi for _n, _lo, hi in rows_data if hi is not None]
    top = max(caps) if caps else 1
    top = top or 1                        # 全是 0 时别拿 0 当分母
    lo_sum = sum(lo for _n, lo, _hi in rows_data if lo is not None)
    hi_sum = sum(hi for _n, _lo, hi in rows_data if hi is not None)
    rows = ""
    for name, lo, hi in rows_data:
        if lo is not None and hi is not None:
            left = lo / top * 100
            width = max((hi - lo) / top * 100, 1.5)
            bar = f'<div class="bar"><i style="left:{left:.1f}%;width:{width:.1f}%"></i></div>'
            amt = f"{lo:g} – {hi:g}"
        else:
            bar = ""
            amt = "未查到"
        rows += (f'<tr><td>{e(name)}{bar}</td>'
                 f'<td class="num">{e(amt)}</td></tr>')
    rows += (f'<tr><td class="total">合计（{e(cur)}）</td>'
             f'<td class="num total"><span class="hl">{lo_sum:g} – {hi_sum:g}</span></td></tr>')
    cap = nw.num(g(req, "budget", "amount_max"))
    alert = ""
    if cap is not None and hi_sum > cap:
        alert = (f'<div class="note">分项上限合计 {hi_sum:g}，比预算 {cap:g} 高 '
                 f'{hi_sum - cap:g}。要么压住宿和餐饮，要么把预算上调。</div>')
    return h2("budget", "预算明细") + f'<table><tbody>{rows}</tbody></table>{alert}'


def _checklist(it):
    cl = _objs(it.get("checklist"))
    if not cl:
        return ""
    # 状态是渲染时就定死的：数据里 status=done 才画「已办」标记。
    # 页面上不提供可点的勾选框——离线单文件改了也不落盘，点了等于白点，
    # 真正的状态管理走宿主的待办/提醒（见 SKILL.md S7）。
    done_n = 0
    items = ""
    for c in cl:
        done = c.get("status") == "done"
        done_n += 1 if done else 0
        prio = "必办" if c.get("priority") == "must" else "可选"
        dl = f'　截止 {e(c.get("deadline"))}' if c.get("deadline") else ""
        unres = (f'<div class="nt">没办成怎么办：{e(c["if_unresolved"])}</div>'
                 if c.get("if_unresolved") else "")
        mark = '<span class="mark mark-done" title="已办">✓</span>' if done \
            else '<span class="mark" title="未办"></span>'
        # 「买门票/订酒店/订车票」这类待办直接挂平台入口，用户不用自己再搜一遍。
        bk = c.get("booking") or {}
        chips = (nw.link_chips(nw.booking_links(bk.get("keyword"), bk.get("kind") or "ticket"))
                 if isinstance(bk, dict) and bk.get("keyword") else "")
        items += ('<div class="todo' + (" is-done" if done else "") + '">' + mark
                  + '<span class="txt"><span class="ti">【' + prio + "】"
                  + e(c.get("task", "")) + dl + "</span>" + chips + unres + "</span></div>")
    summary = (f'<div class="todo-sum">已办 {done_n} / {len(cl)}</div>'
               if len(cl) > 1 else "")
    return h2("todo", "出发前待办") + '<div class="card">' + summary + items + "</div>"


def _pitfalls(trip):
    ps = _objs(trip.get("pitfalls"))
    if not ps:
        return ""
    out = [h2("pitfalls", "踩坑指南")]
    for p in ps:
        level = {"high": "容易出事", "medium": "容易踩"}.get(p.get("level"), "留意")
        when = f'<div class="when">什么时候遇到：{e(p["when"])}</div>' if p.get("when") else ""
        how = f'<div class="when">怎么绕：{e(p["how"])}</div>' if p.get("how") else ""
        out.append(f'<div class="warn"><b>{e(p.get("title", ""))}</b>'
                   f'<span class="tag sev-minor">{e(level)}</span>'
                   f'<div>{e(p.get("detail", ""))}</div>{when}{how}</div>')
    return "".join(out)



def _sources(trip):
    facts = _objs(trip.get("facts"))
    sources = _objs(trip.get("sources"))
    if not facts and not sources:
        return ""
    smap = {s.get("source_id"): s for s in sources}
    out = [h2("sources", "依据索引"),
           '<div class="legend">方案里每条关键数据都能追到下面某一项。追不到的标了「估算」或「未查到」。</div>']
    if facts:
        rows = ""
        for f_ in facts:
            st = {"verified": "已核实", "estimated": "估算", "unverified": "未核实",
                  "unknown": "未查到"}.get(f_.get("status"), f_.get("status", ""))
            sids = f_.get("source_ids") or []
            names = "、".join((smap.get(sid, {}) or {}).get("name", sid) for sid in sids) or "—"
            dates = "、".join((smap.get(sid, {}) or {}).get("retrieved_at", "?") for sid in sids) or "—"
            rows += (f'<tr><td>{e(f_.get("fact_id", ""))}</td>'
                     f'<td>{e(f_.get("claim") or f_.get("conclusion") or "")}</td>'
                     f'<td>{e(st)}</td><td class="srcs">{e(names)}</td><td class="srcs">{e(dates)}</td></tr>')
        out.append('<table class="facts-tbl"><thead><tr><th>编号</th><th>结论</th><th>状态</th>'
                   f'<th>来源</th><th>取数日期</th></tr></thead><tbody>{rows}</tbody></table>')
    if sources:
        items = ""
        for s in sources:
            url = (' <a class="src-link" href="' + e(s["url"]) + '" target="_blank" '
                   'rel="noopener noreferrer">' + e(s["url"]) + "</a>") if s.get("url") else ""
            items += (f'<li><code>{e(s.get("source_id", ""))}</code> {e(s.get("name", ""))}'
                      f'（{e(s.get("kind") or "未标注类型")}，取数 {e(s.get("retrieved_at") or "?")}）{url}</li>')
        # 来源清单放进折叠里：要核对时点开，平时不占版面
        out.append(f'<details><summary>数据来源清单（{len(sources)} 项，点击展开）</summary>'
                   f'<div class="body srcs"><ul>{items}</ul>'
                   '<div class="legend">网址只作核对用，页面本身不联网加载。</div>'
                   '</div></details>')
    return "".join(out)


# 章节小图标：内联 SVG，1.9px 描边、圆头圆角，颜色跟随 currentColor，不引任何图标库
_ICON_SVG = {
    "glance": '<path d="M13 3 6.5 13H11l-1 8 7.5-10.5H12z"/>',
    "weather": '<path d="M7 18h9.5a4 4 0 0 0 .6-7.96A6 6 0 0 0 5.6 11.6 3.2 3.2 0 0 0 7 18z"/>',
    "nature": '<path d="M4 20c0-8 6-14 16-14 0 10-6 14-16 14z"/>'
              '<path d="M8.5 15.5c2-2.6 4.6-4.4 8-5.4"/>',
    "days": '<rect x="3.5" y="5" width="17" height="15.5" rx="3.5"/>'
            '<path d="M8 3.5v3M16 3.5v3M3.5 10.5h17"/>',
    "food": '<path d="M3.5 11h17a8.5 8.5 0 0 1-17 0z"/>'
            '<path d="M7.5 8.5V4M12 8.5V3.5M16.5 8.5V4"/>',
    "budget": '<rect x="3" y="6" width="18" height="13" rx="3.5"/>'
              '<path d="M3 10.5h18M16 14.5h1.5"/>',
    "todo": '<rect x="4" y="4" width="16" height="16" rx="4.5"/>'
            '<path d="M8.5 12.3l2.4 2.4 4.6-5.1"/>',
    "pitfalls": '<path d="M12 4.5 3.6 19h16.8L12 4.5z"/><path d="M12 10v4M12 16.6h.01"/>',
    "sources": '<path d="M10.2 13.4a4 4 0 0 0 5.6 0l2.6-2.6a4 4 0 0 0-5.6-5.6l-1.1 1.1"/>'
               '<path d="M13.8 10.6a4 4 0 0 0-5.6 0l-2.6 2.6a4 4 0 0 0 5.6 5.6l1.1-1.1"/>',
}


def icon(name):
    """章节标题前的小色牌图标。"""
    return ('<span class="ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
            'stroke-linejoin="round">' + _ICON_SVG.get(name, "") + "</svg></span>")


# 章节小贴图：内联 SVG，糖果色 + 1.8px 描边，尺寸由 CSS 的 .stk-* 控制。
# 全部本地绘制，不引图标库、不引远程资源。
_STK_HEAD = ('<svg viewBox="0 0 32 32" fill="none" stroke-width="1.8" '
             'stroke-linecap="round" stroke-linejoin="round">')
STICKER = {
    "sparkle": '<path d="M16 3l3.4 9.6L29 16l-9.6 3.4L16 29l-3.4-9.6L3 16l9.6-3.4z" '
               'fill="#ffe066" stroke="#e0a400"/>',
    "shell": '<path d="M4 25a12 12 0 0 1 24 0z" fill="#ffd9e6" stroke="#e5457a"/>'
             '<path d="M16 13v12M10 15l2.6 10M22 15l-2.6 10" stroke="#e5457a"/>',
    "bird": '<path d="M4 19c4-7 10-11 18-11 0 9-6 15-14 15H4z" fill="#bfe9dc" '
            'stroke="#12836b"/><path d="M10 19c3-2 6-2 9 0" stroke="#12836b"/>'
            '<circle cx="24.5" cy="12" r="1.7" fill="#12836b"/>'
            '<path d="M26.5 12.6l4 1.4-4 1.4z" fill="#ffb01a" stroke="#ffb01a"/>',
    "sun": '<circle cx="16" cy="16" r="7" fill="#ffe066" stroke="#ffb01a"/>'
           '<path d="M16 2.5v3M16 26.5v3M2.5 16h3M26.5 16h3M6.5 6.5l2 2M23.5 23.5l2 2'
           'M25.5 6.5l-2 2M8.5 23.5l-2 2" stroke="#ffb01a"/>',
    "moon": '<path d="M21.5 4a12 12 0 1 0 6.5 20.5A13 13 0 0 1 21.5 4z" fill="#ffe9f1" '
            'stroke="#b48cf2"/>',
    "cloud": '<path d="M9 24h13a5.5 5.5 0 0 0 .8-10.9A7.5 7.5 0 0 0 8 14.2 4.2 4.2 0 0 0 9 24z" '
             'fill="#e3f6fd" stroke="#00a1d6"/>',
    "heart": '<path d="M16 27S4 19.6 4 12.6A6.5 6.5 0 0 1 16 8.8 6.5 6.5 0 0 1 28 12.6'
             'C28 19.6 16 27 16 27z" fill="#ffd1e3" stroke="#e5457a"/>',
    "bow": '<path d="M15 16 4.5 9.6v12.8zM17 16l10.5-6.4v12.8z" fill="#ffd1e3" '
           'stroke="#e5457a"/><circle cx="16" cy="16" r="3.2" fill="#fff" stroke="#e5457a"/>',
    "camera": '<rect x="4" y="10" width="24" height="16" rx="4.5" fill="#e3f6fd" '
              'stroke="#00a1d6"/><path d="M12 10l2-3h4l2 3" fill="#cfeffb" stroke="#00a1d6"/>'
              '<circle cx="16" cy="18" r="4.6" fill="#fff" stroke="#00a1d6"/>',
    "pin": '<path d="M16 3a9 9 0 0 1 9 9c0 7-9 17-9 17S7 19 7 12a9 9 0 0 1 9-9z" '
           'fill="#ffe9f1" stroke="#e5457a"/><circle cx="16" cy="12" r="3.4" fill="#fff" '
           'stroke="#e5457a"/>',
    "cup": '<path d="M8.5 11h15l-2 16h-11z" fill="#fff4dc" stroke="#b06a00"/>'
           '<path d="M7 8h18" stroke="#b06a00"/><path d="M20.5 3.5L17 10" stroke="#e5457a"/>'
           '<circle cx="13" cy="18" r="1.7" fill="#b06a00"/>'
           '<circle cx="18.5" cy="22" r="1.7" fill="#b06a00"/>',
    "coin": '<circle cx="16" cy="16" r="11" fill="#ffe066" stroke="#e0a400"/>'
            '<path d="M16 9v14M20 12.5h-5.5a2.6 2.6 0 0 0 0 5.2h3a2.6 2.6 0 0 1 0 5.2H11" '
            'stroke="#b06a00"/>',
    "cone": '<path d="M16 4l9 22H7z" fill="#ffd1e3" stroke="#e5457a"/>'
            '<path d="M11.4 15h9.2M9.4 21h13.2" stroke="#e5457a"/>'
            '<rect x="4" y="25.5" width="24" height="4.5" rx="2.2" fill="#ff9f7a" '
            'stroke="#e5457a"/>',
    "flower": '<circle cx="16" cy="8.5" r="4.4" fill="#ffd1e3" stroke="#e5457a"/>'
              '<circle cx="16" cy="23.5" r="4.4" fill="#ffd1e3" stroke="#e5457a"/>'
              '<circle cx="8.5" cy="16" r="4.4" fill="#ffd1e3" stroke="#e5457a"/>'
              '<circle cx="23.5" cy="16" r="4.4" fill="#ffd1e3" stroke="#e5457a"/>'
              '<circle cx="16" cy="16" r="3.6" fill="#ffe066" stroke="#e0a400"/>',
    "leaf": '<path d="M5 27C5 15 13 7 27 7c0 12-8 20-20 20z" fill="#bfe9dc" stroke="#12836b"/>'
            '<path d="M10 22c3-4 7-7 12-9" stroke="#12836b"/>',
}
# 九个章节各配一枚，颜色和 .ico 的六色循环错开
SECTION_STICKER = {
    "glance": "heart", "weather": "cloud", "nature": "leaf", "days": "camera",
    "food": "cup", "budget": "coin", "todo": "sparkle", "pitfalls": "cone",
    "sources": "pin",
}
# 自然专项场景里贴的那一枚
NATURE_STICKER = {
    "birding": "bird", "stargazing": "sparkle", "sunrise": "cloud",
    "beachcombing": "shell", "glow": "flower",
}


def sticker_svg(name):
    """取一张小贴图的完整 SVG；名字不认识就退回四角星。"""
    return _STK_HEAD + STICKER.get(name, STICKER["sparkle"]) + "</svg>"


# 封面右上角飘着的那几张
COVER_STICKERS = "".join(
    '<i class="stk stk-s{0}" aria-hidden="true">{1}</i>'.format(i + 1, sticker_svg(n))
    for i, n in enumerate(("shell", "bird", "bow"))
)
# 封面自带的珠光星点
COVER_SPARKS = "".join('<i class="spark"></i>' for _ in range(5))
# 满页会闪的小星星（装饰层，负 z 层，压在所有内容下面）
_BLING = '<div class="bling" aria-hidden="true">' + "<i></i>" * 12 + "</div>"


def h2(key, text):
    """章节标题：图标色牌 + 标题 + 渐变分隔线 + 一枚小贴图（贴纸浮在最右）。"""
    stk = sticker_svg(SECTION_STICKER.get(key, "sparkle"))
    return ('<h2>' + icon(key) + e(text)
            + '<i class="stk" aria-hidden="true">' + stk + "</i></h2>")


# 章节锚点：导航、各节 id 共用这一份定义，避免两边对不上
SECTION_NAV = [
    ("sec-glance", "速览"),
    ("sec-weather", "天气"),
    ("sec-nature", "自然专项"),
    ("sec-days", "行程"),
    ("sec-food", "美食"),
    ("sec-budget", "预算"),
    ("sec-todo", "待办"),
    ("sec-pitfalls", "踩坑"),
    ("sec-sources", "依据"),
]


def _nav(present):
    """顶部目录：只给真实存在的章节链接。纯锚点跳转，不需要脚本。"""
    links = "".join(f'<a href="#{sid}">{e(label)}</a>'
                    for sid, label in SECTION_NAV if sid in present)
    return f'<nav class="toc" aria-label="章节导航">{links}</nav>'


def html_from_trip(trip: dict) -> str:
    req = _dict(trip.get("request"))
    it = _dict(trip.get("itinerary"))
    ver = trip.get("plan_version", 1)
    title = e(trip.get("title") or trip.get("trip_id", "旅行方案"))
    blocks = [
        ("sec-glance", _glance(trip)),
        ("sec-weather", _weather(it)),
        ("sec-nature", _nature(it)),
        ("sec-days", _days(it)),
        ("sec-food", _food(it)),
        ("sec-budget", _budget(it, req)),
        ("sec-todo", _checklist(it)),
        ("sec-pitfalls", _pitfalls(trip)),
        ("sec-sources", _sources(trip)),
    ]
    present = {sid for sid, html in blocks if html}
    sections = "".join(f'<section id="{sid}" class="sec">{html}</section>'
                       for sid, html in blocks if html)
    body = "".join([
        _cover(trip),
        '<div class="wrap">',
        _nav(present),
        sections,
        f'<footer>{e(ATTR_LINE)} · v{ver} · 离线单文件，可直接打印</footer>',
        '</div>',
        '<a class="totop" href="#top" aria-label="回到顶部">↑</a>',
        _BLING,
    ])
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{title} · 旅行方案</title>\n<style>{load_css()}</style>\n</head>\n"
            f"<body id=\"top\">{body}</body>\n</html>\n")




def write_outputs(trip_path: str, out_dir: str | None = None,
                  force: bool = False, skip_structure_gate: bool = False) -> dict:
    """渲染 MD + HTML。

    skip_structure_gate=True 供 `build_trip.py render` 使用：那条路已经校验过一次，
    同一份数据、同一套规则再校验一遍纯属重复收费（校验本身不慢，但每次失败都要
    多跑一遍全量扫描）。只有一条路会真正开门：
      · build_trip.py render → 自己先 validate，再 skip 渲染器这道
      · render_plan.py 直接跑 → 走这里的结构门
    """
    trip, _label, src_dir, origin = read_trip_source(trip_path, with_origin=True)
    if not skip_structure_gate:
        problems = structure_problems(trip, origin)
        if problems and not force:
            more = ("\n  ... 其余 " + str(len(problems) - 12) + " 项省略"
                    if len(problems) > 12 else "")
            raise TripDataError(
                "结构校验未通过，拒绝渲染（" + str(len(problems)) + " 项）：\n  - "
                + "\n  - ".join(problems[:12]) + more
                + "\n  按每项后面标注的分片文件改；确认要出图再加 --force")
    base = out_dir or src_dir
    os.makedirs(base, exist_ok=True)
    tid = trip.get("trip_id") or "travel-plan"
    ver = trip.get("plan_version", 1)
    md_path = os.path.join(base, f"{tid}_v{ver}.md")
    html_path = os.path.join(base, f"{tid}_v{ver}.html")
    # 两份都在内存里算完再落盘：中途抛异常时不会留下"MD 是新的、HTML 是旧的"
    # 这种半新半旧的产物——读者分不出哪份对，比一份都没有更糟。
    md_text = md_from_trip(trip)
    html_text = html_from_trip(trip)
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(md_text)
    with open(html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html_text)
    return {"trip_id": tid, "plan_version": ver, "md": md_path, "html": html_path,
            "content_hash": canonical_hash(trip)}


def check_outputs(trip_path: str, out_dir: str | None = None) -> dict:
    trip, _label, src_dir = read_trip_source(trip_path)
    base = out_dir or src_dir
    tid = trip.get("trip_id") or "travel-plan"
    ver = trip.get("plan_version", 1)
    md_path = os.path.join(base, f"{tid}_v{ver}.md")
    html_path = os.path.join(base, f"{tid}_v{ver}.html")
    problems = []
    for p in (md_path, html_path):
        if not os.path.exists(p):
            problems.append(f"缺失产物: {p}")
    if not problems:
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
        with open(html_path, encoding="utf-8") as f:
            htm = f.read()
        if ATTR_LINE not in md or ATTR_LINE not in htm:
            problems.append("产物缺少署名")
        # 离线校验：禁脚本、禁远程资源加载。
        # 正文里出现来源网址是正常的，不算远程依赖；
        # 样式注释里提到关键字也不算，所以先去掉注释再扫。
        low = _strip_css_comments(htm).lower()
        if "<script" in low:
            problems.append("HTML 含脚本（应为纯离线单文件）")
        for pat, label in (('src="http', "远程 src"), ("src='http", "远程 src"),
                           ("<link ", "外链 <link>"), ("@import", "CSS @import"),
                           ("url(http", "远程 url()"), ("url('http", "远程 url()")):
            if pat in low:
                problems.append(f"HTML 引用了远程资源（{label}）")
        if "http-equiv" in low and "refresh" in low:
            problems.append("HTML 含自动跳转")
        # 数据完整性：方案里的关键数字应能在依据索引里找到
        blob = md
        for probe, label in ((g(trip, "itinerary", "nature", "sunrise", "sunrise"), "日出时刻"),
                             (g(trip, "itinerary", "nature", "stargazing", "moon_phase"), "月相")):
            if probe and str(probe) not in blob:
                problems.append(f"{label} {probe} 未出现在产物中")
    return {"ok": not problems, "problems": problems, "files": [md_path, html_path]}


def main(argv=None) -> int:
    configure_console()
    ap = argparse.ArgumentParser(description="travel-orchestrator 交付渲染器")
    ap.add_argument("trip", help="方案目录（读其中的 parts/）或 trip.json 路径")
    ap.add_argument("--out-dir", help="输出目录（默认与数据同目录）")
    ap.add_argument("--check", action="store_true", help="只检查产物一致性")
    ap.add_argument("--force", action="store_true",
                    help="结构校验不通过也渲染（只用于排障，不推荐）")
    args = ap.parse_args(argv)
    try:
        if args.check:
            r = check_outputs(args.trip, args.out_dir)
            print("检查结果:", "通过" if r["ok"] else "未通过")
            for p in r["problems"]:
                print("  x", p)
            return 0 if r["ok"] else 1
        r = write_outputs(args.trip, args.out_dir, force=args.force)
        print(f"ok 已生成 v{r['plan_version']}:")
        print(f"  MD  : {r['md']}")
        print(f"  HTML: {r['html']}")
        return 0
    except TripDataError as exc:            # 结构门：退出码 3，与读取失败区分开
        print(f"x {exc}", file=sys.stderr)
        return 3
    except AttributeError as exc:
        # 坏数据的最后一层网：元素写成字符串之类的问题本该被结构门拦住，
        # 万一漏到这儿，也要给一句人话 + 退出码，而不是甩一段 traceback。
        print(f"x 数据里有字段类型不对，渲染中断：{exc}\n"
              "  先用 python scripts/build_trip.py validate <方案目录> 定位到具体分片",
              file=sys.stderr)
        return 4
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"x {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
