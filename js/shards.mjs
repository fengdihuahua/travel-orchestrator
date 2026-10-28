// shards.mjs · 行程分片的合并 / 读取 / 排序（build_trip.py 的 Node 移植，行为对齐用）
// 与 scripts/build_trip.py 同名函数一一对应：deep_merge / load_parts / resolve_trip / order_top
import fs from "node:fs";
import path from "node:path";

export const WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

// 合并后的顶层键序（与 build_trip.TOP_ORDER 一致）
export const TOP_ORDER = ["trip_id", "plan_version", "status", "status_note", "request",
  "itinerary", "pitfalls", "facts", "sources", "red_team", "decisions", "title"];

export function orderTop(trip) {
  if (trip === null || typeof trip !== "object" || Array.isArray(trip)) return trip;
  const out = {};
  for (const k of TOP_ORDER) if (k in trip) out[k] = trip[k];
  for (const k of Object.keys(trip)) if (!(k in out)) out[k] = trip[k];
  return out;
}

export function die(msg, hints) {
  console.error("x " + msg);
  for (const h of hints || []) console.error("  -> " + h);
  process.exit(2);
}

function typeName(v) {
  if (v === null) return "NoneType";
  if (Array.isArray(v)) return "list";
  if (typeof v === "object") return "dict";
  if (typeof v === "string") return "str";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  return typeof v;
}

// 深合并 patch 到 base：嵌套 dict 递归；键名以 + 结尾追加到列表；其余覆盖。
// origin 记录「字段来自哪个分片」，conflicts 记录会静默丢数据的合并。
export function deepMerge(base, patch, pathStr, origin, src, conflicts) {
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch;
  if (base === null || typeof base !== "object" || Array.isArray(base)) base = {};
  for (const key of Object.keys(patch)) {
    const val = patch[key];
    const append = key.endsWith("+");
    const real = append ? key.slice(0, -1) : key;
    const here = pathStr ? pathStr + "/" + real : real;
    if (append) {
      let cur = base[real];
      if (cur === undefined || cur === null) {
        cur = [];
        base[real] = cur;
      } else if (!Array.isArray(cur)) {
        if (conflicts !== null && conflicts !== undefined) {
          conflicts.push(src + "：前面已有分片把 `" + real + "` 写成了 " + typeName(cur) +
            "（不是列表），`" + real + "+` 会把它整块丢掉");
        }
        cur = [];
        base[real] = cur;
      }
      const vals = Array.isArray(val) ? val : [val];
      for (const v of vals) {
        cur.push(v);
        if (origin !== null && origin !== undefined && src) {
          origin[here + "[" + (cur.length - 1) + "]"] = src;
        }
      }
      continue;
    }
    if (val !== null && typeof val === "object" && !Array.isArray(val) &&
        base[real] !== null && typeof base[real] === "object" && !Array.isArray(base[real])) {
      deepMerge(base[real], val, here, origin, src, conflicts);
    } else {
      if (conflicts !== null && conflicts !== undefined && origin &&
          Array.isArray(base[real]) && (here + "[0]") in origin) {
        conflicts.push(src + "：用 `" + real + "` 覆盖了前面分片用 `" + real +
          "+` 追加的列表（" + base[real].length + " 项），追加的内容会全部丢失");
      }
      base[real] = val;
      if (origin !== null && origin !== undefined && src) origin[here] = src;
    }
  }
  return base;
}

// 分片文件名排序：数字段按数值比（11_days_D2 < 11_days_D10），与 Python 版同口径
function partSortKey(name) {
  const parts = name.match(/\d+|\D+/g) || [];
  return parts.map((p) => (p.match(/^\d+$/) ? [0, parseInt(p, 10), ""] : [1, 0, p]));
}

export function comparePartNames(a, b) {
  const ka = partSortKey(a), kb = partSortKey(b);
  const n = Math.max(ka.length, kb.length);
  for (let i = 0; i < n; i++) {
    const x = ka[i] || [1, 0, ""], y = kb[i] || [1, 0, ""];
    if (x[0] !== y[0]) return x[0] - y[0];
    if (x[1] !== y[1]) return x[1] - y[1];
    if (x[2] !== y[2]) return x[2] < y[2] ? -1 : 1;
  }
  return 0;
}

export function loadParts(partsDir, quiet) {
  if (!fs.existsSync(partsDir) || !fs.statSync(partsDir).isDirectory()) {
    die("分片目录不存在：" + partsDir,
      ["先用 `skeleton` 生成骨架，或把分片 json 放进该目录",
       "也可以直接用 validate 校验已有的 trip.json"]);
  }
  const files = fs.readdirSync(partsDir)
    .filter((f) => f.endsWith(".json") && !f.startsWith("_"))
    .sort(comparePartNames);
  if (!files.length) die("分片目录里没有 .json 文件：" + partsDir);
  const merged = {}, origin = {}, conflicts = [];
  for (const name of files) {
    const p = path.join(partsDir, name);
    let data;
    try {
      data = JSON.parse(fs.readFileSync(p, "utf8"));
    } catch (e) {
      die("分片 " + name + " 不是合法 JSON：" + e.message);
    }
    if (data === null || typeof data !== "object" || Array.isArray(data)) {
      die("分片 " + name + " 顶层必须是 JSON 对象（{}），当前是 " + typeName(data));
    }
    deepMerge(merged, data, "", origin, name, conflicts);
    if (!quiet) console.log("  + 合并 " + name + " (" + fs.statSync(p).size + " B)");
  }
  if (conflicts.length) {
    die("分片合并会丢数据，" + conflicts.length + " 处冲突：",
      conflicts.concat(["同一个列表只能选一种写法：要么各片都用 `days+` 追加，",
        "要么只在最后一片用 `days` 整块给；两种混用结果取决于文件名顺序"]));
  }
  return [merged, files, origin];
}

export function loadJsonFile(p) {
  let data;
  try {
    data = JSON.parse(fs.readFileSync(p, "utf8"));
  } catch (e) {
    die("不是合法 JSON：" + p + " " + e.message,
      ["多半是写入被截断或引号不匹配；改用分片存放可避免"]);
  }
  return data;
}

export function partsDirOf(p) {
  const abs = path.resolve(p);
  if (fs.existsSync(abs) && fs.statSync(abs).isDirectory()) {
    const inner = path.join(abs, "parts");
    return fs.existsSync(inner) && fs.statSync(inner).isDirectory() ? inner : abs;
  }
  return path.join(path.dirname(abs), "parts");
}

// 统一取数入口：目录 → 合并 parts/*.json；文件 → 直接读。返回 [trip, label, nParts, origin]
export function resolveTrip(p, quiet, withOrigin) {
  const abs = path.resolve(p);
  if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
    const trip = orderTop(loadJsonFile(abs));
    if (withOrigin) return [trip, path.basename(abs), 0, {}];
    return [trip, path.basename(abs), 0];
  }
  if (!fs.existsSync(abs) || !fs.statSync(abs).isDirectory()) {
    die("找不到方案目录或 trip.json：" + p,
      ["方案目录一般形如 travel-plan/<trip_id>/，里面有 parts/ 或 trip.json",
       "也可以直接把 trip.json 的路径传进来"]);
  }
  const pd = partsDirOf(abs);
  const [merged, files, origin] = loadParts(pd, quiet);
  const label = path.basename(pd) + "/（" + files.length + " 个分片）";
  if (withOrigin) {
    const prefix = path.resolve(pd) === abs ? "" : path.basename(pd) + "/";
    const out = {};
    for (const k of Object.keys(origin)) out[k] = prefix + origin[k];
    return [orderTop(merged), label, files.length, out];
  }
  return [orderTop(merged), label, files.length];
}

// ---- 日期小工具（与 Python datetime 口径一致：周一=0） ----
export function isValidDate(s) {
  if (typeof s !== "string") return false;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return false;
  const y = +m[1], mo = +m[2], d = +m[3];
  if (mo < 1 || mo > 12 || d < 1) return false;
  return d <= new Date(Date.UTC(y, mo, 0)).getUTCDate();
}

export function weekdayOf(dateStr) {
  // 周一=0 … 周日=6（与 Python datetime.weekday() 一致）
  const [y, mo, d] = dateStr.split("-").map(Number);
  return (new Date(Date.UTC(y, mo - 1, d)).getUTCDay() + 6) % 7;
}
