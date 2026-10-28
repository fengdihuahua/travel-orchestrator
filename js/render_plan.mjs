// render_plan.mjs · scripts/render_plan.py 的 Node 移植（MD/HTML 输出逐字节对齐）
// 从同一份行程数据渲染 Markdown 攻略 + 单文件 HTML 手账（离线、无脚本、无远程资源）。
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import {
  escapeHtml as e, pyQuote, pyPercent, pyG, pyFixed, pyStr, pyGet, pyTruthy, pyOr, isDict,
  pyLen, pySlice, pyRound, nowStamp,
} from "./pyfmt.mjs";
import * as nw from "./nature_widgets.mjs";
import { resolveTrip } from "./shards.mjs";
import { validate } from "./validate.mjs";
import {
  FALLBACK_CSS, ICON_SVG, STICKER, MOTIF, KIND_LABEL, KIND_CLASS,
  STATUS_LABEL, PALETTE, NATURE_THEME, SECTION_STICKER, NATURE_STICKER,
  SECTION_NAV,
} from "./pydata.mjs";

const VERSION = "3.11.0"; // 与 scripts/render_plan.py 同口径（仅诊断用，不进产物）
const ATTR_LINE = "由 旅行规划总编排（travel-orchestrator）生成";

const BOOKING_MD = { confirmed: "已订", not_required: "无需预约", todo: "待预约", unavailable: "约不到" };
const FACT_STATUS = { verified: "已核实", estimated: "估算", unverified: "未核实", unknown: "未查到" };
const PITFALL_MD = { high: "🔴", medium: "🟡" };
const PITFALL_HTML = { high: "容易出事", medium: "容易踩" };

export class TripDataError extends Error {}

// ---------------------------------------------------------------- 小工具

function g(d, ...keys) {
  const default_ = keys.length && typeof keys[keys.length - 1] === "object" && keys[keys.length - 1] !== null && "default" in keys[keys.length - 1] ? keys.pop().default : undefined;
  let cur = d;
  for (const k of keys) {
    if (!isDict(cur)) return default_;
    cur = pyGet(cur, k, null);
    if (cur === null || cur === undefined) return default_;
  }
  return cur === undefined ? default_ : cur;
}

function dict(v) { return isDict(v) ? v : {}; }
function objs(v) { return (v || []).filter(isDict); }
function geoOf(i) { const geo = pyGet(i, "geo", null); return isDict(geo) ? geo : {}; }

// len(...) 的多态口径：str 按码点、list 按长度、dict 按键数（与 Python len 对齐）
function pyLenAny(v) {
  if (typeof v === "string") return pyLen(v);
  if (Array.isArray(v)) return v.length;
  if (isDict(v)) return Object.keys(v).length;
  return 0;
}

function tideRow(p) {
  if (isDict(p)) {
    const tm = pyOr(pyGet(p, "t", null), pyGet(p, "time", null));
    let h = pyGet(p, "h", null);
    if (h === null || h === undefined) h = pyGet(p, "height_m", null);
    return [tm, nw.num(h)];
  }
  if (Array.isArray(p) && p.length >= 2) return [p[0], nw.num(p[1])];
  return [null, null];
}

function asItems(v) {
  if (v === null || v === undefined || v === "") return [];
  if (Array.isArray(v)) return [...v];
  if (typeof v === "string") return Array.from(v); // Python list("abc") = ['a','b','c']
  return [v];
}

function texts(v) {
  return asItems(v).filter((x) => x !== null && x !== undefined && x !== "").map((x) => pyStr(x));
}

function labels(v, ...keys) {
  const out = [];
  for (const x of v || []) {
    let t;
    if (isDict(x)) {
      t = undefined;
      for (const k of keys) {
        if (pyTruthy(pyGet(x, k, null))) { t = pyGet(x, k, null); break; }
      }
    } else {
      t = x;
    }
    t = pyStr(t || "").trim();
    if (t) out.push(t);
  }
  return out;
}

function money(v, cur = "CNY") {
  if (typeof v === "number") return pyPercent("%g %s", pyG(v), cur);
  return "待确认";
}

function sub(d, key) {
  const v = pyGet(d, key, null);
  return isDict(v) ? v : {};
}

function mapUrl(place) { return nw.mapUrl(place); }
function mapLink(place) { return nw.mapLink(place); }

function mdPlaces(value) {
  if (!pyTruthy(value)) return "未查到";
  const items = Array.isArray(value) ? value : nw.splitPlaces(value);
  const out = [];
  for (const item of items) {
    let name, note;
    if (isDict(item)) {
      name = pyOr(pyGet(item, "name", null), "");
      note = pyOr(pyGet(item, "note", null), "");
    } else {
      name = pyStr(item); note = "";
    }
    if (!pyTruthy(name)) continue;
    const link = pyPercent("[%s](%s)", pyStr(name), mapUrl(nw.searchKeyword(name)));
    out.push(link + (note ? pyPercent("（%s）", note) : ""));
  }
  return out.length ? out.join("、") : "未查到";
}

function blankItem(i) {
  return !["title", "time", "place", "notes", "kind"].some(
    (k) => pyStr(pyOr(pyGet(i, k, null), "")).trim());
}

function shortDate(value) {
  const s = pyStr(value ?? "");
  const m = /^\d{4}-(\d{2}-\d{2})/.exec(s);
  return m ? m[1] : s;
}

function dayAnchor(day, idx) {
  const raw = pyStr(pyOr(pyGet(day, "day_id", null), "D" + pyStr(idx)));
  return "day-" + raw.replace(/[^0-9A-Za-z_-]/g, "-");
}

// ---------------------------------------------------------------- Markdown

export function mdFromTrip(trip) {
  const req = dict(pyGet(trip, "request", null));
  const it = dict(pyGet(trip, "itinerary", null));
  const ver = pyGet(trip, "plan_version", 1);
  const status = pyOr(pyGet(trip, "status", null), "draft");
  const L = [];

  L.push("# " + pyStr(pyOr(pyGet(trip, "trip_id", null), "旅行方案")));
  L.push("");
  L.push("> 方案状态：**" + pyStr(pyGet(STATUS_LABEL, status, status) ?? status) + "**（`" + pyStr(status) + "`）"
    + (pyTruthy(pyGet(trip, "status_note", null)) ? " · " + pyStr(pyGet(trip, "status_note", null)) : ""));
  L.push("> 版本 v" + pyStr(ver) + " · 生成于 " + nowStamp());
  L.push("");
  L.push("**使用须知**");
  L.push("");
  let nature = pyOr(pyGet(it, "nature", null), {});
  const changing = ["时间", "价格", "预约"];
  if (pyTruthy(sub(nature, "beachcombing"))) changing.push("潮汐");
  if (pyTruthy(sub(nature, "stargazing"))) changing.push("月相");
  if (pyTruthy(sub(nature, "sunrise"))) changing.push("云量");
  changing.push("天气");
  const focus = ["景区预约"];
  for (const [key, fLabel] of [["beachcombing", "赶海潮汐"], ["sunrise", "日出日落"],
    ["stargazing", "月相与云量"], ["birding", "鸟况"]]) {
    if (pyTruthy(sub(nature, key))) focus.push(fLabel);
  }
  L.push("方案里的" + changing.join("、") + "都可能变。出发前请对照官方渠道复核一遍，"
    + "尤其是" + focus.slice(0, 4).join("、") + "。查不到的信息这里会写「未查到」，"
    + "不会用估计值凑数。");
  L.push("");

  // ---- 一页速览
  L.push("## 一页速览");
  L.push("");
  const origin = pyOr(g(req, "origin", "name"), "未定");
  const ds = pyOr(g(req, "dates", "start"), "?");
  const de = pyOr(g(req, "dates", "end"), "?");
  const budgetReq = dict(pyGet(req, "budget", null));
  L.push("| 项目 | 内容 |");
  L.push("|---|---|");
  L.push("| 出发地 | " + pyStr(origin) + " |");
  L.push("| 日期 | " + pyStr(ds) + " → " + pyStr(de) + " |");
  L.push("| 同行 | " + pyStr(pyOr(pyGet(req, "companions", null), "未定")) + " |");
  const modeRaw = pyGet(budgetReq, "mode", null);
  const modeTxt = modeRaw === "total" ? "总额" : modeRaw === "per_person" ? "人均" : "口径未定";
  L.push("| 预算 | " + money(pyGet(budgetReq, "amount_max", null), pyOr(pyGet(budgetReq, "currency", null), "CNY"))
    + "（" + modeTxt + "） |");
  L.push("| 节奏 | " + pyStr(pyOr(pyGet(req, "pace", null), "未定")) + " |");
  const interests = dict(pyGet(req, "interests", null));
  const must = labels(pyGet(interests, "must", null), "label", "name").join("、");
  const prefer = labels(pyGet(interests, "prefer", null)).join("、");
  if (must || prefer) L.push("| 专项偏好 | " + prefText(must, prefer) + " |");
  const why = pyStr(pyOr(pyGet(req, "why", null), "")).trim();
  if (why) L.push("| 这趟的主题 | " + why + " |");
  const consRaw = pyOr(pyGet(req, "constraints", null), []);
  const cons = (typeof consRaw === "string" ? Array.from(consRaw) : consRaw)
    .map((c) => pyStr(c).trim()).filter((c) => c);
  if (cons.length) L.push("| 硬约束 | " + cons.join("；") + " |");
  L.push("");

  // ---- 天气研判
  const weather = isDict(pyGet(it, "weather", null)) ? pyGet(it, "weather", null) : {};
  L.push("## 天气研判");
  L.push("");
  const wxEntries = objs(pyGet(weather, "entries", null));
  if (pyGet(weather, "kind", null) === "forecast" && wxEntries.length) {
    for (const w of wxEntries) {
      const src = pyTruthy(pyGet(w, "source_id", null)) ? "（" + pyStr(pyGet(w, "source_id", null)) + "）" : "";
      L.push("- " + pyStr(pyGet(w, "date", "")) + "：" + pyStr(pyGet(w, "summary", "")) + src);
    }
  } else {
    L.push("- " + pyStr(pyOr(pyGet(weather, "summary", null), "规划期气候参考。出发前请查逐日预报。")));
  }
  L.push("");

  // ---- 自然观察专项
  nature = isDict(pyGet(it, "nature", null)) ? pyGet(it, "nature", null) : {};
  if (Object.keys(nature).length) {
    L.push("## 自然观察专项");
    L.push("");
    const b = sub(nature, "birding");
    if (Object.keys(b).length) {
      L.push("### 观鸟");
      L.push("");
      const speciesRaw = pyOr(pyGet(b, "species", null), []);
      const species = typeof speciesRaw === "string" ? Array.from(speciesRaw) : speciesRaw;
      const rich = species.filter(isDict);
      if (rich.length) {
        L.push("| 鸟种 | 学名 | 保护等级 | 本地最佳月份 | 图鉴 |");
        L.push("|---|---|---|---|---|");
        for (const x of rich) {
          let ranks = pyOr(pyGet(x, "status", null), pyGet(x, "保护等级", null), []);
          if (typeof ranks === "string") ranks = [ranks];
          const rk = ranks.map((r) => isDict(r)
            ? pyStr(pyGet(r, "rank", "")) + (pyTruthy(pyGet(r, "source", null)) ? pyPercent("（%s）", pyGet(r, "source", null)) : "")
            : pyStr(r)).join("；") || "未查到";
          let months = pyOr(pyGet(x, "best_months", null), pyGet(x, "最佳月份", null), "未查到");
          if (Array.isArray(months)) months = months.map((m) => pyStr(m)).join("、");
          const url = pyOr(pyGet(x, "taxa_url", null), pyGet(x, "species_url", null), "");
          L.push(pyPercent("| %s | %s | %s | %s | %s |", pyGet(x, "name", ""), pyOr(pyGet(x, "sci", null), "—"),
            rk, months, url ? pyPercent("[图鉴](%s)", url) : "—"));
        }
        L.push("");
        for (const x of rich) {
          if (pyTruthy(pyGet(x, "intro", null))) {
            const src = pyTruthy(pyGet(x, "intro_source", null))
              ? pyPercent("（来源：%s）", pyGet(x, "intro_source", null)) : "";
            L.push(pyPercent("- **%s**：%s%s", pyGet(x, "name", ""), pyGet(x, "intro", null), src));
          }
        }
      }
      const plain = species.filter((x) => !isDict(x)).map((x) => pyStr(x));
      if (plain.length) {
        L.push(pyPercent("- 近期可见鸟种：%s", plain.join("、")));
        L.push(pyPercent("  - 查图：[iNaturalist](https://www.inaturalist.org/search?q=%s)｜"
          + "[图片搜索](https://image.baidu.com/search/index?tn=baiduimage&word=%s)", plain[0], plain[0]));
      }
      if (pyTruthy(pyGet(b, "hotspots", null))) {
        L.push(pyPercent("- 观察点：%s", mdPlaces(pyGet(b, "hotspots", null))));
      }
      L.push(pyPercent("- 最佳时段：%s（正午鸟少，别硬等）", pyOr(pyGet(b, "best_time", null), "日出后 2 小时")));
      if (pyTruthy(pyGet(b, "notes", null))) L.push(pyPercent("- %s", pyGet(b, "notes", null)));
      L.push("");
    }
    const s = sub(nature, "stargazing");
    if (Object.keys(s).length) {
      L.push("### 观星");
      L.push("");
      if (pyTruthy(pyGet(s, "moon_phase", null))) L.push(pyPercent("- 月相：%s", pyGet(s, "moon_phase", null)));
      if (pyTruthy(pyGet(s, "dark_window", null))) L.push(pyPercent("- 暗夜窗口：%s", pyGet(s, "dark_window", null)));
      if (pyTruthy(pyGet(s, "planets", null))) {
        const pl = asItems(pyGet(s, "planets", null)).map((p) => isDict(p) ? pyStr(pyGet(p, "name", "")) : pyStr(p));
        L.push(pyPercent("- 可见行星：%s", pl.filter((x) => x).join("、") || "—"));
      }
      if (pyTruthy(pyGet(s, "rating", null))) L.push(pyPercent("- 观星条件：%s", pyGet(s, "rating", null)));
      if (pyTruthy(pyGet(s, "events", null))) L.push(pyPercent("- 近期天文事件：%s", texts(pyGet(s, "events", null)).join("；")));
      for (const t of objs(pyGet(s, "targets", null))) {
        if (isDict(t)) {
          L.push(pyPercent("- 目标：%s%s%s", pyGet(t, "name", ""),
            pyTruthy(pyGet(t, "type", null)) ? pyPercent("（%s）", pyGet(t, "type", null)) : "",
            pyTruthy(pyOr(pyGet(t, "note", null), pyGet(t, "how", null)))
              ? pyPercent(" —— %s", pyOr(pyGet(t, "note", null), pyGet(t, "how", null))) : ""));
        }
      }
      const cfg = isDict(pyGet(s, "chart", null)) ? pyGet(s, "chart", null) : {};
      if (Object.keys(cfg).length) {
        const latN = nw.num(pyGet(cfg, "lat", null)), lngN = nw.num(pyGet(cfg, "lng", null));
        L.push(pyPercent("- 星图：%s 内嵌示意星图（按 %.2f°N %.2f°E、%s 当地时刻算地平坐标，"
          + "只画地平线以上的亮星；未做大气折射与光污染修正）。"
          + "精确星图见 [Stellarium Web](https://stellarium-web.org/)。",
          pyTruthy(pyGet(s, "spots", null)) ? mdPlaces(pyGet(s, "spots", null)) : "见攻略页",
          latN === null ? 0.0 : latN, lngN === null ? 0.0 : lngN,
          pyOr(pyGet(cfg, "at", null), "")));
      }
      L.push("");
    }
    const r = sub(nature, "sunrise");
    const gl = sub(nature, "glow");
    if (Object.keys(r).length) {
      L.push("### 日出日落");
      L.push("");
      if (pyTruthy(pyGet(r, "sunrise", null)) || pyTruthy(pyGet(r, "sunset", null))) {
        L.push(pyPercent("- 日出 %s｜日落 %s", pyOr(pyGet(r, "sunrise", null), "未查到"),
          pyOr(pyGet(r, "sunset", null), "未查到")));
      }
      if (pyTruthy(pyGet(r, "golden_hour", null))) L.push(pyPercent("- 黄金时刻：%s", pyGet(r, "golden_hour", null)));
      if (pyTruthy(pyGet(r, "blue_hour", null))) L.push(pyPercent("- 蓝调时刻：%s", pyGet(r, "blue_hour", null)));
      if (pyTruthy(pyGet(r, "best_spot", null))) L.push(pyPercent("- 观测点：%s", mdPlaces(pyGet(r, "best_spot", null))));
      if (pyGet(gl, "score", null) !== null && pyGet(gl, "score", null) !== undefined) {
        L.push(pyPercent("- 霞光（火烧云）指数：%s/3 %s", pyGet(gl, "score", null), pyOr(pyGet(gl, "level", null), "")));
        L.push(pyPercent("  - 估算依据：%s", texts(pyGet(gl, "reasons", null)).join("；")));
        L.push("  - 这是按云量分层、通透度、水汽算出的参考值，不是官方预报。");
      }
      L.push("");
    } else if (Object.keys(gl).length) {
      L.push("### 朝晚霞");
      L.push("");
      if (pyTruthy(pyGet(gl, "rating", null))) L.push(pyPercent("- 预报：%s", pyGet(gl, "rating", null)));
      L.push("- 说明：这是按云量、通透度、水汽、气溶胶算出的参考值，不是官方预报。");
      L.push("");
    }
    const t = sub(nature, "beachcombing");
    if (Object.keys(t).length) {
      L.push("### 赶海");
      L.push("");
      if (pyTruthy(pyGet(t, "tide_summary", null))) L.push(pyPercent("- 潮汐：%s", pyGet(t, "tide_summary", null)));
      if (pyTruthy(pyGet(t, "best_window", null))) L.push(pyPercent("- 赶海窗口：%s", pyGet(t, "best_window", null)));
      if (pyTruthy(pyGet(t, "tide_phase", null))) L.push(pyPercent("- 潮期：%s", pyGet(t, "tide_phase", null)));
      if (pyTruthy(pyGet(t, "spots", null))) L.push(pyPercent("- 地点：%s", mdPlaces(pyGet(t, "spots", null))));
      if (pyTruthy(pyGet(t, "safety", null))) L.push(pyPercent("- ⚠️ %s", pyGet(t, "safety", null)));
      const curve = Array.isArray(pyGet(t, "curve", null)) ? pyGet(t, "curve", null) : [];
      if (curve.length >= 3) {
        L.push(pyPercent("- 逐时潮位（%s，点选看每小时能不能下滩见攻略页）：", pyOr(pyGet(t, "unit", null), "潮位")));
        L.push("");
        L.push("| 时刻 | 潮位 |");
        L.push("|---|---|");
        for (const p of curve) {
          const [tm, h] = tideRow(p);
          if (h === null) continue;
          L.push(pyPercent("| %s | %+.2f m |", pyStr(tm).slice(-5), h));
        }
        for (const ex of objs(pyGet(t, "extremes", null))) {
          if (pyTruthy(pyGet(ex, "type", null))) {
            let h = nw.num(pyGet(ex, "height_m", null));
            if (h === null) h = nw.num(pyGet(ex, "h", null));
            if (h === null) continue;
            L.push(pyPercent("| %s | %+.2f m（%s） |", pyStr(pyGet(ex, "time", null)).slice(-5), h, pyGet(ex, "type", null)));
          }
        }
        L.push("");
      }
      L.push("");
    }
  }

  // ---- 逐日行程
  L.push("## 逐日行程");
  L.push("");
  L.push(...mdRoute(it));
  for (const d of objs(pyGet(it, "days", null))) {
    let head = "### " + pyStr(pyGet(d, "day_id", "")) + " · " + pyStr(pyGet(d, "date", ""));
    if (pyTruthy(pyGet(d, "weekday", null))) head += "（" + pyStr(pyGet(d, "weekday", null)) + "）";
    if (pyTruthy(pyGet(d, "title", null))) head += " — " + pyStr(pyGet(d, "title", null));
    L.push(head);
    L.push("");
    for (const i of objs(pyGet(d, "items", null))) {
      if (blankItem(i)) continue;
      const kind = pyGet(KIND_LABEL, pyGet(i, "kind", null), "");
      const tag = kind ? "`" + kind + "` " : "";
      const place = pyTruthy(pyGet(i, "place", null))
        ? " @ [" + pyStr(pyGet(i, "place", null)) + "](" + mapUrl(pyGet(i, "place", null)) + ")" : "";
      const st = pyGet(i, "booking_status", null);
      let stTxt = Object.prototype.hasOwnProperty.call(BOOKING_MD, st) ? BOOKING_MD[st] : (pyTruthy(st) ? st : "");
      stTxt = stTxt ? "（" + stTxt + "）" : "";
      L.push("- **" + pyStr(pyOr(pyGet(i, "time", null), "")) + "** " + tag + pyStr(pyGet(i, "title", "")) + place + stTxt);
      if (pyTruthy(pyGet(i, "notes", null))) L.push("  - " + pyStr(pyGet(i, "notes", null)));
    }
    for (const a of objs(pyGet(d, "alternatives", null))) {
      L.push("- *备选*：" + pyStr(pyGet(a, "title", "")) + "｜触发条件：" + pyStr(pyOr(pyGet(a, "trigger", null), "未写")));
    }
    L.push("");
  }

  // ---- 美食推荐
  const food = objs(pyGet(it, "food", null));
  if (food.length) {
    L.push("## 美食推荐");
    L.push("");
    L.push("| 店/摊 | 人均 | 为什么去 | 备注 |");
    L.push("|---|---|---|---|");
    for (const f of food) {
      const nm = pyStr(pyGet(f, "name", ""));
      const kw = pyOr(pyGet(f, "search", null), nm);
      const bks = nw.bookingLinks(kw, pyOr(pyGet(f, "booking_kind", null), "food"), f);
      const cell = nm + (bks.length ? "（" + bks.map(([t, u]) => pyPercent("[%s](%s)", t, u)).join(" / ") + "）" : "");
      L.push(pyPercent("| %s | %s | %s | %s |", cell, pyStr(pyGet(f, "price", "未查到")),
        pyStr(pyGet(f, "why", "")), pyStr(pyOr(pyGet(f, "note", null), "—"))));
    }
    L.push("");
    L.push("> 人均是平台实时价，会变；点平台入口看当天报价，本方案不写死价格。");
    L.push("");
  }

  // ---- 预算明细
  const budgetIt = isDict(pyGet(it, "budget", null)) ? pyGet(it, "budget", null) : {};
  const cats = objs(pyGet(budgetIt, "categories", null));
  if (cats.length) {
    const cur = pyOr(pyGet(budgetIt, "currency", null), "CNY");
    L.push("## 预算明细");
    L.push("");
    L.push(pyPercent("| 分项 | 区间（%s） |", cur));
    L.push("|---|---|");
    let loSum = 0.0, hiSum = 0.0;
    for (const c of cats) {
      const lo = nw.num(pyGet(c, "min", null)), hi = nw.num(pyGet(c, "max", null));
      if (lo !== null && hi !== null) {
        loSum += lo;
        hiSum += hi;
        L.push(pyPercent("| %s | %g – %g |", pyGet(c, "name", ""), pyG(lo), pyG(hi)));
      } else {
        L.push(pyPercent("| %s | 未查到 |", pyGet(c, "name", "")));
      }
    }
    if (loSum || hiSum) L.push(pyPercent("| **合计** | **%g – %g** |", pyG(loSum), pyG(hiSum)));
    const cap = nw.num(g(req, "budget", "amount_max"));
    if (cap !== null && hiSum > cap) {
      L.push("");
      L.push(pyPercent("> 注意：分项上限合计 %g，比预算 %g 高出 %g。要么压住宿和餐饮，要么把预算上调。",
        pyG(hiSum), pyG(cap), pyG(hiSum - cap)));
    }
    L.push("");
  }

  // ---- 待办
  const checklist = objs(pyGet(it, "checklist", null));
  if (checklist.length) {
    L.push("## 出发前待办");
    L.push("");
    for (const c of checklist) {
      const mark = pyGet(c, "status", null) === "done" ? "x" : " ";
      const prio = pyGet(c, "priority", null) === "must" ? "【必办】" : "【可选】";
      const dl = pyTruthy(pyGet(c, "deadline", null)) ? "，截止 " + pyStr(pyGet(c, "deadline", null)) : "";
      L.push(pyPercent("- [%s] %s%s%s", mark, prio, pyStr(pyGet(c, "task", "")), dl));
      if (pyTruthy(pyGet(c, "if_unresolved", null))) {
        L.push("  - 没办成怎么办：" + pyStr(pyGet(c, "if_unresolved", null)));
      }
    }
    L.push("");
  }

  // ---- 踩坑指南
  const pitfalls = objs(pyGet(trip, "pitfalls", null));
  if (pitfalls.length) {
    L.push("## 踩坑指南");
    L.push("");
    for (const p of pitfalls) {
      const level = pyGet(PITFALL_MD, pyGet(p, "level", null), "⚠️");
      L.push(pyPercent("- %s **%s**", level, pyStr(pyGet(p, "title", ""))));
      L.push("  - " + pyStr(pyGet(p, "detail", "")));
      if (pyTruthy(pyGet(p, "when", null))) L.push("  - 什么时候会遇到：" + pyStr(pyGet(p, "when", null)));
      if (pyTruthy(pyGet(p, "how", null))) L.push("  - 怎么绕：" + pyStr(pyGet(p, "how", null)));
    }
    L.push("");
  }

  // ---- 依据索引
  const facts = objs(pyGet(trip, "facts", null));
  const sources = objs(pyGet(trip, "sources", null));
  if (facts.length || sources.length) {
    L.push("## 依据索引");
    L.push("");
    L.push("方案里每条关键数据都能追到下面某一项。追不到的都标了「估算」或「未查到」。");
    L.push("");
    if (facts.length) {
      L.push("| 编号 | 结论 | 状态 | 来源 | 取数日期 |");
      L.push("|---|---|---|---|---|");
      const smap = new Map(sources.map((s) => [pyGet(s, "source_id", null), s]));
      for (const f of facts) {
        const stRaw = pyGet(f, "status", null);
        const st = Object.prototype.hasOwnProperty.call(FACT_STATUS, stRaw)
          ? FACT_STATUS[stRaw] : pyGet(f, "status", "");
        const sids = pyOr(pyGet(f, "source_ids", null), []);
        const look = (sid, k, dflt) => {
          const s = smap.has(sid) ? smap.get(sid) : null;
          const obj = pyTruthy(s) ? s : {};
          return pyGet(obj, k, dflt);
        };
        const names = sids.map((sid) => look(sid, "name", sid)).join("、") || "—";
        const dates = sids.map((sid) => look(sid, "retrieved_at", "?")).join("、") || "—";
        const claim = pyOr(pyGet(f, "claim", null), pyGet(f, "conclusion", null), "");
        L.push(pyPercent("| %s | %s | %s | %s | %s |", pyGet(f, "fact_id", ""), claim, pyStr(st), names, dates));
      }
      L.push("");
    }
    if (sources.length) {
      L.push("**数据来源**");
      L.push("");
      for (const s of sources) {
        const url = pyTruthy(pyGet(s, "url", null)) ? " <" + pyStr(pyGet(s, "url", null)) + ">" : "";
        L.push(pyPercent("- `%s` %s（%s，取数 %s）%s", pyGet(s, "source_id", ""), pyGet(s, "name", ""),
          pyOr(pyGet(s, "kind", null), "未标注类型"), pyOr(pyGet(s, "retrieved_at", null), "?"), url));
      }
      L.push("");
    }
  }

  L.push("---");
  L.push("");
  L.push("*" + ATTR_LINE + " · v" + pyStr(ver) + "*");
  return L.join("\n");
}


// ---------------------------------------------------------------- HTML 共用

function prefText(must, prefer) {
  const parts = [];
  if (must) parts.push("必须玩到：" + must);
  if (prefer) parts.push("能做则做：" + prefer);
  return parts.join("　·　") || "未定";
}

function mdRoute(it) {
  const [rows] = nw.routeStops(it);
  if (!rows.length) return [];
  const lines = [], pts = [];
  for (const [did, stops] of rows) {
    for (const [name, n, lat, lng] of stops) {
      if (lat !== null && lng !== null) pts.push([lng, lat, name]);
      lines.push(pyPercent("%d. [%s](%s)（%s）", n, name, mapUrl(name), did));
    }
  }
  const out = ["**动线一览**（按行程顺序；编号与下方逐日停靠点一致）", ""];
  out.push(...lines);
  const links = [
    pts.length ? pyPercent("[在高德看全部点位](%s)", nw.markersUrl(pts)) : "",
    pts.length >= 2 ? pyPercent("[规划整条路线](%s)", nw.routeUrl(pts)) : "",
  ].filter((x) => x);
  if (links.length) out.push("", links.join(" · "));
  out.push("");
  return out;
}

// ---------------------------------------------------------------- HTML

const CSS_COMMENT = /\/\*[\s\S]*?\*\//g;
// 只认真正的远程引用（@import 远程地址 / url(http…)）
const CSS_REMOTE = /@import\s["']?\s*(?:https?:)?\/\//i;

function stripCssComments(css) { return css.replace(CSS_COMMENT, ""); }

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function loadCss() {
  // 读 assets/handbook.css；读不到或含远程引用时退回内置兜底样式
  const cssPath = path.join(REPO_ROOT, "assets", "handbook.css");
  try {
    const css = fs.readFileSync(cssPath, "utf-8");
    if (css.trim()) {
      if (CSS_REMOTE.test(stripCssComments(css))) {
        console.error("[警告] assets/handbook.css 含远程引用，已改用内置兜底样式");
      } else {
        return css;
      }
    }
  } catch {
    // 读不到 → 兜底
  }
  return FALLBACK_CSS;
}

function clip(text, limit) {
  for (const sep of ["（", "；", "，"]) {
    const head = text.split(sep)[0];
    if (pyLen(head) >= 6) { text = head; break; }
  }
  return pyLen(text) <= limit ? text : pySlice(text, limit) + "…";
}

function danmaku(trip) {
  const req = dict(pyGet(trip, "request", null));
  const it = dict(pyGet(trip, "itinerary", null));
  const nat = pyOr(pyGet(it, "nature", null), {});
  const pills = [];
  const dest = pyOr(g(req, "destination", "name"), pyGet(trip, "title", null), "");
  const org = pyOr(g(req, "origin", "name"), "");
  if (dest && org) pills.push(pyStr(org) + " → " + pyStr(dest));
  else if (dest) pills.push(pyStr(dest) + " 行程");
  else if (org) pills.push("从" + pyStr(org) + "出发");
  const daysRaw = pyOr(pyGet(it, "days", null), []);
  const days = pyLenAny(daysRaw);
  if (days) pills.push(pyStr(days) + " 天");
  const sunrise = pyOr(pyGet(sub(nat, "sunrise"), "sunrise", null), "");
  if (pyTruthy(sunrise)) pills.push("日出 " + pyStr(sunrise));
  const tide = pyOr(pyGet(sub(nat, "beachcombing"), "tide_summary", null), "");
  if (pyTruthy(tide)) pills.push(clip(pyStr(tide), 22));
  const moon = pyOr(pyGet(sub(nat, "stargazing"), "moon_phase", null), "");
  if (pyTruthy(moon)) pills.push("月相 " + clip(pyStr(moon), 18));
  for (const [key, head] of [["birding", "观鸟"], ["glow", "朝晚霞"]]) {
    if (pyTruthy(pyGet(nat, key, null)) && pills.length < 5) pills.push(head);
  }
  const five = pills.slice(0, 5);
  if (!five.length) return "";
  return ('<div class="danmaku" aria-hidden="true">'
    + five.map((p) => "<i>" + e(p) + "</i>").join("") + "</div>");
}

const STK_HEAD = '<svg viewBox="0 0 32 32" fill="none" stroke-width="1.8" '
  + 'stroke-linecap="round" stroke-linejoin="round">';

function stickerSvg(name) {
  return STK_HEAD + (pyGet(STICKER, name, null) ?? STICKER.sparkle) + "</svg>";
}

const COVER_STICKERS = ["shell", "bird", "bow"].map((n, i) =>
  pyPercent('<i class="stk stk-s%d" aria-hidden="true">%s</i>', i + 1, stickerSvg(n))).join("");
const COVER_SPARKS = '<i class="spark"></i>'.repeat(5);
const BLING = '<div class="bling" aria-hidden="true">' + "<i></i>".repeat(12) + "</div>";

function cover(trip) {
  const req = dict(pyGet(trip, "request", null));
  const status = pyOr(pyGet(trip, "status", null), "draft");
  const ds = pyOr(g(req, "dates", "start"), "?");
  const de = pyOr(g(req, "dates", "end"), "?");
  const daysRaw = g(trip, "itinerary", "days", { default: [] });
  const days = pyLenAny(pyOr(daysRaw, []));
  const chips = [pyStr(ds) + " → " + pyStr(de), pyStr(days) + " 天", routeText(req, trip),
    pyOr(pyGet(req, "companions", null), "同行未定")];
  const chipsHtml = chips.filter((c) => pyTruthy(c) && c !== "未定")
    .map((c) => "<span>" + e(c) + "</span>").join("");
  const note = pyTruthy(pyGet(trip, "status_note", null))
    ? pyPercent('<div class="note">%s</div>', e(pyGet(trip, "status_note", null))) : "";
  return ('<header class="cover">' + COVER_SPARKS + COVER_STICKERS
    + "<h1>" + e(pyOr(pyGet(trip, "title", null), pyGet(trip, "trip_id", "旅行方案")))
    + pyPercent('<span class="badge" style="background:%s">', pyGet(PALETTE, status, "#d6226b"))
    + e(pyGet(STATUS_LABEL, status, status) ?? status) + "</span></h1>"
    + pyPercent('<div class="sub">v%s · 生成于 %s · 时间价格预约以官方为准</div>',
      pyStr(pyGet(trip, "plan_version", 1)), nowStamp())
    + '<div class="facts">' + chipsHtml + "</div>" + note
    + danmaku(trip) + "</header>");
}

function originTxt(req) {
  const o = g(req, "origin", "name");
  return o ? "从" + pyStr(o) + "出发" : "";
}

function routeText(req, trip) {
  const dest = pyOr(g(req, "destination", "name"), pyGet(trip, "title", null), "");
  const org = pyOr(g(req, "origin", "name"), "");
  if (org && dest) return pyStr(org) + " → " + pyStr(dest);
  return pyOr(dest, org, "未定");
}

function glance(trip) {
  const req = dict(pyGet(trip, "request", null));
  const b = dict(pyGet(req, "budget", null));
  const mode = pyGet({ total: "总额", per_person: "人均" }, pyGet(b, "mode", null), "口径未定");
  const interests = dict(pyGet(req, "interests", null));
  const must = labels(pyGet(interests, "must", null), "label", "name").join("、");
  const prefer = labels(pyGet(interests, "prefer", null)).join("、");

  const cells = [];
  const why = pyStr(pyOr(pyGet(req, "why", null), "")).trim();
  if (why) cells.push(["这趟的主题", e(why), "wide"]);

  const mid = [["预算", money(pyGet(b, "amount_max", null), pyOr(pyGet(b, "currency", null), "CNY")) + "（" + mode + "）"]];
  const pace = pyStr(pyOr(pyGet(req, "pace", null), "")).trim();
  if (pace) mid.push(["节奏", pace]);
  const pref = prefText(must, prefer);
  if (pref !== "未定") mid.push(["专项偏好", pref]);
  const spanOf = { 1: "", 2: "half", 4: "wide" };
  const widths = { 3: [1, 1, 2], 2: [2, 2], 1: [4] }[mid.length];
  mid.forEach(([k, v], ix) => cells.push([k, e(v), spanOf[widths[ix]]]));

  const consRaw = pyOr(pyGet(req, "constraints", null), []);
  const cons = (typeof consRaw === "string" ? Array.from(consRaw) : consRaw)
    .map((c) => pyStr(c).trim()).filter((c) => c);
  if (cons.length) {
    const chips = cons.map((c) => '<i class="chip">' + e(c) + "</i>").join("");
    cells.push(["硬约束", '<span class="chips">' + chips + "</span>", "wide"]);
  }

  if (!cells.length) return "";
  const inner = cells.map(([k, v, sp]) =>
    '<div class="cell' + (sp ? " " + sp : "") + '"><b>' + e(k) + "</b>"
    + "<span>" + v + "</span></div>").join("");
  return h2("glance", "一页速览") + '<div class="grid g-glance">' + inner + "</div>";
}

function icon(name) {
  return ('<span class="ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" '
    + 'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
    + 'stroke-linejoin="round">' + (pyGet(ICON_SVG, name, null) ?? "") + "</svg></span>");
}

function h2(key, text) {
  const stk = stickerSvg(pyGet(SECTION_STICKER, key, null) ?? "sparkle");
  return ('<h2>' + icon(key) + e(text)
    + '<i class="stk" aria-hidden="true">' + stk + "</i></h2>");
}

function weatherSection(it) {
  const w = isDict(pyGet(it, "weather", null)) ? pyGet(it, "weather", null) : {};
  if (!Object.keys(w).length) return "";
  const out = [h2("weather", "天气研判")];
  const entries = objs(pyGet(w, "entries", null));
  if (pyGet(w, "kind", null) === "forecast" && entries.length) {
    const cells = entries.map((x) => {
      const src = pyTruthy(pyGet(x, "source_id", null))
        ? pyPercent('<b style="display:block;font-size:12px;color:var(--ink-3)">%s</b>',
          e(pyGet(x, "source_id", ""))) : "";
      return pyPercent('<div class="cell"><b>%s</b><span>%s</span>%s</div>',
        e(pyGet(x, "date", "")), e(pyGet(x, "summary", "")), src);
    });
    out.push('<div class="grid">' + cells.join("") + "</div>");
  } else {
    out.push(pyPercent('<div class="card">%s</div>',
      e(pyOr(pyGet(w, "summary", null), "规划期气候参考，出发前请查逐日预报。"))));
  }
  return out.join("");
}

function scene(theme, label) {
  return (pyPercent('<div class="scene scene-%s">', theme) + (pyGet(MOTIF, theme, null) ?? "")
    + pyPercent('<span class="label">%s</span></div>', e(label)));
}

function natureCard(title, rows, key, extra = "", extraFirst = false) {
  const [theme, label] = pyGet(NATURE_THEME, key, null) ?? ["glow", title];
  const kvs = [];
  for (const row of rows) {
    const k = row[0], v = row[1];
    const raw = row.length > 2 && Boolean(row[2]);
    const wide = row.length > 3 && Boolean(row[3]);
    kvs.push('<div class="kv' + (wide ? " wide" : "") + '"><dt>' + e(k)
      + "</dt><dd>" + (raw ? v : e(v)) + "</dd></div>");
  }
  const stk = stickerSvg(pyGet(NATURE_STICKER, key, null) ?? "sparkle");
  let body = kvs.length ? '<dl class="dl">' + kvs.join("") + "</dl>" : "";
  if (extra) body = extraFirst ? extra + body : body + extra;
  const cls = extra ? "card tall" : "card";
  return (pyPercent('<div class="%s"><div class="scene scene-%s">', cls, theme)
    + (pyGet(MOTIF, theme, null) ?? "")
    + '<span class="label">' + e(label) + "</span>"
    + '<i class="stk" aria-hidden="true">' + stk + "</i></div>"
    + '<div class="body">' + body + "</div></div>");
}

function natureSection(it) {
  const n = isDict(pyGet(it, "nature", null)) ? pyGet(it, "nature", null) : {};
  if (!Object.keys(n).length) return "";
  const cards = [];

  const b = sub(n, "birding");
  if (Object.keys(b).length) {
    const rows = [];
    if (pyTruthy(pyGet(b, "hotspots", null))) {
      rows.push(["观察点", nw.placeLinks(pyGet(b, "hotspots", null)), true, true]);
    }
    rows.push(["最佳时段", pyOr(pyGet(b, "best_time", null), "日出后 2 小时")]);
    if (pyTruthy(pyGet(b, "notes", null))) rows.push(["注意", pyGet(b, "notes", null)]);
    cards.push(natureCard("观鸟", rows, "birding",
      nw.speciesList(pyGet(b, "species", null)), true));
  }

  const s = sub(n, "stargazing");
  if (Object.keys(s).length) {
    const rows = [];
    if (pyTruthy(pyGet(s, "moon_phase", null))) rows.push(["月相", pyGet(s, "moon_phase", null)]);
    if (pyTruthy(pyGet(s, "dark_window", null))) rows.push(["暗夜窗口", pyGet(s, "dark_window", null)]);
    if (pyTruthy(pyGet(s, "planets", null))) {
      let pl = asItems(pyGet(s, "planets", null)).map((p) => isDict(p) ? pyStr(pyGet(p, "name", "")) : pyStr(p));
      pl = pl.filter((x) => x);
      rows.push(["可见行星", pl.length ? pl.join("、") : "—"]);
    }
    if (pyTruthy(pyGet(s, "rating", null))) rows.push(["观星条件", pyGet(s, "rating", null)]);
    if (pyTruthy(pyGet(s, "events", null))) rows.push(["天文事件", texts(pyGet(s, "events", null)).join("；")]);
    if (pyTruthy(pyGet(s, "spots", null))) {
      rows.push(["观测点", nw.placeLinks(pyGet(s, "spots", null)), true, true]);
    }
    cards.push(natureCard("观星", rows, "stargazing", nw.starSection(s)));
  }

  const r = sub(n, "sunrise");
  const gl = sub(n, "glow");
  if (Object.keys(r).length) {
    const rows = [];
    if (pyTruthy(pyGet(r, "sunrise", null)) || pyTruthy(pyGet(r, "sunset", null))) {
      rows.push(["时刻", pyPercent("日出 %s · 日落 %s", pyOr(pyGet(r, "sunrise", null), "未查到"),
        pyOr(pyGet(r, "sunset", null), "未查到"))]);
    }
    if (pyTruthy(pyGet(r, "golden_hour", null))) rows.push(["黄金时刻", pyGet(r, "golden_hour", null)]);
    if (pyTruthy(pyGet(r, "blue_hour", null))) rows.push(["蓝调时刻", pyGet(r, "blue_hour", null)]);
    if (pyTruthy(pyGet(r, "best_spot", null))) {
      rows.push(["观测点", nw.placeLinks(pyGet(r, "best_spot", null)), true, true]);
    }
    cards.push(natureCard("日出日落", rows, "sunrise", nw.sunSection(r, gl)));
  } else if (Object.keys(gl).length) {
    const rows = [];
    if (pyTruthy(pyGet(gl, "rating", null))) rows.push(["预报", pyGet(gl, "rating", null)]);
    cards.push(natureCard("朝晚霞", rows, "glow", nw.glowMeter(gl)));
  }

  const t = sub(n, "beachcombing");
  if (Object.keys(t).length) {
    const rows = [];
    if (pyTruthy(pyGet(t, "tide_summary", null))) rows.push(["潮汐", pyGet(t, "tide_summary", null)]);
    if (pyTruthy(pyGet(t, "best_window", null))) rows.push(["赶海窗口", pyGet(t, "best_window", null)]);
    if (pyTruthy(pyGet(t, "tide_phase", null))) rows.push(["潮期", pyGet(t, "tide_phase", null)]);
    if (pyTruthy(pyGet(t, "spots", null))) {
      rows.push(["地点", nw.placeLinks(pyGet(t, "spots", null)), true, true]);
    }
    if (pyTruthy(pyGet(t, "safety", null))) rows.push(["安全", pyGet(t, "safety", null)]);
    cards.push(natureCard("赶海", rows, "beachcombing", nw.tideSection(t, "beachcombing")));
  }

  if (!cards.length) return "";
  return h2("nature", "自然观察专项") + '<div class="nature">' + cards.join("") + "</div>";
}

function spread(xy, radius = 16.0) {
  // 分组键是 round(x,1)/round(y,1) 的**舍入值**；圆心也用舍入值，不是原坐标
  const groups = new Map();
  xy.forEach(([x, y], k) => {
    const rx = pyRound(x, 1), ry = pyRound(y, 1);
    const key = rx + "," + ry;
    if (!groups.has(key)) groups.set(key, { rx, ry, idxs: [] });
    groups.get(key).idxs.push(k);
  });
  const out = xy.map((p) => [...p]);
  for (const { rx, ry, idxs } of groups.values()) {
    if (idxs.length < 2) continue;
    idxs.forEach((k, pos) => {
      const ang = 2 * Math.PI * pos / idxs.length - Math.PI / 2;
      out[k] = [rx + radius * Math.cos(ang), ry + radius * Math.sin(ang)];
    });
  }
  return out;
}

function smoothPath(points) {
  if (points.length < 3) {
    return "M" + points.map((p) => pyPercent("%.1f %.1f", p[0], p[1])).join(" L");
  }
  let d = pyPercent("M%.1f %.1f", points[0][0], points[0][1]);
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = i > 0 ? points[i - 1] : points[i];
    const p1 = points[i], p2 = points[i + 1];
    const p3 = i + 2 < points.length ? points[i + 2] : p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0];
    d += pyPercent(" C%.1f %.1f %.1f %.1f %.1f %.1f", c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]);
  }
  return d;
}

function flowSection(it) {
  const [rowsData] = nw.routeStops(it);
  if (!rowsData.length) return "";
  const rows = [], pts = [];
  rowsData.forEach(([did, stops], ix) => {
    const dayNo = ix + 1;
    const nodes = [];
    for (const [name, n, lat, lng] of stops) {
      if (lat !== null && lng !== null) pts.push([lng, lat, name, dayNo, n]);
      nodes.push(pyPercent('<a class="rnode" href="%s" target="_blank" rel="noopener"><i>%s</i>%s</a>',
        e(mapUrl(name)), n, e(name)));
    }
    rows.push('<div class="rline"><b>' + e(did)
      + '</b><div class="rstops">' + nodes.join('<span class="rarrow">→</span>')
      + "</div></div>");
  });

  const head = ('<div class="rhead">动线一览<span class="rsub">'
    + '按行程顺序连起来；标号与 MD 的停靠点清单一致</span></div>');
  const body = head + rows.join("");

  const links = [];
  const mk = nw.markersUrl(pts);
  if (mk) {
    links.push('<a class="rbtn" href="' + e(mk) + '" target="_blank" '
      + 'rel="noopener">在高德看全部点位</a>');
  }
  const rt = nw.routeUrl(pts);
  if (rt) {
    links.push('<a class="rbtn ghost" href="' + e(rt) + '" target="_blank" '
      + 'rel="noopener">规划整条路线</a>');
  }
  let foot;
  if (links.length) {
    let tip = "本页不含路线图，路况与导航以地图为准";
    if (!pts.length) tip = "停靠点补 geo 坐标后，这里可以一键跳到地图看全部点位";
    else if (pts.length > nw.MARKER_LIMIT) tip += "；「全部点位」只标前 " + pyStr(nw.MARKER_LIMIT) + " 个";
    foot = '<div class="rfoot">' + links.join("") + "<span>" + tip + "</span></div>";
  } else {
    foot = ('<div class="rfoot"><span>按行程顺序排的点位；'
      + '名字可点，点开就是地图搜索</span></div>');
  }
  return '<div class="route">' + body + foot + "</div>";
}

function daysSection(it) {
  const days = objs(pyGet(it, "days", null));
  if (!days.length) return "";
  const out = [h2("days", "逐日行程")];
  const flow = flowSection(it);
  if (flow) out.push(flow);
  const anchors = [];
  days.forEach((d, ix) => {
    const did = dayAnchor(d, ix + 1);
    anchors.push('<a href="#' + did + '"><b>'
      + e(pyOr(pyGet(d, "day_id", null), "D" + pyStr(ix + 1))) + "</b>"
      + e(shortDate(pyGet(d, "date", null))) + "</a>");
  });
  out.push('<div class="daynav">' + anchors.join("") + "</div>");
  days.forEach((d, ix) => {
    const did = dayAnchor(d, ix + 1);
    const items = objs(pyGet(d, "items", null)).filter((i) => !blankItem(i));
    const wk = pyTruthy(pyGet(d, "weekday", null)) ? "（" + e(pyGet(d, "weekday", null)) + "）" : "";
    out.push('<details class="day" id="' + did + '" open><summary>'
      + '<span class="dnum">' + e(pyOr(pyGet(d, "day_id", null), "D" + pyStr(ix + 1))) + "</span>"
      + '<span class="dmeta"><em>' + e(shortDate(pyGet(d, "date", null))) + wk + "</em>"
      + e(pyOr(pyGet(d, "title", null), "")) + "</span>"
      + '<span class="dcnt">' + pyStr(items.length) + " 项</span></summary>"
      + '<div class="dbody"><div class="tl">');
    for (const i of items) {
      const kind = pyOr(pyGet(i, "kind", null), "visit");
      const tag = ('<span class="tag ' + (pyGet(KIND_CLASS, kind, null) ?? "k-visit") + '">'
        + e(pyGet(KIND_LABEL, kind, null) ?? "") + "</span>");
      const place = pyTruthy(pyGet(i, "place", null))
        ? '<span class="pl"> @ ' + mapLink(pyGet(i, "place", null)) + "</span>" : "";
      const st = pyGet(i, "booking_status", null);
      const stTxt = pyGet(BOOKING_MD, st, null);
      const stHtml = stTxt ? '<span class="pl">（' + e(stTxt) + "）</span>" : "";
      const book = pyOr(pyGet(i, "booking", null), {});
      let chips = "";
      if (isDict(book) && pyTruthy(pyGet(book, "keyword", null))) {
        chips = nw.linkChips(nw.bookingLinks(pyGet(book, "keyword", null),
          pyOr(pyGet(book, "kind", null), "ticket"), book));
      }
      const note = pyTruthy(pyGet(i, "notes", null))
        ? pyPercent('<div class="nt">%s</div>', e(pyGet(i, "notes", null))) : "";
      const meta = ((place || stHtml) ? "<div>" + place + stHtml + "</div>" : "") + chips;
      const tHtml = pyTruthy(pyGet(i, "time", null))
        ? pyPercent('<div class="t">%s</div>', e(pyGet(i, "time", null))) : "";
      out.push('<div class="slot">' + tHtml
        + '<div class="ti">' + e(pyOr(pyGet(i, "title", null), "")) + tag + "</div>"
        + meta + note + "</div>");
    }
    out.push("</div>");
    for (const a of objs(pyGet(d, "alternatives", null))) {
      if (pyTruthy(pyGet(a, "title", null)) || pyTruthy(pyGet(a, "trigger", null))) {
        out.push('<div class="alt"><b>备选</b> ' + e(pyGet(a, "title", ""))
          + "｜触发条件：" + e(pyOr(pyGet(a, "trigger", null), "未写")) + "</div>");
      }
    }
    out.push("</div></details>");
  });
  return out.join("");
}

function foodSection(it) {
  const foodRaw = pyOr(pyGet(it, "food", null), []);
  const foodAll = typeof foodRaw === "string" ? Array.from(foodRaw) : foodRaw;
  const food = foodAll.filter(
    (x) => isDict(x) && pyStr(pyOr(pyGet(x, "name", null), "")).trim());
  if (!food.length) return "";
  let rows = "";
  for (const x of food) {
    const name = pyStr(pyGet(x, "name", ""));
    const kw = pyStr(pyOr(pyGet(x, "search", null), name)).trim();
    const chips = nw.linkChips(nw.bookingLinks(kw, pyOr(pyGet(x, "booking_kind", null), "food"), x));
    rows += (pyPercent("<tr><td>%s%s</td>", e(name), chips)
      + pyPercent('<td class="num">%s</td>', e(pyOr(pyGet(x, "price", null), "未查到")))
      + pyPercent("<td>%s</td>", e(pyGet(x, "why", "")))
      + pyPercent("<td>%s</td></tr>", e(pyOr(pyGet(x, "note", null), "—"))));
  }
  return (h2("food", "美食推荐")
    + '<table><thead><tr><th>店/摊</th><th class="num">人均</th>'
    + '<th>为什么去</th><th>备注</th></tr></thead><tbody>' + rows + "</tbody></table>"
    + '<div class="exlog">人均是平台实时价，会变；点店名旁的入口看当天报价，'
    + "本方案不写死价格。</div>");
}

function budgetSection(it, req) {
  const b = isDict(pyGet(it, "budget", null)) ? pyGet(it, "budget", null) : {};
  const cats = objs(pyGet(b, "categories", null));
  if (!cats.length) return "";
  const cur = pyOr(pyGet(b, "currency", null), "CNY");
  const rowsData = cats.map((c) => [pyGet(c, "name", ""), nw.num(pyGet(c, "min", null)), nw.num(pyGet(c, "max", null))]);
  const caps = rowsData.filter((r) => r[2] !== null).map((r) => r[2]);
  let top = caps.length ? Math.max(...caps) : 1;
  top = top || 1;
  const loSum = rowsData.filter((r) => r[1] !== null).reduce((a, r) => a + r[1], 0);
  const hiSum = rowsData.filter((r) => r[2] !== null).reduce((a, r) => a + r[2], 0);
  let rows = "";
  for (const [name, lo, hi] of rowsData) {
    let bar, amt;
    if (lo !== null && hi !== null) {
      const left = lo / top * 100;
      const width = Math.max((hi - lo) / top * 100, 1.5);
      bar = pyPercent('<div class="bar"><i style="left:%s;width:%s"></i></div>',
        pyFixed(left, 1) + "%", pyFixed(width, 1) + "%");
      amt = pyPercent("%g – %g", pyG(lo), pyG(hi));
    } else {
      bar = "";
      amt = "未查到";
    }
    rows += (pyPercent("<tr><td>%s%s</td>", e(name), bar)
      + pyPercent('<td class="num">%s</td></tr>', e(amt)));
  }
  rows += (pyPercent('<tr><td class="total">合计（%s）</td>', e(cur))
    + pyPercent('<td class="num total"><span class="hl">%g – %g</span></td></tr>',
      pyG(loSum), pyG(hiSum)));
  const cap = nw.num(g(req, "budget", "amount_max"));
  let alert = "";
  if (cap !== null && hiSum > cap) {
    alert = (pyPercent('<div class="note">分项上限合计 %g，比预算 %g 高 %g。'
      + '要么压住宿和餐饮，要么把预算上调。</div>', pyG(hiSum), pyG(cap), pyG(hiSum - cap)));
  }
  return h2("budget", "预算明细") + "<table><tbody>" + rows + "</tbody></table>" + alert;
}

function checklistSection(it) {
  const cl = objs(pyGet(it, "checklist", null));
  if (!cl.length) return "";
  let doneN = 0;
  let items = "";
  for (const c of cl) {
    const done = pyGet(c, "status", null) === "done";
    if (done) doneN += 1;
    const prio = pyGet(c, "priority", null) === "must" ? "必办" : "可选";
    const dl = pyTruthy(pyGet(c, "deadline", null))
      ? "　截止 " + e(pyGet(c, "deadline", null)) : "";
    const unres = pyTruthy(pyGet(c, "if_unresolved", null))
      ? pyPercent('<div class="nt">没办成怎么办：%s</div>', e(pyGet(c, "if_unresolved", null))) : "";
    const mark = done
      ? '<span class="mark mark-done" title="已办">✓</span>'
      : '<span class="mark" title="未办"></span>';
    const bk = pyOr(pyGet(c, "booking", null), {});
    const chips = (isDict(bk) && pyTruthy(pyGet(bk, "keyword", null)))
      ? nw.linkChips(nw.bookingLinks(pyGet(bk, "keyword", null), pyOr(pyGet(bk, "kind", null), "ticket"), bk))
      : "";
    items += ('<div class="todo' + (done ? " is-done" : "") + '">' + mark
      + '<span class="txt"><span class="ti">【' + prio + "】"
      + e(pyGet(c, "task", "")) + dl + "</span>" + chips + unres + "</span></div>");
  }
  const summary = cl.length > 1
    ? pyPercent('<div class="todo-sum">已办 %d / %d</div>', doneN, cl.length) : "";
  return h2("todo", "出发前待办") + '<div class="card">' + summary + items + "</div>";
}

function pitfallsSection(trip) {
  const ps = objs(pyGet(trip, "pitfalls", null));
  if (!ps.length) return "";
  const out = [h2("pitfalls", "踩坑指南")];
  for (const p of ps) {
    const level = pyGet(PITFALL_HTML, pyGet(p, "level", null), "留意");
    const when = pyTruthy(pyGet(p, "when", null))
      ? pyPercent('<div class="when">什么时候遇到：%s</div>', e(pyGet(p, "when", null))) : "";
    const how = pyTruthy(pyGet(p, "how", null))
      ? pyPercent('<div class="when">怎么绕：%s</div>', e(pyGet(p, "how", null))) : "";
    out.push(pyPercent('<div class="warn"><b>%s</b>', e(pyGet(p, "title", "")))
      + pyPercent('<span class="tag sev-minor">%s</span>', e(level))
      + pyPercent("<div>%s</div>", e(pyGet(p, "detail", ""))) + when + how + "</div>");
  }
  return out.join("");
}

function sourcesSection(trip) {
  const facts = objs(pyGet(trip, "facts", null));
  const sources = objs(pyGet(trip, "sources", null));
  if (!facts.length && !sources.length) return "";
  const smap = new Map(sources.map((s) => [pyGet(s, "source_id", null), s]));
  const out = [h2("sources", "依据索引"),
    '<div class="legend">方案里每条关键数据都能追到下面某一项。追不到的标了「估算」或「未查到」。</div>'];
  if (facts.length) {
    let rows = "";
    for (const f of facts) {
      const stRaw = pyGet(f, "status", null);
      const st = Object.prototype.hasOwnProperty.call(FACT_STATUS, stRaw)
        ? FACT_STATUS[stRaw] : pyGet(f, "status", "");
      const sids = pyOr(pyGet(f, "source_ids", null), []);
      const look = (sid, k, dflt) => {
        const s = smap.has(sid) ? smap.get(sid) : null;
        const obj = pyTruthy(s) ? s : {};
        return pyGet(obj, k, dflt);
      };
      const names = sids.map((sid) => look(sid, "name", sid)).join("、") || "—";
      const dates = sids.map((sid) => look(sid, "retrieved_at", "?")).join("、") || "—";
      rows += (pyPercent("<tr><td>%s</td>", e(pyGet(f, "fact_id", "")))
        + pyPercent("<td>%s</td>", e(pyOr(pyGet(f, "claim", null), pyGet(f, "conclusion", null), "")))
        + pyPercent("<td>%s</td>", e(st))
        + pyPercent('<td class="srcs">%s</td>', e(names))
        + pyPercent('<td class="srcs">%s</td></tr>', e(dates)));
    }
    out.push('<table class="facts-tbl"><thead><tr><th>编号</th><th>结论</th><th>状态</th>'
      + "<th>来源</th><th>取数日期</th></tr></thead><tbody>" + rows + "</tbody></table>");
  }
  if (sources.length) {
    let items = "";
    for (const s of sources) {
      const url = pyTruthy(pyGet(s, "url", null))
        ? pyPercent(' <a class="src-link" href="%s" target="_blank" rel="noopener noreferrer">%s</a>',
          e(pyGet(s, "url", null)), e(pyGet(s, "url", null))) : "";
      items += (pyPercent("<li><code>%s</code> %s", e(pyGet(s, "source_id", "")), e(pyGet(s, "name", "")))
        + pyPercent("（%s，取数 %s）%s</li>", e(pyOr(pyGet(s, "kind", null), "未标注类型")),
          e(pyOr(pyGet(s, "retrieved_at", null), "?")), url));
    }
    out.push(pyPercent("<details><summary>数据来源清单（%d 项，点击展开）</summary>", sources.length)
      + pyPercent('<div class="body srcs"><ul>%s</ul>', items)
      + '<div class="legend">网址只作核对用，页面本身不联网加载。</div>'
      + "</div></details>");
  }
  return out.join("");
}

function navSection(present) {
  const links = SECTION_NAV.filter(([sid]) => present.has(sid))
    .map(([sid, fLabel]) => pyPercent('<a href="#%s">%s</a>', sid, e(fLabel))).join("");
  return '<nav class="toc" aria-label="章节导航">' + links + "</nav>";
}

export function htmlFromTrip(trip) {
  const req = dict(pyGet(trip, "request", null));
  const it = dict(pyGet(trip, "itinerary", null));
  const ver = pyGet(trip, "plan_version", 1);
  const title = e(pyOr(pyGet(trip, "title", null), pyGet(trip, "trip_id", "旅行方案")));
  const blocks = [
    ["sec-glance", glance(trip)],
    ["sec-weather", weatherSection(it)],
    ["sec-nature", natureSection(it)],
    ["sec-days", daysSection(it)],
    ["sec-food", foodSection(it)],
    ["sec-budget", budgetSection(it, req)],
    ["sec-todo", checklistSection(it)],
    ["sec-pitfalls", pitfallsSection(trip)],
    ["sec-sources", sourcesSection(trip)],
  ];
  const present = new Set(blocks.filter(([, html]) => pyTruthy(html)).map(([sid]) => sid));
  const sections = blocks.filter(([, html]) => pyTruthy(html))
    .map(([sid, html]) => pyPercent('<section id="%s" class="sec">%s</section>', sid, html)).join("");
  const body = [
    cover(trip),
    '<div class="wrap">',
    navSection(present),
    sections,
    pyPercent("<footer>%s · v%s · 离线单文件，可直接打印</footer>", e(ATTR_LINE), pyStr(ver)),
    "</div>",
    '<a class="totop" href="#top" aria-label="回到顶部">↑</a>',
    BLING,
  ].join("");
  return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
    + "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
    + pyPercent("<title>%s · 旅行方案</title>\n<style>%s</style>\n</head>\n", title, loadCss())
    + "<body id=\"top\">" + body + "</body>\n</html>\n");
}

// ---------------------------------------------------------------- 落盘 / 检查

function canonicalJson(data) {
  // json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
  if (data === null || data === undefined) return "null";
  if (typeof data === "boolean") return data ? "true" : "false";
  if (typeof data === "number") {
    if (!Number.isFinite(data)) throw new Error("Out of range float values are not JSON compliant");
    if (Number.isInteger(data) && Math.abs(data) < 1e16) return String(data);
    return pyStr(data); // 浮点按 Python repr（2.0 → "2.0"）
  }
  if (typeof data === "string") return JSON.stringify(data);
  if (Array.isArray(data)) return "[" + data.map(canonicalJson).join(",") + "]";
  const keys = Object.keys(data).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalJson(data[k])).join(",") + "}";
}

export function canonicalHash(data) {
  return crypto.createHash("sha256").update(canonicalJson(data), "utf-8").digest("hex");
}

function readTripSource(srcPath, withOrigin = false) {
  return resolveTrip(srcPath, true, withOrigin);
}

function structureProblems(trip, origin) {
  const [_ok, errors] = validate(trip, "trip.json", true, origin);
  return errors;
}

export function writeOutputs(tripPath, outDir = null, force = false, skipStructureGate = false) {
  const p = path.resolve(tripPath);
  const [trip, _label, _n, origin] = readTripSource(p, true);
  const srcDir = fs.existsSync(p) && fs.statSync(p).isDirectory() ? p : path.dirname(p);
  if (!skipStructureGate) {
    const problems = structureProblems(trip, origin);
    if (problems.length && !force) {
      const more = problems.length > 12
        ? "\n  ... 其余 " + pyStr(problems.length - 12) + " 项省略" : "";
      throw new TripDataError(
        "结构校验未通过，拒绝渲染（" + pyStr(problems.length) + " 项）：\n  - "
        + problems.slice(0, 12).join("\n  - ") + more
        + "\n  按每项后面标注的分片文件改；确认要出图再加 --force");
    }
  }
  const base = outDir || srcDir;
  fs.mkdirSync(base, { recursive: true });
  const tid = pyOr(pyGet(trip, "trip_id", null), "travel-plan");
  const ver = pyGet(trip, "plan_version", 1);
  const mdPath = path.join(base, pyPercent("%s_v%s.md", tid, pyStr(ver)));
  const htmlPath = path.join(base, pyPercent("%s_v%s.html", tid, pyStr(ver)));
  // 先全部算完再落盘：中途抛异常不留半新半旧的产物
  const mdText = mdFromTrip(trip);
  const htmlText = htmlFromTrip(trip);
  fs.writeFileSync(mdPath, mdText, { encoding: "utf-8" });
  fs.writeFileSync(htmlPath, htmlText, { encoding: "utf-8" });
  return { trip_id: tid, plan_version: ver, md: mdPath, html: htmlPath, content_hash: canonicalHash(trip) };
}

export function checkOutputs(tripPath, outDir = null) {
  const p = path.resolve(tripPath);
  const [trip] = readTripSource(p);
  const srcDir = fs.existsSync(p) && fs.statSync(p).isDirectory() ? p : path.dirname(p);
  const base = outDir || srcDir;
  const tid = pyOr(pyGet(trip, "trip_id", null), "travel-plan");
  const ver = pyGet(trip, "plan_version", 1);
  const mdPath = path.join(base, pyPercent("%s_v%s.md", tid, pyStr(ver)));
  const htmlPath = path.join(base, pyPercent("%s_v%s.html", tid, pyStr(ver)));
  const problems = [];
  for (const p of [mdPath, htmlPath]) {
    if (!fs.existsSync(p)) problems.push("缺失产物: " + p);
  }
  if (!problems.length) {
    const md = fs.readFileSync(mdPath, "utf-8");
    const htm = fs.readFileSync(htmlPath, "utf-8");
    if (!md.includes(ATTR_LINE) || !htm.includes(ATTR_LINE)) problems.push("产物缺少署名");
    const low = stripCssComments(htm).toLowerCase();
    if (low.includes("<script")) problems.push("HTML 含脚本（应为纯离线单文件）");
    for (const [pat, fLabel] of [['src="http', "远程 src"], ["src='http", "远程 src"],
      ["<link ", "外链 <link>"], ["@import", "CSS @import"],
      ["url(http", "远程 url()"], ["url('http", "远程 url()"]]) {
      if (low.includes(pat)) problems.push("HTML 引用了远程资源（" + fLabel + "）");
    }
    if (low.includes("http-equiv") && low.includes("refresh")) problems.push("HTML 含自动跳转");
    const blob = md;
    for (const [probe, fLabel] of [
      [g(trip, "itinerary", "nature", "sunrise", "sunrise"), "日出时刻"],
      [g(trip, "itinerary", "nature", "stargazing", "moon_phase"), "月相"]]) {
      if (pyTruthy(probe) && !blob.includes(pyStr(probe))) {
        problems.push(fLabel + " " + pyStr(probe) + " 未出现在产物中");
      }
    }
  }
  return { ok: !problems.length, problems, files: [mdPath, htmlPath] };
}

// ---------------------------------------------------------------- CLI

export function main(argv = process.argv.slice(2)) {
  let trip = null, outDir = null, check = false, force = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--check") check = true;
    else if (a === "--force") force = true;
    else if (a === "--out-dir") outDir = argv[++i];
    else if (trip === null) trip = a;
  }
  if (trip === null) {
    console.error("用法: node render_plan.mjs <方案目录|trip.json> [--out-dir 目录] [--check] [--force]");
    return 2;
  }
  try {
    if (check) {
      const r = checkOutputs(trip, outDir);
      console.log("检查结果:", r.ok ? "通过" : "未通过");
      for (const p of r.problems) console.log("  x", p);
      return r.ok ? 0 : 1;
    }
    const r = writeOutputs(trip, outDir, force);
    console.log(pyPercent("ok 已生成 v%s:", pyStr(r.plan_version)));
    console.log("  MD  : " + r.md);
    console.log("  HTML: " + r.html);
    return 0;
  } catch (err) {
    if (err instanceof TripDataError) {
      console.error("x " + err.message); // 结构门：退出码 3，与读取失败区分
      return 3;
    }
    console.error("x " + err.message);
    return 2;
  }
}

const isCli = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCli) process.exit(main());
