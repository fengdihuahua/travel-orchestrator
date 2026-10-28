#!/usr/bin/env node
// -*- coding: utf-8 -*-
/**
 * nature_forecast.mjs — nature_forecast.py 的 Node 双实现（M3c）
 *
 * 移植范围：tide / sun / astro / glow 四个子命令。
 * 观鸟类（bird/species/birdinfo/birdtime/birdspot）与 selftest 不移植——
 * 遇到时给出诚实提示并以 2 退出（Python 侧能跑，属已声明的非目标）。
 *
 * 数值口径：Python 区分 int/float，JS 只有 number。pyJsonLoads 会把含 ./e 的
 * JSON 字面量包成 PyFloat，凡 Python 语义产 float 处（round(x,n)、/、均值）用 pyF()
 * 补标，pyJsonDumps 按 Python repr 渲染（12.0 -> "12.0"）——这是产物逐字节对齐的前提。
 */
import {
  pyFixed, pyRound, pyStr, pyQuote, pyGet, isDict,
  daysFromCivil, civilFromDays, shiftHours,
  PyFloat, pyF, num, pyJsonDumps, pyJsonLoads,
} from "./pyfmt.mjs";
import { pathToFileURL } from "node:url";

const VERSION = "2.4.0";
const UA = "travel-orchestrator/" + VERSION;
const QUERY_DATE = new Date().toISOString().slice(0, 10);   // UTC 今天（对齐 Python）
const TZ_CN = "Asia/Shanghai";

const HTTP_TIMEOUT = 25;
const HTTP_DEADLINE = 45;
const HTTP_MAX_BYTES = 4 * 1024 * 1024;

const SAFE_NAME = /^[一-龥A-Za-z0-9 \-'.]+$/;

// ---------------------------------------------------------------- 通用工具

// urllib.parse.urlencode：quote_plus（空格 -> +，'_.-~' 之外全编码，大小写十六进制）
export function qs(pairs) {
  const enc = (v) => {
    let s = encodeURIComponent(String(v));
    s = s.replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
    return s.replace(/%20/g, "+");
  };
  return pairs.map(([k, v]) => enc(k) + "=" + enc(v)).join("&");
}

async function fetchJson(url, { headers = {}, timeout = HTTP_TIMEOUT,
                                deadline = HTTP_DEADLINE, maxBytes = HTTP_MAX_BYTES } = {}) {
  const ctrl = new AbortController();
  const started = Date.now();
  const timer = setTimeout(() => ctrl.abort(), timeout * 1000);
  try {
    const resp = await fetch(url, { signal: ctrl.signal,
                                    headers: { "User-Agent": UA, ...headers } });
    if (!resp.ok) throw new Error("HTTP Error " + resp.status + ": " + (resp.statusText || ""));
    const reader = resp.body.getReader();
    const chunks = [];
    let total = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      total += value.length;
      if (total > maxBytes) {
        throw new Error("响应超过 " + Math.floor(maxBytes / 1048576) + " MB 上限（已下载 "
                        + Math.floor(total / 1048576) + " MB），查询范围过大");
      }
      if ((Date.now() - started) / 1000 > deadline) {
        ctrl.abort();
        throw new TimeoutError("请求超过 " + deadline + " 秒总时限（已下载 "
                               + Math.floor(total / 1024) + " KB），通常是因为查询范围过大");
      }
    }
    const body = Buffer.concat(chunks).toString("utf-8");
    return pyJsonLoads(body);
  } finally {
    clearTimeout(timer);
  }
}

class TimeoutError extends Error {}

const PLACE_WARNINGS = [];

function out(title, data, source, note = null, disclaimer = null) {
  console.log("=".repeat(62));
  console.log(title);
  console.log("-".repeat(62));
  console.log(pyJsonDumps(data, 2));
  console.log("-".repeat(62));
  for (const w of PLACE_WARNINGS) console.log("[注意] " + w);
  if (note) console.log(note);
  console.log("数据源: " + source + " | 查询日期: " + QUERY_DATE);
  console.log(disclaimer || "[提示] 天文/海洋预报仅供参考，最终以官方来源（国家海洋信息中心/天文台）为准。");
}

function downgrade(msg, suggestions) {
  console.log("[降级] " + msg);
  for (const s of suggestions) console.log("  -> " + s);
}

export function placeAmbiguity(city, results) {
  if (!results || !results.length) return "";
  const first = results[0];
  const key = pyStr(first["name"] ?? city).trim();
  const region0 = pyStr(first["admin1"] ?? first["country"] ?? "").trim();
  const others = [];
  for (const r of results) {
    if (pyStr(r["name"] ?? "").trim() !== key) continue;
    const region = pyStr(r["admin1"] ?? r["country"] ?? "").trim();
    if (!region || region === region0) continue;
    let lat = 0.0, lng = 0.0;
    try {
      lat = parseFloat(num(r["latitude"]));
      lng = parseFloat(num(r["longitude"]));
    } catch { lat = lng = 0.0; }
    const item = key + "/" + region + " (" + pyFixed(lat, 4) + ", " + pyFixed(lng, 4) + ")";
    if (!others.includes(item)) others.push(item);
  }
  if (!others.length) return "";
  const picked = key + "/" + (region0 || "?");
  return ("城市名「" + city + "」在多个省/国家有同名地点，本次取的是 " + picked
          + "；同名候选：" + others.join("；") + "。"
          + "取错了不会报错，但日出日落、月相、霞光会整体偏移（大同的坑："
          + "山西与黑龙江差 46 分钟）。这类时刻敏感数据请改用 --lat/--lng，"
          + "详见 references/nature-apis.md §6.2。");
}

async function resolvePlace(city = null, lat = null, lng = null) {
  if (lat !== null && lng !== null) {
    const la = num(lat), ln = num(lng);
    if (!(-90 <= la && la <= 90 && -180 <= ln && ln <= 180)) {
      throw new ValueError("经纬度超出合法范围");
    }
    return [pyF(la), pyF(ln), pyStr(la) + "," + pyStr(ln)];
  }
  if (!city) throw new ValueError("需要提供 --city 或同时提供 --lat/--lng");
  if (city.length > 40 || !SAFE_NAME.test(city)) {
    throw new ValueError("城市名含非法字符或过长，只允许中英文/数字/空格/-/./'");
  }
  const url = "https://geocoding-api.open-meteo.com/v1/search?" + qs([
    ["name", city], ["count", 5], ["language", "zh"], ["format", "json"],
  ]);
  const data = await fetchJson(url);
  const results = data["results"] || [];
  if (!results.length) {
    throw new ValueError("未找到城市：" + city + "（建议改用 --lat/--lng 精确指定）");
  }
  const note = placeAmbiguity(city, results);
  if (note) {
    if (!PLACE_WARNINGS.includes(note)) PLACE_WARNINGS.push(note);
    console.error("[注意] " + note);
  }
  const r = results[0];
  let label = r["name"] || city;
  const extra = r["admin1"] || r["country"] || "";
  if (extra) label = label + "/" + extra;
  return [r["latitude"], r["longitude"], label];   // PyFloat（保 float 语义）
}

// ---------------------------------------------------------------- 日期时间

// datetime.fromisoformat 的常用子集 -> {y,m,d,hh,mm,ss,offMin|null}
export function parseIso(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?)?(Z|[+-]\d{2}:?\d{2})?$/
    .exec(String(s));
  if (!m) throw new ValueError("Invalid isoformat string: '" + s + "'");
  let offMin = null;
  if (m[7] === "Z") offMin = 0;
  else if (m[7]) offMin = (m[7][0] === "-" ? -1 : 1)
    * (parseInt(m[7].slice(1, 3), 10) * 60 + parseInt(m[7].slice(-2), 10));
  return { y: +m[1], m: +m[2], d: +m[3],
           hh: m[4] ? +m[4] : 0, mm: m[5] ? +m[5] : 0, ss: m[6] ? parseFloat(m[6]) : 0,
           offMin };
}

function fmtDateTime(dt) {
  const p = (x, n = 2) => String(x).padStart(n, "0");
  return p(dt.y, 4) + "-" + p(dt.m) + "-" + p(dt.d) + " " + p(dt.hh) + ":" + p(dt.mm);
}

function fmtHM(dt) {
  const p = (x) => String(x).padStart(2, "0");
  return p(dt.hh) + ":" + p(dt.mm);
}

// parse_iso_utc_naive：统一换算成 naive UTC，便于比较
export function parseIsoUtcNaive(s) {
  const dt = parseIso(s);
  if (dt.offMin === null) return dt;
  const base = daysFromCivil(dt.y, dt.m, dt.d) * 86400 + dt.hh * 3600 + dt.mm * 60 + dt.ss;
  const total = base - dt.offMin * 60;
  const days = Math.floor(total / 86400);
  let rem = total - days * 86400;
  const hh = Math.floor(rem / 3600);
  rem -= hh * 3600;
  const mm = Math.floor(rem / 60);
  const ss = rem - mm * 60;
  const [y, m, d] = civilFromDays(days);
  return { y, m, d, hh, mm, ss, offMin: null };
}

function epochOf(dt) {
  return daysFromCivil(dt.y, dt.m, dt.d) * 86400 + dt.hh * 3600 + dt.mm * 60 + dt.ss;
}

// shift_hour(iso_str, hours)：parse_iso + timedelta(hours=+) → 'YYYY-MM-DD HH:MM'
// 注意 pyfmt.shiftHours 是减法语义（为星图服务），这里取负对齐加法。
export function shiftHour(isoStr, hours) {
  const dt = parseIso(isoStr);
  const r = shiftHours(dt, -hours);
  return fmtDateTime(r);
}

function todayCn() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

export function daysForDate(dateStr, maxDays = 16) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
    throw new ValueError("time data " + dateStr + " does not match format '%Y-%m-%d'");
  }
  const target = dateStr;
  const today = todayCn();
  const delta = Math.round((Date.parse(target) - Date.parse(today)) / 86400000);
  if (delta < 0) throw new ValueError("日期已过去：" + dateStr);
  const need = delta + 2;
  if (need > maxDays) {
    throw new ValueError("日期 " + dateStr + " 距今 " + delta + " 天，超出 " + maxDays
                         + " 天预报窗口。请临近出行再查，或改用官方潮汐表/当地气象台。");
  }
  return Math.max(1, need);
}

class ValueError extends Error {
  constructor(msg) { super(msg); this.name = "ValueError"; }
}

// ---------------------------------------------------------------- 1. 赶海-潮汐

export function findExtremes(times, heights) {
  const ex = [];
  for (let i = 1; i < heights.length - 1; i++) {
    const a = heights[i - 1], b = heights[i], c = heights[i + 1];
    if (a === null || a === undefined || b === null || b === undefined
        || c === null || c === undefined) continue;
    const av = num(a), bv = num(b), cv = num(c);
    let kind = null;
    if (bv >= av && bv >= cv && (bv > av || bv > cv)) kind = "满潮";
    else if (bv <= av && bv <= cv && (bv < av || bv < cv)) kind = "干潮";
    else continue;
    const denom = av - 2 * bv + cv;
    let off, val;
    if (Math.abs(denom) < 1e-9) { off = 0.0; val = bv; }
    else {
      off = 0.5 * (av - cv) / denom;
      off = Math.max(-0.75, Math.min(0.75, off));
      val = bv - 0.25 * (av - cv) * off;
    }
    ex.push({ type: kind, time: shiftHour(times[i], off), height_m: pyF(pyRound(val, 2)) });
  }
  return ex;
}

async function cmdTide(args) {
  const [lat, lng, label] = await resolvePlace(args.city, args.lat, args.lng);
  if (args.date && !/^\d{4}-\d{2}-\d{2}$/.test(args.date)) {
    throw new ValueError("--date 格式应为 YYYY-MM-DD");
  }
  let days, daysSet;
  if (args.date) {
    days = daysForDate(args.date, 16);
    daysSet = new Set([args.date]);
  } else {
    days = Math.max(1, Math.min(args.days, 7));
    daysSet = null;
  }
  const url = "https://marine-api.open-meteo.com/v1/marine?" + qs([
    ["latitude", pyStr(num(lat))], ["longitude", pyStr(num(lng))],
    ["hourly", "sea_level_height_msl"],
    ["forecast_days", days], ["timezone", TZ_CN],
  ]);
  try {
    const data = await fetchJson(url);
    const h = data["hourly"] || {};
    const times = h["time"] || [];
    const heights = h["sea_level_height_msl"] || [];
    if (!times.length || !heights.length) {
      throw new ValueError("接口未返回潮位数据（该坐标可能非海域）");
    }
    if (daysSet === null) {
      daysSet = new Set(Array.from(new Set(times.map((t) => t.slice(0, 10)))).slice(0, days).sort());
    }
    let lastI = -1;
    for (let i = 0; i < heights.length; i++) {
      if (heights[i] !== null && heights[i] !== undefined) lastI = i;
    }
    const validUntil = lastI >= 0 ? times[lastI] : null;
    const warnings = [];
    if (lastI < times.length - 1) {
      warnings.push("潮位数据仅覆盖至 " + String(validUntil)
                    + "（Open-Meteo Marine 实际有效期约 10 天）；"
                    + "此后的日期请临近出行再查，或改用官方潮汐表。");
    }
    const extremes = findExtremes(times, heights);
    const byDay = {};
    for (const e of extremes) {
      const d = e.time.slice(0, 10);
      (byDay[d] = byDay[d] || []).push(e);
    }
    const report = {
      location: label, lat: pyF(pyRound(num(lat), 4)), lng: pyF(pyRound(num(lng), 4)),
      unit: "m (相对平均海平面 MSL，非当地潮高基准面)",
      data_valid_until: validUntil,
      forecast_days_used: days,
      days: [],
      warnings,
    };
    for (const d of Array.from(daysSet).sort()) {
      const dayIdx = [];
      for (let i = 0; i < times.length; i++) {
        if (times[i].startsWith(d)) dayIdx.push(i);
      }
      const dayVals = dayIdx.map((i) => heights[i]);
      if (!dayIdx.length || dayVals.every((v) => v === null || v === undefined)) {
        report.days.push({
          date: d, extremes: [], best_beachcombing_windows: [],
          partial: true,
          note: "该日期超出潮位数据覆盖范围（至 " + String(validUntil) + "），"
                + "请临近出行再查，或改用官方潮汐表。",
        });
        continue;
      }
      const partial = dayVals.some((v) => v === null || v === undefined);
      const dayEx = byDay[d] || [];
      const windows = [];
      for (const low of dayEx.filter((e) => e.type === "干潮")) {
        const lt = parseIso(low.time.replace(" ", "T"));
        windows.push({
          start: fmtHM(shiftHours(lt, 1.5)),    // lt - 1.5h
          end: fmtHM(shiftHours(lt, -1.5)),     // lt + 1.5h
          low_time: low.time.slice(11), low_height_m: low.height_m,
        });
      }
      const entry = {
        date: d, extremes: dayEx,
        best_beachcombing_windows: windows,
        note: "当日最佳窗口 = 干潮前后 1.5 小时，滩涂裸露最广；涨潮前 1 小时必须撤离",
      };
      if (partial) {
        entry.partial = true;
        entry.note = ("当日潮位数据不完整（覆盖至 " + String(validUntil) + "），"
                      + "极值可能缺失；赶海前必须用官方潮汐表复核，勿依赖本结果。");
      }
      report.days.push(entry);
    }
    for (const w of warnings) console.error("[注意] " + w);
    const title = args.date
      ? "潮汐预报（赶海） - " + label + " - " + args.date
      : "潮汐预报（赶海） - " + label + " - " + days + " 天";
    out(title, report, "Open-Meteo Marine API (marine-api.open-meteo.com)",
        "[提示] 窗口 = 干潮前后 1.5 小时；农历初一/十五前后为大潮(退得远、收获多)；\n"
        + "       海面风力 5 级以上或台风天禁赶海；\n"
        + "       潮位为 MSL 基准，与当地潮汐表基准可能有系统偏差，安全决策请对照官方潮汐表。");
  } catch (e) {
    if (e instanceof ValueError) throw e;
    downgrade("潮汐接口调用失败: " + e.message, [
      "用 web 搜索『城市 潮汐表 日期』，优先潮汐表精灵(eisk.cn)聚合页",
      "提取：满潮时刻 / 干潮时刻 / 潮高 / 推荐赶海时间",
      "推荐赶海窗口 = 干潮前后 1-2 小时；农历初一/十五前后大潮退得远",
    ]);
    process.exit(1);
  }
}

// ---------------------------------------------------------------- 2. 日出日落

async function cmdSun(args) {
  const [lat, lng, label] = await resolvePlace(args.city, args.lat, args.lng);
  const date = args.date || QUERY_DATE;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    console.error("x --date 格式应为 YYYY-MM-DD");
    process.exit(2);
  }
  const url = ("https://api.sunrise-sunset.org/json?lat=" + pyStr(num(lat))
               + "&lng=" + pyStr(num(lng)) + "&date=" + date + "&formatted=0");
  try {
    const data = await fetchJson(url);
    const res = data["results"] || {};
    const payload = {
      location: label, date, times_utc: res,
      note_utc: "以上均为 UTC，需按目的地时区换算；接口亦返回 tzid 可用于校验",
    };
    out("日出日落 - " + label + " - " + date + " (UTC)", payload,
        "api.sunrise-sunset.org",
        "[提示] 黄金时刻 ~ 日出后/日落前 1 小时；蓝调时刻 ~ 日出前/日落后 20-40 分钟；\n"
        + "       云海日出需前一天降水 + 次日晨晴；日出前 30 分钟到点占位。");
  } catch (e) {
    downgrade("日出日落接口调用失败: " + e.message, [
      "用 web 搜索『目的地 + 日出时间 + 日期』",
    ]);
    process.exit(1);
  }
}

// ---------------------------------------------------------------- 3. 观星

async function cmdAstro(args) {
  const [lat, lng, label] = await resolvePlace(args.city, args.lat, args.lng);
  const at = args.at || new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const url = ("https://www.cyclecalcs.com/v2/today?lat=" + pyStr(num(lat))
               + "&lon=" + pyStr(num(lng)) + "&at=" + pyQuote(at, "/"));
  try {
    const data = await fetchJson(url);
    const d = data["data"] || data;
    const moon = d["moon"] || {};
    const night = d["night"] || {};
    const phase = moon["phase"] || {};
    const summary = {
      location: label,
      queried_at_utc: at,
      moon_phase: phase["name"] ?? null,
      moon_illumination_percent: phase["illumination_percent"] ?? null,
      moon_age_days: phase["age_days"] ?? null,
      constellation: (moon["constellation"] || {})["name"] ?? null,
      dark_window_utc: { start: night["dark_start"] ?? null, end: night["dark_end"] ?? null },
      dark_minutes: night["dark_minutes"] ?? null,
      moonless_dark_minutes: night["moonless_dark_minutes"] ?? null,
      night_verdict: night["verdict"] ?? null,
      planets_up: d["planets_up"] ?? null,
      next_events: d["next_events"] ?? null,
    };
    out("观星情报 - " + label + " @ " + at + " (UTC)", summary, "cyclecalcs.com",
        "[提示] moonless_dark_minutes 是最关键指标——无月暗夜时长，越长越适合深空/银河；\n"
        + "       新月前后 7 天最佳；月照>50% 深空受影响；银河 3-10 月（夏季最亮）；\n"
        + "       流星雨：英仙座 8 月中、双子座 12 月中、象限仪座 1 月初。");
  } catch (e) {
    downgrade("观星接口调用失败: " + e.message, [
      "用 web 搜索『目的地 + 月相 + 光污染』",
      "查询 lightpollutionmap.info（波特尔暗空分级 1-9，1 级最佳）",
    ]);
    process.exit(1);
  }
}

// ---------------------------------------------------------------- 4. 火烧云

export function scoreGlow(low, mid, high, visKm, rh, aod) {
  let score = 0;
  const reasons = [];
  if (mid !== null && high !== null) {
    const mh = (mid + high) / 2.0;
    if (20 <= mh && mh <= 75) {
      score += 1;
      reasons.push("中高云 " + pyStr(pyRound(mh)) + "%（适中，有可被照亮的云层）");
    } else if (mh < 20) {
      reasons.push("中高云 " + pyStr(pyRound(mh)) + "%（偏少，云层不足以形成大范围霞光）");
    } else {
      reasons.push("中高云 " + pyStr(pyRound(mh)) + "%（偏厚，低角度阳光可能被遮）");
    }
  }
  if (low !== null) {
    if (low <= 30) {
      score += 1;
      reasons.push("低云 " + pyStr(pyRound(low)) + "%（通透，阳光可达云底）");
    } else if (low <= 60) {
      reasons.push("低云 " + pyStr(pyRound(low)) + "%（偏多，存在遮挡地平线的风险）");
    } else {
      score -= 1;
      reasons.push("低云 " + pyStr(pyRound(low)) + "%（过厚，霞光基本被挡）");
    }
  }
  if (visKm !== null) {
    if (visKm >= 15) {
      score += 1;
      reasons.push("能见度 " + pyStr(pyRound(visKm)) + "km（空气通透）");
    } else if (visKm < 8) {
      score -= 1;
      reasons.push("能见度 " + pyStr(pyRound(visKm)) + "km（有霾，色彩会发灰）");
    }
  }
  if (rh !== null) {
    if (40 <= rh && rh <= 85) {
      reasons.push("相对湿度 " + pyStr(pyRound(rh)) + "%（水汽适中）");
    } else if (rh > 90) {
      score -= 1;
      reasons.push("相对湿度 " + pyStr(pyRound(rh)) + "%（过高，易起雾或低云）");
    }
  }
  if (aod !== null) {
    if (0.10 <= aod && aod <= 0.40) {
      score += 1;
      reasons.push("气溶胶光学厚度 " + pyFixed(aod, 2) + "（利于散射出红橙色）");
    } else if (aod > 0.60) {
      reasons.push("气溶胶光学厚度 " + pyFixed(aod, 2) + "（空气偏浑浊，颜色发暗）");
    }
  }
  score = Math.max(0, Math.min(3, Math.trunc(score)));
  return [score, reasons];
}

const GLOW_LABEL = { 0: "基本无霞光", 1: "一般", 2: "较好", 3: "绝佳" };

export function avgWindow(times, values, centerIso, halfHours = 1) {
  if (!times || !times.length || !values || !values.length) return null;
  const c = epochOf(parseIsoUtcNaive(centerIso));
  const picked = [];
  for (let i = 0; i < Math.min(times.length, values.length); i++) {
    const v = values[i];
    if (v === null || v === undefined) continue;
    if (Math.abs(epochOf(parseIsoUtcNaive(times[i])) - c) <= halfHours * 3600) {
      picked.push(num(v));
    }
  }
  if (!picked.length) return null;
  return picked.reduce((a, b) => a + b, 0) / picked.length;
}

async function cmdGlow(args) {
  const [lat, lng, label] = await resolvePlace(args.city, args.lat, args.lng);
  if (args.date && !/^\d{4}-\d{2}-\d{2}$/.test(args.date)) {
    throw new ValueError("--date 格式应为 YYYY-MM-DD");
  }
  const fdays = args.date ? daysForDate(args.date, 16) : 3;
  const sunUrl = ("https://api.sunrise-sunset.org/json?lat=" + pyStr(num(lat))
                  + "&lng=" + pyStr(num(lng)) + "&date=" + (args.date || QUERY_DATE)
                  + "&formatted=0");
  let sun;
  try {
    sun = (await fetchJson(sunUrl))["results"] || {};
  } catch (e) {
    downgrade("无法获取日出日落时刻: " + e.message, ["请先确认 --date 与网络，或改用 web 搜索"]);
    process.exit(1);
  }
  const event = args.event;
  let centerUtc, labelCn;
  if (event === "set") { centerUtc = sun["sunset"]; labelCn = "日落（晚霞）"; }
  else { centerUtc = sun["sunrise"]; labelCn = "日出（朝霞）"; }
  if (!centerUtc) {
    console.error("x 未能取得日出/日落时刻");
    process.exit(1);
  }
  const wxUrl = "https://api.open-meteo.com/v1/forecast?" + qs([
    ["latitude", pyStr(num(lat))], ["longitude", pyStr(num(lng))],
    ["hourly", "cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,relative_humidity_2m"],
    ["forecast_days", fdays], ["timezone", "UTC"],
  ]);
  const aqUrl = "https://air-quality-api.open-meteo.com/v1/air-quality?" + qs([
    ["latitude", pyStr(num(lat))], ["longitude", pyStr(num(lng))],
    ["hourly", "aerosol_optical_depth"],
    ["forecast_days", fdays], ["timezone", "UTC"],
  ]);
  try {
    const wx = (await fetchJson(wxUrl))["hourly"] || {};
    const times = wx["time"] || [];
    if (!times.length) throw new ValueError("天气接口未返回数据");
    const low = avgWindow(times, wx["cloud_cover_low"] || [], centerUtc);
    const mid = avgWindow(times, wx["cloud_cover_mid"] || [], centerUtc);
    const high = avgWindow(times, wx["cloud_cover_high"] || [], centerUtc);
    const vis = avgWindow(times, wx["visibility"] || [], centerUtc);
    const rh = avgWindow(times, wx["relative_humidity_2m"] || [], centerUtc);
    const visKm = vis !== null ? vis / 1000.0 : null;
    let aod = null;
    try {
      const aq = (await fetchJson(aqUrl))["hourly"] || {};
      aod = avgWindow(aq["time"] || [], aq["aerosol_optical_depth"] || [], centerUtc);
    } catch { aod = null; }
    const [score, reasons] = scoreGlow(low, mid, high, visKm, rh, aod);
    const payload = {
      location: label,
      date: args.date || QUERY_DATE,
      event: labelCn,
      reference_time_utc: centerUtc,
      window_used: "中心时刻前后各 1 小时平均",
      inputs: {
        cloud_cover_low_pct: low !== null ? pyF(pyRound(low, 1)) : null,
        cloud_cover_mid_pct: mid !== null ? pyF(pyRound(mid, 1)) : null,
        cloud_cover_high_pct: high !== null ? pyF(pyRound(high, 1)) : null,
        visibility_km: visKm !== null ? pyF(pyRound(visKm, 1)) : null,
        relative_humidity_pct: rh !== null ? pyF(pyRound(rh, 1)) : null,
        aerosol_optical_depth: aod !== null ? pyF(pyRound(aod, 3)) : null,
      },
      glow_score: score,
      glow_level: GLOW_LABEL[score],
      reasons,
    };
    out("火烧云/霞光预报（本地模型） - " + label + " - "
        + (args.date || QUERY_DATE) + " " + labelCn,
        payload,
        "Open-Meteo Forecast + Air Quality API（自建评分模型）",
        "[说明] sunsetbot.top 已于 2026-09 下线，本模型改用公开气象数据自建评分：\n"
        + "       高空有中高云 + 低空通透 + 水汽适中 + 适度气溶胶 -> 霞光概率高。\n"
        + "       评分为 0-3 级参考值，非官方发布，出行前建议结合当地实景判断。");
  } catch (e) {
    if (e instanceof ValueError) throw e;
    downgrade("火烧云模型计算失败: " + e.message, [
      "用 web 搜索『城市 + 火烧云/晚霞 预报』",
      "或用彩云天气 App / 当地气象台的霞光指数",
    ]);
    process.exit(1);
  }
}

// ---------------------------------------------------------------- CLI

const UNPORTED = new Set(["bird", "species", "birdinfo", "birdtime", "birdspot", "selftest"]);

function usage() {
  return ("用法: node js/nature_forecast.mjs {tide|sun|astro|glow} [--city 名 | --lat X --lng Y]\n"
          + "       [--date YYYY-MM-DD] [--days N] [--at ISO] [--event set|rise]");
}

function parseArgs(argv) {
  const cmd = argv[0];
  if (!cmd) { console.error(usage()); process.exit(2); }
  if (UNPORTED.has(cmd)) {
    console.error("x 子命令 " + cmd + " 的 Node 实现未移植（观鸟类查询请用 Python 宿主"
                  + "或 scripts/nature_forecast.py），属已声明的非目标");
    process.exit(2);
  }
  if (!["tide", "sun", "astro", "glow"].includes(cmd)) {
    console.error(usage());
    process.exit(2);
  }
  const args = { cmd, city: null, lat: null, lng: null, date: null,
                 days: 3, at: null, event: "set" };
  for (let i = 1; i < argv.length; i++) {
    const k = argv[i];
    const need = (name) => {
      if (i + 1 >= argv.length) { console.error("x 参数 " + name + " 缺少值"); process.exit(2); }
      return argv[++i];
    };
    if (k === "--city") args.city = need(k);
    else if (k === "--lat") { const v = parseFloat(need(k)); if (Number.isNaN(v)) { console.error("x --lat 需要数字"); process.exit(2); } args.lat = v; }
    else if (k === "--lng") { const v = parseFloat(need(k)); if (Number.isNaN(v)) { console.error("x --lng 需要数字"); process.exit(2); } args.lng = v; }
    else if (k === "--date") args.date = need(k);
    else if (k === "--days") { const v = parseInt(need(k), 10); if (Number.isNaN(v)) { console.error("x --days 需要整数"); process.exit(2); } args.days = v; }
    else if (k === "--at") args.at = need(k);
    else if (k === "--event") { const v = need(k); if (!["set", "rise"].includes(v)) { console.error("x --event 只能是 set 或 rise"); process.exit(2); } args.event = v; }
    else { console.error("x 未知参数：" + k); console.error(usage()); process.exit(2); }
  }
  return args;
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  try {
    if (args.cmd === "tide") await cmdTide(args);
    else if (args.cmd === "sun") await cmdSun(args);
    else if (args.cmd === "astro") await cmdAstro(args);
    else if (args.cmd === "glow") await cmdGlow(args);
    return 0;
  } catch (e) {
    if (e instanceof ValueError) { console.error("x " + e.message); return 2; }
    if (e instanceof TimeoutError) {
      console.error("x 请求超时：" + e.message);
      console.error("  建议：缩小 --radius 或 --days 后重试。");
      return 1;
    }
    console.error("x " + (e && e.message ? e.message : String(e)));
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().then((rc) => process.exit(rc));
}
