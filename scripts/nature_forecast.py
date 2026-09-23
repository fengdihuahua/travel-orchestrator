#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nature_forecast.py — 自然观察专项实时数据查询脚本 (v2.3.0)
归属 skill: travel-orchestrator
用途: 为观鸟 / 观星 / 日出日落 / 赶海(潮汐) / 火烧云 提供实时数据查询。

v2.3.0 重名地名告警:
  - 地理编码改用 count=5，同名地点跨省时打印 [注意] 并写进输出，取数仍用第一条。
    中文重名地名极多（大同 → 山西 / 黑龙江，日出差 46 分钟且脚本不报错），
    涉及日出日落/月相/霞光这类时刻敏感的数据应改用 --lat/--lng。
    详见 references/nature-apis.md §6.2。

v2.1.0 观鸟接口改为免注册:
  - 观鸟：原 eBird 需免费 Token 注册，对"装完即用"的 skill 体验不友好。
    现默认走 iNaturalist API（免 Key）：真实观测记录 + 当季鸟种名录 + 简体中文名
    + 逐月物候直方图；GBIF 作为备用数据源；eBird 降级为可选增强（有 Token 时更全）。
  - 新增 birdinfo：物种卡（简中名 / 学名 / 科属 / 保护等级 / 简介 / 本地最佳月份）
  - 新增 birdtime：观鸟黄金时段（日出日落 + 风/降水/能见度约束，本地计算）
  - birdspot 改为免 Key：按真实记录的记录点数排序热点地点
  - 无法免 Key 的能力诚实标注：鸟鸣识别（Xeno-canto 需 Key、Macaulay 有反爬）
    只给人工可点的参考链接，不伪造识别结果

v2.0.0 接口修复:
  - 潮汐：cnss.com.cn tideJson 已失效(404) -> 改用 Open-Meteo Marine API
    (sea_level_height_msl)，逐小时潮位曲线 + 抛物线插值求满潮/干潮时刻，附带赶海窗口。
  - 火烧云：sunsetbot.top 域名已下线(nginx 404 / SSL 525) -> 改用 Open-Meteo 自建评分模型
    (云量分层 + 能见度 + 湿度 + 气溶胶光学厚度)，输出 0-3 级霞光评分与理由。
  - 新增城市名解析(Open-Meteo Geocoding)，并加白名单校验，修复 --city 参数注入风险。
  - 新增 --selftest：一键检测所有接口可用性。
原则: 优先免费无 Key 接口; 失败自动降级并给出提示; 所有输出标注数据源与日期。

用法示例:
  python nature_forecast.py tide  --city 威海 --date 2026-10-01
  python nature_forecast.py sun   --lat 37.5 --lng 122.1 --date 2026-10-01
  python nature_forecast.py astro --lat 29.6 --lng 106.5
  python nature_forecast.py glow  --city 威海 --event set
  python nature_forecast.py bird      --city 威海
  python nature_forecast.py species   --city 威海 --month 10
  python nature_forecast.py birdinfo  --name 丹顶鹤
  python nature_forecast.py birdtime  --city 威海 --date 2026-10-02
  python nature_forecast.py birdspot  --city 威海
  python nature_forecast.py bird      --region CN-37 --token YOUR_EBIRD_TOKEN   # 可选增强
  python nature_forecast.py selftest
"""

import argparse
import calendar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

VERSION = "2.3.0"
UA = "travel-orchestrator/" + VERSION
QUERY_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TZ_CN = "Asia/Shanghai"

# 请求护栏：urllib 的 timeout 只管单次 socket 操作，不管"总共下载了多久"。
# 实测（2026-09-22）iNaturalist 的 /observations 端点：
#   半径 25km/30 天 -> 2.9s/132KB；半径 60km/30 天 -> 7.9s/405KB；
#   半径 25km/90 天 -> 10.1s/368KB；半径≥25km 且回溯 ≥180 天 -> 响应涨到 1.3MB+
#   并被服务端缓慢滴流，超过 70 秒仍未传完（表现为"命令卡死"）。
# 结论：观鸟查询一律用短窗口 + 小 per_page；总时限兜底，宁可快速失败也不静默卡住。
HTTP_TIMEOUT = 25              # 单次 socket 操作超时
HTTP_DEADLINE = 45             # 单次请求总时限（秒）
HTTP_MAX_BYTES = 4 * 1024 * 1024   # 响应体积上限
# 观鸟记录类查询的回溯上限（超过此值时 iNat 响应会明显变慢）
OBS_MAX_DAYS = 90

# iNaturalist：Aves（鸟纲）的 taxon id，用于限定检索范围为鸟类
AVES_TAXON_ID = 3
# GBIF：Aves 的 taxonKey（备用数据源）
GBIF_AVES_KEY = 212

# 城市名白名单：中英文、数字、空格、连字符、点、撇号；长度另限
SAFE_NAME = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9 \-'.]+$")


# ---------------------------------------------------------------- 通用工具
def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def fetch_json(url, headers=None, timeout=HTTP_TIMEOUT, deadline=HTTP_DEADLINE,
               max_bytes=HTTP_MAX_BYTES):
    """GET 请求并解析 JSON。

    与直接 resp.read() 的区别：带**总时限**与**体积上限**，防止大查询把流程挂死。
    超限时抛 ValueError / TimeoutError，由调用方转成可执行的降级提示。
    """
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        chunks = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    "响应超过 " + str(max_bytes // 1048576) + " MB 上限（已下载 "
                    + str(total // 1048576) + " MB），查询范围过大")
            if time.time() - started > deadline:
                raise TimeoutError(
                    "请求超过 " + str(deadline) + " 秒总时限（已下载 "
                    + str(total // 1024) + " KB），通常是因为查询范围过大")
        body = b"".join(chunks)
    return json.loads(body.decode("utf-8"))


def out(title, data, source, note=None, disclaimer=None):
    """统一输出格式：标题 + 数据 + 来源标注。"""
    print("=" * 62)
    print(title)
    print("-" * 62)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("-" * 62)
    # 地点歧义必须在正文里再说一遍：解析错了不报错，只会静默偏移
    for w in PLACE_WARNINGS:
        print("[注意] " + w)
    if note:
        print(note)
    print("数据源: " + source + " | 查询日期: " + QUERY_DATE)
    print(disclaimer or "[提示] 天文/海洋预报仅供参考，最终以官方来源（国家海洋信息中心/天文台）为准。")


def downgrade(msg, suggestions):
    print("[降级] " + msg)
    for s in suggestions:
        print("  -> " + s)


PLACE_WARNINGS = []  # 城市名歧义提示，resolve_place 收集、out() 复述


def place_ambiguity(city, results):
    """同名地点跨省/跨国时的告警文案；没有歧义返回空串。

    纯函数，不联网，便于回归测试。只负责「把歧义说出来」，不改选择结果：
    仍然取 results[0]。
    """
    if not results:
        return ""
    first = results[0]
    key = str(first.get("name") or city).strip()
    region0 = str(first.get("admin1") or first.get("country") or "").strip()
    others = []
    for r in results:
        if str(r.get("name") or "").strip() != key:
            continue
        region = str(r.get("admin1") or r.get("country") or "").strip()
        if not region or region == region0:
            continue
        try:
            lat = float(r.get("latitude"))
            lng = float(r.get("longitude"))
        except (TypeError, ValueError):
            lat = lng = 0.0
        item = "%s/%s (%.4f, %.4f)" % (key, region, lat, lng)
        if item not in others:
            others.append(item)
    if not others:
        return ""
    picked = key + "/" + (region0 or "?")
    return ("城市名「%s」在多个省/国家有同名地点，本次取的是 %s；同名候选：%s。"
            "取错了不会报错，但日出日落、月相、霞光会整体偏移（大同的坑："
            "山西与黑龙江差 46 分钟）。这类时刻敏感数据请改用 --lat/--lng，"
            "详见 references/nature-apis.md §6.2。" % (city, picked, "；".join(others)))


def resolve_place(city=None, lat=None, lng=None):
    """把 --city 或 --lat/--lng 统一解析为 (lat, lng, label)。带白名单校验。"""
    if lat is not None and lng is not None:
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("经纬度超出合法范围")
        return float(lat), float(lng), str(lat) + "," + str(lng)
    if not city:
        raise ValueError("需要提供 --city 或同时提供 --lat/--lng")
    if len(city) > 40 or not SAFE_NAME.match(city):
        raise ValueError("城市名含非法字符或过长，只允许中英文/数字/空格/-/./'")
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": city, "count": 5, "language": "zh", "format": "json"})
    data = fetch_json(url)
    results = data.get("results") or []
    if not results:
        raise ValueError("未找到城市：" + city + "（建议改用 --lat/--lng 精确指定）")
    note = place_ambiguity(city, results)
    if note:
        if note not in PLACE_WARNINGS:
            PLACE_WARNINGS.append(note)
        print("[注意] " + note, file=sys.stderr)
    r = results[0]
    label = r.get("name") or city
    extra = r.get("admin1") or r.get("country") or ""
    if extra:
        label = label + "/" + extra
    return r["latitude"], r["longitude"], label


def parse_iso(s):
    return datetime.fromisoformat(s)


def parse_iso_utc_naive(s):
    """把 naive / 带时区 / 带 Z 的时间字符串统一为 naive UTC datetime，便于比较。"""
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def shift_hour(iso_str, hours):
    """ISO 时间字符串加减小时，返回 'YYYY-MM-DD HH:MM'。"""
    dt = parse_iso(iso_str) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%d %H:%M")


def days_for_date(date_str, max_days=16):
    """按目标日期计算需要请求的预报天数（含 1 天缓冲）；已过去或超出窗口则抛错。"""
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()   # 按北京时间算"今天"
    delta = (target - today).days
    if delta < 0:
        raise ValueError("日期已过去：" + date_str)
    need = delta + 2
    if need > max_days:
        raise ValueError(
            "日期 " + date_str + " 距今 " + str(delta) + " 天，超出 " + str(max_days)
            + " 天预报窗口。请临近出行再查，或改用官方潮汐表/当地气象台。")
    return max(1, need)


# ---------------------------------------------------------------- 1. 赶海-潮汐
def find_extremes(times, heights):
    """从逐小时潮位曲线提取满潮/干潮：局部极值 + 三点抛物线插值精化时刻与潮高。"""
    ex = []
    for i in range(1, len(heights) - 1):
        a, b, c = heights[i - 1], heights[i], heights[i + 1]
        if a is None or b is None or c is None:
            continue
        if b >= a and b >= c and (b > a or b > c):
            kind = "满潮"
        elif b <= a and b <= c and (b < a or b < c):
            kind = "干潮"
        else:
            continue
        denom = a - 2 * b + c
        if abs(denom) < 1e-9:
            off, val = 0.0, b
        else:
            off = 0.5 * (a - c) / denom
            off = max(-0.75, min(0.75, off))          # 顶点必在相邻小时之间
            val = b - 0.25 * (a - c) * off            # 抛物线顶点值
        ex.append({
            "type": kind,
            "time": shift_hour(times[i], off),
            "height_m": round(val, 2),
        })
    return ex


def cmd_tide(args):
    """赶海潮汐：Open-Meteo Marine API（免费无 Key，全球覆盖，最多 16 天预报）。"""
    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    if args.date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        raise ValueError("--date 格式应为 YYYY-MM-DD")
    if args.date:
        days = days_for_date(args.date, max_days=16)
        days_set = {args.date}
    else:
        days = max(1, min(args.days, 7))
        days_set = None
    url = "https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lng,
        "hourly": "sea_level_height_msl",
        "forecast_days": days, "timezone": TZ_CN,
    })
    try:
        data = fetch_json(url)
        h = data.get("hourly") or {}
        times = h.get("time") or []
        heights = h.get("sea_level_height_msl") or []
        if not times or not heights:
            raise ValueError("接口未返回潮位数据（该坐标可能非海域）")
        if days_set is None:
            days_set = set(sorted({t[:10] for t in times})[:days])
        # 实际数据有效期：Open-Meteo Marine 潮位仅覆盖约 10 天，其后 time 仍在但值为 None
        last_i = -1
        for i, v in enumerate(heights):
            if v is not None:
                last_i = i
        valid_until = times[last_i] if last_i >= 0 else None
        warnings = []
        if last_i < len(times) - 1:
            warnings.append(
                "潮位数据仅覆盖至 " + str(valid_until)
                + "（Open-Meteo Marine 实际有效期约 10 天）；"
                "此后的日期请临近出行再查，或改用官方潮汐表。")
        extremes = find_extremes(times, heights)
        by_day = {}
        for e in extremes:
            by_day.setdefault(e["time"][:10], []).append(e)
        report = {
            "location": label, "lat": round(lat, 4), "lng": round(lng, 4),
            "unit": "m (相对平均海平面 MSL，非当地潮高基准面)",
            "data_valid_until": valid_until,
            "forecast_days_used": days,
            "days": [],
            "warnings": warnings,
        }
        for d in sorted(days_set):
            day_idx = [i for i, x in enumerate(times) if x.startswith(d)]
            day_vals = [heights[i] for i in day_idx]
            if not day_idx or all(v is None for v in day_vals):
                report["days"].append({
                    "date": d, "extremes": [], "best_beachcombing_windows": [],
                    "partial": True,
                    "note": "该日期超出潮位数据覆盖范围（至 " + str(valid_until) + "），"
                            "请临近出行再查，或改用官方潮汐表。",
                })
                continue
            partial = any(v is None for v in day_vals)
            day_ex = by_day.get(d, [])
            windows = []
            for low in [e for e in day_ex if e["type"] == "干潮"]:
                lt = parse_iso(low["time"].replace(" ", "T"))
                windows.append({
                    "start": (lt - timedelta(hours=1.5)).strftime("%H:%M"),
                    "end": (lt + timedelta(hours=1.5)).strftime("%H:%M"),
                    "low_time": low["time"][11:], "low_height_m": low["height_m"],
                })
            entry = {
                "date": d, "extremes": day_ex,
                "best_beachcombing_windows": windows,
                "note": "当日最佳窗口 = 干潮前后 1.5 小时，滩涂裸露最广；涨潮前 1 小时必须撤离",
            }
            if partial:
                entry["partial"] = True
                entry["note"] = ("当日潮位数据不完整（覆盖至 " + str(valid_until) + "），"
                                 "极值可能缺失；赶海前必须用官方潮汐表复核，勿依赖本结果。")
            report["days"].append(entry)
        for w in warnings:
            print("[注意] " + w, file=sys.stderr)
        if args.date:
            title = "潮汐预报（赶海） - " + label + " - " + args.date
        else:
            title = "潮汐预报（赶海） - " + label + " - " + str(days) + " 天"
        out(title, report, "Open-Meteo Marine API (marine-api.open-meteo.com)",
            note=("[提示] 窗口 = 干潮前后 1.5 小时；农历初一/十五前后为大潮(退得远、收获多)；\n"
                  "       海面风力 5 级以上或台风天禁赶海；\n"
                  "       潮位为 MSL 基准，与当地潮汐表基准可能有系统偏差，安全决策请对照官方潮汐表。"))
    except Exception as e:
        downgrade("潮汐接口调用失败: " + str(e), [
            "用 web 搜索『城市 潮汐表 日期』，优先潮汐表精灵(eisk.cn)聚合页",
            "提取：满潮时刻 / 干潮时刻 / 潮高 / 推荐赶海时间",
            "推荐赶海窗口 = 干潮前后 1-2 小时；农历初一/十五前后大潮退得远",
        ])
        sys.exit(1)


# ---------------------------------------------------------------- 2. 日出日落
def cmd_sun(args):
    """Sunrise-Sunset.org 免费 API，返回 UTC 时间需换算本地。"""
    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    date = args.date or QUERY_DATE
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        print("x --date 格式应为 YYYY-MM-DD", file=sys.stderr)
        sys.exit(2)
    url = ("https://api.sunrise-sunset.org/json?lat=" + str(lat) + "&lng=" + str(lng)
           + "&date=" + date + "&formatted=0")
    try:
        data = fetch_json(url)
        res = data.get("results", {})
        payload = {
            "location": label, "date": date, "times_utc": res,
            "note_utc": "以上均为 UTC，需按目的地时区换算；接口亦返回 tzid 可用于校验",
        }
        out("日出日落 - " + label + " - " + date + " (UTC)", payload,
            "api.sunrise-sunset.org",
            note=("[提示] 黄金时刻 ~ 日出后/日落前 1 小时；蓝调时刻 ~ 日出前/日落后 20-40 分钟；\n"
                  "       云海日出需前一天降水 + 次日晨晴；日出前 30 分钟到点占位。"))
    except Exception as e:
        downgrade("日出日落接口调用失败: " + str(e), [
            "用 web 搜索『目的地 + 日出时间 + 日期』",
        ])
        sys.exit(1)


# ---------------------------------------------------------------- 3. 观星
def cmd_astro(args):
    """CycleCalcs 免费 API：月相、行星可见性、暗夜窗口、近期天文事件。"""
    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    at = args.at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = ("https://www.cyclecalcs.com/v2/today?lat=" + str(lat) + "&lon=" + str(lng)
           + "&at=" + urllib.parse.quote(at))
    try:
        data = fetch_json(url)
        d = data.get("data", data)
        moon = d.get("moon") or {}
        night = d.get("night") or {}
        phase = moon.get("phase") or {}
        summary = {
            "location": label,
            "queried_at_utc": at,
            "moon_phase": phase.get("name"),
            "moon_illumination_percent": phase.get("illumination_percent"),
            "moon_age_days": phase.get("age_days"),
            "constellation": (moon.get("constellation") or {}).get("name"),
            "dark_window_utc": {"start": night.get("dark_start"), "end": night.get("dark_end")},
            "dark_minutes": night.get("dark_minutes"),
            "moonless_dark_minutes": night.get("moonless_dark_minutes"),
            "night_verdict": night.get("verdict"),
            "planets_up": d.get("planets_up"),
            "next_events": d.get("next_events"),
        }
        out("观星情报 - " + label + " @ " + at + " (UTC)", summary, "cyclecalcs.com",
            note=("[提示] moonless_dark_minutes 是最关键指标——无月暗夜时长，越长越适合深空/银河；\n"
                  "       新月前后 7 天最佳；月照>50% 深空受影响；银河 3-10 月（夏季最亮）；\n"
                  "       流星雨：英仙座 8 月中、双子座 12 月中、象限仪座 1 月初。"))
    except Exception as e:
        downgrade("观星接口调用失败: " + str(e), [
            "用 web 搜索『目的地 + 月相 + 光污染』",
            "查询 lightpollutionmap.info（波特尔暗空分级 1-9，1 级最佳）",
        ])
        sys.exit(1)


# ---------------------------------------------------------------- 4. 火烧云
def score_glow(low, mid, high, vis_km, rh, aod):
    """火烧云/霞光评分 0-3（0=无 1=一般 2=较好 3=绝佳），返回 (score, reasons)。

    依据气象学常识：高空有中高云(散射与承光层) + 低空通透(阳光可达云底)
    + 水汽适中 + 适度气溶胶(利于红橙散射)。
    """
    score = 0
    reasons = []
    if mid is not None and high is not None:
        mh = (mid + high) / 2.0
        if 20 <= mh <= 75:
            score += 1
            reasons.append("中高云 " + str(round(mh)) + "%（适中，有可被照亮的云层）")
        elif mh < 20:
            reasons.append("中高云 " + str(round(mh)) + "%（偏少，云层不足以形成大范围霞光）")
        else:
            reasons.append("中高云 " + str(round(mh)) + "%（偏厚，低角度阳光可能被遮）")
    if low is not None:
        if low <= 30:
            score += 1
            reasons.append("低云 " + str(round(low)) + "%（通透，阳光可达云底）")
        elif low <= 60:
            reasons.append("低云 " + str(round(low)) + "%（偏多，存在遮挡地平线的风险）")
        else:
            score -= 1
            reasons.append("低云 " + str(round(low)) + "%（过厚，霞光基本被挡）")
    if vis_km is not None:
        if vis_km >= 15:
            score += 1
            reasons.append("能见度 " + str(round(vis_km)) + "km（空气通透）")
        elif vis_km < 8:
            score -= 1
            reasons.append("能见度 " + str(round(vis_km)) + "km（有霾，色彩会发灰）")
    if rh is not None:
        if 40 <= rh <= 85:
            reasons.append("相对湿度 " + str(round(rh)) + "%（水汽适中）")
        elif rh > 90:
            score -= 1
            reasons.append("相对湿度 " + str(round(rh)) + "%（过高，易起雾或低云）")
    if aod is not None:
        if 0.10 <= aod <= 0.40:
            score += 1
            reasons.append("气溶胶光学厚度 " + format(aod, ".2f") + "（利于散射出红橙色）")
        elif aod > 0.60:
            reasons.append("气溶胶光学厚度 " + format(aod, ".2f") + "（空气偏浑浊，颜色发暗）")
    score = max(0, min(3, int(score)))
    return score, reasons


GLOW_LABEL = {0: "基本无霞光", 1: "一般", 2: "较好", 3: "绝佳"}


def _avg_window(times, values, center_iso, half_hours=1):
    """取 center 前后 half_hours 小时内所有非 None 值的平均（统一按 UTC 比较）。"""
    if not times or not values:
        return None
    c = parse_iso_utc_naive(center_iso)
    picked = []
    for t, v in zip(times, values):
        if v is None:
            continue
        if abs((parse_iso_utc_naive(t) - c).total_seconds()) <= half_hours * 3600:
            picked.append(v)
    if not picked:
        return None
    return sum(picked) / len(picked)


def cmd_glow(args):
    """火烧云/朝晚霞：Open-Meteo 自建评分模型（云量分层 + 能见度 + 湿度 + 气溶胶）。"""
    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    if args.date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        raise ValueError("--date 格式应为 YYYY-MM-DD")
    fdays = days_for_date(args.date, max_days=16) if args.date else 3
    sun_url = ("https://api.sunrise-sunset.org/json?lat=" + str(lat) + "&lng=" + str(lng)
               + "&date=" + (args.date or QUERY_DATE) + "&formatted=0")
    try:
        sun = fetch_json(sun_url).get("results", {})
    except Exception as e:
        downgrade("无法获取日出日落时刻: " + str(e), ["请先确认 --date 与网络，或改用 web 搜索"])
        sys.exit(1)

    event = args.event
    if event == "set":
        center_utc = sun.get("sunset")
        label_cn = "日落（晚霞）"
    else:
        center_utc = sun.get("sunrise")
        label_cn = "日出（朝霞）"
    if not center_utc:
        print("x 未能取得日出/日落时刻", file=sys.stderr)
        sys.exit(1)

    wx_url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lng,
        "hourly": "cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,relative_humidity_2m",
        "forecast_days": fdays, "timezone": "UTC",
    })
    aq_url = "https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lng,
        "hourly": "aerosol_optical_depth",
        "forecast_days": fdays, "timezone": "UTC",
    })
    try:
        wx = (fetch_json(wx_url).get("hourly") or {})
        times = wx.get("time") or []
        if not times:
            raise ValueError("天气接口未返回数据")
        low = _avg_window(times, wx.get("cloud_cover_low", []), center_utc)
        mid = _avg_window(times, wx.get("cloud_cover_mid", []), center_utc)
        high = _avg_window(times, wx.get("cloud_cover_high", []), center_utc)
        vis = _avg_window(times, wx.get("visibility", []), center_utc)
        rh = _avg_window(times, wx.get("relative_humidity_2m", []), center_utc)
        vis_km = (vis / 1000.0) if vis is not None else None
        aod = None
        try:
            aq = (fetch_json(aq_url).get("hourly") or {})
            aod = _avg_window(aq.get("time", []), aq.get("aerosol_optical_depth", []), center_utc)
        except Exception:
            aod = None  # 气溶胶为可选增强，失败不阻塞
        score, reasons = score_glow(low, mid, high, vis_km, rh, aod)
        payload = {
            "location": label,
            "date": args.date or QUERY_DATE,
            "event": label_cn,
            "reference_time_utc": center_utc,
            "window_used": "中心时刻前后各 1 小时平均",
            "inputs": {
                "cloud_cover_low_pct": round(low, 1) if low is not None else None,
                "cloud_cover_mid_pct": round(mid, 1) if mid is not None else None,
                "cloud_cover_high_pct": round(high, 1) if high is not None else None,
                "visibility_km": round(vis_km, 1) if vis_km is not None else None,
                "relative_humidity_pct": round(rh, 1) if rh is not None else None,
                "aerosol_optical_depth": round(aod, 3) if aod is not None else None,
            },
            "glow_score": score,
            "glow_level": GLOW_LABEL[score],
            "reasons": reasons,
        }
        out("火烧云/霞光预报（本地模型） - " + label + " - "
            + (args.date or QUERY_DATE) + " " + label_cn,
            payload,
            "Open-Meteo Forecast + Air Quality API（自建评分模型）",
            note=("[说明] sunsetbot.top 已于 2026-09 下线，本模型改用公开气象数据自建评分：\n"
                  "       高空有中高云 + 低空通透 + 水汽适中 + 适度气溶胶 -> 霞光概率高。\n"
                  "       评分为 0-3 级参考值，非官方发布，出行前建议结合当地实景判断。"))
    except Exception as e:
        downgrade("火烧云模型计算失败: " + str(e), [
            "用 web 搜索『城市 + 火烧云/晚霞 预报』",
            "或用彩云天气 App / 当地气象台的霞光指数",
        ])
        sys.exit(1)


# ---------------------------------------------------------------- 5. 观鸟（默认免注册）
# 数据源策略：iNaturalist（主源，免 Key）> GBIF（备用，免 Key）> eBird（可选增强，需免费 Token）
INAT_API = "https://api.inaturalist.org/v1"
GBIF_API = "https://api.gbif.org/v1"
EBIRD_API = "https://api.ebird.org/v2"
# eBird 免费 Token 的候选环境变量名（--token 优先级最高）
EBIRD_TOKEN_ENV = ("EBIRD_API_TOKEN", "EBIRD_TOKEN")

EBIRD_TOKEN_HOWTO = (
    "eBird Token 免费申请（非商业约 1000 次/天）：https://ebird.org/api/keygen\n"
    "  申请后任选一种方式传入：\n"
    "    1) 命令参数   --token 你的Token\n"
    "    2) 环境变量   set EBIRD_API_TOKEN=你的Token   (Windows)\n"
    "                  export EBIRD_API_TOKEN=你的Token (macOS/Linux)\n"
    "  验证是否可用：python nature_forecast.py ebirdcheck")

BIRD_NOTE = (
    "[提示] 最佳时段：日出前 30 分钟到日出后 3 小时（鸟最活跃），傍晚日落前 2 小时次之；\n"
    "       迁徙季（3-5 月 / 9-11 月）过境鸟最多，繁殖季（5-7 月）鸣唱最密；\n"
    "       观鸟伦理：不惊鸟、不诱拍、不使用鸟鸣回放、不靠近巢穴、不公开珍稀鸟精确坐标。")
BIRD_DISCLAIMER = (
    "[提示] 观鸟记录来自公民科学平台，只代表「有人记录到」，不等于「你一定能看到」；\n"
    "       珍稀鸟种坐标已被平台模糊化处理，请勿据此精确搜寻。")


def inat_get(path, params=None, timeout=25):
    url = INAT_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return fetch_json(url, timeout=timeout)


def today_cn():
    """按北京时间取今天。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def inat_zh_names(taxon_ids):
    """批量取简体中文名。

    注意两点（都实测踩过）：
      1) iNat 的 locale=zh 返回繁体（黑尾鷗），必须用 locale=zh-CN 才是简体（黑尾鸥）；
      2) /taxa 默认每页 30 条，一次传太多 id 会被静默截断、部分物种回退成英文名，
         所以这里按 100 个一批请求并显式指定 per_page。
    """
    ids = [i for i in dict.fromkeys(taxon_ids) if i]
    if not ids:
        return {}
    out_map = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = inat_get("/taxa", {"id": ",".join(str(x) for x in chunk),
                                  "locale": "zh-CN", "per_page": len(chunk)})
        for t in (data.get("results") or []):
            out_map[t.get("id")] = t.get("preferred_common_name") or t.get("name")
    return out_map


def inat_recent_birds(lat, lng, radius_km, back_days, limit):
    """iNaturalist 近期鸟类观测记录（免 Key）。返回 (行列表, 总记录数)。

    per_page 上限取 100（iNat 允许 200，但 200 条带地理与照片字段的响应体可达数 MB，
    在弱网下会明显拖慢，实测踩过这个坑）。
    """
    d2 = today_cn()
    d1 = d2 - timedelta(days=back_days)
    data = inat_get("/observations", {
        "taxon_id": AVES_TAXON_ID, "lat": lat, "lng": lng, "radius": radius_km,
        "d1": d1.strftime("%Y-%m-%d"), "d2": d2.strftime("%Y-%m-%d"),
        "per_page": max(1, min(limit, 100)),
        "order_by": "observed_on", "order": "desc", "verifiable": "true",
    })
    rows = []
    for o in data.get("results") or []:
        tax = o.get("taxon") or {}
        if not tax:
            continue
        xy = (o.get("geojson") or {}).get("coordinates") or []
        rows.append({
            "date": o.get("observed_on") or "",
            "taxon_id": tax.get("id"),
            "scientific_name": tax.get("name"),
            "common_name_en": tax.get("preferred_common_name"),
            "place_guess": (o.get("place_guess") or "").strip(),
            "quality": o.get("quality_grade"),
            "lat": xy[1] if len(xy) == 2 else None,
            "lng": xy[0] if len(xy) == 2 else None,
            "record_url": o.get("uri"),
        })
    return rows, data.get("total_results")


def inat_species_counts(lat, lng, radius_km, d1, d2, per_page=100):
    """iNaturalist 物种统计（免 Key）。返回 (results, 物种总数)。"""
    data = inat_get("/observations/species_counts", {
        "taxon_id": AVES_TAXON_ID, "lat": lat, "lng": lng, "radius": radius_km,
        "d1": str(d1), "d2": str(d2), "per_page": max(1, min(per_page, 200)),
    })
    return data.get("results") or [], data.get("total_results")


def gbif_recent_birds(lat, lng, radius_km, limit=50, years_back=2):
    """GBIF 备用源（免 Key）：按经纬度包围盒 + 年份检索鸟类出现记录。"""
    d = max(radius_km, 1) / 111.0
    y1 = today_cn().year
    y0 = y1 - years_back
    data = fetch_json(GBIF_API + "/occurrence/search?" + urllib.parse.urlencode({
        "taxonKey": GBIF_AVES_KEY,
        "decimalLatitude": "%.3f,%.3f" % (lat - d, lat + d),
        "decimalLongitude": "%.3f,%.3f" % (lng - d, lng + d),
        "year": "%d,%d" % (y0, y1),
        "hasCoordinate": "true", "limit": max(1, min(limit, 300)),
    }))
    rows = []
    for o in data.get("results") or []:
        rows.append({
            "date": (o.get("eventDate") or "")[:10],
            "taxon_id": None,
            "scientific_name": o.get("scientificName") or o.get("acceptedScientificName"),
            "common_name_en": o.get("vernacularName"),
            "place_guess": o.get("locality") or o.get("stateProvince") or "",
            "quality": o.get("basisOfRecord"),
            "lat": o.get("decimalLatitude"), "lng": o.get("decimalLongitude"),
            "record_url": "https://www.gbif.org/occurrence/" + str(o.get("key")),
        })
    return rows, data.get("count")


def _species_rows(results, limit=None):
    """把 species_counts 结果整理成带中文名的物种列表。"""
    picked = results[:limit] if limit else results
    zh = inat_zh_names([(r.get("taxon") or {}).get("id") for r in picked])
    rows = []
    for r in picked:
        t = r.get("taxon") or {}
        rows.append({
            "中文名": zh.get(t.get("id")) or t.get("preferred_common_name") or "",
            "学名": t.get("name"),
            "记录数": r.get("count"),
            "taxon_id": t.get("id"),
        })
    return rows


def _ebird_only(args):
    """兼容旧用法：只给 --region/--token 时走 eBird（可选增强）。"""
    if not re.match(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$", args.region):
        raise ValueError("--region 格式应如 CN 或 CN-37")
    token = resolve_ebird_token(getattr(args, "token", None))
    if not token:
        raise ValueError("需要 eBird Token。\n" + EBIRD_TOKEN_HOWTO)
    back = max(1, min(int(args.days), 30))
    try:
        data = ebird_get("/data/obs/" + args.region + "/recent", token, {"back": back})
    except urllib.error.HTTPError as e:
        _ebird_http_error(e)
    out("eBird 近期观测 - " + args.region + " - 近 " + str(back) + " 天", data,
        "api.ebird.org (eBird/康奈尔)", note=BIRD_NOTE, disclaimer=BIRD_DISCLAIMER)
    return 0


# ---------------------------------------------------------------- 5b. eBird 完整支持
def resolve_ebird_token(explicit=None):
    """按 参数 > 环境变量 的顺序解析 eBird Token；都没有则返回 None。"""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    for key in EBIRD_TOKEN_ENV:
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return None


def ebird_get(path, token, params=None, timeout=25):
    """调用 eBird API。所有端点统一用 X-eBirdApiToken 头传递 Token。"""
    url = EBIRD_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return fetch_json(url, headers={"X-eBirdApiToken": token}, timeout=timeout)


def ebird_geo_recent(lat, lng, dist_km, back_days, token, max_results=100, notable=False):
    """按经纬度查近期观测（免 region 编码，最适合旅行场景）。

    参数上限：dist<=50km，back<=30 天。notable=True 时只返回该地区罕见的鸟种。
    """
    path = "/data/obs/geo/recent" + ("/notable" if notable else "")
    params = {
        "lat": round(float(lat), 5), "lng": round(float(lng), 5),
        "dist": max(1, min(int(dist_km), 50)),
        "back": max(1, min(int(back_days), 30)),
    }
    if not notable:
        params["maxResults"] = max(1, min(int(max_results), 10000))
    return ebird_get(path, token, params)


def ebird_hotspots(lat, lng, dist_km, token, limit=30):
    """附近观鸟热点（eBird 全球热点库，质量高于用户自由填写的地点名）。"""
    data = ebird_get("/ref/hotspot/geo", token, {
        "lat": round(float(lat), 5), "lng": round(float(lng), 5),
        "dist": max(1, min(int(dist_km), 50)), "fmt": "json",
    })
    if not isinstance(data, list):
        return []
    rows = []
    for h in data[:limit]:
        rows.append({
            "热点名称": h.get("locName"),
            "eBird代码": h.get("locId"),
            "鸟种数": h.get("numSpeciesAllTime"),
            "最近记录": (h.get("latestObsDt") or "")[:10],
            "坐标": [h.get("lat"), h.get("lng")],
            "页面": "https://ebird.org/hotspot/" + str(h.get("locId")),
        })
    return rows


def _ebird_rows(data):
    """把 eBird 观测数组整理成与 iNat 对齐的记录行。"""
    rows = []
    for o in data if isinstance(data, list) else []:
        sub = o.get("subId")
        rows.append({
            "date": (o.get("obsDt") or "")[:10],
            "中文名": o.get("comName"),
            "学名": o.get("sciName"),
            "eBird代码": o.get("speciesCode"),
            "地点": o.get("locName"),
            "数量": o.get("howMany"),
            "lat": o.get("lat"), "lng": o.get("lng"),
            "record_url": ("https://ebird.org/checklist/" + str(sub)) if sub else None,
        })
    return rows


def _ebird_http_error(e):
    """把 eBird 的 HTTP 错误翻译成能照着做的提示。"""
    if e.code in (401, 403):
        downgrade("eBird 鉴权失败（HTTP " + str(e.code) + "）", [
            "Token 无效、过期或未提供 —— 注意不要带引号或多余空格",
            EBIRD_TOKEN_HOWTO,
        ])
    elif e.code == 429:
        downgrade("eBird 请求超限（HTTP 429）", [
            "免费 Token 约 1000 次/天，请稍后再试或减少 --limit",
        ])
    else:
        downgrade("eBird 接口异常（HTTP " + str(e.code) + "）", [
            "检查网络后重试；若持续失败可去掉 --source ebird 改用免 Key 的 iNaturalist",
        ])
    sys.exit(1)


def cmd_bird(args):
    """观鸟：近期真实观测记录。

    默认免注册（iNaturalist 主源 + GBIF 备用）；提供 eBird Token 时自动启用 eBird 增强，
    并额外给出「珍稀鸟种」提醒。--source 可强制指定单一数据源。
    """
    has_place = bool(args.city) or (args.lat is not None and args.lng is not None)
    if not has_place and args.token and args.region:
        return _ebird_only(args)
    if not has_place:
        raise ValueError("需要 --city 或 --lat/--lng（eBird 也可按经纬度直接查询）")

    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    radius = max(1, min(int(args.radius), 200))
    days = max(1, min(int(args.days), 365))
    limit = max(1, min(int(args.limit), 100))
    token = resolve_ebird_token(getattr(args, "token", None))

    src = args.source
    if src == "auto":
        src = "ebird+inat" if token else "inat"
    if src == "ebird" and not token:
        raise ValueError("--source ebird 需要 Token。\n" + EBIRD_TOKEN_HOWTO)

    payload = {
        "location": label, "lat": round(lat, 4), "lng": round(lng, 4),
        "radius_km": radius, "back_days": days, "source_mode": src,
    }
    sources_used = []
    notes = []
    merged = []

    # ---- eBird（有 Token 时启用；--source ebird 时为唯一数据源）
    if src in ("ebird", "ebird+inat"):
        try:
            raw = ebird_geo_recent(lat, lng, radius, min(days, 30), token,
                                   max_results=limit * 10)
            rows = _ebird_rows(raw)
            sources_used.append("eBird (api.ebird.org)")
            payload["ebird_recent_count"] = len(rows)
            if days > 30:
                payload["ebird_window_note"] = ("eBird geo 端点回溯上限 30 天，"
                                                "已按 30 天查询（iNaturalist 结果仍按 "
                                                + str(days) + " 天）")
            for r in rows:
                r["来源"] = "eBird"
                merged.append(r)
            try:
                notable = _ebird_rows(ebird_geo_recent(
                    lat, lng, radius, min(days, 30), token, notable=True))
                for r in notable:
                    r["来源"] = "eBird(珍稀)"
                payload["ebird_notable"] = notable
                if notable:
                    notes.append("eBird 标记出 " + str(len(notable))
                                 + " 条该地区罕见鸟种记录（见 ebird_notable）；"
                                 "珍稀鸟请勿公开精确坐标，也不要为拍摄惊扰。")
            except urllib.error.HTTPError as e2:
                payload["ebird_notable"] = None
                notes.append("eBird 珍稀鸟种端点失败（HTTP " + str(e2.code)
                             + "），不影响其他结果。")
        except urllib.error.HTTPError as e:
            if src == "ebird":
                _ebird_http_error(e)
            notes.append("eBird 增强失败（HTTP " + str(e.code) + "），已回退到免 Key 数据源。")
            src = "inat"

    # ---- iNaturalist（免 Key 主源）
    if src in ("inat", "ebird+inat"):
        try:
            rows, total = inat_recent_birds(lat, lng, radius, days, limit)
            zh = inat_zh_names([r.get("taxon_id") for r in rows])
            for r in rows:
                merged.append({
                    "来源": "iNaturalist", "date": r.get("date"),
                    "中文名": zh.get(r.get("taxon_id")) or r.get("common_name_en") or "",
                    "学名": r.get("scientific_name"), "地点": r.get("place_guess"),
                    "数量": None, "record_url": r.get("record_url"),
                    "lat": r.get("lat"), "lng": r.get("lng"),
                })
            sources_used.append("iNaturalist (api.inaturalist.org，免注册)")
            payload["inat_recent_total_from_source"] = total
        except Exception as e:
            notes.append("iNaturalist 调用失败（" + str(e)[:70] + "）。")
            if not merged:
                try:
                    rows, total = gbif_recent_birds(lat, lng, radius, limit=limit)
                    for r in rows:
                        merged.append({
                            "来源": "GBIF", "date": r.get("date"),
                            "中文名": r.get("common_name_en") or "",
                            "学名": r.get("scientific_name"),
                            "地点": r.get("place_guess"), "数量": None,
                            "record_url": r.get("record_url"),
                            "lat": r.get("lat"), "lng": r.get("lng"),
                        })
                    sources_used.append("GBIF (api.gbif.org，免注册)")
                    notes.append("已切换 GBIF 备用源。GBIF 以标本/调查记录为主，"
                                 "时效性弱于公民科学平台。")
                except Exception as e2:
                    downgrade("观鸟数据源全部失败: " + str(e2)[:80], [
                        "用 web 搜索『城市名 观鸟 记录』，或查 ebird.org/explore",
                        "中国观鸟记录中心 birdreport.cn（页面可查，暂无公开 API）",
                    ])
                    sys.exit(1)

    # ---- 当季鸟种名录（iNaturalist 独有：eBird 无等价的聚合端点）
    # per_page 只要 15：响应体越小越快，实测比请求 60 条再截断快一倍以上
    season_plus, season_total = [], None
    if src in ("inat", "ebird+inat"):
        try:
            sres, season_total = inat_species_counts(
                lat, lng, radius, today_cn() - timedelta(days=365), today_cn(), 15)
            season_plus = _species_rows(sres, 12)
        except Exception:
            pass
    elif src == "ebird":
        notes.append("当前为 --source ebird 单一源，不含「当季鸟种名录」"
                     "（该名录依赖 iNaturalist 的历史记录聚合）；"
                     "需要的话用 --source auto 同时取两个源。")

    merged.sort(key=lambda r: (r.get("date") or ""), reverse=True)
    merged = merged[:limit]

    seen = set()
    for r in merged:
        key = r.get("学名")
        if key and key not in seen:
            seen.add(key)
    payload.update({
        "sources_used": sources_used,
        "recent_records_count": len(merged),
        "distinct_species": len(seen),
        "recent_records": merged,
        "species_last_365d": season_plus,
        "species_last_365d_total": season_total,
        "notes": notes,
    })
    if not merged:
        notes.append("近 " + str(days) + " 天该范围内没有新记录，"
                     "请优先看 species_last_365d（近一年同地出现过的鸟种）。")
    out("观鸟情报 - " + label + " - 半径 " + str(radius) + "km - 近 " + str(days) + " 天",
        payload, " + ".join(sources_used) or "（无）",
        note=BIRD_NOTE, disclaimer=BIRD_DISCLAIMER)
    return 0


def cmd_ebirdcheck(args):
    """验证 eBird Token 是否可用，并实测本脚本实际依赖的端点。"""
    token = resolve_ebird_token(getattr(args, "token", None))
    print("=" * 62)
    print("eBird Token 自检")
    print("=" * 62)
    if not token:
        print("[FAIL] 未找到 Token（--token 与环境变量 " + " / ".join(EBIRD_TOKEN_ENV) + " 均为空）")
        print("-" * 62)
        print(EBIRD_TOKEN_HOWTO)
        return 1
    print("Token: " + token[:4] + "****" + token[-4:] if len(token) > 8 else "Token: ****")
    print("-" * 62)
    lat, lng = 37.50914, 122.11356
    checks = [
        ("近期观测 geo/recent", lambda: _ebird_rows(
            ebird_geo_recent(lat, lng, 25, 14, token, max_results=10))),
        ("珍稀鸟种 geo/recent/notable", lambda: _ebird_rows(
            ebird_geo_recent(lat, lng, 25, 14, token, notable=True))),
        ("观鸟热点 ref/hotspot/geo", lambda: ebird_hotspots(lat, lng, 25, token, 5)),
    ]
    ok = 0
    for name, fn in checks:
        try:
            rows = fn()
            ok += 1
            print("  [OK]   " + name.ljust(28) + " 返回 " + str(len(rows)) + " 条")
        except urllib.error.HTTPError as e:
            tip = "Token 无效或过期" if e.code in (401, 403) else "HTTP " + str(e.code)
            print("  [FAIL] " + name.ljust(28) + " " + tip)
        except Exception as e:
            print("  [FAIL] " + name.ljust(28) + " " + str(e)[:60])
    print("-" * 62)
    if ok == len(checks):
        print("Token 可用（" + str(ok) + "/" + str(len(checks)) + " 个端点实测通过）。"
              "之后运行 bird / birdspot 时会自动启用 eBird。")
        return 0
    if ok == 0:
        print("Token 不可用。请重新申请：" + EBIRD_TOKEN_HOWTO)
        return 1
    print("部分端点通过（" + str(ok) + "/" + str(len(checks)) + "）。"
          "eBird 偶尔会调整端点，失败的端点会自动降级到免 Key 源，不影响主流程。")
    return 0


def cmd_species(args):
    """当季鸟种名录：合并最近 3 年同一月份的真实记录，免 Key。"""
    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    radius = max(1, min(int(args.radius), 200))
    limit = max(1, min(int(args.limit), 100))
    mon = int(args.month) if args.month else today_cn().month
    if not (1 <= mon <= 12):
        raise ValueError("--month 应为 1-12")
    today = today_cn()
    merged, per_year, used_years = {}, {}, []
    for k in range(3):
        y = today.year - k
        d1 = date(y, mon, 1)
        d2 = date(y, mon, calendar.monthrange(y, mon)[1])
        if d1 > today:
            continue                       # 该月还没到（如当前 9 月却查 12 月）
        if d2 > today:
            d2 = today                     # 当月只统计到今天
        try:
            # 每年多取一点（limit*2），避免跨年合并时漏掉某年排名靠后、合计却靠前的物种；
            # 但仍远小于 100，兼顾速度
            res, _ = inat_species_counts(lat, lng, radius, d1, d2,
                                         max(10, min(limit * 2, 60)))
        except Exception as e:
            print("[注意] " + str(y) + " 年 " + str(mon) + " 月查询失败：" + str(e)[:70],
                  file=sys.stderr)
            continue
        used_years.append(str(y))
        per_year[str(y) + "-%02d" % mon] = len(res)
        for r in res:
            t = r.get("taxon") or {}
            tid = t.get("id")
            if not tid:
                continue
            cur = merged.setdefault(tid, {"count": 0, "sci": t.get("name"),
                                          "en": t.get("preferred_common_name"), "years": []})
            cur["count"] += r.get("count") or 0
            cur["years"].append(str(y))
    if not used_years:
        downgrade("未能取得任何年份的同月记录", [
            "该地区该月可能确实缺少记录；可加大 --radius 或改用 web 搜索",
        ])
        sys.exit(1)
    zh = inat_zh_names(list(merged))
    ranked = sorted(merged.items(), key=lambda kv: -kv[1]["count"])[:limit]
    rows = [{
        "中文名": zh.get(tid) or kv["en"] or "",
        "学名": kv["sci"],
        "记录数_近3年同月合计": kv["count"],
        "出现年份": sorted(set(kv["years"])),
        "taxon_id": tid,
    } for tid, kv in ranked]
    payload = {
        "location": label, "lat": round(lat, 4), "lng": round(lng, 4),
        "month": mon, "radius_km": radius,
        "years_used": used_years, "records_per_year": per_year,
        "species_total": len(merged), "top_species": rows,
        "data_note": "记录数 = 近 3 年该月在此半径内的观测量合计；记录多说明该月较易见到，"
                     "不代表一定能看到。",
    }
    out("当季鸟种名录 - " + label + " - " + str(mon) + " 月（近 3 年）",
        payload, "iNaturalist API (api.inaturalist.org，免注册)",
        note=BIRD_NOTE, disclaimer=BIRD_DISCLAIMER)
    return 0


def cmd_birdinfo(args):
    """物种卡：中文名/学名/分类/保护等级/简介/（可选）本地最佳月份。免 Key。"""
    query = args.name
    if len(query) > 40 or not SAFE_NAME.match(query):
        raise ValueError("物种名含非法字符或过长")
    try:
        ac = inat_get("/taxa/autocomplete", {"q": query, "per_page": 8, "locale": "zh-CN"})
    except Exception as e:
        downgrade("iNaturalist 物种检索失败: " + str(e)[:80], [
            "改用 web 搜索『物种名 识别 保护等级』",
        ])
        sys.exit(1)
    cands = [t for t in (ac.get("results") or [])
             if t.get("rank") in ("species", "subspecies")]
    if not cands:
        downgrade("未找到该物种：" + query, [
            "换用学名或更常见的俗名重试，例如 birdinfo --name \"Grus japonensis\"",
        ])
        sys.exit(1)
    exact = [t for t in cands if (t.get("name") or "").lower() == query.lower()
             or (t.get("preferred_common_name") or "") == query]
    pick = (exact or cands)[0]

    detail = inat_get("/taxa/" + str(pick.get("id")), {"locale": "zh-CN"})
    t = (detail.get("results") or [{}])[0]
    lineage = {}
    for a in (t.get("ancestors") or []):
        if a.get("rank") in ("class", "order", "family", "genus"):
            lineage[a.get("rank")] = a.get("name")
    statuses = []
    for s in (t.get("conservation_statuses") or []):
        statuses.append({"评级": s.get("status"), "来源": s.get("authority")})
    summary = re.sub(r"<[^>]+>", "", str(t.get("wikipedia_summary") or ""))
    summary = re.sub(r"\s+", " ", summary).strip()

    local = None
    if args.city or (args.lat is not None and args.lng is not None):
        try:
            lat, lng, label = resolve_place(args.city, args.lat, args.lng)
            h = inat_get("/observations/histogram", {
                "taxon_id": t.get("id"), "lat": lat, "lng": lng,
                "radius": max(1, min(int(args.radius), 200)),
                "interval": "month", "d1": "2015-01-01", "d2": str(today_cn()),
            })
            by_month = {}
            for k, v in ((h.get("results") or {}).get("month") or {}).items():
                by_month[k[5:7]] = by_month.get(k[5:7], 0) + v
            ordered = sorted(by_month.items(), key=lambda kv: -kv[1])
            local = {
                "location": label,
                "records_by_month": {k + "月": by_month[k] for k in sorted(by_month)},
                "best_months": [k + "月" for k, v in ordered[:3] if v > 0],
                "data_note": "按该物种在此地的历史记录月份统计；空白月份 = 无记录，"
                             "可能是不在该季，也可能是记录少。",
            }
        except Exception as e:
            print("[注意] 本地物候查询失败：" + str(e)[:70], file=sys.stderr)

    payload = {
        "查询词": query,
        "中文名": t.get("preferred_common_name") or pick.get("preferred_common_name"),
        "学名": t.get("name"),
        "分类": {"纲": lineage.get("class"), "目": lineage.get("order"),
                 "科": lineage.get("family"), "属": lineage.get("genus")},
        "保护等级": statuses or None,
        "中国保护级别": "未查到（iNaturalist 不含《国家重点保护野生动物名录》数据；"
                        "涉及拍摄许可或执法问题请人工核对官方名录）",
        "全球观测记录数": t.get("observations_count"),
        "简介": summary[:600] if summary else None,
        "简介来源": t.get("wikipedia_url"),
        "本地物候": local,
        "参考链接": {
            "iNaturalist 物种页": "https://www.inaturalist.org/taxa/" + str(t.get("id"))
                                  + "（含照片与分布记录）",
            "中国观鸟记录中心": "https://www.birdreport.cn/（站内搜索物种名；无公开 API，人工查询）",
            "eBird 全球观测地图": "https://ebird.org/explore（人工访问；脚本调用需免费 Token）",
            "鸟鸣识别": "本脚本不提供：xeno-canto 需 Key、Macaulay 有反爬。"
                        "建议用 Merlin Bird ID 或 BirdNET App 离线识别",
        },
    }
    out("物种卡 - " + str(payload["中文名"] or query) + " (" + str(t.get("name")) + ")",
        payload, "iNaturalist API (api.inaturalist.org，免注册)",
        note=BIRD_NOTE, disclaimer=BIRD_DISCLAIMER)
    return 0


def _to_min(hhmm_str):
    return int(hhmm_str[:2]) * 60 + int(hhmm_str[3:5])


def cmd_birdtime(args):
    """观鸟黄金时段：由当地日出日落 + 风/降水/能见度约束算出，本地计算，免 Key。"""
    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    if args.date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        raise ValueError("--date 格式应为 YYYY-MM-DD")
    fdays = days_for_date(args.date, max_days=16) if args.date else 3
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lng,
        "daily": "sunrise,sunset",
        "hourly": "temperature_2m,wind_speed_10m,precipitation_probability,"
                  "visibility,cloud_cover",
        "forecast_days": fdays, "timezone": "auto",
    })
    try:
        data = fetch_json(url)
    except Exception as e:
        downgrade("天气接口调用失败: " + str(e)[:80], [
            "用 web 搜索『目的地 日出时间 日期』，按日出前后 3 小时自行安排",
        ])
        sys.exit(1)
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise ValueError("天气接口未返回逐小时数据")

    series = {k: hourly.get(k) or [None] * len(times) for k in
              ("temperature_2m", "wind_speed_10m", "precipitation_probability",
               "visibility", "cloud_cover")}

    def window_stats(t0, t1, day):
        """统计当地某日 [t0,t1) 分钟区间内的气象均值/极值。"""
        idx = []
        for i, s in enumerate(times):
            if s.startswith(day) and t0 <= _to_min(s[11:16]) < t1:
                idx.append(i)
        if not idx:
            return None
        def avg(key):
            vals = [series[key][i] for i in idx if series[key][i] is not None]
            return round(sum(vals) / len(vals), 1) if vals else None
        def mx(key):
            vals = [series[key][i] for i in idx if series[key][i] is not None]
            return max(vals) if vals else None
        vis = avg("visibility")
        return {
            "温度_℃": avg("temperature_2m"),
            "风速_kmh": avg("wind_speed_10m"),
            "降水概率_最大pct": mx("precipitation_probability"),
            "能见度_km": round(vis / 1000.0, 1) if vis else None,
            "云量_pct": avg("cloud_cover"),
        }

    def verdict(st):
        if not st:
            return "无数据"
        wind, rain, vis = st.get("风速_kmh"), st.get("降水概率_最大pct"), st.get("能见度_km")
        if wind is not None and wind >= 30:
            return "差（风大，鸟多躲藏且观察困难）"
        if rain is not None and rain >= 60:
            return "差（降水概率高）"
        if vis is not None and vis < 3:
            return "差（能见度太低）"
        if (wind is None or wind < 20) and (rain is None or rain < 30):
            if vis is not None and vis < 5:
                return "一般（能见度偏低，远处水鸟看不清）"
            return "好"
        return "一般"

    def plus_min(s, delta):
        total = (_to_min(s) + delta) % 1440
        return "%02d:%02d" % (total // 60, total % 60)

    days_out = []
    for i, day in enumerate(daily.get("time") or []):
        if args.date and day != args.date:
            continue
        srs, sss = daily.get("sunrise") or [], daily.get("sunset") or []
        if i >= len(srs) or i >= len(sss) or not srs[i] or not sss[i]:
            continue
        sr, ss = srs[i][11:16], sss[i][11:16]
        am = window_stats(_to_min(plus_min(sr, -30)), _to_min(plus_min(sr, 180)), day)
        pm = window_stats(_to_min(plus_min(ss, -120)), _to_min(ss), day)
        days_out.append({
            "date": day, "sunrise": sr, "sunset": ss,
            "morning_window": plus_min(sr, -30) + "-" + plus_min(sr, 180),
            "morning_conditions": am, "morning_verdict": verdict(am),
            "evening_window": plus_min(ss, -120) + "-" + ss,
            "evening_conditions": pm, "evening_verdict": verdict(pm),
        })
    if not days_out:
        raise ValueError("未取到指定日期的日出日落数据")
    mon = int((args.date or str(today_cn()))[5:7])
    if mon in (3, 4, 5, 9, 10, 11):
        season = "迁徙季（过境鸟多，水鸟/猛禽高峰）"
    elif mon in (6, 7):
        season = "繁殖季（鸣唱密，林鸟看点多）"
    else:
        season = "越冬季（雁鸭/鸥类集群）"
    payload = {
        "location": label, "timezone": data.get("timezone"),
        "season_judgement": season, "days": days_out,
        "rule": "晨窗 = 日出前 30 分钟 至 日出后 3 小时；昏窗 = 日落前 2 小时 至 日落。"
                "判定：风速<20km/h 且 降水概率<30% 为好；风速≥30km/h 或降水概率≥60% 为差。",
    }
    out("观鸟黄金时段 - " + label + (" - " + args.date if args.date else ""),
        payload, "Open-Meteo Forecast API（本地规则计算，免注册）",
        note=BIRD_NOTE, disclaimer=BIRD_DISCLAIMER)
    return 0


def cmd_birdspot(args):
    """附近观鸟地点：有 Token 时用 eBird 官方热点库，否则按真实记录密度排序。

    默认只取近 30 天记录——这不是偷懒，是实测的取舍：
    iNat 的 /observations 端点在回溯 ≥180 天时响应体涨到 MB 级并被服务端缓慢滴流，
    会直接卡死几十秒以上；而"最近 30 天有人在哪看到鸟"对做行程本来就更相关。
    """
    lat, lng, label = resolve_place(args.city, args.lat, args.lng)
    radius = max(1, min(int(args.radius), 200))
    limit = max(1, min(int(args.limit), 40))
    days = max(1, min(int(getattr(args, "days", 30) or 30), OBS_MAX_DAYS))
    token = resolve_ebird_token(getattr(args, "token", None))
    notes = []
    if getattr(args, "days", None) and args.days > OBS_MAX_DAYS:
        notes.append("回溯天数已从 " + str(args.days) + " 收窄到 " + str(OBS_MAX_DAYS)
                     + " 天：实测 iNat 在更长窗口下响应过慢，会卡住几十秒。")

    if token:
        try:
            spots = ebird_hotspots(lat, lng, min(radius, 50), token, limit)
            out("附近观鸟热点（eBird 官方热点库） - " + label
                + " - 半径 " + str(min(radius, 50)) + "km",
                {"location": label, "radius_km": min(radius, 50),
                 "hotspot_count": len(spots), "hotspots": spots,
                 "data_note": "鸟种数 = 该热点历史累计记录鸟种数；热点由 eBird 审核，"
                              "地点名比用户自由填写更规范。",
                 "notes": notes},
                "api.ebird.org /ref/hotspot/geo", note=BIRD_NOTE,
                disclaimer=BIRD_DISCLAIMER)
            return 0
        except urllib.error.HTTPError as e:
            notes.append("eBird 热点端点失败（HTTP " + str(e.code) + "），"
                         "已回退到 iNaturalist 记录密度。")
        except Exception as e:
            notes.append("eBird 热点端点失败（" + str(e)[:60] + "），已回退。")

    try:
        rows, total = inat_recent_birds(lat, lng, radius, days, 100)
    except (TimeoutError, ValueError) as e:
        downgrade("观鸟地点查询超时或范围过大: " + str(e)[:90], [
            "把 --radius 调小（例如 25）或 --days 降到 30 后重试",
            "或先跑 species --city 城市 看当季鸟种，再向当地观鸟群问热点",
        ])
        sys.exit(1)
    except Exception as e:
        downgrade("iNaturalist 调用失败: " + str(e)[:80], [
            "用 web 搜索『城市名 观鸟点 / 湿地公园』",
        ])
        sys.exit(1)
    spots = {}
    for r in rows:
        p = (r.get("place_guess") or "").strip()
        if not p:
            continue
        s = spots.setdefault(p, {"记录数": 0, "鸟种ids": set(), "最近记录": "",
                                 "坐标": None, "示例记录": None})
        s["记录数"] += 1
        if r.get("taxon_id"):
            s["鸟种ids"].add(r["taxon_id"])
        if r.get("date") and r["date"] > s["最近记录"]:
            s["最近记录"] = r["date"]
        if s["坐标"] is None and r.get("lat"):
            s["坐标"] = [round(r["lat"], 4), round(r["lng"], 4)]
        if s["示例记录"] is None:
            s["示例记录"] = r.get("record_url")
    ranked = sorted(spots.items(),
                    key=lambda kv: (-len(kv[1]["鸟种ids"]), -kv[1]["记录数"]))[:limit]
    out_rows = [{
        "地点": k, "鸟种数": len(v["鸟种ids"]), "记录数": v["记录数"],
        "最近记录日期": v["最近记录"], "大致坐标": v["坐标"], "示例记录": v["示例记录"],
    } for k, v in ranked]
    payload = {
        "location": label, "lat": round(lat, 4), "lng": round(lng, 4),
        "radius_km": radius, "window": "近 " + str(days) + " 天",
        "sampled_records": len(rows), "source_total": total,
        "spots": out_rows,
        "data_note": "地点名来自观察者填写的 place_guess，未标准化，同一地点可能有多种写法；"
                     "热度只反映记录活跃度，不代表观赏效果。",
        "notes": notes,
    }
    out("附近观鸟地点热度（按记录密度） - " + label + " - 半径 " + str(radius)
        + "km - 近 " + str(days) + " 天",
        payload, "iNaturalist API (api.inaturalist.org，免注册)",
        note=BIRD_NOTE, disclaimer=BIRD_DISCLAIMER)
    return 0


# ---------------------------------------------------------------- 6. 自检
def cmd_selftest(args):
    """一键检测所有接口可用性，输出状态表。"""
    LAT, LNG = 37.50914, 122.11356
    inat = ("https://api.inaturalist.org/v1/observations?taxon_id=3&lat=" + str(LAT)
            + "&lng=" + str(LNG) + "&radius=50&per_page=1")
    checks = [
        ("日出日落", "sunrise-sunset.org（免 Key）",
         "https://api.sunrise-sunset.org/json?lat=" + str(LAT) + "&lng=" + str(LNG)
         + "&date=" + QUERY_DATE + "&formatted=0", None, True),
        ("观星", "cyclecalcs.com（免 Key）",
         "https://www.cyclecalcs.com/v2/today?lat=" + str(LAT) + "&lon=" + str(LNG), None, True),
        ("潮汐", "Open-Meteo Marine（免 Key）",
         "https://marine-api.open-meteo.com/v1/marine?latitude=" + str(LAT) + "&longitude="
         + str(LNG) + "&hourly=sea_level_height_msl&forecast_days=1", None, True),
        ("云量湿度", "Open-Meteo Forecast（免 Key）",
         "https://api.open-meteo.com/v1/forecast?latitude=" + str(LAT) + "&longitude=" + str(LNG)
         + "&hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,relative_humidity_2m&forecast_days=1",
         None, True),
        ("气溶胶", "Open-Meteo AirQuality（免 Key）",
         "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=" + str(LAT)
         + "&longitude=" + str(LNG) + "&hourly=aerosol_optical_depth&forecast_days=1", None, True),
        ("地理编码", "Open-Meteo Geocoding（免 Key）",
         "https://geocoding-api.open-meteo.com/v1/search?name=weihai&count=1&format=json", None, True),
        ("观鸟-观测", "iNaturalist（免 Key）", inat, None, True),
        ("观鸟-物种检索", "iNaturalist taxa（免 Key）",
         "https://api.inaturalist.org/v1/taxa/autocomplete?q=%E9%BB%91%E5%B0%BE%E9%B8%A5&per_page=1",
         None, True),
        ("观鸟-备份", "GBIF（免 Key）",
         "https://api.gbif.org/v1/occurrence/search?taxonKey=212&year=2025,2026&limit=1",
         None, True),
    ]
    eb_token = resolve_ebird_token(getattr(args, "token", None))
    eb_url = ("https://api.ebird.org/v2/data/obs/geo/recent?lat=" + str(LAT)
              + "&lng=" + str(LNG) + "&dist=25&back=7")
    checks.append(("eBird 增强", "api.ebird.org（需免费 Token）", eb_url,
                   {"X-eBirdApiToken": eb_token or "NO-TOKEN"}, False))

    print("=" * 62)
    print("接口自检 - travel-orchestrator nature_forecast v" + VERSION)
    print("=" * 62)
    ok_count = 0
    for name, source, url, headers, required in checks:
        status, detail = "OK", ""
        try:
            data = fetch_json(url, headers=headers, timeout=20)
            if isinstance(data, list):
                detail = str(len(data)) + " 条记录"
            elif isinstance(data, dict):
                detail = "keys=" + ",".join(list(data)[:4])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and not required:
                status = "NEED-TOKEN"
                detail = ("未配置 Token（非必需）" if not eb_token
                          else "HTTP " + str(e.code) + "（Token 可能无效，运行 ebirdcheck 验证）")
            else:
                status, detail = "FAIL", "HTTP " + str(e.code)
        except Exception as e:
            status, detail = "FAIL", str(e)[:70]
        if status != "FAIL":
            ok_count += 1
        mark = {"OK": "  [OK]", "NEED-TOKEN": "[TOKEN]", "FAIL": "[FAIL]"}[status]
        print(mark + " " + name.ljust(14) + " " + source.ljust(30) + " " + detail)
    print("-" * 62)
    print("通过 " + str(ok_count) + "/" + str(len(checks))
          + " 项（NEED-TOKEN 计入通过：eBird 是可选的增强源）")
    print("观鸟默认免注册；如需 eBird 全部能力，申请免费 Token 后运行 ebirdcheck 验证")
    print("已停用: sunsetbot.top（域名下线）/ cnss.com.cn tideJson（404）"
          "/ macaulaylibrary.org（反爬）/ xeno-canto.org（需 Key），均已替代或改为人工查询")
    return 0 if ok_count >= len(checks) - 1 else 1


# ---------------------------------------------------------------- main
def build_parser():
    parser = argparse.ArgumentParser(
        description="自然观察专项实时数据查询 v" + VERSION,
        epilog="示例: nature_forecast.py tide --city 威海")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_place(p):
        p.add_argument("--city", help="城市名（自动地理编码）")
        p.add_argument("--lat", type=float, help="纬度")
        p.add_argument("--lng", type=float, help="经度")

    p_tide = sub.add_parser("tide", help="赶海潮汐（Open-Meteo Marine）")
    add_place(p_tide)
    p_tide.add_argument("--date", default=None, help="只显示该日 YYYY-MM-DD")
    p_tide.add_argument("--days", type=int, default=3, help="显示天数 1-7，默认 3")

    p_sun = sub.add_parser("sun", help="日出日落（Sunrise-Sunset.org）")
    add_place(p_sun)
    p_sun.add_argument("--date", default=None, help="日期 YYYY-MM-DD")

    p_astro = sub.add_parser("astro", help="观星情报（CycleCalcs）")
    add_place(p_astro)
    p_astro.add_argument("--at", default=None, help="UTC 时间 ISO8601，默认当前")

    p_glow = sub.add_parser("glow", help="火烧云/霞光（Open-Meteo 自建模型）")
    add_place(p_glow)
    p_glow.add_argument("--date", default=None, help="日期 YYYY-MM-DD")
    p_glow.add_argument("--event", default="set", choices=["set", "rise"],
                        help="set=晚霞(日落) rise=朝霞(日出)")

    p_bird = sub.add_parser("bird", help="观鸟近期观测（默认免注册）")
    add_place(p_bird)
    p_bird.add_argument("--token", default=None,
                        help="eBird Token（可选；也可用环境变量 EBIRD_API_TOKEN）")
    p_bird.add_argument("--source", default="auto", choices=["auto", "inat", "gbif", "ebird"],
                        help="auto=有Token时 eBird+iNaturalist，否则 iNaturalist；默认 auto")
    p_bird.add_argument("--days", type=int, default=14, help="回溯天数 1-365，默认 14")
    p_bird.add_argument("--radius", type=int, default=25, help="半径 km 1-200，默认 25")
    p_bird.add_argument("--limit", type=int, default=20, help="最多返回记录数 1-100，默认 20")
    p_bird.add_argument("--region", default="CN", help="eBird 区域码，仅 --source ebird 且无城市时用")
    p_bird.add_argument("--back", type=int, default=7, help="（旧参数）等同 --days 的 eBird 回溯")

    p_species = sub.add_parser("species", help="当季鸟种名录（免注册）")
    add_place(p_species)
    p_species.add_argument("--month", type=int, default=None, help="月份 1-12，默认当前月")
    p_species.add_argument("--radius", type=int, default=60, help="半径 km 1-200，默认 60")
    p_species.add_argument("--limit", type=int, default=20, help="最多返回物种数 1-100，默认 20")

    p_binfo = sub.add_parser("birdinfo", help="物种卡：名/分类/保护等级/简介（免注册）")
    p_binfo.add_argument("--name", required=True, help="中文名或学名，如 丹顶鹤 / Grus japonensis")
    add_place(p_binfo)
    p_binfo.add_argument("--radius", type=int, default=100, help="本地物候统计半径 km，默认 100")

    p_btime = sub.add_parser("birdtime", help="观鸟黄金时段（免注册，日出日落+天气）")
    add_place(p_btime)
    p_btime.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认未来 3 天")

    p_spot = sub.add_parser("birdspot", help="附近观鸟地点（有Token用eBird热点库）")
    add_place(p_spot)
    p_spot.add_argument("--token", default=None, help="eBird Token（可选）")
    p_spot.add_argument("--radius", type=int, default=30, help="半径 km 1-200，默认 30")
    p_spot.add_argument("--days", type=int, default=30,
                        help="回溯天数 1-90，默认 30（上限 90：更长窗口实测会卡住）")
    p_spot.add_argument("--limit", type=int, default=10, help="最多返回地点数 1-40，默认 10")
    p_spot.add_argument("--dist", type=float, default=None, help="（旧参数）等同 --radius")

    p_ebc = sub.add_parser("ebirdcheck", help="验证 eBird Token 并实测本脚本用到的端点")
    p_ebc.add_argument("--token", default=None, help="eBird Token；不传则读环境变量")

    sub.add_parser("selftest", help="检测所有接口可用性")
    return parser


def main(argv=None):
    configure_console()
    args = build_parser().parse_args(argv)
    # 旧参数兼容：--dist / --back 分别映射到 --radius / --days
    if getattr(args, "dist", None):
        args.radius = int(args.dist)
    if getattr(args, "back", None) and args.cmd == "bird" and args.days == 14:
        args.days = max(1, min(int(args.back), 365))
    handlers = {
        "tide": cmd_tide, "sun": cmd_sun, "astro": cmd_astro,
        "glow": cmd_glow, "bird": cmd_bird, "species": cmd_species,
        "birdinfo": cmd_birdinfo, "birdtime": cmd_birdtime,
        "birdspot": cmd_birdspot, "ebirdcheck": cmd_ebirdcheck,
        "selftest": cmd_selftest,
    }
    try:
        rc = handlers[args.cmd](args)
        return rc if isinstance(rc, int) else 0
    except ValueError as e:
        print("x " + str(e), file=sys.stderr)
        return 2
    except TimeoutError as e:
        print("x 请求超时：" + str(e), file=sys.stderr)
        print("  建议：缩小 --radius 或 --days 后重试。", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as e:
        print("x 网络请求失败：HTTP " + str(e.code), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("x 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
