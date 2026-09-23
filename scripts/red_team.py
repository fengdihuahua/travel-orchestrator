#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
red_team.py · travel-orchestrator 红方复核器（对抗式挑刺 + 反幻觉溯源）

交付之前，先让一个不客气的质疑者把方案过一遍：日期对不对、时间排不排得开、
钱够不够、潮汐会不会把人困在滩涂上、每条结论有没有出处。
挑出来的问题按 severity 分级，blocking 没清零就不许交付。

用法：
  python red_team.py check <trip.json>
  python red_team.py check <trip.json> --json
  python red_team.py rules

退出码：0 = 无 blocking；1 = 存在 blocking；2 = 用法/数据错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

VERSION = "1.2.0"

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 各种星期写法 -> 序号（周一=0 … 周日=6）
_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
                  "日": 6, "天": 6, "七": 6}
_WEEKDAY_RE = re.compile(r"(?:周|週|星期|礼拜|禮拜)\s*([一二三四五六日天七])")


def weekday_index(text):
    """从「周五」「星期五（中秋节）」「礼拜五」等写法里解析出 0-6；解析不出返回 None。"""
    if not text:
        return None
    m = _WEEKDAY_RE.search(str(text))
    if not m:
        return None
    return _WEEKDAY_CHARS.get(m.group(1))

# 时效性数据的来源超过这个天数就报警（潮汐/日出/月相/天气都会变）
STALE_DAYS = 30
# 视为"外部实时数据"的来源类型
REALTIME_KINDS = ("api", "official_site", "web_search")

# 自然专项 -> 溯源关键词（用于判断某项数据是否在 facts 里出现过）
NATURE_KEYS = {
    "birding": ("鸟", "bird"),
    "stargazing": ("月相", "月亮", "月照", "照度", "观星", "银河", "流星", "行星", "moon"),
    "sunrise": ("日出", "日落", "黄金时刻", "蓝调", "sun"),
    "beachcombing": ("潮", "tide"),
    "glow": ("霞", "火烧云", "glow"),
}

# 全部规则清单（人读用）
RULES = [
    ("RT-S01", "minor", "结构", "日期与标注的星期不符"),
    ("RT-S02", "blocking", "结构", "item_id 重复"),
    ("RT-S03", "major", "结构", "同一天行程时间窗相互重叠"),
    ("RT-S04", "major", "结构", "时间窗无效（结束早于开始或跨日）"),
    ("RT-S05", "major", "结构", "预算分项 min > max"),
    ("RT-S06", "major", "预算", "预算合计上限超出用户给的预算"),
    ("RT-S07", "blocking", "结构", "缺少必填字段（日期/同行人）"),
    ("RT-S08", "minor", "结构", "天气条目数与行程天数不一致"),
    ("RT-F01", "major", "溯源", "结论标了 verified 却没有来源"),
    ("RT-F02", "major", "溯源", "结论引用了不存在的来源 ID"),
    ("RT-F03", "minor", "溯源", "来源缺少名称或取回日期"),
    ("RT-F04", "major", "溯源", "实时数据的来源已过期（超 30 天）"),
    ("RT-F05", "major", "溯源", "值为 unknown 却写了具体数值"),
    ("RT-F06", "minor", "溯源", "自然专项数据没有对应结论可溯源"),
    ("RT-L01", "blocking", "安全", "赶海窗口与干潮时刻对不上"),
    ("RT-L02", "major", "逻辑", "日出/日落活动时刻与天文时刻矛盾"),
    ("RT-L03", "major", "逻辑", "满月夜安排了观星且未提示"),
    ("RT-L04", "blocking", "安全", "赶海窗口落在涨潮段"),
    ("RT-L05", "minor", "逻辑", "凌晨专项与次日清晨专项间隔不足 5 小时"),
    ("RT-L06", "minor", "逻辑", "主要景点没有雨天/满员备选"),
    ("RT-L07", "minor", "结构", "方案标了 conditional 但没写清条件"),
    ("RT-L08", "minor", "逻辑", "踩坑指南为空"),
    ("RT-X01", "info", "复核", "未执行红方复核（缺 red_team 字段）"),
]


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# ------------------------------------------------------------------ 小工具
def parse_hm(s):
    """'06:30' -> 390（分钟）。不合法返回 None。"""
    if not isinstance(s, str):
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def parse_range(s):
    """'06:00-08:00' -> (360, 480)。支持 '15:00' 单点 -> (900, 900)。"""
    if not isinstance(s, str):
        return None
    parts = re.split(r"\s*[-~—至]\s*", s.strip())
    if len(parts) == 1:
        t = parse_hm(parts[0])
        return (t, t) if t is not None else None
    if len(parts) == 2:
        a, b = parse_hm(parts[0]), parse_hm(parts[1])
        if a is not None and b is not None:
            return (a, b)
    return None


def hm(mins):
    return "%02d:%02d" % (mins // 60, mins % 60)


def as_num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def fact_text(f):
    """结论正文。兼容 claim / conclusion 两种写法。"""
    return str(f.get("claim") or f.get("conclusion") or "")


def find_time_in_text(text, keyword):
    """从文本里抓关键字附近的时刻，两种语序都认。

    '10/1 干潮 09:12（0.4m）' 和 '09:12 干潮（0.4m）' 都能取到 09:12。
    """
    if not isinstance(text, str):
        return None
    # 语序一：关键字在前，时刻在后
    m = re.search(re.escape(keyword) + r"[^0-9]{0,8}(\d{1,2}):(\d{2})", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    # 语序二：时刻在前，关键字在后
    m = re.search(r"(\d{1,2}):(\d{2})[^0-9]{0,8}" + re.escape(keyword), text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


class Report:
    def __init__(self, trip_id):
        self.trip_id = trip_id
        self.findings = []

    def add(self, rule_id, severity, category, target, message, suggestion):
        self.findings.append({
            "rule_id": rule_id, "severity": severity, "category": category,
            "target": target, "message": message, "suggestion": suggestion,
        })

    def count(self, sev):
        return sum(1 for f in self.findings if f["severity"] == sev)

    def verdict(self):
        if self.count("blocking"):
            return "需修改"
        if self.count("major"):
            return "有条件通过"
        return "通过"


# ------------------------------------------------------------------ 结构层
def check_structure(trip, rep):
    req = trip.get("request") or {}
    it = trip.get("itinerary") or {}
    days = it.get("days") or []

    # RT-S07 必填字段
    dates = req.get("dates") or {}
    if not dates.get("start") or not dates.get("end"):
        rep.add("RT-S07", "blocking", "结构", "request.dates",
                "出发或返程日期缺失，方案没有可执行的时间锚点。",
                "补齐 dates.start / dates.end 后再交付。")
    if not req.get("companions"):
        rep.add("RT-S07", "blocking", "结构", "request.companions",
                "同行人结构缺失，直接决定行程节奏与预算口径。",
                "补齐同行人（含是否带娃/老人）。")

    seen_item = {}
    for d in days:
        day_id = d.get("day_id") or "?"
        date = d.get("date")

        # RT-S01 星期核对
        if date:
            try:
                real = WEEKDAY_CN[datetime.strptime(date, "%Y-%m-%d").weekday()]
            except ValueError:
                rep.add("RT-S01", "minor", "结构", f"{day_id} {date}",
                        "日期格式无法解析，无法核对星期。",
                        "统一用 YYYY-MM-DD。")
                real = None
            stated = d.get("weekday")
            # 按"星期序号"比较，而不是比字符串。
            # 之前写成 `stated not in real`：只要标签带了后缀（如「周五（中秋节）」）
            # 就一定不相等，属于方向写反导致的稳定误报；
            # 而且「星期五」「礼拜五」等写法同样会被误判。这里统一解析成 0-6 再比。
            si = weekday_index(stated)
            if real and stated and si is not None and si != datetime.strptime(
                    date, "%Y-%m-%d").weekday():
                rep.add("RT-S01", "minor", "结构", f"{day_id} {date}",
                        f"标注星期为 {stated}，实际是 {real}。",
                        f"改成 {real}（可保留备注，如「{real}（中秋节）」）。")

        # 收集时间窗
        spans = []
        for i in d.get("items") or []:
            iid = i.get("item_id")
            # RT-S02 ID 重复
            if iid:
                if iid in seen_item:
                    rep.add("RT-S02", "blocking", "结构", iid,
                            f"item_id 与 {seen_item[iid]} 重复，版本比对会错乱。",
                            "改成全局唯一 ID。")
                else:
                    seen_item[iid] = f"{day_id} {i.get('title') or ''}".strip()
            rng = parse_range(i.get("time"))
            label = f"{day_id} {i.get('time') or '?'} {i.get('title') or ''}"
            # RT-S04 时间窗无效
            if i.get("time") and rng is None:
                rep.add("RT-S04", "major", "结构", label,
                        f"时间写法无法解析：{i.get('time')!r}。",
                        "用 'HH:MM-HH:MM' 或单点 'HH:MM'。")
            elif rng and rng[1] < rng[0]:
                rep.add("RT-S04", "major", "结构", label,
                        f"结束时间 {hm(rng[1])} 早于开始时间 {hm(rng[0])}。",
                        "核对是否应为跨零点或写反了。")
            elif rng:
                spans.append((rng[0], rng[1], i.get("title") or iid or "?"))

        # RT-S03 时间重叠
        spans.sort()
        for a, b in zip(spans, spans[1:]):
            if b[0] < a[1]:
                rep.add("RT-S03", "major", "结构", f"{day_id} {hm(b[0])}",
                        f"「{a[2]}」({hm(a[0])}-{hm(a[1])}) 与「{b[2]}」({hm(b[0])}-{hm(b[1])}) 时间重叠。",
                        "错开时段，或合并为同一项。")

    # RT-S08 天气条目 vs 天数
    weather = it.get("weather") or {}
    entries = weather.get("entries") or []
    if weather.get("kind") == "forecast" and days and len(entries) != len(days):
        rep.add("RT-S08", "minor", "结构", "itinerary.weather",
                f"逐日天气预报 {len(entries)} 条，行程 {len(days)} 天，对不上。",
                "补齐每日预报，或改为 seasonal 只给气候特征。")

    # RT-L06 备选
    for d in days:
        alts = d.get("alternatives") or []
        if not alts:
            rep.add("RT-L06", "minor", "逻辑", d.get("day_id") or "?",
                    "这一天没有备选方案，遇到下雨或景点满员只能临时抓瞎。",
                    "至少给 1 条带触发条件的替代路线。")

    # RT-L07 conditional 未说明
    if trip.get("status") == "conditional":
        note = (trip.get("status_note") or "").strip()
        has_gate = bool((it.get("checklist") or [])) and any(
            c.get("if_unresolved") for c in (it.get("checklist") or []))
        if not note and not has_gate:
            rep.add("RT-L07", "minor", "结构", "status",
                    "标了 conditional，但没说清在等什么条件成立。",
                    "写明待确认项（如车票/门票）与对应的兜底做法。")


# ------------------------------------------------------------------ 预算层
def check_budget(trip, rep):
    req = trip.get("request") or {}
    budget_it = (trip.get("itinerary") or {}).get("budget") or {}
    cats = budget_it.get("categories") or []
    total_max = 0.0
    total_min = 0.0
    for c in cats:
        lo, hi = as_num(c.get("min")), as_num(c.get("max"))
        name = c.get("name") or "?"
        if lo is not None and hi is not None and lo > hi:
            rep.add("RT-S05", "major", "结构", f"预算/{name}",
                    f"下限 {lo:g} 大于上限 {hi:g}。",
                    "调换数值。")
        if lo is not None:
            total_min += lo
        if hi is not None:
            total_max += hi

    cap = as_num((req.get("budget") or {}).get("amount_max"))
    cur = (req.get("budget") or {}).get("currency", "CNY")
    if cap and total_max > cap:
        over = total_max - cap
        rep.add("RT-S06", "major", "预算", "预算合计",
                f"分项上限合计 {total_max:g} {cur}，超出用户预算上限 {cap:g} {cur}（超 {over:g}）。",
                "压住宿/餐饮区间，或向用户说明需提高预算，别默认超支。")


# ------------------------------------------------------------------ 溯源层
def check_provenance(trip, rep):
    facts = trip.get("facts") or []
    sources = trip.get("sources") or []
    src_ids = {s.get("source_id") for s in sources if s.get("source_id")}
    src_map = {s.get("source_id"): s for s in sources if s.get("source_id")}

    for s in sources:
        if not s.get("name") or not s.get("retrieved_at"):
            rep.add("RT-F03", "minor", "溯源", f"来源 {s.get('source_id') or '?'}",
                    "来源缺少名称或取回日期，用户无法判断可信度与时效。",
                    "补 name 与 retrieved_at。")

    today = datetime.now().date()
    for f in facts:
        fid = f.get("fact_id") or fact_text(f) or "?"
        status = f.get("status")
        sids = f.get("source_ids") or []
        label = (fact_text(f) or str(f.get("value") or fid))[:40]

        if status == "verified" and not sids:
            rep.add("RT-F01", "major", "溯源", f"结论 {fid}",
                    f"标为已验证但没有来源：{label}。",
                    "补 source_ids，或把状态降为 estimated/unverified。")

        for sid in sids:
            if sid not in src_ids:
                rep.add("RT-F02", "major", "溯源", f"结论 {fid}",
                        f"引用了不存在的来源 {sid!r}。",
                        "补上该来源，或修正 ID。")

        # 时效性检查
        for sid in sids:
            s = src_map.get(sid) or {}
            kind = (s.get("kind") or "").lower()
            ra = s.get("retrieved_at")
            if kind in REALTIME_KINDS and ra:
                try:
                    age = (today - datetime.strptime(ra[:10], "%Y-%m-%d").date()).days
                except ValueError:
                    continue
                if age > STALE_DAYS:
                    rep.add("RT-F04", "major", "溯源", f"结论 {fid}",
                            f"依据的 {s.get('name') or sid} 是 {age} 天前取的，实时数据早已过期。",
                            "重新取数后再交付，或在方案里标注已过期。")

        # 未知值不能写成数字
        val = f.get("value")
        if status == "unknown" and val not in (None, "", "unknown", "null"):
            rep.add("RT-F05", "major", "溯源", f"结论 {fid}",
                    f"状态是 unknown，却写了具体值 {val!r}。",
                    "未知就是未知，值留空，别用 0 或估计值冒充。")

    # RT-F06 自然专项有没有溯源
    nature = (trip.get("itinerary") or {}).get("nature") or {}
    fact_blob = " ".join(fact_text(f) + " " + str(f.get("value") or "") for f in facts)
    for key, kws in NATURE_KEYS.items():
        sec = nature.get(key)
        if not sec:
            continue
        if isinstance(sec, dict) and not any(sec.values()):
            continue
        if not any(kw in fact_blob for kw in kws):
            rep.add("RT-F06", "minor", "溯源", f"自然专项/{key}",
                    "该专项给了具体数据，但结论表里找不到对应的可溯源条目。",
                    "把这条数据的来源补进 facts/sources（数据源 + 日期）。")

    # RT-X01 复核记录
    rt = trip.get("red_team")
    if not rt or not (rt.get("rounds") or rt.get("findings")):
        rep.add("RT-X01", "info", "复核", "red_team",
                "还没有红方复核记录。",
                "跑一遍质疑清单，把结论写进 red_team（哪怕结论是'无问题'）。")


# ------------------------------------------------------------------ 逻辑/安全层
def check_logic(trip, rep):
    it = trip.get("itinerary") or {}
    nature = it.get("nature") or {}
    days = it.get("days") or []

    tide = nature.get("beachcombing") or {}
    if tide:
        low = find_time_in_text(tide.get("tide_summary") or "", "干潮")
        rng = parse_range(tide.get("best_window"))
        target = "自然专项/赶海"
        if low is not None and rng:
            if not (rng[0] <= low <= rng[1]):
                rep.add("RT-L01", "blocking", "安全", target,
                        f"推荐窗口 {hm(rng[0])}-{hm(rng[1])} 没盖住干潮时刻 {hm(low)}，"
                        "赶海时可能已经在水里了。",
                        "窗口按干潮前后各 1.5 小时重算。")
            elif rng[1] > low + 96:  # 干潮后超过约 1.6 小时
                rep.add("RT-L04", "blocking", "安全", target,
                        f"窗口拖到 {hm(rng[1])}，已过干潮 {low and hm(low)} 约 "
                        f"{(rng[1]-low)/60:.1f} 小时，滩涂开始回水。",
                        f"窗口收在 {hm(low + 90)} 之前。")
        elif low is None:
            rep.add("RT-L01", "blocking", "安全", target,
                    "给了赶海窗口，但潮汐摘要里没有干潮时刻，窗口无据可依。",
                    "补干潮时刻（含数据源与日期）。")

    # 日出活动 vs 天文时刻
    sun = nature.get("sunrise") or {}
    sr = find_time_in_text(sun.get("sunrise") or "", "日出") or parse_hm(sun.get("sunrise"))
    ss = find_time_in_text(sun.get("sunset") or "", "日落") or parse_hm(sun.get("sunset"))
    for d in days:
        for i in d.get("items") or []:
            title = i.get("title") or ""
            rng = parse_range(i.get("time"))
            if not rng:
                continue
            if "日出" in title and sr is not None:
                if not (rng[0] - 60 <= sr <= rng[1] + 60):
                    rep.add("RT-L02", "major", "逻辑",
                            f"{d.get('day_id')} {title}",
                            f"日出活动排在 {hm(rng[0])}-{hm(rng[1])}，而当天日出是 {hm(sr)}，对不上。",
                            "把活动窗对准日出前后各 1 小时。")
            if "日落" in title and ss is not None:
                if not (rng[0] - 60 <= ss <= rng[1] + 60):
                    rep.add("RT-L02", "major", "逻辑",
                            f"{d.get('day_id')} {title}",
                            f"日落活动排在 {hm(rng[0])}-{hm(rng[1])}，而当天日落是 {hm(ss)}，对不上。",
                            "把活动窗对准日落前后各 1 小时。")

    # 满月夜观星：除非方案已明确改为月面/亮星团等抗月光目标
    star = nature.get("stargazing") or {}
    if star:
        m = re.search(r"(\d{1,3})\s*%", str(star.get("moon_phase") or ""))
        illum = int(m.group(1)) if m else None
        if illum is not None and illum > 50:
            star_items = [i for d in days for i in (d.get("items") or [])
                          if "观星" in (i.get("title") or "")]
            if star_items:
                # 判定"是否知情"要同时看两处：
                #   1) 当日观星条目的标题与备注
                #   2) 自然专项 stargazing 整块的文字（很多方案把"满月改看月面"
                #      写在专项说明里，只在条目里找会漏判 → 稳定误报）
                haystack = json.dumps(star, ensure_ascii=False)
                for i in star_items:
                    haystack += " " + (i.get("title") or "") + " " + (i.get("notes") or "")
                aware = bool(re.search(
                    r"月面|亮星团|月落|月光干扰|不推荐深空|只看月|银河不可见|不仅看银河",
                    haystack))
                if not aware:
                    rep.add("RT-L03", "major", "逻辑", "自然专项/观星",
                            f"月亮照度 {illum}%，这种夜晚银河和深空基本看不到，方案仍照排观星。",
                            "明确改成看月面/亮星团，或换到月落之后的时段。")

    # 凌晨与清晨冲突：观测结束 -> 次日日出活动开始
    star_end = None
    for d in days:
        for i in d.get("items") or []:
            if "观星" in (i.get("title") or ""):
                r = parse_range(i.get("time"))
                if r:
                    star_end = max(star_end or 0, r[1])
    sun_start = None
    for d in days:
        for i in d.get("items") or []:
            if "日出" in (i.get("title") or ""):
                r = parse_range(i.get("time"))
                if r:
                    sun_start = min(sun_start if sun_start is not None else r[0], r[0])
    if star_end is not None and sun_start is not None and sun_start < star_end:
        gap_h = ((24 * 60 - star_end) + sun_start) / 60.0
    elif star_end is not None and sun_start is not None:
        gap_h = (sun_start - star_end) / 60.0
    else:
        gap_h = None
    if gap_h is not None and gap_h < 5:
        rep.add("RT-L05", "minor", "逻辑", "行程强度",
                f"前一夜观星到 {hm(star_end)}，次日 {hm(sun_start)} 又要拍日出，"
                f"中间只剩 {gap_h:.1f} 小时，含路上和洗漱基本睡不了。",
                "二选一，或把日出改到行程后段。")

    # 踩坑指南
    if not (trip.get("pitfalls") or []):
        rep.add("RT-L08", "minor", "逻辑", "pitfalls",
                "踩坑指南是空的。",
                "把调研中发现的陷阱、管制、高峰时段写进去。")


# ------------------------------------------------------------------ 主流程
def run(trip):
    rep = Report(trip.get("trip_id") or "travel-plan")
    check_structure(trip, rep)
    check_budget(trip, rep)
    check_provenance(trip, rep)
    check_logic(trip, rep)
    order = {"blocking": 0, "major": 1, "minor": 2, "info": 3}
    rep.findings.sort(key=lambda f: (order.get(f["severity"], 9), f["rule_id"]))
    return {
        "trip_id": rep.trip_id,
        "red_team_version": VERSION,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": {s: rep.count(s) for s in ("blocking", "major", "minor", "info")},
        "verdict": rep.verdict(),
        "findings": rep.findings,
    }


def print_report(r):
    icon = {"blocking": "[阻断]", "major": "[严重]", "minor": "[提醒]", "info": "[说明]"}
    print(f"红方复核 · {r['trip_id']} · 结论：{r['verdict']}")
    s = r["summary"]
    print(f"阻断 {s['blocking']} / 严重 {s['major']} / 提醒 {s['minor']} / 说明 {s['info']}")
    print("-" * 62)
    if not r["findings"]:
        print("没挑出问题。")
        return
    for f in r["findings"]:
        print(f"{icon.get(f['severity'], '')} {f['rule_id']} {f['target']}")
        print(f"      {f['message']}")
        print(f"      建议：{f['suggestion']}")


def _load_source(path):
    """取数：目录 → 就地合并 parts/（与渲染器同一份数据）；文件 → 读该文件。

    返回 (行程字典, 分片数)。
    """
    try:
        import build_trip as bt
    except ImportError:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), 0
    trip, _label, n = bt.resolve_trip(path, quiet=True)
    return trip, n


def main(argv=None):
    configure_console()
    ap = argparse.ArgumentParser(description="travel-orchestrator 红方复核器")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="复核一份方案（目录读 parts/，也可传 trip.json）")
    c.add_argument("trip")
    c.add_argument("--json", action="store_true", help="输出 JSON")
    sub.add_parser("rules", help="列出全部规则")
    args = ap.parse_args(argv)

    if args.cmd == "rules":
        for rid, sev, cat, desc in RULES:
            print(f"{rid}  {sev:<8} {cat}  {desc}")
        return 0
    if args.cmd != "check":
        ap.print_help()
        return 2

    if not os.path.exists(args.trip):
        print(f"x 找不到：{args.trip}（传方案目录或 trip.json 路径）", file=sys.stderr)
        return 2
    try:
        trip, n_parts = _load_source(args.trip)
    except SystemExit:
        return 2
    except (OSError, ValueError) as e:
        print(f"x 读取失败：{e}", file=sys.stderr)
        return 2
    if n_parts:
        print(f"数据来源：parts/（{n_parts} 个分片）")

    r = run(trip)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print_report(r)
    return 1 if r["summary"]["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
