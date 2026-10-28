// validate.mjs · 结构校验（build_trip.py validate() 的 Node 移植，消息逐字对齐）
import { WEEKDAY_CN, isValidDate, weekdayOf } from "./shards.mjs";

const REQUIRED_TOP = { trip_id: "str", plan_version: "int", status: "str",
  request: "dict", itinerary: "dict" };
const REQUIRED_ITINERARY = { days: "list", food: "list", budget: "dict", checklist: "list" };
const OPTIONAL_TOP = ["pitfalls", "facts", "sources", "red_team", "status_note", "title"];

function typeName(v) {
  if (v === null) return "NoneType";
  if (Array.isArray(v)) return "list";
  if (typeof v === "object") return "dict";
  if (typeof v === "string") return "str";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  return typeof v;
}

// Python isinstance 语义：bool 是 int 的子类，True 过 isinstance(x, int)
function matchesType(v, typ) {
  const t = typeName(v);
  if (typ === "int") return t === "int" || t === "bool";
  return t === typ;
}

// origin 查询：按最后一个 [ 或 / 往上截，最多 12 层
function makeWhere(origin) {
  return function where(key) {
    if (!origin) return "";
    let cur = key;
    for (let i = 0; i < 12; i++) {
      if (cur in origin) return "（见 " + origin[cur] + "）";
      const a = cur.lastIndexOf("["), b = cur.lastIndexOf("/");
      const cut = Math.max(a, b);
      if (cut <= 0) return "";
      cur = cur.slice(0, cut);
    }
    return "";
  };
}

export function validate(trip, label, quiet, origin) {
  const errors = [], warns = [];
  const where = makeWhere(origin);

  if (trip === null || typeof trip !== "object" || Array.isArray(trip)) {
    return [false, ["顶层不是 JSON 对象"], []];
  }
  for (const [key, typ] of Object.entries(REQUIRED_TOP)) {
    if (!(key in trip)) {
      errors.push("缺少顶层字段 " + key + where(key));
    } else if (!matchesType(trip[key], typ)) {
      errors.push("字段 " + key + " 应为 " + typ + "，实际是 " + typeName(trip[key]) + where(key));
    }
  }
  const itin = (trip.itinerary !== null && typeof trip.itinerary === "object" &&
    !Array.isArray(trip.itinerary)) ? trip.itinerary : {};
  const req = (trip.request !== null && typeof trip.request === "object" &&
    !Array.isArray(trip.request)) ? trip.request : {};
  if (trip.request !== null && typeof trip.request === "object" && !Array.isArray(trip.request)) {
    const dates = (req.dates !== null && typeof req.dates === "object" && !Array.isArray(req.dates))
      ? req.dates : {};
    if (!dates.start || !dates.end) {
      errors.push("request.dates 缺少 start / end（渲染与红方复核都依赖它）" +
        where("request/dates"));
    } else {
      for (const k of ["start", "end"]) {
        if (!isValidDate(dates[k])) {
          errors.push("request.dates." + k + " 格式应为 YYYY-MM-DD，当前 " +
            String(dates[k]) + where("request/dates/" + k));
        }
      }
    }
    if (!req.companions) {
      errors.push("request.companions 为空（红方判定为阻断项）" + where("request/companions"));
    } else if (typeof req.companions !== "string") {
      warns.push("request.companions 建议用字符串（如「2 成人」），当前是 " +
        typeName(req.companions) + where("request/companions"));
    }
    if (req.budget === null || typeof req.budget !== "object" || Array.isArray(req.budget)) {
      warns.push("request.budget 缺失或不是对象（含 amount_max/currency/mode）" +
        where("request/budget"));
    } else if (req.budget.amount_max === null || req.budget.amount_max === undefined) {
      warns.push("request.budget.amount_max 为 null，预算超支检查（RT-S06）将跳过" +
        where("request/budget/amount_max"));
    }
    for (const k of ["origin", "destination"]) {
      const r = req[k];
      if (r === null || typeof r !== "object" || Array.isArray(r) || !r.name) {
        warns.push("request." + k + ".name 为空，封面会显示「未定」" + where("request/" + k + "/name"));
      }
    }
  }
  for (const [key, typ] of Object.entries(REQUIRED_ITINERARY)) {
    if (!(key in itin)) {
      errors.push("itinerary 缺少字段 " + key + where("itinerary/" + key));
    } else if (typeName(itin[key]) !== typ) {
      errors.push("itinerary." + key + " 应为 " + typ + "，实际是 " +
        typeName(itin[key]) + where("itinerary/" + key));
    }
  }
  const days = Array.isArray(itin.days) ? itin.days : [];
  if (Array.isArray(itin.days) && !days.length) {
    errors.push("itinerary.days 为空，方案至少要有一天" + where("itinerary/days"));
  }
  const seenIds = new Set();
  days.forEach((d, i) => {
    const dayKey = "itinerary/days[" + i + "]";
    if (d === null || typeof d !== "object" || Array.isArray(d)) {
      errors.push("itinerary.days[" + i + "] 不是对象" + where(dayKey));
      return;
    }
    const did = d.day_id || ("#" + (i + 1));
    if (!d.date) {
      errors.push(did + " 缺 date" + where(dayKey + "/date"));
    } else if (!isValidDate(d.date)) {
      errors.push(did + " date 格式应为 YYYY-MM-DD，当前 " +
        String(d.date) + where(dayKey + "/date"));
    } else {
      const real = WEEKDAY_CN[weekdayOf(d.date)];
      if (d.weekday && String(d.weekday).indexOf(real) < 0) {
        warns.push(did + " weekday 写的是 " + String(d.weekday) + "，实际是 " +
          real + where(dayKey + "/weekday"));
      }
    }
    if (!Array.isArray(d.items) || !d.items.length) {
      warns.push(did + " 没有 items（行程条目），渲染出来会是空白天" + where(dayKey + "/items"));
    }
    (Array.isArray(d.items) ? d.items : []).forEach((it, j) => {
      const itemKey = dayKey + "/items[" + j + "]";
      if (it === null || typeof it !== "object" || Array.isArray(it)) {
        errors.push(did + " items[" + j + "] 不是对象" + where(itemKey));
        return;
      }
      const iid = it.item_id;
      if (!iid) {
        warns.push(did + " items[" + j + "] 缺 item_id" + where(itemKey + "/item_id"));
      } else if (seenIds.has(iid)) {
        errors.push("item_id 重复：" + String(iid) + where(itemKey + "/item_id"));
      } else {
        seenIds.add(iid);
      }
      if (!it.title) {
        warns.push(did + " items[" + j + "] 缺 title" + where(itemKey + "/title"));
      }
    });
  });
  if (itin.budget !== null && typeof itin.budget === "object" && !Array.isArray(itin.budget)) {
    const b = itin.budget;
    if (!("categories" in b) && !("total" in b)) {
      warns.push("itinerary.budget 既没有 categories 也没有 total" + where("itinerary/budget"));
    }
    (Array.isArray(b.categories) ? b.categories : []).forEach((c, k) => {
      const catKey = "itinerary/budget/categories[" + k + "]";
      if (c === null || typeof c !== "object" || Array.isArray(c)) {
        errors.push("budget.categories[" + k + "] 不是对象" + where(catKey));
        return;
      }
      if (c.min === null || c.min === undefined || c.max === null || c.max === undefined) {
        warns.push("预算分项「" + String(c.name) + "」缺 min/max" + where(catKey));
      } else if (typeof c.min === "number" && typeof c.max === "number" && c.min > c.max) {
        errors.push("预算分项「" + String(c.name) + "」min 大于 max" + where(catKey + "/min"));
      }
    });
  }
  const wx = itin.weather;
  if (wx !== null && typeof wx === "object" && !Array.isArray(wx)) {
    if (!Array.isArray(wx.entries)) {
      warns.push("itinerary.weather 缺少 entries 列表" + where("itinerary/weather"));
    }
  } else if (wx !== null && wx !== undefined && !Array.isArray(wx)) {
    errors.push("itinerary.weather 应为对象（含 entries）或列表，实际是 " +
      typeName(wx) + where("itinerary/weather"));
  }

  function checkObjList(items, pathStr, label, sampleKeys) {
    if (!Array.isArray(items)) return;
    items.forEach((it, i) => {
      const at = pathStr + "[" + i + "]";
      if (it === null || typeof it !== "object" || Array.isArray(it)) {
        errors.push(label + "[" + i + "] 应为对象，实际是 " + typeName(it) + where(at));
      } else if (sampleKeys.length && !sampleKeys.some((k) => k in it)) {
        warns.push(label + "[" + i + "] 里 " + sampleKeys.join("/") +
          " 一个都没有，渲染出来可能是空行" + where(at));
      }
    });
  }

  checkObjList(itin.food, "itinerary/food", "itinerary.food", ["name"]);
  checkObjList(itin.checklist, "itinerary/checklist", "itinerary.checklist", ["task"]);
  checkObjList(trip.sources, "sources", "sources", ["source_id"]);
  checkObjList(trip.facts, "facts", "facts", ["claim", "conclusion"]);
  const interests = (req.interests !== null && typeof req.interests === "object" &&
    !Array.isArray(req.interests)) ? req.interests : {};
  checkObjList(interests.must, "request/interests/must", "request.interests.must", ["label", "name"]);

  const missingOpt = OPTIONAL_TOP.filter((k) => !(k in trip));
  if (missingOpt.length) {
    warns.push("可选字段未填（不阻塞，但会影响交付完整度）：" + missingOpt.join("、"));
  }
  if (!quiet) {
    console.log("-".repeat(60));
    console.log("校验 " + label + "：" + (errors.length ? "不通过" : "通过"));
    if (errors.length) {
      console.log("  错误 " + errors.length + " 项：");
      for (const e of errors.slice(0, 20)) console.log("    ! " + e);
      if (errors.length > 20) console.log("    ... 其余 " + (errors.length - 20) + " 项省略");
    }
    if (warns.length) {
      console.log("  提示 " + warns.length + " 项：");
      for (const w of warns.slice(0, 10)) console.log("    - " + w);
      if (warns.length > 10) console.log("    ... 其余 " + (warns.length - 10) + " 项省略");
    }
  }
  return [errors.length === 0, errors, warns];
}
