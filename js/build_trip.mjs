#!/usr/bin/env node
// build_trip.mjs · 行程分片工具 Node 移植（与 scripts/build_trip.py 对齐，parity_check.py 把关）
// 用法：node js/build_trip.mjs <skeleton|render|split|validate|assemble|schema> ...
// render 依赖 js/render_plan.mjs（M3b 落地前给出诚实降级说明）
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { validate } from "./validate.mjs";
import { writeOutputs } from "./render_plan.mjs";
import { die, isValidDate, loadParts, orderTop, resolveTrip,
         WEEKDAY_CN, weekdayOf } from "./shards.mjs";

const VERSION = "1.4.0";

// JSON 序列化与 Python json.dumps(indent=2, ensure_ascii=False) 对齐（无尾换行，与 json.dump 到文件一致）
export function dumpJson(obj) {
  return JSON.stringify(obj, null, 2);
}

function cmdValidate(tripPath) {
  if (!fs.existsSync(tripPath)) {
    die("路径不存在：" + tripPath,
      ["传方案目录（读其中的 parts/）或直接传 trip.json 路径",
       "方案目录一般是 travel-plan/<trip_id>/"]);
  }
  const [trip, label, n, origin] = resolveTrip(tripPath, true, true);
  if (n) {
    console.log("数据来源：" + label);
    console.log();
  }
  console.log("校验对象：" + path.resolve(tripPath));
  const [ok] = validate(trip, label, false, origin);
  return ok ? 0 : 1;
}

function cmdAssemble(tripDir, opts) {
  const partsDir = path.resolve(opts.parts || partsDirOfLocal(tripDir));
  const out = path.resolve(opts.out || path.join(path.resolve(tripDir), "trip.json"));
  console.log("组装 trip.json（只在需要一份独立大 JSON 时才用）");
  console.log("  分片目录: " + partsDir);
  const [merged, files, origin] = loadParts(partsDir);
  const ordered = orderTop(merged);
  const [ok] = validate(ordered, path.basename(out), false, origin);
  if (opts.dryRun) {
    console.log("  [dry-run] 未写入。" + (ok ? "结构校验通过。" : "结构校验未通过，见上。"));
    return ok ? 0 : 1;
  }
  if (!ok && !opts.force) {
    die("校验未通过，已放弃写入（避免产出坏数据）。",
      ["修正上列错误后重跑", "确实要强行写入：加 --force（不推荐，渲染阶段还会再报一次）"]);
  }
  if (fs.existsSync(out)) {
    fs.copyFileSync(out, out + ".bak");
    console.log("  已备份旧文件 -> " + path.basename(out) + ".bak");
  }
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, dumpJson(ordered), "utf8");
  JSON.parse(fs.readFileSync(out, "utf8"));
  console.log("  已写入 " + out + " (" + fs.statSync(out).size + " B, "
    + files.length + " 个分片)");
  console.log("  注意：这份 trip.json 是产物不是数据源，改数据改 parts/。");
  console.log("  下一步：node js/build_trip.mjs render \"" + path.resolve(tripDir) + "\"");
  return 0;
}

function partsDirOfLocal(p) {
  const abs = path.resolve(p);
  if (fs.existsSync(abs) && fs.statSync(abs).isDirectory()) {
    const inner = path.join(abs, "parts");
    return fs.existsSync(inner) && fs.statSync(inner).isDirectory() ? inner : abs;
  }
  return path.join(path.dirname(abs), "parts");
}

function splitTrip(trip) {
  const out = [];
  const head = {};
  for (const k of ["trip_id", "plan_version", "status", "status_note", "title"]) {
    if (k in trip) head[k] = trip[k];
  }
  if ("request" in trip) head.request = trip.request;
  out.push(["01_request.json", head]);

  const itin = (trip.itinerary !== null && typeof trip.itinerary === "object" &&
    !Array.isArray(trip.itinerary)) ? trip.itinerary : {};
  const used = new Set();
  for (const [idx, key] of [[2, "weather"], [3, "nature"]]) {
    if (key in itin) {
      out.push([String(idx).padStart(2, "0") + "_" + key + ".json",
        { itinerary: { [key]: itin[key] } }]);
      used.add(key);
    }
  }
  const days = Array.isArray(itin.days) ? itin.days : null;
  if (days && days.length) {
    days.forEach((day, i) => {
      out.push([String(10 + i).padStart(2, "0") + "_days_D" + (i + 1) + ".json",
        { itinerary: { "days+": [day] } }]);
    });
  } else if ("days" in itin) {
    out.push(["10_days.json", { itinerary: { days: [] } }]);
  }
  used.add("days");
  let n = 20;
  for (const key of ["food", "budget", "checklist"]) {
    if (key in itin) {
      out.push([String(n).padStart(2, "0") + "_" + key + ".json",
        { itinerary: { [key]: itin[key] } }]);
      used.add(key);
    }
    n += 1;
  }
  const restItin = {};
  for (const k of Object.keys(itin)) if (!used.has(k)) restItin[k] = itin[k];
  if (Object.keys(restItin).length) {
    out.push([String(n).padStart(2, "0") + "_itinerary_other.json",
      { itinerary: restItin }]);
    n += 1;
  }
  const skip = new Set(["trip_id", "plan_version", "status", "status_note", "title",
    "request", "itinerary"]);
  const restTop = {};
  for (const k of Object.keys(trip)) if (!skip.has(k)) restTop[k] = trip[k];
  for (const key of ["pitfalls", "facts", "sources", "red_team", "decisions"]) {
    if (key in restTop) {
      out.push([String(n).padStart(2, "0") + "_" + key + ".json", { [key]: restTop[key] }]);
      delete restTop[key];
      n += 1;
    }
  }
  if (Object.keys(restTop).length) {
    out.push([String(n).padStart(2, "0") + "_rest.json", restTop]);
  }
  return out;
}

function cmdSplit(tripJson, opts) {
  const [trip, label] = resolveTrip(tripJson);
  const tripDir = path.resolve(opts.tripDir || path.dirname(path.resolve(tripJson)));
  const parts = path.resolve(opts.parts || path.join(tripDir, "parts"));
  const pieces = splitTrip(trip);
  const sizes = pieces.map(([, o]) => JSON.stringify(o).length);
  console.log("拆分 " + label + " -> " + parts);
  console.log("  " + pieces.length + " 个分片，正文合计 " + sizes.reduce((a, b) => a + b, 0)
    + " 字符，最大一片 " + Math.max(...sizes) + " 字符");
  if (opts.dryRun) {
    for (const [name] of pieces) console.log("  [dry-run] " + name);
    return 0;
  }
  fs.mkdirSync(parts, { recursive: true });
  for (const [name, obj] of pieces) {
    const p = path.join(parts, name);
    if (fs.existsSync(p) && !opts.force) {
      die("分片已存在：" + name + "（确认要覆盖就加 --force）");
    }
    fs.writeFileSync(p, dumpJson(obj), "utf8");
    console.log("  + " + name + " (" + fs.statSync(p).size + " B)");
  }
  // 自检：合并回来必须与原文完全等值
  const [merged] = loadParts(parts, true);
  const canon = (o) => JSON.stringify(sortKeysDeep(o));
  if (canon(merged) !== canon(trip)) {
    die("拆分自检失败：合并结果与原文不等值，请勿使用刚写入的分片",
      ["这是一个 bug，请保留 parts/ 目录与原始 trip.json 反馈"]);
  }
  console.log("  自检通过：合并结果与原文完全等值");
  const [ok, errors] = validate(merged, label, true);
  console.log("  结构校验：" + (ok ? "通过" : "未通过（" + errors.length + " 项）"));
  console.log();
  console.log("下一步：确认无误后可删掉 " + path.basename(tripJson)
    + "，之后统一用 `render " + tripDir + "`。");
  return ok ? 0 : 1;
}

function cmdRender(tripDir, opts) {
  // 合并 + 校验 + 渲染。默认不落中间 trip.json。
  // 校验只做一次：这里通过后，交给渲染器时带 skipStructureGate，
  // 避免同一份数据、同一条命令里被校验两遍。
  const src = path.resolve(tripDir);
  const [trip, label, _n, origin] = resolveTrip(src, false, true);
  console.log("-".repeat(60));
  console.log("渲染自 " + label);
  const [ok] = validate(trip, label, false, origin);
  if (!ok && !opts.force) {
    die("结构校验未通过，已放弃渲染（避免产出看着成功的废交付物）。",
      ["按每项后面标注的分片文件改，改完重跑本命令",
       "确实要强行渲染：加 --force（不推荐，红方复核还会再拦一次）"]);
  }
  if (opts.writeJson) {
    const base = fs.existsSync(src) && fs.statSync(src).isDirectory() ? src : path.dirname(src);
    const out = path.resolve(opts.out || path.join(base, "trip.json"));
    writeTripFile(trip, out);
  }
  const r = writeOutputs(src, opts.outDir || null, opts.force, true);
  console.log("ok 已生成 v" + String(r.plan_version) + "：");
  console.log("  MD  : " + r.md);
  console.log("  HTML: " + r.html);
  return 0;
}

// 与 scripts/build_trip.py _write_trip 对齐：先备份旧的，写完读回验证真的能解析
function writeTripFile(trip, out) {
  if (fs.existsSync(out)) {
    fs.copyFileSync(out, out + ".bak");
    console.log("  已备份旧文件 -> " + path.basename(out) + ".bak");
  }
  fs.mkdirSync(path.dirname(out) || ".", { recursive: true });
  fs.writeFileSync(out, dumpJson(trip), "utf8");
  JSON.parse(fs.readFileSync(out, "utf8"));
  console.log("  已写入 " + out + " (" + fs.statSync(out).size + " B)");
}

function cmdSchema() {
  const OPTIONAL_TOP = ["pitfalls", "facts", "sources", "red_team", "status_note", "title"];
  const schema = {
    trip_id: "字符串，唯一标识",
    plan_version: "整数，从 1 开始",
    status: "draft | conditional | executable_as_of_check",
    status_note: "方案状态的说明",
    request: { origin: { name: "出发城市" },
      destination: { name: "目的地" },
      dates: { start: "YYYY-MM-DD", end: "YYYY-MM-DD" },
      companions: "字符串（如 2 成人），不是数组",
      budget: { amount_max: 0, currency: "CNY", mode: "total" },
      pace: "节奏描述",
      interests: { must: [{ label: "赶海" }], prefer: ["观星", "日出"] } },
    itinerary: {
      weather: { kind: "forecast", summary: "整段概述",
        entries: [{ date: "YYYY-MM-DD", summary: "晴 16-22℃" }] },
      nature: { birding: {}, stargazing: {}, sunrise: {}, beachcombing: {} },
      days: [{ day_id: "D1", date: "YYYY-MM-DD", weekday: "周一",
        title: "当日主题", alternatives: "",
        items: [{ item_id: "d1-1（全局唯一）", time: "08:00-10:00",
          title: "", kind: "nature|transport_major|meal|sight",
          place: "", booking_status: "not_required", notes: "" }] }],
      food: [{ name: "", note: "" }],
      budget: { currency: "CNY",
        categories: [{ name: "住宿", min: 0, max: 0 }],
        total: { min: 0, max: 0 } },
      checklist: [{ text: "", due: "" }],
    },
    pitfalls: [{ title: "", detail: "", avoid: "" }],
    facts: [{ fact_id: "f1", claim: "一句话结论", value: "具体值",
      status: "verified|estimated|unverified|unknown", source_ids: ["s1"] }],
    sources: [{ source_id: "s1", name: "", kind: "api|official_site|web_search",
      url: "", retrieved_at: "YYYY-MM-DD" }],
    red_team: { verdict: "", findings: [] },
  };
  console.log("渲染器需要的最小结构（缺一项就会报错）—— 与 render_plan.py 实际读取一致");
  process.stdout.write(JSON.stringify(schema, null, 2) + "\n");
  console.log();
  console.log("可选字段：" + OPTIONAL_TOP.join("、"));
  console.log("三个高频坑（写错必然返工，改之前先看这里）：");
  console.log("  1. 逐日行程一天一片，必须写 `\"days+\"`（追加）而不是 `\"days\"`：");
  console.log("     写 days 会把前面分片里的天数整个覆盖掉，且不报错。");
  console.log("  2. request.companions 是**字符串**（如「2 成人」），不是数组。");
  console.log("  3. item_id 全局唯一（跨所有天），预算分项 min 不得大于 max —— 这两项是阻断级。");
  console.log("完整说明见 references/data-schema.md。");
  return 0;
}

function cmdSkeleton(tripDir, opts) {
  const abs = path.resolve(tripDir);
  const parts = path.join(abs, "parts");
  fs.mkdirSync(parts, { recursive: true });
  const start = opts.start;
  if (!isValidDate(start)) die("--start 应为 YYYY-MM-DD，当前 " + start);
  const days = Math.max(1, Math.min(parseInt(opts.days, 10) || 2, 30));
  const tripId = opts.tripId || path.basename(abs);

  function dump(name, obj) {
    const p = path.join(parts, name);
    if (fs.existsSync(p) && !opts.force) {
      console.log("  跳过已存在: " + name);
      return;
    }
    fs.writeFileSync(p, dumpJson(obj), "utf8");
    console.log("  生成: " + name);
  }

  const [sy, sm, sd] = start.split("-").map(Number);
  const dstr = (d) => d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0") +
    "-" + String(d.getUTCDate()).padStart(2, "0");
  const dadd = (i) => new Date(Date.UTC(sy, sm - 1, sd + i));

  console.log("生成骨架 -> " + parts);
  dump("01_request.json", {
    trip_id: tripId,
    plan_version: 1,
    status: "draft",
    status_note: "骨架阶段，待补实时数据后改为 conditional / executable_as_of_check",
    request: {
      origin: { name: "" },
      destination: { name: "" },
      dates: { start, end: dstr(dadd(days - 1)) },
      companions: "（人数与关系，如 2 成人）",
      budget: { amount_max: null, currency: "CNY", mode: "total" },
      pace: "",
      interests: { must: [], prefer: [] },
    },
    itinerary: { "days+": [], food: [],
      budget: { currency: "CNY", categories: [], total: {} },
      checklist: [],
      weather: { kind: "forecast", summary: "", entries: [] },
      nature: {} },
  });
  for (let i = 0; i < days; i++) {
    const d = dadd(i);
    dump(String(10 + i).padStart(2, "0") + "_days_D" + (i + 1) + ".json", {
      itinerary: { "days+": [{
        day_id: "D" + (i + 1),
        date: dstr(d),
        weekday: WEEKDAY_CN[weekdayOf(dstr(d))],
        theme: "",
        items: [],
        stay: "",
      }] },
    });
  }
  dump(String(20 + days).padStart(2, "0") + "_food.json", { itinerary: { food: [] } });
  dump(String(21 + days).padStart(2, "0") + "_budget.json", {
    itinerary: { budget: { currency: "CNY", categories: [], total: { min: null, max: null } } },
  });
  dump(String(22 + days).padStart(2, "0") + "_checklist.json", { itinerary: { checklist: [] } });
  dump(String(23 + days).padStart(2, "0") + "_weather.json", {
    itinerary: { weather: { kind: "forecast", summary: "",
      entries: Array.from({ length: days }, (_, i) => ({ date: dstr(dadd(i)), summary: "" })) } },
  });
  dump(String(24 + days).padStart(2, "0") + "_nature.json", { nature: {} });
  dump(String(25 + days).padStart(2, "0") + "_pitfalls.json", { pitfalls: [] });
  dump(String(26 + days).padStart(2, "0") + "_facts.json", { facts: [], sources: [] });
  console.log();
  console.log("下一步：逐个填 parts/*.json（每个都很小，写起来不会失败），");
  console.log("        然后 node js/build_trip.mjs render \"" + abs + "\"");
  console.log("        （渲染器直接读目录合并，不需要先组装 trip.json）");
  return 0;
}

function sortKeysDeep(o) {
  if (Array.isArray(o)) return o.map(sortKeysDeep);
  if (o !== null && typeof o === "object") {
    const out = {};
    for (const k of Object.keys(o).sort()) out[k] = sortKeysDeep(o[k]);
    return out;
  }
  return o;
}

function usage() {
  console.error("用法: node build_trip.mjs <skeleton|render|split|validate|assemble|schema> ...");
}

export function main(argv) {
  const args = argv.slice(2);
  const cmd = args[0];
  const opt = (name, dflt) => {
    const i = args.indexOf(name);
    return i >= 0 ? args[i + 1] : dflt;
  };
  const has = (name) => args.includes(name);
  try {
    switch (cmd) {
      case "validate":
        return cmdValidate(args[1]);
      case "assemble":
        return cmdAssemble(args[1], { parts: opt("--parts"), out: opt("--out"),
          force: has("--force"), dryRun: has("--dry-run") });
      case "split":
        return cmdSplit(args[1], { tripDir: opt("--trip-dir"), parts: opt("--parts"),
          force: has("--force"), dryRun: has("--dry-run") });
      case "render":
        return cmdRender(args[1], { outDir: opt("--out-dir"), writeJson: has("--write-json"),
          out: opt("--out"), force: has("--force") });
      case "schema":
        return cmdSchema();
      case "skeleton":
        return cmdSkeleton(args[1], { tripId: opt("--trip-id"), start: opt("--start"),
          days: opt("--days", "2"), force: has("--force") });
      default:
        usage();
        return 2;
    }
  } catch (e) {
    console.error("x 未预期的错误：" + e.name + ": " + e.message);
    console.error("  请把上面这行贴出来以便定位。");
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(main(process.argv));
}
