#!/usr/bin/env node
// red_team.mjs · 红方复核器的 Node 移植（与 scripts/red_team.py 逐字对齐，parity_check.py 把关）
// 用法：node js/red_team.mjs check <trip> [--json] | rules
// 退出码：0 = 无 blocking；1 = 存在 blocking；2 = 用法/数据错误（与 Python 版一致）
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { resolveTrip, weekdayOf, WEEKDAY_CN } from "./shards.mjs";

const VERSION = "1.3.0";

const _WEEKDAY_CHARS = { "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "七": 6 };
const _WEEKDAY_RE = /(?:周|週|星期|礼拜|禮拜)\s*([一二三四五六日天七])/;

function weekdayIndex(text) {
  if (!text) return null;
  const m = String(text).match(_WEEKDAY_RE);
  if (!m) return null;
  return _WEEKDAY_CHARS[m[1]];
}

const STALE_DAYS = 30;
const REALTIME_KINDS = ["api", "official_site", "web_search"];

const NATURE_KEYS = {
  birding: ["鸟", "bird"],
  stargazing: ["月相", "月亮", "月照", "照度", "观星", "银河", "流星", "行星", "moon"],
  sunrise: ["日出", "日落", "黄金时刻", "蓝调", "sun"],
  beachcombing: ["潮", "tide"],
  glow: ["霞", "火烧云", "glow"],
};

// 全部规则清单（与 Python 版 RULES 顺序一致）
export const RULES = [
  ["RT-S01", "minor", "结构", "日期与标注的星期不符"],
  ["RT-S02", "blocking", "结构", "item_id 重复"],
  ["RT-S03", "major", "结构", "同一天行程时间窗相互重叠"],
  ["RT-S04", "major", "结构", "时间窗无效（结束早于开始或跨日）"],
  ["RT-S05", "major", "结构", "预算分项 min > max"],
  ["RT-S06", "major", "预算", "预算合计上限超出用户给的预算"],
  ["RT-S07", "blocking", "结构", "缺少必填字段（日期/同行人）"],
  ["RT-S08", "minor", "结构", "天气条目数与行程天数不一致"],
  ["RT-F01", "major", "溯源", "结论标了 verified 却没有来源"],
  ["RT-F02", "major", "溯源", "结论引用了不存在的来源 ID"],
  ["RT-F03", "minor", "溯源", "来源缺少名称或取回日期"],
  ["RT-F04", "major", "溯源", "实时数据的来源已过期（超 30 天）"],
  ["RT-F05", "major", "溯源", "值为 unknown 却写了具体数值"],
  ["RT-F06", "minor", "溯源", "自然专项数据没有对应结论可溯源"],
  ["RT-L01", "blocking", "安全", "赶海窗口与干潮时刻对不上"],
  ["RT-L02", "major", "逻辑", "日出/日落活动时刻与天文时刻矛盾"],
  ["RT-L03", "major", "逻辑", "满月夜安排了观星且未提示"],
  ["RT-L04", "blocking", "安全", "赶海窗口落在涨潮段"],
  ["RT-L05", "minor", "逻辑", "凌晨专项与次日清晨专项间隔不足 5 小时"],
  ["RT-L06", "minor", "逻辑", "主要景点没有雨天/满员备选"],
  ["RT-L07", "minor", "结构", "方案标了 conditional 但没写清条件"],
  ["RT-L08", "minor", "逻辑", "踩坑指南为空"],
  ["RT-X01", "info", "复核", "未执行红方复核（缺 red_team 字段）"],
];

// ---- 数字/字符串格式化（与 Python f-string 输出逐字对齐） ----

// Python repr() 的常用子集：字符串带单引号；数字 5.0 → "5.0"
export function pyRepr(v) {
  if (typeof v === "string") {
    if (v.includes("'") && !v.includes('"')) return '"' + v + '"';
    return "'" + v.replace(/'/g, "\\'") + "'";
  }
  if (typeof v === "number") {
    if (Number.isInteger(v)) return String(v);
    return String(v);
  }
  if (typeof v === "boolean") return v ? "True" : "False";
  if (v === null || v === undefined) return "None";
  return String(v);
}

// Python "%g"（默认精度 6）：固定 6 位有效数字，去尾零；指数域 exp<-4 或 >=6 走科学计数
export function pyG(x) {
  if (x === 0) return "0";
  const neg = x < 0;
  const ax = Math.abs(x);
  if (!Number.isFinite(ax)) return ax === Infinity ? (neg ? "-inf" : "inf") : "nan";
  const exp = Math.floor(Math.log10(ax));
  let out;
  if (exp < -4 || exp >= 6) {
    // 科学计数：尾数 precision-1 位，去尾零；指数至少两位、带符号
    let mant = (ax / Math.pow(10, exp)).toPrecision(6);
    mant = mant.includes("e") ? mant : String(parseFloat(mant)); // 去尾零
    const e = exp >= 0 ? "+" + String(exp).padStart(2, "0") : "-" + String(-exp).padStart(2, "0");
    out = mant + "e" + e;
  } else {
    const digits = 5 - exp; // 小数点后位数
    let s = ax.toFixed(Math.max(0, digits));
    if (s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
    out = s;
  }
  return (neg ? "-" : "") + out;
}

// Python ".1f"：对 double 的精确十进制做银行家舍入（round-half-even）
export function pyFixed1(x) {
  if (!Number.isFinite(x)) return String(x);
  const neg = x < 0;
  const s = Math.abs(x).toFixed(20);
  const dot = s.indexOf(".");
  const intPart = s.slice(0, dot);
  const dec = s.slice(dot + 1);
  const d1 = dec[0] || "0";
  const d2 = dec[1] || "0";
  const rest = dec.slice(2);
  let up;
  if (d2 > "5") up = true;
  else if (d2 < "5") up = false;
  else up = rest.split("").some((c) => c !== "0") || (parseInt(d1, 10) % 2 === 1);
  let ip = intPart;
  let dd = d1;
  if (up) {
    if (dd === "9") {
      dd = "0";
      ip = String(parseInt(ip || "0", 10) + 1);
    } else {
      dd = String(parseInt(dd, 10) + 1);
    }
  }
  return (neg ? "-" : "") + ip + "." + dd;
}

// ---- 小工具 ----

function parseHm(s) {
  if (typeof s !== "string") return null;
  const m = s.match(/^\s*(\d{1,2}):(\d{2})\s*$/);
  if (!m) return null;
  const h = parseInt(m[1], 10), mi = parseInt(m[2], 10);
  if (h > 23 || mi > 59) return null;
  return h * 60 + mi;
}

function parseRange(s) {
  if (typeof s !== "string") return null;
  const parts = s.trim().split(/\s*[-~—至]\s*/);
  if (parts.length === 1) {
    const t = parseHm(parts[0]);
    return t !== null ? [t, t] : null;
  }
  if (parts.length === 2) {
    const a = parseHm(parts[0]), b = parseHm(parts[1]);
    if (a !== null && b !== null) return [a, b];
  }
  return null;
}

function hm(mins) {
  return String(Math.floor(mins / 60)).padStart(2, "0") + ":" +
    String(mins % 60).padStart(2, "0");
}

function asNum(v) {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  return v;
}

function objs(v) {
  return (v || []).filter((x) => x !== null && typeof x === "object" && !Array.isArray(x));
}

function sub(d, key) {
  const v = (d && typeof d === "object") ? d[key] : undefined;
  return (v !== null && typeof v === "object" && !Array.isArray(v)) ? v : {};
}

function factText(f) {
  return String(f.claim || f.conclusion || "");
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function findTimeInText(text, keyword) {
  if (typeof text !== "string") return null;
  let m = text.match(new RegExp(escapeRe(keyword) + "[^0-9]{0,8}(\\d{1,2}):(\\d{2})"));
  if (m) return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
  m = text.match(new RegExp("(\\d{1,2}):(\\d{2})[^0-9]{0,8}" + escapeRe(keyword)));
  if (m) return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
  return null;
}

class Report {
  constructor(tripId) {
    this.tripId = tripId;
    this.findings = [];
  }
  add(ruleId, severity, category, target, message, suggestion) {
    this.findings.push({ rule_id: ruleId, severity, category, target, message, suggestion });
  }
  count(sev) {
    return this.findings.filter((f) => f.severity === sev).length;
  }
  verdict() {
    if (this.count("blocking")) return "需修改";
    if (this.count("major")) return "有条件通过";
    return "通过";
  }
}

// ------------------------------------------------------------------ 结构层
function checkStructure(trip, rep) {
  const req = sub(trip, "request");
  const it = sub(trip, "itinerary");
  const days = objs(it.days);

  // RT-S07 必填字段
  const dates = sub(req, "dates");
  if (!dates.start || !dates.end) {
    rep.add("RT-S07", "blocking", "结构", "request.dates",
      "出发或返程日期缺失，方案没有可执行的时间锚点。",
      "补齐 dates.start / dates.end 后再交付。");
  }
  if (!req.companions) {
    rep.add("RT-S07", "blocking", "结构", "request.companions",
      "同行人结构缺失，直接决定行程节奏与预算口径。",
      "补齐同行人（含是否带娃/老人）。");
  }

  const seenItem = {};
  for (const d of days) {
    const dayId = d.day_id || "?";
    const date = d.date;

    // RT-S01 星期核对
    if (date) {
      let real = null;
      const m = String(date).match(/^(\d{4})-(\d{2})-(\d{2})$/);
      const valid = m && (parseInt(m[2], 10) >= 1 && parseInt(m[2], 10) <= 12) &&
        (parseInt(m[3], 10) >= 1 &&
          parseInt(m[3], 10) <= new Date(Date.UTC(+m[1], +m[2], 0)).getUTCDate());
      if (!valid) {
        rep.add("RT-S01", "minor", "结构", dayId + " " + date,
          "日期格式无法解析，无法核对星期。", "统一用 YYYY-MM-DD。");
      } else {
        real = WEEKDAY_CN[weekdayOf(String(date))];
      }
      const stated = d.weekday;
      const si = weekdayIndex(stated);
      if (real && stated && si !== null && si !== weekdayOf(String(date))) {
        rep.add("RT-S01", "minor", "结构", dayId + " " + date,
          "标注星期为 " + stated + "，实际是 " + real + "。",
          "改成 " + real + "（可保留备注，如「" + real + "（中秋节）」）。");
      }
    }

    // 收集时间窗
    const spans = [];
    for (const i of objs(d.items)) {
      const iid = i.item_id;
      // RT-S02 ID 重复
      if (iid) {
        if (iid in seenItem) {
          rep.add("RT-S02", "blocking", "结构", iid,
            "item_id 与 " + seenItem[iid] + " 重复，版本比对会错乱。",
            "改成全局唯一 ID。");
        } else {
          seenItem[iid] = (dayId + " " + (i.title || "")).trim();
        }
      }
      const rng = parseRange(i.time);
      const label = dayId + " " + (i.time || "?") + " " + (i.title || "");
      // RT-S04 时间窗无效
      if (i.time && rng === null) {
        rep.add("RT-S04", "major", "结构", label,
          "时间写法无法解析：" + pyRepr(i.time) + "。",
          "用 'HH:MM-HH:MM' 或单点 'HH:MM'。");
      } else if (rng && rng[1] < rng[0]) {
        rep.add("RT-S04", "major", "结构", label,
          "结束时间 " + hm(rng[1]) + " 早于开始时间 " + hm(rng[0]) + "。",
          "核对是否应为跨零点或写反了。");
      } else if (rng) {
        spans.push([rng[0], rng[1], i.title || iid || "?"]);
      }
    }

    // RT-S03 时间重叠
    spans.sort((a, b) =>
      (a[0] - b[0]) || (a[1] - b[1]) || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0));
    for (let i = 0; i + 1 < spans.length; i++) {
      const a = spans[i], b = spans[i + 1];
      if (b[0] < a[1]) {
        rep.add("RT-S03", "major", "结构", dayId + " " + hm(b[0]),
          "「" + a[2] + "」(" + hm(a[0]) + "-" + hm(a[1]) + ") 与「" + b[2] + "」(" +
          hm(b[0]) + "-" + hm(b[1]) + ") 时间重叠。",
          "错开时段，或合并为同一项。");
      }
    }
  }

  // RT-S08 天气条目 vs 天数
  const weather = sub(it, "weather");
  const entries = objs(weather.entries);
  if (weather.kind === "forecast" && days.length && entries.length !== days.length) {
    rep.add("RT-S08", "minor", "结构", "itinerary.weather",
      "逐日天气预报 " + entries.length + " 条，行程 " + days.length + " 天，对不上。",
      "补齐每日预报，或改为 seasonal 只给气候特征。");
  }

  // RT-L06 备选
  for (const d of days) {
    const alts = objs(d.alternatives);
    if (!alts.length) {
      rep.add("RT-L06", "minor", "逻辑", d.day_id || "?",
        "这一天没有备选方案，遇到下雨或景点满员只能临时抓瞎。",
        "至少给 1 条带触发条件的替代路线。");
    }
  }

  // RT-L07 conditional 未说明
  if (trip.status === "conditional") {
    const note = (trip.status_note || "").trim();
    const checklist = objs(it.checklist);
    const hasGate = checklist.length > 0 && checklist.some((c) => c.if_unresolved);
    if (!note && !hasGate) {
      rep.add("RT-L07", "minor", "结构", "status",
        "标了 conditional，但没说清在等什么条件成立。",
        "写明待确认项（如车票/门票）与对应的兜底做法。");
    }
  }
}

// ------------------------------------------------------------------ 预算层
function checkBudget(trip, rep) {
  const req = sub(trip, "request");
  const budgetIt = sub(sub(trip, "itinerary"), "budget");
  const cats = objs(budgetIt.categories);
  let totalMax = 0.0, totalMin = 0.0;
  for (const c of cats) {
    const lo = asNum(c.min), hi = asNum(c.max);
    const name = c.name || "?";
    if (lo !== null && hi !== null && lo > hi) {
      rep.add("RT-S05", "major", "结构", "预算/" + name,
        "下限 " + pyG(lo) + " 大于上限 " + pyG(hi) + "。", "调换数值。");
    }
    if (lo !== null) totalMin += lo;
    if (hi !== null) totalMax += hi;
  }

  const cap = asNum(sub(req, "budget").amount_max);
  const cur = sub(req, "budget").currency || "CNY";
  if (cap && totalMax > cap) {
    const over = totalMax - cap;
    rep.add("RT-S06", "major", "预算", "预算合计",
      "分项上限合计 " + pyG(totalMax) + " " + cur + "，超出用户预算上限 " +
      pyG(cap) + " " + cur + "（超 " + pyG(over) + "）。",
      "压住宿/餐饮区间，或向用户说明需提高预算，别默认超支。");
  }
}

// ------------------------------------------------------------------ 溯源层
function todayLocal() {
  const n = new Date();
  return [n.getFullYear(), n.getMonth() + 1, n.getDate()];
}

function checkProvenance(trip, rep) {
  const facts = objs(trip.facts);
  const sources = objs(trip.sources);
  const srcIds = new Set(sources.filter((s) => s.source_id).map((s) => s.source_id));
  const srcMap = {};
  for (const s of sources) if (s.source_id) srcMap[s.source_id] = s;

  for (const s of sources) {
    if (!s.name || !s.retrieved_at) {
      rep.add("RT-F03", "minor", "溯源", "来源 " + (s.source_id || "?"),
        "来源缺少名称或取回日期，用户无法判断可信度与时效。",
        "补 name 与 retrieved_at。");
    }
  }

  const [ty, tm, td] = todayLocal();
  const todayMs = Date.UTC(ty, tm - 1, td);
  for (const f of facts) {
    const fid = f.fact_id || factText(f) || "?";
    const status = f.status;
    const sids = f.source_ids || [];
    const label = (factText(f) || String(f.value !== undefined && f.value !== null ? f.value : fid)).slice(0, 40);

    if (status === "verified" && !sids.length) {
      rep.add("RT-F01", "major", "溯源", "结论 " + fid,
        "标为已验证但没有来源：" + label + "。",
        "补 source_ids，或把状态降为 estimated/unverified。");
    }

    for (const sid of sids) {
      if (!srcIds.has(sid)) {
        rep.add("RT-F02", "major", "溯源", "结论 " + fid,
          "引用了不存在的来源 " + pyRepr(sid) + "。",
          "补上该来源，或修正 ID。");
      }
    }

    // 时效性检查
    for (const sid of sids) {
      const s = srcMap[sid] || {};
      const kind = String(s.kind || "").toLowerCase();
      const ra = s.retrieved_at;
      if (REALTIME_KINDS.includes(kind) && ra) {
        const m = String(ra).slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!m) continue;
        const age = Math.floor((todayMs - Date.UTC(+m[1], +m[2] - 1, +m[3])) / 86400000);
        if (age > STALE_DAYS) {
          rep.add("RT-F04", "major", "溯源", "结论 " + fid,
            "依据的 " + (s.name || sid) + " 是 " + age + " 天前取的，实时数据早已过期。",
            "重新取数后再交付，或在方案里标注已过期。");
        }
      }
    }

    // 未知值不能写成数字
    const val = f.value;
    if (status === "unknown" && val !== null && val !== undefined && val !== "" &&
        val !== "unknown" && val !== "null") {
      rep.add("RT-F05", "major", "溯源", "结论 " + fid,
        "状态是 unknown，却写了具体值 " + pyRepr(val) + "。",
        "未知就是未知，值留空，别用 0 或估计值冒充。");
    }
  }

  // RT-F06 自然专项有没有溯源
  const nature = sub(sub(trip, "itinerary"), "nature");
  const factBlob = facts.map((f) => factText(f) + " " + String(f.value || "")).join(" ");
  for (const [key, kws] of Object.entries(NATURE_KEYS)) {
    const sec = nature[key];
    if (!sec) continue;
    if (typeof sec === "object" && !Array.isArray(sec) && !Object.values(sec).some(Boolean)) continue;
    if (!kws.some((kw) => factBlob.includes(kw))) {
      rep.add("RT-F06", "minor", "溯源", "自然专项/" + key,
        "该专项给了具体数据，但结论表里找不到对应的可溯源条目。",
        "把这条数据的来源补进 facts/sources（数据源 + 日期）。");
    }
  }

  // RT-X01 复核记录
  const rt = trip.red_team;
  if (!rt || !(rt.rounds || rt.findings)) {
    rep.add("RT-X01", "info", "复核", "red_team",
      "还没有红方复核记录。",
      "跑一遍质疑清单，把结论写进 red_team（哪怕结论是'无问题'）。");
  }
}

// ------------------------------------------------------------------ 逻辑/安全层
function checkLogic(trip, rep) {
  const it = sub(trip, "itinerary");
  const nature = sub(it, "nature");
  const days = objs(it.days);

  const tide = sub(nature, "beachcombing");
  if (tide && Object.keys(tide).length) {
    const low = findTimeInText(tide.tide_summary || "", "干潮");
    const rng = parseRange(tide.best_window);
    const target = "自然专项/赶海";
    if (low !== null && rng) {
      if (!(rng[0] <= low && low <= rng[1])) {
        rep.add("RT-L01", "blocking", "安全", target,
          "推荐窗口 " + hm(rng[0]) + "-" + hm(rng[1]) + " 没盖住干潮时刻 " +
          hm(low) + "，赶海时可能已经在水里了。",
          "窗口按干潮前后各 1.5 小时重算。");
      } else if (rng[1] > low + 96) {
        rep.add("RT-L04", "blocking", "安全", target,
          "窗口拖到 " + hm(rng[1]) + "，已过干潮 " + (low && hm(low)) + " 约 " +
          pyFixed1((rng[1] - low) / 60) + " 小时，滩涂开始回水。",
          "窗口收在 " + hm(low + 90) + " 之前。");
      }
    } else if (low === null) {
      rep.add("RT-L01", "blocking", "安全", target,
        "给了赶海窗口，但潮汐摘要里没有干潮时刻，窗口无据可依。",
        "补干潮时刻（含数据源与日期）。");
    }
  }

  // 日出活动 vs 天文时刻
  const sun = sub(nature, "sunrise");
  // Python 是 `or`（真值语义）：find 返回 0 会继续试 parse_hm，用 || 而不是 ??
  const sr = findTimeInText(sun.sunrise || "", "日出") || parseHm(sun.sunrise);
  const ss = findTimeInText(sun.sunset || "", "日落") || parseHm(sun.sunset);
  for (const d of days) {
    for (const i of objs(d.items)) {
      const title = i.title || "";
      const rng = parseRange(i.time);
      if (!rng) continue;
      if (title.includes("日出") && sr !== null) {
        if (!(rng[0] - 60 <= sr && sr <= rng[1] + 60)) {
          rep.add("RT-L02", "major", "逻辑", d.day_id + " " + title,
            "日出活动排在 " + hm(rng[0]) + "-" + hm(rng[1]) + "，而当天日出是 " +
            hm(sr) + "，对不上。",
            "把活动窗对准日出前后各 1 小时。");
        }
      }
      if (title.includes("日落") && ss !== null) {
        if (!(rng[0] - 60 <= ss && ss <= rng[1] + 60)) {
          rep.add("RT-L02", "major", "逻辑", d.day_id + " " + title,
            "日落活动排在 " + hm(rng[0]) + "-" + hm(rng[1]) + "，而当天日落是 " +
            hm(ss) + "，对不上。",
            "把活动窗对准日落前后各 1 小时。");
        }
      }
    }
  }

  // 满月夜观星
  const star = sub(nature, "stargazing");
  if (star && Object.keys(star).length) {
    const m = String(star.moon_phase || "").match(/(\d{1,3})\s*%/);
    const illum = m ? parseInt(m[1], 10) : null;
    if (illum !== null && illum > 50) {
      const starItems = days.flatMap((d) => objs(d.items))
        .filter((i) => (i.title || "").includes("观星"));
      if (starItems.length) {
        let haystack = JSON.stringify(star);
        for (const i of starItems) {
          haystack += " " + (i.title || "") + " " + (i.notes || "");
        }
        const aware = /月面|亮星团|月落|月光干扰|不推荐深空|只看月|银河不可见|不仅看银河/
          .test(haystack);
        if (!aware) {
          rep.add("RT-L03", "major", "逻辑", "自然专项/观星",
            "月亮照度 " + illum + "%，这种夜晚银河和深空基本看不到，方案仍照排观星。",
            "明确改成看月面/亮星团，或换到月落之后的时段。");
        }
      }
    }
  }

  // 凌晨与清晨冲突
  let starEnd = null;
  for (const d of days) {
    for (const i of objs(d.items)) {
      if ((i.title || "").includes("观星")) {
        const r = parseRange(i.time);
        if (r) starEnd = Math.max(starEnd === null ? 0 : starEnd, r[1]);
      }
    }
  }
  let sunStart = null;
  for (const d of days) {
    for (const i of objs(d.items)) {
      if ((i.title || "").includes("日出")) {
        const r = parseRange(i.time);
        if (r) sunStart = sunStart === null ? r[0] : Math.min(sunStart, r[0]);
      }
    }
  }
  let gapH = null;
  if (starEnd !== null && sunStart !== null && sunStart < starEnd) {
    gapH = ((24 * 60 - starEnd) + sunStart) / 60.0;
  } else if (starEnd !== null && sunStart !== null) {
    gapH = (sunStart - starEnd) / 60.0;
  }
  if (gapH !== null && gapH < 5) {
    rep.add("RT-L05", "minor", "逻辑", "行程强度",
      "前一夜观星到 " + hm(starEnd) + "，次日 " + hm(sunStart) + " 又要拍日出，" +
      "中间只剩 " + pyFixed1(gapH) + " 小时，含路上和洗漱基本睡不了。",
      "二选一，或把日出改到行程后段。");
  }

  // 踩坑指南
  if (!objs(trip.pitfalls).length) {
    rep.add("RT-L08", "minor", "逻辑", "pitfalls",
      "踩坑指南是空的。",
      "把调研中发现的陷阱、管制、高峰时段写进去。");
  }
}

// ------------------------------------------------------------------ 主流程
export function run(trip) {
  const rep = new Report(trip.trip_id || "travel-plan");
  checkStructure(trip, rep);
  checkBudget(trip, rep);
  checkProvenance(trip, rep);
  checkLogic(trip, rep);
  const order = { blocking: 0, major: 1, minor: 2, info: 3 };
  rep.findings.sort((a, b) =>
    ((order[a.severity] ?? 9) - (order[b.severity] ?? 9)) ||
    (a.rule_id < b.rule_id ? -1 : a.rule_id > b.rule_id ? 1 : 0));
  const now = new Date();
  const pad = (x) => String(x).padStart(2, "0");
  return {
    trip_id: rep.tripId,
    red_team_version: VERSION,
    checked_at: now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate()) +
      " " + pad(now.getHours()) + ":" + pad(now.getMinutes()),
    summary: {
      blocking: rep.count("blocking"), major: rep.count("major"),
      minor: rep.count("minor"), info: rep.count("info"),
    },
    verdict: rep.verdict(),
    findings: rep.findings,
  };
}

export function printReport(r) {
  const icon = { blocking: "[阻断]", major: "[严重]", minor: "[提醒]", info: "[说明]" };
  console.log("红方复核 · " + r.trip_id + " · 结论：" + r.verdict);
  const s = r.summary;
  console.log("阻断 " + s.blocking + " / 严重 " + s.major + " / 提醒 " + s.minor + " / 说明 " + s.info);
  console.log("-".repeat(62));
  if (!r.findings.length) {
    console.log("没挑出问题。");
    return;
  }
  for (const f of r.findings) {
    console.log((icon[f.severity] || "") + " " + f.rule_id + " " + f.target);
    console.log("      " + f.message);
    console.log("      建议：" + f.suggestion);
  }
}

// ------------------------------------------------------------------ CLI
export function main(argv) {
  const args = argv.slice(2);
  const cmd = args[0];
  if (cmd === "rules") {
    for (const [rid, sev, cat, desc] of RULES) {
      console.log(rid + "  " + sev.padEnd(8) + " " + cat + "  " + desc);
    }
    return 0;
  }
  if (cmd !== "check") {
    console.error("用法: node red_team.mjs check <trip> [--json] | rules");
    return 2;
  }
  const tripPath = args[1];
  const asJson = args.includes("--json");
  if (!tripPath || !fs.existsSync(tripPath)) {
    console.error("x 找不到：" + (tripPath || "") + "（传方案目录或 trip.json 路径）");
    return 2;
  }
  let trip, nParts;
  try {
    [trip, , nParts] = resolveTrip(tripPath, true);
  } catch (e) {
    if (e && e.message && !e.code) { console.error("x " + e.message); return 2; }
    throw e;
  }
  if (nParts) console.log("数据来源：parts/（" + nParts + " 个分片）");

  const r = run(trip);
  if (asJson) {
    console.log(JSON.stringify(r, null, 2));
  } else {
    printReport(r);
  }
  return r.summary.blocking ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(main(process.argv));
}
