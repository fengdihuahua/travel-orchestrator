// nature_widgets.mjs · scripts/nature_widgets.py 的 Node 移植（输出逐字节对齐）
// 纯函数 + 字符串拼装，与 Python 版同样的容错口径（坏数据最多少一块，不崩整页）。
import {
  escapeHtml as _e, pyQuote, pyPercent, pyFixed, pyStr, pyGet, pyTruthy, pyOr, isDict,
  pyStrip, pyLstrip, pyLen, pySlice, pyRound, addDays, shiftHours,
} from "./pyfmt.mjs";
import { SIL, STARS, ASTERISMS, RANK_CN } from "./pydata.mjs";

export const MARKER_LIMIT = 12;

const AMAP = "https://uri.amap.com/search?keyword=";
const NAV = "https://uri.amap.com/navigation?";
const MARKERS = "https://uri.amap.com/marker?";
const DIANPING = "https://www.dianping.com/search/keyword/0/0_";
const MEITUAN = "https://i.meituan.com/s/";
const QUNAR_PIAO = "https://touch.piao.qunar.com/touch/list.htm?keyword=";
const CTRIP_TRAIN = "https://trains.ctrip.com/webapp/train/list";
const CTRIP_HOTEL = "https://m.ctrip.com/webapp/hotel/hotelsearch";
const RAIL_12306 = "https://www.12306.cn/index/";
const BOOK_PLATFORMS = [["大众点评", DIANPING], ["美团", MEITUAN]];

// ---------------------------------------------------------------- 基础工具

// Python float(v)：布尔/空/非有限 → null；字符串剥空白后转
export function num(v) {
  if (typeof v === "boolean") return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  if (s === "") return null;
  const f = Number(s);
  return Number.isFinite(f) ? f : null;
}

// '05:45' / '日出 05:45' / '2026-10-01 06:08' -> 分钟数；取不到返回 null
export function hm(text) {
  const m = /(\d{1,2}):(\d{2})/.exec(String(text ?? ""));
  if (!m) return null;
  const h = parseInt(m[1], 10), mi = parseInt(m[2], 10);
  if (h > 23 || mi > 59) return null;
  return h * 60 + mi;
}

export function hmTxt(mins) {
  const r = pyRound(mins);
  return pyPercent("%02d:%02d", Math.floor(r / 60), r - Math.floor(r / 60) * 60);
}

// '05:20-05:45' / '05:45-06:45（10/1）；…' -> [a, b]
export function firstRange(text) {
  const m = /(\d{1,2}):(\d{2})\s*[-~—－至]\s*(\d{1,2}):(\d{2})/.exec(String(text ?? ""));
  if (!m) return null;
  const a = hm(`${m[1]}:${m[2]}`), b = hm(`${m[3]}:${m[4]}`);
  if (a === null || b === null || b <= a) return null;
  return [a, b];
}

function asList(v) {
  if (!pyTruthy(v)) return [];
  if (Array.isArray(v)) return v.filter((x) => x !== null && x !== undefined && x !== "");
  return String(v).split(/[、,，;；/|]+/).map((p) => p.trim()).filter((p) => p);
}

export function searchKeyword(text) {
  const raw = String(text ?? "").trim();
  let s = raw.replace(/[（(][^）)]*[）)]/g, "").trim();
  s = pyStrip(s.replace(/(为免费备选|免费备选|备选点?)$/, ""), " ·-—");
  return pyLen(s) >= 2 ? s : raw;
}

export function mapUrl(place, clean = true) {
  const text = clean ? searchKeyword(place) : String(place ?? "");
  return AMAP + pyQuote(text);
}

export function mapLink(place, keyword = null) {
  if (!pyTruthy(place)) return "";
  return ('<a class="map" href="' + _e(mapUrl(keyword || place)) + '" target="_blank" '
    + 'rel="noopener noreferrer">' + _e(place) + "</a>");
}

// ---------------------------------------------------------------- 生活服务

export function bookingLinks(keyword, kind = "food", extra = null) {
  const kw = String(keyword ?? "").trim();
  const ex = isDict(extra) ? extra : {};
  kind = String(kind ?? "food");
  const links = [];

  if (kind === "train") {
    const f_ = String(pyGet(ex, "from", "") ?? "").trim();
    const t_ = String(pyGet(ex, "to", "") ?? "").trim();
    const d_ = String(pyGet(ex, "date", "") ?? "").trim();
    if (f_.length >= 2 && t_.length >= 2 && /^\d{4}-\d{2}-\d{2}$/.test(d_)) {
      links.push(["携程火车票", CTRIP_TRAIN + "?dStation=" + pyQuote(f_)
        + "&aStation=" + pyQuote(t_) + "&dDate=" + d_]);
    } else {
      links.push(["12306 官网", RAIL_12306]);
    }
    return links;
  }

  if (kind === "ticket" && kw.length >= 2) {
    links.push(["去哪儿门票", QUNAR_PIAO + pyQuote(kw)]);
  } else if (kind === "hotel") {
    const city = String(pyGet(ex, "city", "") ?? "").trim();
    if (city.length >= 2) {
      const qs = ["cityname=" + pyQuote(city)];
      const checkin = String(pyOr(pyGet(ex, "checkin", null), pyGet(ex, "date", null), "") ?? "").trim();
      if (/^\d{4}-\d{2}-\d{2}$/.test(checkin)) {
        qs.push("checkin=" + checkin);
        let checkout = String(pyGet(ex, "checkout", "") ?? "").trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(checkout)) {
          checkout = addDays(checkin, 1) || "";
        }
        if (checkout) qs.push("checkout=" + checkout);
      }
      links.push(["携程订房", CTRIP_HOTEL + "?" + qs.join("&")]);
    }
  }

  if (kw.length >= 2) {
    const base = kind === "hotel" ? [...BOOK_PLATFORMS].reverse() : BOOK_PLATFORMS;
    for (const [label, tpl] of base) links.push([label, tpl + pyQuote(kw)]);
  }
  return links;
}

export function linkChips(links, cls = "bk") {
  const items = (links || [])
    .map(([a, b]) => [String(a).trim(), String(b).trim()])
    .filter(([a, b]) => a && b.startsWith("http"));
  if (!items.length) return "";
  return ('<span class="' + _e(cls) + 's">'
    + items.map(([t, u]) => '<a class="' + _e(cls) + '" href="' + _e(u) + '" target="_blank" '
      + 'rel="noopener noreferrer">' + _e(t) + "</a>").join("")
    + "</span>");
}

// ---------------------------------------------------------------- 路线 / 多点

function cleanName(name) {
  const s = String(name ?? "").replace(/[,|&=]+/g, " ");
  return s.split(/\s+/).filter(Boolean).join(" ");
}

function pt(x) {
  if (!Array.isArray(x) || x.length < 2) return null;
  const lng = num(x[0]), lat = num(x[1]);
  if (lng === null || lat === null) return null;
  const name = (x.length > 2 && x[2] !== null && x[2] !== undefined) ? pyStr(x[2]).trim() : "";
  return [lng, lat, name];
}

export function markersUrl(points, limit = MARKER_LIMIT) {
  let ps = (points || []).map(pt).filter(Boolean);
  if (limit) ps = ps.slice(0, limit);
  if (!ps.length) return "";
  const seg = ps.map((p) => pyPercent("%.5f,%.5f,%s", p[0], p[1], cleanName(p[2]))).join("|");
  return MARKERS + "markers=" + pyQuote(seg);
}

export function routeUrl(points, mode = "car") {
  const ps = (points || []).map(pt).filter(Boolean);
  if (ps.length < 2) return "";
  const a = ps[0], b = ps[ps.length - 1];
  let mid = ps.length > 2 ? ps[Math.floor(ps.length / 2)] : null;
  if (mid !== null && mid[0] === a[0] && mid[1] === a[1]) mid = null;
  const q = ["from=" + pyPercent("%.5f,%.5f,%s", a[0], a[1], cleanName(a[2]) || "起点"),
    "to=" + pyPercent("%.5f,%.5f,%s", b[0], b[1], cleanName(b[2]) || "终点"),
    "mode=" + (cleanName(mode) || "car")];
  if (mid !== null) q.push("via=" + pyPercent("%.5f,%.5f,%s", mid[0], mid[1], cleanName(mid[2]) || "途经"));
  return NAV + pyQuote(q.join("&"), "=&,");
}

const DEG2RAD = Math.PI / 180.0; // math.radians：x * (pi/180)
const RAD2DEG = 180.0 / Math.PI; // math.degrees：x * (180/pi)

export function projectPoints(points, width = 640, height = 300, pad = 34) {
  const ps = (points || []).map(pt).filter(Boolean);
  if (!ps.length) return [];
  const lngs = ps.map((p) => p[0]), lats = ps.map((p) => p[1]);
  const loLng = Math.min(...lngs), hiLng = Math.max(...lngs);
  const loLat = Math.min(...lats), hiLat = Math.max(...lats);
  const midLat = (loLat + hiLat) / 2.0;
  const k = Math.max(Math.cos(midLat * DEG2RAD), 0.2);
  const iw = Math.max(width - 2 * pad, 1), ih = Math.max(height - 2 * pad, 1);
  if ((hiLng - loLng) < 1e-6 && (hiLat - loLat) < 1e-6) {
    return ps.map((p) => [pad + iw / 2.0, pad + ih / 2.0, p[0], p[1], p[2]]);
  }
  const spanX = Math.max((hiLng - loLng) * k, 1e-9);
  const spanY = Math.max(hiLat - loLat, 1e-9);
  const scale = Math.min(iw / spanX, ih / spanY);
  const ox = pad + (iw - spanX * scale) / 2.0;
  const oy = pad + (ih - spanY * scale) / 2.0;
  return ps.map(([lng, lat, name]) => [
    ox + (lng - loLng) * k * scale,
    oy + (hiLat - lat) * scale,
    lng, lat, name,
  ]);
}

export function projectRoute(points, radius = 0.045) {
  const ps = (points || []).map(pt).filter(Boolean);
  if (!ps.length) return [];
  const pad = 0.06;
  const proj = projectPoints(ps.map((p) => [p[0], p[1], p[2]]), 1000, 1000, pad * 1000);
  const out = proj.map((p) => [p[0] / 1000.0, p[1] / 1000.0, p[2], p[3], p[4]]);
  if (out.length < 2) {
    return out.map((p) => [pyRound(p[0], 6), pyRound(p[1], 6), p[2], p[3], p[4]]);
  }
  const md = radius;
  for (let iter = 0; iter < 220; iter++) {
    let moved = false;
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const dx = out[j][0] - out[i][0];
        const dy = out[j][1] - out[i][1];
        const d2 = dx * dx + dy * dy;
        if (d2 >= md * md) continue;
        if (d2 <= 1e-12) {
          out[j][0] += md * 0.5;
          moved = true;
          continue;
        }
        const d = Math.sqrt(d2);
        const push = (md - d) / d * 0.5 * 0.45;
        out[i][0] -= dx * push;
        out[i][1] -= dy * push;
        out[j][0] += dx * push;
        out[j][1] += dy * push;
        moved = true;
      }
    }
    for (const p of out) {
      p[0] = Math.min(Math.max(p[0], pad * 0.5), 1 - pad * 0.5);
      p[1] = Math.min(Math.max(p[1], pad * 0.5), 1 - pad * 0.5);
    }
    if (!moved) break;
  }
  return out.map((p) => [pyRound(p[0], 6), pyRound(p[1], 6), p[2], p[3], p[4]]);
}

export function splitPlaces(text) {
  return String(text ?? "").split(/[、,，;；/|]+/).map((p) => p.trim()).filter((p) => p);
}

export function placeLinks(value, sep = "") {
  if (!pyTruthy(value)) return "";
  const items = Array.isArray(value) ? value : splitPlaces(value);
  const chunks = [];
  for (const item of items) {
    let name, note;
    if (isDict(item)) {
      name = pyTruthy(pyGet(item, "name")) ? pyGet(item, "name") : pyGet(item, "place", "");
      note = pyTruthy(pyGet(item, "note")) ? pyGet(item, "note") : pyGet(item, "why", "");
    } else {
      name = String(item); note = "";
    }
    if (!pyTruthy(name)) continue;
    let one = mapLink(name);
    if (pyTruthy(note)) one += '<span class="pl-note">' + _e(note) + "</span>";
    chunks.push('<span class="pl-item">' + one + "</span>");
  }
  return chunks.length ? '<span class="pl">' + chunks.join(sep) + "</span>" : "";
}

export function routeStops(it, detail = false) {
  const days = (pyGet(it, "days", []) || []).filter(isDict);
  const rows = [];
  let seq = 0;
  days.forEach((d, ix) => {
    const did = pyTruthy(pyGet(d, "day_id")) ? pyGet(d, "day_id") : "D" + String(ix + 1);
    const stops = [];
    for (const i of (pyGet(d, "items", []) || []).filter(isDict)) {
      const name = String(pyGet(i, "place", "") ?? "").trim();
      if (!name) continue;
      if (stops.some((s) => name === s[0])) continue;
      const geo = isDict(pyGet(i, "geo")) ? pyGet(i, "geo") : {};
      seq += 1;
      const item = [name, seq, num(pyGet(geo, "lat")), num(pyGet(geo, "lng"))];
      if (detail) {
        item.push(String(pyGet(i, "title", "") ?? "").trim());
        item.push(String(pyGet(i, "kind", "") ?? "").trim());
      }
      stops.push(item);
    }
    if (stops.length) rows.push([did, stops]);
  });
  return [rows, seq];
}

// ---------------------------------------------------------------- 鸟种剪影

const SHAPE_RULES = [
  [["laridae", "鸥"], "gull"],
  [["scolopacidae", "charadriidae", "鹬", "鸻"], "wader"],
  [["anatidae", "cygnus", "anser", "天鹅", "雁", "鸭"], "swan"],
  [["accipitridae", "falconidae", "鹰", "隼", "鸮", "雕", "鸢"], "raptor"],
  [["phalacrocoracidae", "gaviidae", "podicipedidae", "ardeidae", "gruidae",
    "鸬鹚", "潜鸟", "鹭", "鹤"], "longneck"],
];

function birdShape(...texts) {
  const blob = texts.map((t) => String(t ?? "")).join(" ").toLowerCase();
  for (const [keys, shape] of SHAPE_RULES) {
    if (keys.some((k) => blob.includes(k.toLowerCase()))) return shape;
  }
  return "songbird";
}

export function silhouetteSvg(shape) {
  return ('<svg viewBox="0 0 32 32" fill="currentColor" fill-opacity=".2" '
    + 'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
    + 'stroke-linejoin="round" aria-hidden="true">'
    + (SIL[shape] || SIL.songbird) + "</svg>");
}

// ---------------------------------------------------------------- 鸟种卡

function rankItems(sp) {
  const raw = pyOr(pyGet(sp, "status", null), pyGet(sp, "保护等级", null), []);
  if (typeof raw === "string") raw = [raw];
  const out = [];
  for (const item of raw || []) {
    if (isDict(item)) {
      out.push({
        rank: String(pyTruthy(pyGet(item, "rank")) ? pyGet(item, "rank") : pyGet(item, "评级", "") ?? "").trim(),
        source: String(pyTruthy(pyGet(item, "source")) ? pyGet(item, "source") : pyGet(item, "来源", "") ?? "").trim(),
      });
    } else if (pyTruthy(item)) {
      out.push({ rank: String(item).trim(), source: "" });
    }
  }
  return out.filter((x) => pyTruthy(x.rank));
}

function pickRank(items) {
  for (const it of items) if (it.source.toLowerCase().includes("iucn")) return it;
  for (const it of items) if (it.rank.toUpperCase() in RANK_CN) return it;
  return items.length ? items[0] : null;
}

const PLAIN_BREAKS = "（(，,；;：:、";
const STRIP_CHARS = " 　·．.。";

export function splitSpeciesText(text) {
  const t = String(text ?? "").trim();
  if (!t) return ["", ""];
  let cut = Array.from(t).length;
  const chars = Array.from(t);
  for (let i = 0; i < chars.length; i++) {
    if (PLAIN_BREAKS.includes(chars[i])) { cut = i; break; }
  }
  let name = pyStrip(chars.slice(0, cut).join(""), STRIP_CHARS);
  let note = pyLstrip(chars.slice(cut).join(""), PLAIN_BREAKS + " 　").trim();
  if (!pyTruthy(name)) return [t, note];
  if (pyLen(name) > 12) {
    if (!pyTruthy(note)) note = Array.from(t).slice(Array.from(name).length).join("");
    name = pySlice(name, 12) + "…";
  }
  return [name, note];
}

function speciesNormalize(sp) {
  if (typeof sp === "string") return { name: sp.trim() };
  if (isDict(sp)) {
    const d = { ...sp };
    d.name = String(pyTruthy(pyGet(d, "name")) ? pyGet(d, "name") : pyGet(d, "中文名", "") ?? "").trim();
    d.sci = String(pyTruthy(pyGet(d, "sci")) ? pyGet(d, "sci") : pyGet(d, "学名", "") ?? "").trim();
    return d;
  }
  return {};
}

function speciesLinks(sp, name, compact) {
  const q = pyQuote(name);
  let taxa = pyGet(sp, "taxa_url", null);
  if (!pyTruthy(taxa)) taxa = pyGet(sp, "species_url", null);
  if (!pyTruthy(taxa)) taxa = pyGet(sp, "iNat", null);
  taxa = String(pyTruthy(taxa) ? taxa : "https://www.inaturalist.org/search?q=" + q);
  let links = [
    [taxa, "图鉴页"],
    ["https://image.baidu.com/search/index?tn=baiduimage&word=" + q, "查图片"],
    ["https://www.birdreport.cn/", "本地记录"],
    ["https://www.birdreport.cn/", "观测记录中心"],
  ];
  if (compact) links = links.slice(0, 2);
  return links.map(([u, t]) => '<a class="sp-link" href="' + _e(u) + '" target="_blank" rel="noopener noreferrer">' + _e(t) + "</a>").join("");
}

export function speciesList(species) {
  const items = (species || []).map(speciesNormalize).filter((x) => pyTruthy(pyGet(x, "name")));
  if (!items.length) return "";
  const cards = [];
  for (const [i, sp] of items.entries()) {
    let [name, dangling] = splitSpeciesText(pyGet(sp, "name"));
    if (!pyTruthy(name)) name = pyGet(sp, "name");
    const sci = pyOr(pyGet(sp, "sci", null), "") || "";
    const tax = pyOr(pyGet(sp, "family", null), pyGet(sp, "分类", null), "") || "";
    const ranks = rankItems(sp);
    const best = pickRank(ranks);
    const intro = pyOr(pyGet(sp, "intro", null), pyGet(sp, "简介", null), "") || "";
    const isPlain = ![sci, tax, ranks.length, intro].some(pyTruthy);
    const shape = pyGet(sp, "shape", "") || birdShape(name, tax);
    const cls = pyPercent("sp sp-c%d", i % 6 + 1);
    if (isPlain) {
      let noteHtml = "";
      if (pyTruthy(dangling)) {
        noteHtml = pyPercent('<span class="sp-note" title="%s">%s</span>', _e(dangling), _e(dangling));
      }
      cards.push(pyPercent('<div class="%s sp-plain"><i class="sp-ico">%s</i>'
        + '<span class="sp-name">%s</span>%s%s</div>',
        cls, silhouetteSvg(shape), _e(name), noteHtml,
        speciesLinks(sp, name, true)));
      continue;
    }

    let badgeHtml;
    if (best) {
      const code = best.rank.toUpperCase();
      const cn = pyGet(RANK_CN, code, "");
      let badge = code + (cn ? pyPercent("（%s）", cn) : "");
      if (!String(best.source || "").toLowerCase().includes("iucn")) badge += "*";
      badgeHtml = pyPercent('<span class="sp-badge">%s</span>', _e(badge));
    } else {
      badgeHtml = '<span class="sp-badge sp-badge-none">保护等级未查到</span>';
    }

    const meta = [];
    if (pyTruthy(tax)) meta.push(["分类", tax]);
    if (pyTruthy(dangling)) meta.push(["说明", dangling]);
    if (ranks.length) {
      let text = ranks.slice(0, 5).map((r) => r.rank + (r.source ? pyPercent("（%s）", r.source) : "")).join("；");
      if (ranks.length > 5) text += pyPercent("；等 %d 项", ranks.length);
      meta.push(["保护等级", text]);
    }
    let months = pyOr(pyGet(sp, "best_months", null), pyGet(sp, "最佳月份", null), "");
    if (pyTruthy(months)) {
      if (Array.isArray(months)) months = months.map((m) => String(m)).join("、");
      meta.push(["本地最佳月份", String(months)]);
    }
    if (pyTruthy(pyGet(sp, "global_records"))) {
      meta.push(["全球观测记录", String(pyGet(sp, "global_records"))]);
    }
    const metaHtml = meta.map(([k, v]) => pyPercent('<div class="sp-m"><dt>%s</dt><dd>%s</dd></div>', _e(k), _e(v))).join("");

    const introSrc = pyOr(pyGet(sp, "intro_source", null), pyGet(sp, "简介来源", null), "") || "";
    const body = ['<div class="sp-links">' + speciesLinks(sp, name, false) + "</div>"];
    if (metaHtml) body.push('<dl class="sp-metas">' + metaHtml + "</dl>");
    if (pyTruthy(intro)) body.push(pyPercent('<p class="sp-intro">%s</p>', _e(intro)));
    if (pyTruthy(introSrc)) {
      body.push(pyPercent('<p class="sp-src">简介来源：<a class="src-link" href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>',
        _e(introSrc), _e(introSrc)));
    }
    if (ranks.some((r) => pyTruthy(r.source))) {
      body.push('<p class="sp-src">等级来源见上；iNaturalist 不含《国家重点保护野生'
        + '动物名录》，涉及拍摄许可或执法请人工核对官方名录。</p>');
    }

    cards.push('<details class="' + cls + '">'
      + '<summary><i class="sp-ico">' + silhouetteSvg(shape) + "</i>"
      + '<span class="sp-name">' + _e(name) + "</span>"
      + (sci ? '<span class="sp-sci">' + _e(sci) + "</span>" : "")
      + badgeHtml
      + '<span class="sp-more">查图 · 详情</span></summary>'
      + '<div class="sp-body">' + body.join("") + "</div></details>");
  }
  return '<div class="splist">' + cards.join("") + "</div>";
}


// ---------------------------------------------------------------- 星图
// 内置亮星表 / 星群在 pydata.mjs（由 Python 原样导出）

function jdUtc(dt) {
  // 儒略日；dt = {y,m,d,hh,mm,ss} 视为 UTC
  let y = dt.y, m = dt.m;
  const d = dt.d + (dt.hh + dt.mm / 60.0 + dt.ss / 3600.0) / 24.0;
  if (m <= 2) { y -= 1; m += 12; }
  const a = Math.floor(y / 100);
  const b = 2 - a + Math.floor(a / 4);
  return Math.trunc(365.25 * (y + 4716)) + Math.trunc(30.6001 * (m + 1)) + d + b - 1524.5;
}

function gmstHours(jd) {
  const t = (jd - 2451545.0) / 36525.0;
  const deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
    + 0.000387933 * t * t - t * t * t / 38710000.0);
  return (deg % 360.0) / 15.0;
}

function altaz(raH, decD, latD, lstH) {
  // 赤道坐标 -> 地平坐标。返回 [高度角, 方位角]，方位从正北起顺时针
  const ha = ((lstH - raH) * 15.0 + 180.0) % 360.0 - 180.0;
  const haR = ha * DEG2RAD, decR = decD * DEG2RAD, latR = latD * DEG2RAD;
  let sinAlt = Math.sin(decR) * Math.sin(latR) + Math.cos(decR) * Math.cos(latR) * Math.cos(haR);
  sinAlt = Math.max(-1.0, Math.min(1.0, sinAlt));
  const alt = Math.asin(sinAlt);
  const denom = Math.cos(alt) * Math.cos(latR);
  if (Math.abs(denom) < 1e-9) return [alt * RAD2DEG, 0.0];
  const cosAz = Math.max(-1.0, Math.min(1.0, (Math.sin(decR) - sinAlt * Math.sin(latR)) / denom));
  let az = Math.acos(cosAz) * RAD2DEG;
  if (Math.sin(haR) > 0) az = 360.0 - az;
  return [alt * RAD2DEG, az % 360.0];
}

// '2026-10-01T21:30' / '2026-10-01 21:30' -> {y,m,d,hh,mm,ss}；解析不了返回 null
export function parseAt(value) {
  const s = String(value ?? "").trim().replace(/\//g, "-");
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$/.exec(s);
  if (m) {
    const [, ys, ms, ds, hs, mis, ss2] = m;
    const y = +ys, mo = +ms, d = +ds, hh = +hs, mi = +mis, sec = ss2 ? +ss2 : 0;
    if (mo >= 1 && mo <= 12 && d >= 1 && d <= 31 && hh <= 23 && mi <= 59 && sec <= 61) {
      return { y, m: mo, d, hh, mm: mi, ss: sec };
    }
    return null;
  }
  m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (m) return { y: +m[1], m: +m[2], d: +m[3], hh: 0, mm: 0, ss: 0 };
  return null;
}

const SKY_W = 460, SKY_H = 420, SKY_CX = 230, SKY_CY = 198, SKY_R = 150;

export function starMapSvg(cfg) {
  const lat = num(pyGet(cfg, "lat", null));
  const lng = num(pyGet(cfg, "lng", null));
  const when = parseAt(pyGet(cfg, "at", null));
  if (lat === null || lng === null || when === null) return "";
  let tz = num(pyGet(cfg, "tz", null));
  tz = tz === null ? 8.0 : tz;
  const utc = shiftHours(when, tz); // when - timedelta(hours=tz)
  const jd = jdUtc(utc);
  const lst = (gmstHours(jd) + lng / 15.0) % 24.0;

  const placed = [];
  for (const [name, [ra, dec, mag]] of Object.entries(STARS)) {
    const [alt, az] = altaz(ra, dec, lat, lst);
    if (alt <= 0.4) continue;
    const r = SKY_R * (1.0 - alt / 90.0);
    const x = SKY_CX + r * Math.sin(az * DEG2RAD);
    const y = SKY_CY - r * Math.cos(az * DEG2RAD);
    placed.push([name, x, y, mag, alt, az]);
  }

  const coords = {};
  for (const p of placed) coords[p[0]] = [p[1], p[2]];
  const lines = [], labels = [], occupied = [];
  for (const [cname, members] of ASTERISMS) {
    const pts = members.filter((mm) => mm in coords).map((mm) => coords[mm]);
    if (pts.length < 2) continue;
    const d = "M" + pts.map((p) => pyPercent("%.1f %.1f", p[0], p[1])).join(" L");
    lines.push(pyPercent('<path class="cst" d="%s"/>', d));
    if (pts.length >= Math.max(2, members.length - 1)) {
      const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
      const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
      const vx = cx - SKY_CX, vy = cy - SKY_CY;
      const norm = Math.hypot(vx, vy) || 1.0;
      let lx = cx + vx / norm * 30, ly = cy + vy / norm * 30 + 4;
      const dist = Math.hypot(lx - SKY_CX, ly - SKY_CY);
      const cap = SKY_R * 0.84;
      if (dist > cap) {
        lx = SKY_CX + (lx - SKY_CX) / dist * cap;
        ly = SKY_CY + (ly - SKY_CY) / dist * cap;
      }
      labels.push(pyPercent('<text class="cstlbl" x="%.1f" y="%.1f">%s</text>', lx, ly, _e(cname)));
      occupied.push([lx, ly, 1700.0]);
    }
  }

  const stars = [];
  const byMag = placed.slice().sort((a, b) => a[3] - b[3]);
  for (const [name, x, y, mag, alt, az] of byMag) {
    const rad = Math.max(1.0, 3.9 - 0.42 * mag);
    const tip = pyPercent("%s｜高度 %.0f°｜方位 %.0f°｜星等 %.2f", name, alt, az, mag);
    stars.push(pyPercent('<circle class="st" cx="%.1f" cy="%.1f" r="%.2f"><title>%s</title></circle>',
      x, y, rad, _e(tip)));
    if (mag <= 1.7 && alt > 7) {
      if (occupied.some(([ox, oy, d2]) => (x - ox) * (x - ox) + (y - oy) * (y - oy) < d2)) continue;
      const anchor = x < SKY_CX + 60 ? "start" : "end";
      const dx = anchor === "start" ? 7 : -7;
      const lx = x + dx, ly = y + 3.4;
      labels.push(pyPercent('<text class="stlbl" x="%.1f" y="%.1f" text-anchor="%s">%s</text>',
        lx, ly, anchor, _e(name)));
      occupied.push([lx + (anchor === "start" ? 14 : -14), ly, 900.0]);
    }
  }

  if (!placed.length) return "";
  const ring = ('<circle class="skygrid" cx="%d" cy="%d" r="%.1f"/>'
    + '<circle class="skygrid" cx="%d" cy="%d" r="%.1f"/>');
  const ringTxt = pyPercent(ring, SKY_CX, SKY_CY, SKY_R * 2 / 3.0, SKY_CX, SKY_CY, SKY_R / 3.0);
  const cross = pyPercent('<path class="skygrid" d="M%d %d V%d M%d %d H%d"/>',
    SKY_CX, SKY_CY - SKY_R, SKY_CY + SKY_R, SKY_CX - SKY_R, SKY_CY, SKY_CX + SKY_R);
  const cards = [
    [SKY_CX, SKY_CY - SKY_R - 12, "北"],
    [SKY_CX + SKY_R + 18, SKY_CY + 4, "东"],
    [SKY_CX, SKY_CY + SKY_R + 24, "南"],
    [SKY_CX - SKY_R - 18, SKY_CY + 4, "西"],
  ];
  const cardsTxt = cards.map(([x, y, t]) =>
    pyPercent('<text class="cardi" x="%.1f" y="%.1f" text-anchor="middle">%s</text>', x, y, t)).join("");
  return pyPercent('<svg class="skychart" viewBox="0 0 %d %d" role="img" aria-label="当地当时的示意星图">'
    + '<defs><clipPath id="to-skyclip"><circle cx="%d" cy="%d" r="%d"/></clipPath></defs>'
    + '<circle class="sky-bg" cx="%d" cy="%d" r="%d"/>'
    + '<g clip-path="url(#to-skyclip)">%s%s%s%s</g>'
    + '<circle class="sky-ring" cx="%d" cy="%d" r="%d"/>%s</svg>',
    SKY_W, SKY_H, SKY_CX, SKY_CY, SKY_R,
    SKY_CX, SKY_CY, SKY_R,
    ringTxt, cross, lines.join(""), stars.join(""),
    SKY_CX, SKY_CY, SKY_R, labels.join("") + cardsTxt);
}

export function moonDiscSvg(illumPct, waxing = true, size = 56) {
  const k = Math.max(0.0, Math.min(1.0, Number(illumPct) / 100.0));
  const r = 22.0;
  const rx = r * Math.abs(2 * k - 1);
  const sweep = k < 0.5 ? 1 : 0;
  const lit = pyPercent('<path d="M 0 %.1f A %.1f %.1f 0 0 1 0 %.1f A %.2f %.1f 0 0 %d 0 %.1f Z"/>',
    -r, r, r, r, rx, r, sweep, -r);
  const flip = waxing ? "" : ' transform="scale(-1,1)"';
  return pyPercent('<svg class="moondisc" viewBox="-30 -30 60 60" width="%d" height="%d" '
    + 'role="img" aria-label="月相示意，照度 %.0f%%">'
    + '<circle r="%.1f" class="moon-dark"/>'
    + '<g class="moon-lit"%s>%s</g>'
    + '<circle r="%.1f" class="moon-edge"/></svg>',
    size, size, k * 100, r, flip, lit, r);
}

function moonIllum(s) {
  let pct = num(pyGet(s, "illumination_percent", null));
  const text = String(pyOr(pyGet(s, "moon_phase", null), ""));
  if (pct === null) {
    const m = /(\d{1,3})\s*%/.exec(text);
    pct = m ? Number(m[1]) : null;
  }
  if (pct === null) return [null, true];
  let waxing = !["亏", "下弦", "残月"].some((k) => text.includes(k));
  if (["新", "娥眉月", "上弦", "盈"].some((k) => text.includes(k))) waxing = true;
  return [pct, waxing];
}

export function starSection(s) {
  const out = [];
  const cfg = pyOr(pyGet(s, "chart", null), {});
  const svg = pyTruthy(cfg) ? starMapSvg(cfg) : "";
  const [pct, waxing] = moonIllum(s);

  const right = [];
  if (pct !== null) {
    right.push(pyPercent('<figure class="skymoon">%s<figcaption>月面照度 <b>%.0f%%</b><br>'
      + '<span>%s</span></figcaption></figure>',
      moonDiscSvg(pct, waxing), pct,
      _e(!waxing ? "亏凸月（亮面在左）" : "亮面在右")));
  }
  const targets = pyOr(pyGet(s, "targets", null), []);
  if (targets.length) {
    const chips = [];
    for (const t of targets) {
      if (isDict(t)) {
        const tNote = pyOr(pyGet(t, "note", null), pyGet(t, "how", null));
        chips.push(pyPercent('<span class="tg"><b>%s</b>%s%s</span>',
          _e(pyGet(t, "name", null)),
          pyTruthy(pyGet(t, "type", null)) ? pyPercent("<em>%s</em>", _e(pyGet(t, "type", null))) : "",
          pyTruthy(tNote) ? pyPercent('<span class="tg-nt">%s</span>', _e(tNote)) : ""));
      } else {
        chips.push(pyPercent('<span class="tg"><b>%s</b></span>', _e(t)));
      }
    }
    right.push('<div class="tgs"><span class="tgs-t">今晚看什么</span>' + chips.join("") + "</div>");
  }

  let left = "";
  if (svg) {
    const latN = num(pyGet(cfg, "lat", null));
    const lngN = num(pyGet(cfg, "lng", null));
    const cap = pyPercent("按 %s（%.2f°N %.2f°E）%s 当地时刻计算，只画地平线以上的亮星；"
      + "未做大气折射与光污染修正，权当认星提纲，别当精密星历。",
      _e(pyOr(pyGet(cfg, "place", null), "观测点")),
      latN === null ? 0.0 : latN, lngN === null ? 0.0 : lngN,
      _e(pyOr(pyGet(cfg, "at", null), "")));
    left = ('<figure class="skywrap">' + svg
      + '<figcaption class="skycap">' + cap + "</figcaption></figure>");
  }

  if (left || right.length) {
    out.push('<div class="skyrow">' + left
      + (right.length ? '<div class="skycol">' + right.join("") + "</div>" : "")
      + "</div>");
  }

  const links = Object.assign({}, pyOr(pyGet(s, "links", null), {}));
  if (!("在线星图（Stellarium）" in links)) links["在线星图（Stellarium）"] = "https://stellarium-web.org/";
  if (!("光污染地图" in links)) links["光污染地图"] = "https://www.lightpollutionmap.info/";
  out.push('<div class="sp-links">' + Object.entries(links).map(([t, u]) =>
    pyPercent('<a class="sp-link" href="%s" target="_blank" rel="noopener noreferrer">%s</a>',
      _e(u), _e(t))).join("") + "</div>");
  return out.join("");
}

// ---------------------------------------------------------------- 天光条 + 霞光

function skyColorAt(mins, sr, ss) {
  if (sr === null && ss === null) return "#dfeaf6";
  if (sr !== null && mins < sr) return mins < sr - 70 ? "#2b3a67" : "#5d63a8";
  if (ss !== null && mins > ss) return mins > ss + 70 ? "#2b3a67" : "#5d63a8";
  return "#cfeaf7";
}

export function sunBandSvg(r) {
  const sr = hm(pyGet(r, "sunrise", null));
  const ss = hm(pyGet(r, "sunset", null));
  const blue = firstRange(pyGet(r, "blue_hour", null));
  const gold = firstRange(pyGet(r, "golden_hour", null));
  const anchors = [sr, ss, blue ? blue[0] : null, blue ? blue[1] : null,
    gold ? gold[0] : null, gold ? gold[1] : null].filter((x) => x !== null);
  if (!anchors.length) return "";
  const lo = Math.floor((Math.min(...anchors) - 50) / 60) * 60;
  let hi = Math.floor(((Math.max(...anchors) + 50) + 59) / 60) * 60;
  hi = Math.max(hi, lo + 120);
  const span = hi - lo;
  const fx = (m) => 34 + (m - lo) / span * (640 - 34 - 12);

  const W = 640, barY = 34, barH = 44;
  const stops = [[0.0, skyColorAt(lo, sr, ss)]];
  for (let m = lo; m <= hi; m += 15) stops.push([(m - lo) / span, skyColorAt(m, sr, ss)]);
  stops.push([1.0, skyColorAt(hi, sr, ss)]);
  const grad = stops.map(([o, c]) => pyPercent('<stop offset="%.4f" stop-color="%s"/>', o, c)).join("");

  const bands = [];
  if (blue) bands.push([blue, "蓝调", "b-blue"]);
  if (gold) bands.push([gold, "黄金", "b-gold"]);
  if (ss !== null) {
    bands.push([[ss - 60, ss], "黄金", "b-gold"]);
    bands.push([[ss, ss + 25], "蓝调", "b-blue"]);
  }
  const bandHtml = [];
  for (const [[a0, b0], label, cls] of bands) {
    if (b0 <= lo || a0 >= hi) continue;
    const a = Math.max(a0, lo), b = Math.min(b0, hi);
    const w = fx(b) - fx(a);
    if (w <= 0) continue;
    bandHtml.push(pyPercent('<rect class="%s" x="%.1f" y="%d" width="%.1f" height="%d" rx="4"/>',
      cls, fx(a), barY, w, barH));
    if (w >= 34) {
      bandHtml.push(pyPercent('<text class="bandlbl" x="%.1f" y="%d" text-anchor="middle">%s</text>',
        fx(a) + w / 2, barY - 6, _e(label)));
    }
  }
  const ticks = [];
  for (const [m, label, cls] of [[sr, "日出", "t-rise"], [ss, "日落", "t-set"]]) {
    if (m === null) continue;
    const x = fx(m);
    ticks.push(pyPercent('<path class="%s" d="M%.1f %d V%d"/>', cls, x, barY - 3, barY + barH + 3));
    ticks.push(pyPercent('<text class="ticklbl" x="%.1f" y="%d" text-anchor="middle">%s %s</text>',
      x, barY + barH + 17, _e(label), hmTxt(m)));
  }
  const axis = [];
  const step = span > 480 ? 120 : 60;
  let m = lo + (step - lo % step) % step;
  while (m <= hi) {
    axis.push(pyPercent('<text class="axlbl" x="%.1f" y="%d" text-anchor="middle">%s</text>',
      fx(m), barY + barH + 34, hmTxt(m)));
    m += step;
  }
  return pyPercent('<svg class="sunband-svg" viewBox="0 0 %d 118" role="img" '
    + 'aria-label="日出日落天光时间条">'
    + '<defs><linearGradient id="to-sky" x1="0" y1="0" x2="1" y2="0">%s</linearGradient></defs>'
    + '<rect x="34" y="%d" width="%d" height="%d" rx="10" fill="url(#to-sky)"/>%s%s%s</svg>',
    W, grad, barY, 640 - 34 - 12, barH, bandHtml.join(""), ticks.join(""), axis.join(""));
}

export function glowMeter(gl) {
  if (!pyTruthy(gl)) return "";
  const score = num(pyGet(gl, "score", null));
  const level = String(pyOr(pyGet(gl, "level", null), ""));
  let reasons = asList(pyGet(gl, "reasons", null));
  if (!reasons.length) reasons = asList(pyGet(gl, "reason", null));
  const inputs = pyOr(pyGet(gl, "inputs", null), {});
  if (score === null && !reasons.length) {
    const rating = String(pyOr(pyGet(gl, "rating", null), ""));
    if (!rating) return "";
    return ('<div class="glow"><div class="glow-head"><b>霞光指数</b>'
      + '<span class="glow-lv">参考</span></div>'
      + pyPercent('<p class="glow-p">%s</p>', _e(rating))
      + '<p class="glow-note">模型参考值，非官方预报，出发前结合实景判断。</p></div>');
  }
  let dots = "";
  if (score !== null) {
    const n = Math.trunc(Math.max(0, Math.min(3, pyRound(score))));
    dots = [0, 1, 2].map((i) => pyPercent('<i class="%s"></i>', i < n ? "on" : "")).join("");
  }
  const inLabels = [["cloud_cover_low_pct", "低云"], ["cloud_cover_mid_pct", "中高云"],
    ["cloud_cover_high_pct", "高云"], ["visibility_km", "能见度"],
    ["relative_humidity_pct", "湿度"], ["aerosol_optical_depth", "气溶胶"]];
  const chips = [];
  for (const [k, label] of inLabels) {
    const v = num(pyGet(inputs, k, null));
    if (v === null) continue;
    const unit = { visibility_km: " km", aerosol_optical_depth: "" }[k] ?? "%";
    chips.push(pyPercent('<span class="gd"><b>%s</b>%.0f%s</span>', _e(label), v, unit));
  }
  return ('<div class="glow"><div class="glow-head"><b>霞光指数</b>'
    + (level ? pyPercent('<span class="glow-lv">%s</span>', _e(level)) : "")
    + (dots ? '<span class="glow-dots">' + dots + "</span>" : "")
    + (score !== null ? pyPercent('<span class="glow-num">%d/3</span>', Math.trunc(pyRound(score))) : "")
    + "</div>"
    + (reasons.length ? '<ul class="glow-why">' + reasons.slice(0, 4).map((x) => "<li>" + _e(x) + "</li>").join("") + "</ul>" : "")
    + (chips.length ? '<div class="glow-data">' + chips.join("") + "</div>" : "")
    + '<p class="glow-note">按云量分层、通透度、水汽算出的参考值，不是官方预报；'
    + "出门前扫一眼实景天色更靠谱。</p></div>");
}

export function sunSection(r, glow = null) {
  const out = [];
  const svg = sunBandSvg(r);
  if (svg) {
    const legend = ('<div class="sblegend">'
      + '<span class="sb sb-blue">蓝调时刻</span>'
      + '<span class="sb sb-gold">黄金时刻</span>'
      + '<span class="sb sb-day">白昼</span>'
      + '<span class="sb sb-night">夜间</span></div>');
    const cap = ("蓝调与黄金时刻取自接口；黄昏侧按同一套定义（日落前后）推算，未单独取数。"
      + "条上窄到放不下字的时段看下方色块图例对位置。");
    out.push('<figure class="sunband">' + svg + legend
      + '<figcaption class="skycap">' + cap + "</figcaption></figure>");
  }
  if (pyTruthy(glow)) out.push(glowMeter(glow));
  return out.join("");
}

// ---------------------------------------------------------------- 潮汐点选图

function tidePoints(t) {
  const series = [];
  for (const p of pyOr(pyGet(t, "curve", null), [])) {
    let tm = null, h = null;
    if (isDict(p)) {
      tm = hm(pyOr(pyGet(p, "t", null), pyGet(p, "time", null)));
      h = num(p.h != null ? p.h : pyGet(p, "height_m", null));
    } else if (Array.isArray(p) && p.length >= 2) {
      tm = hm(p[0]); h = num(p[1]);
    } else {
      continue;
    }
    if (tm === null || h === null) continue;
    series.push([tm, h, ""]);
  }
  for (const e of pyOr(pyGet(t, "extremes", null), [])) {
    let tm = null, h = null, kind = "";
    if (isDict(e)) {
      tm = hm(pyGet(e, "time", null));
      h = num(pyGet(e, "height_m", null) != null ? pyGet(e, "height_m", null) : pyGet(e, "h", null));
      kind = String(pyOr(pyGet(e, "type", null), ""));
    } else if (Array.isArray(e) && e.length >= 2) {
      tm = hm(e[0]); h = num(e[1]);
    } else {
      continue;
    }
    if (tm === null || h === null) continue;
    if (series.length) {
      let near = 0;
      for (let i = 1; i < series.length; i++) {
        if (Math.abs(series[i][0] - tm) < Math.abs(series[near][0] - tm)) near = i;
      }
      if (Math.abs(series[near][0] - tm) <= 25) {
        series[near] = [tm, h, kind];
        continue;
      }
    }
    series.push([tm, h, kind]);
  }
  series.sort((a, b) => a[0] - b[0]);
  return series;
}

function tideAdvice(kind, trend, inWin, beforeWin) {
  if (kind === "干潮") return "干潮前后，滩涂裸露最广，正是下滩的时刻。";
  if (kind === "满潮") return "满潮，水面最高，滩涂几乎全淹，这个时段别下滩。";
  if (!inWin) {
    if (beforeWin) return "还没进窗口，潮水一般还在退，可以岸边准备装备。";
    return "已经出了推荐窗口，滩涂回水很快，岸边看海就好。";
  }
  if (trend === null) return "潮位基本持平。";
  if (trend > 0.05) return "潮位在回涨，滩面开始回水，别再往深处走。";
  if (trend < -0.05) return "潮水还在退，可以沿滩面向外走，注意脚下潮沟。";
  return "平潮阶段，水位变化很小。";
}

export function tideSection(t, key = "beachcombing") {
  let pts = tidePoints(t);
  if (pts.length < 3) return "";
  const win = firstRange(pyGet(t, "best_window", null));
  const unit = pyOr(pyGet(t, "unit", null), "m（MSL 基准）");
  const lo = Math.min(...pts.map((p) => p[0]));
  const hi = Math.max(...pts.map((p) => p[0]));
  const xLo = Math.floor(lo / 60) * 60;
  const xHi = Math.max(hi + 30, xLo + 120);
  const hLo = Math.min(...pts.map((p) => p[1]));
  const hHi = Math.max(...pts.map((p) => p[1]));
  const pad = Math.max(0.15, (hHi - hLo) * 0.16);
  const yLo = hLo - pad, yHi = hHi + pad;

  const W = 640, H = 214;
  const px0 = 40, px1 = 630, py0 = 26, py1 = 168;
  const fx = (m) => px0 + (m - xLo) / (xHi - xLo) * (px1 - px0);
  const fy = (h) => py1 - (h - yLo) / (yHi - yLo) * (py1 - py0);

  // 逐时点抽稀到 32 个以内
  if (pts.length > 32) {
    const step = Math.ceil(pts.length / 32.0);
    const keep = [];
    for (let i = 0; i < pts.length; i += step) keep.push(pts[i]);
    if ((pts.length - 1) % step !== 0) keep.push(pts[pts.length - 1]);
    pts = keep;
  }

  const line = pts.map(([m, h]) => pyPercent("%.1f,%.1f", fx(m), fy(h))).join(" ");
  const area = (pyPercent("M%.1f %.1f L", fx(pts[0][0]), py1) + line.split(" ").join(" L")
    + pyPercent(" L%.1f %.1f Z", fx(pts[pts.length - 1][0]), py1));

  const grid = [];
  for (const frac of [0.0, 0.5, 1.0]) {
    const h = yLo + (yHi - yLo) * frac;
    const y = fy(h);
    grid.push(pyPercent('<path class="tgrid" d="M%d %.1f H%d"/>', px0, y, px1));
    grid.push(pyPercent('<text class="taxlbl" x="%d" y="%.1f" text-anchor="end">%.2f</text>',
      px0 - 6, y + 3.6, h));
  }
  for (let m = xLo; m <= xHi; m += 120) {
    grid.push(pyPercent('<text class="taxlbl" x="%.1f" y="%d" text-anchor="middle">%s</text>',
      fx(m), py1 + 22, hmTxt(m)));
  }

  let band = "";
  if (win) {
    const a = Math.max(win[0], xLo), b = Math.min(win[1], xHi);
    if (b > a) {
      band = (pyPercent('<rect class="twindow" x="%.1f" y="%d" width="%.1f" height="%d" rx="8"/>',
        fx(a), py0, fx(b) - fx(a), py1 - py0)
        + pyPercent('<text class="twlbl" x="%.1f" y="%d" text-anchor="middle">赶海窗口</text>',
          (fx(a) + fx(b)) / 2, py0 + 15));
    }
  }

  const radios = [], marks = [], panels = [];
  pts.forEach(([m, h, kind], i) => {
    const rid = pyPercent("td-%s-%d", key, i);
    const prev = i ? pts[i - 1][1] : null;
    const trend = prev !== null ? h - prev : null;
    const inWin = Boolean(win && win[0] <= m && m <= win[1]);
    const before = Boolean(win && m < win[0]);
    const checked = kind === "干潮" ? " checked" : "";
    radios.push(pyPercent('<input class="tp" type="radio" name="td-%s" id="%s"%s>', key, rid, checked));
    marks.push(pyPercent('<label class="tpdot" for="%s" style="left:%.2f%%;top:%.2f%%" '
      + 'title="%s %s"> <i></i></label>',
      rid, fx(m) / W * 100, fy(h) / H * 100, hmTxt(m), kind || "潮位"));
    const t0 = trend ?? 0;
    panels.push(pyPercent('<div class="tp-panel"><span class="tp-t">%s</span>'
      + '<b class="tp-h">%+.2f m</b>%s'
      + '<span class="tp-tr">%s</span><p>%s</p></div>',
      hmTxt(m), h,
      kind ? pyPercent('<em class="tp-kind">%s</em>', _e(kind)) : "",
      t0 > 0.05 ? "回涨" : t0 < -0.05 ? "退潮" : "平潮",
      _e(tideAdvice(kind, trend, inWin, before))));
  });

  const hint = "点曲线上的圆点，看那一刻的潮位和能不能下滩。";
  const caption = pyPercent("逐时潮位来自 %s；曲线只画接口真实给到的时段，没数据的时段不补线。",
    _e(pyOr(pyGet(t, "source_id", null), "潮汐接口")));
  return pyPercent('<div class="tide">' + radios.join("")
    + '<div class="tide-plot"><svg class="tide-svg" viewBox="0 0 %d %d" role="img" '
    + 'aria-label="逐时潮位曲线，含赶海窗口">'
    + '<path class="tarea" d="%s"/>%s<path class="tline" d="M%s"/>%s</svg>'
    + '<div class="tp-marks">%s</div></div>'
    + '<div class="tp-panels">%s</div>'
    + '<p class="tide-hint">%s</p>'
    + '<p class="skycap">%s</p></div>',
    W, H, area, band, line.split(" ").join(" L"),
    grid.join(""), marks.join(""), panels.join(""), hint, caption);
}
