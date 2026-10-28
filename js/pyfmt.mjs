// pyfmt.mjs · Python 语义对齐层：% 格式化 / str() / round / quote / html.escape
//
// parity 要求与 CPython 输出逐字节一致，所以这里不做"差不多"的格式化：
//   - pyFixed/pyG 走 BigInt 精确十进制展开 + 银行家舍入（round-half-even），
//     对齐 CPython 的 dtoa（JS 的 toFixed 是 half-up，0.125 → 0.13 会露馅）
//   - pyFloatRepr 对齐 repr(float)：最短回环数字 + Python 的科学计数阈值（1e16 起）
//   - pyQuote 对齐 urllib.parse.quote（默认 safe='/'，非 ASCII 走 UTF-8 百分号编码）

// ---------------------------------------------------------------- 精确十进制

function decompose(x) {
  const buf = new DataView(new ArrayBuffer(8));
  buf.setFloat64(0, x);
  const hi = buf.getUint32(0), lo = buf.getUint32(4);
  const sign = hi >>> 31;
  const be = (hi >>> 20) & 0x7ff;
  if (be === 0) {
    return { sign, mant: (BigInt(hi & 0xfffff) << 32n) | BigInt(lo), exp: -1074 };
  }
  return {
    sign,
    mant: ((BigInt(hi & 0xfffff) | 0x100000n) << 32n) | BigInt(lo),
    exp: be - 1075,
  };
}

const _pow5 = [1n];
function pow5(n) {
  while (_pow5.length <= n) _pow5.push(_pow5[_pow5.length - 1] * 5n);
  return _pow5[n];
}

// value = dig × 10^(point - dig.length)，dig 无 leading zero；point = 小数点前的位数
function exactDigits(x) {
  if (x === 0) return { neg: Object.is(x, -0), dig: "0", point: 1 };
  const { sign, mant, exp } = decompose(x);
  if (exp >= 0) {
    const m = (mant << BigInt(exp)).toString();
    return { neg: sign === 1, dig: m, point: m.length };
  }
  const m = (mant * pow5(-exp)).toString();
  return { neg: sign === 1, dig: m, point: m.length + exp };
}

// 把 dig 在 keep 位有效数字处做银行家舍入，返回 {dig, point}
function roundDigits(dig, point, keep) {
  if (keep <= 0) {
    // 全部被舍掉：与半个单位（5×10^(keep-1)…即 dig vs 5后跟0）比较
    const first = dig[0];
    let up;
    if (first > "5") up = true;
    else if (first < "5") up = false;
    else up = dig.slice(1).split("").some((c) => c !== "0"); // 恰一半 → even(0) 不进
    if (!up) return { dig: "0", point };
    // 进位：1 个单位 = 1×10^(point-keep) → dig "1"，point' = point - keep + 1
    return { dig: "1", point: point - keep + 1 };
  }
  if (keep >= dig.length) return { dig, point };
  const kept = dig.slice(0, keep);
  const dropped = dig.slice(keep);
  const rest = dropped.slice(1);
  let up;
  if (dropped[0] > "5") up = true;
  else if (dropped[0] < "5") up = false;
  else up = rest.split("").some((c) => c !== "0") || (parseInt(kept[kept.length - 1], 10) % 2 === 1);
  if (!up) return { dig: kept, point };
  let inc = (BigInt(kept) + 1n).toString();
  if (inc.length > keep) return { dig: inc.slice(0, keep), point: point + 1 };
  return { dig: inc, point };
}

function renderFixed(neg, dig, point, prec) {
  let intPart, fracPart;
  if (point <= 0) {
    intPart = "0";
    fracPart = ("0".repeat(-point) + dig).slice(0, prec);
  } else {
    intPart = (dig + "0".repeat(point)).slice(0, point);
    fracPart = dig.slice(point, point + prec);
  }
  fracPart = (fracPart + "0".repeat(prec)).slice(0, prec);
  return (neg ? "-" : "") + intPart + (prec > 0 ? "." + fracPart : "");
}

function renderSci(neg, dig, point, prec, expWidth = 2) {
  // 尾数：1 位整数 + prec 位小数（不足补零，超出截断）
  const head = dig[0];
  let tail = dig.slice(1, 1 + prec);
  tail = (tail + "0".repeat(prec)).slice(0, prec);
  const mant = prec > 0 ? head + "." + tail : head;
  const ex = point - 1;
  const esign = ex < 0 ? "-" : "+";
  const eabs = String(Math.abs(ex)).padStart(expWidth, "0");
  return (neg ? "-" : "") + mant + "e" + esign + eabs;
}

function nonFinite(x) {
  if (Number.isNaN(x)) return "nan";
  return x < 0 ? "-inf" : "inf";
}

// Python "%.Nf"：精确十进制 + 银行家舍入
export function pyFixed(x, prec) {
  if (!Number.isFinite(x)) return nonFinite(x);
  const { neg, dig, point } = exactDigits(x);
  const r = roundDigits(dig, point, point + prec);
  return renderFixed(neg, r.dig, r.point, prec);
}

// Python "%g"：prec 位有效数字，去尾零；指数域 X<-4 或 >=prec 走科学计数
export function pyG(x, prec = 6) {
  if (!Number.isFinite(x)) return nonFinite(x);
  const neg0 = x < 0 || Object.is(x, -0);
  if (x === 0) return neg0 ? "-0" : "0";
  let { dig, point } = exactDigits(x);
  ({ dig, point } = roundDigits(dig, point, prec));
  dig = dig.replace(/0+$/, "") || "0";
  const X = point - 1;
  if (X < -4 || X >= prec) {
    // %g 科学计数不去尾零不行、也不补零：尾数就是 dig 本身
    const mant = dig.length > 1 ? dig[0] + "." + dig.slice(1) : dig;
    const esign = X < 0 ? "-" : "+";
    return (neg0 ? "-" : "") + mant + "e" + esign + String(Math.abs(X)).padStart(2, "0");
  }
  const fracPrec = prec - 1 - X;
  const r = renderFixed(false, dig, point, fracPrec);
  return (neg0 ? "-" : "") + (r.includes(".") ? r.replace(/0+$/, "").replace(/\.$/, "") : r);
}

// Python repr(float)：最短回环数字，科学计数阈值 X<-4 或 X>=16，指数至少两位
export function pyFloatRepr(x) {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x < 0 ? "-inf" : "inf";
  const neg = x < 0 || Object.is(x, -0);
  const s = String(Math.abs(x));
  let digits, point;
  const ei = s.indexOf("e");
  if (ei >= 0) {
    const mant = s.slice(0, ei);
    const ex = parseInt(s.slice(ei + 1), 10);
    const dot = mant.indexOf(".");
    digits = dot >= 0 ? mant.replace(".", "") : mant;
    point = (dot >= 0 ? dot : mant.length) + ex;
  } else {
    const dot = s.indexOf(".");
    digits = dot >= 0 ? s.replace(".", "") : s;
    point = dot >= 0 ? dot : s.length;
  }
  // 去 leading zero 要同步左移 point；去 trailing zero 不动 point（只影响整数位补零数）
  let lead = 0;
  while (lead < digits.length - 1 && digits[lead] === "0") lead++;
  digits = digits.slice(lead);
  point -= lead;
  digits = digits.replace(/0+$/, "") || "0";
  const X = point - 1;
  let out;
  if (X < -4 || X >= 16) {
    const mant = digits.length > 1 ? digits[0] + "." + digits.slice(1) : digits;
    out = mant + "e" + (X < 0 ? "-" : "+") + String(Math.abs(X)).padStart(2, "0");
  } else if (X >= digits.length - 1 && point >= digits.length) {
    out = digits + "0".repeat(point - digits.length) + ".0";
  } else if (point <= 0) {
    out = "0." + "0".repeat(-point) + digits;
  } else {
    out = digits.slice(0, point) + "." + digits.slice(point);
  }
  return (neg ? "-" : "") + out;
}

// Python round(x[, ndigits])：银行家舍入；ndigits 缺省返回整数值
export function pyRound(x, ndigits = 0) {
  if (!Number.isFinite(x)) return x;
  const { neg, dig, point } = exactDigits(x);
  const r = roundDigits(dig, point, point + ndigits);
  const v = Number((neg ? "-" : "") + r.dig + "e" + (r.point - r.dig.length));
  return ndigits === 0 ? Math.trunc(v) : v;
}

// Python str() 的常用子集：数字按 repr 规则，布尔 True/False，None 由调用方处理
export function pyStr(v) {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") {
    if (Number.isInteger(v) && Math.abs(v) < 1e16) return String(v);
    return pyFloatRepr(v);
  }
  return String(v);
}

// Python dict.get(k, default)：键存在即返回值（哪怕是 null），不存在才用 default
export function pyGet(obj, key, deflt = undefined) {
  if (isDict(obj) && Object.prototype.hasOwnProperty.call(obj, key)) return obj[key];
  return deflt;
}

// Python truthiness：None/False/0/""/空列表/空字典 为假
export function pyTruthy(v) {
  if (v === null || v === undefined || v === false || v === 0 || v === "") return false;
  if (Array.isArray(v) && v.length === 0) return false;
  if (isDict(v) && Object.keys(v).length === 0) return false;
  return true;
}

// Python a or b or c：返回第一个 truthy，全假返回最后一个
export function pyOr(...vals) {
  for (const v of vals) if (pyTruthy(v)) return v;
  return vals[vals.length - 1];
}

export function isDict(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// html.escape(str(v if v is not None else ""), quote=True)
export function escapeHtml(v) {
  const s = pyStr(v === null || v === undefined ? "" : v);
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

const _ALWAYS_SAFE = /^[A-Za-z0-9_.\-~]$/;

// urllib.parse.quote(str(s), safe)：默认 safe='/'；非 ASCII 按 UTF-8 百分号编码，hex 大写
export function pyQuote(s, safe = "/") {
  s = String(s ?? "");
  const enc = new TextEncoder();
  let out = "";
  for (const ch of s) {
    if (ch.codePointAt(0) < 128 && (_ALWAYS_SAFE.test(ch) || safe.includes(ch))) {
      out += ch;
    } else {
      for (const b of enc.encode(ch)) out += "%" + b.toString(16).toUpperCase().padStart(2, "0");
    }
  }
  return out;
}

// ---------------------------------------------------------------- % 格式化

// Python "%" 运算符的常用子集：%[flags][width][.prec][diouxXfFgGcs%]
// flags: '-' 左对齐, '+' 正数加号, ' ' 空格, '0' 零填充
export function pyPercent(fmt, ...args) {
  let out = "", ai = 0, i = 0;
  while (i < fmt.length) {
    const c = fmt[i];
    if (c !== "%") { out += c; i++; continue; }
    i++;
    if (fmt[i] === "%") { out += "%"; i++; continue; }
    let minus = false, plus = false, space = false, zero = false;
    for (;; i++) {
      if (fmt[i] === "-") minus = true;
      else if (fmt[i] === "+") plus = true;
      else if (fmt[i] === " ") space = true;
      else if (fmt[i] === "0") zero = true;
      else break;
    }
    let width = 0;
    while (i < fmt.length && fmt[i] >= "0" && fmt[i] <= "9") { width = width * 10 + (fmt[i].charCodeAt(0) - 48); i++; }
    let prec = null;
    if (fmt[i] === ".") {
      i++; prec = 0;
      while (i < fmt.length && fmt[i] >= "0" && fmt[i] <= "9") { prec = prec * 10 + (fmt[i].charCodeAt(0) - 48); i++; }
    }
    const type = fmt[i]; i++;
    const arg = args[ai++];
    let s;
    switch (type) {
      case "s": {
        s = pyStr(arg);
        if (prec !== null) s = s.slice(0, prec);
        break;
      }
      case "d": case "i": {
        let n = typeof arg === "number" ? arg : Number(arg);
        if (!Number.isFinite(n)) throw new Error(`%${type} 需要数字，得到 ${pyStr(arg)}`);
        n = Math.trunc(n);
        let sign = n < 0 ? "-" : plus ? "+" : space ? " " : "";
        s = sign + String(Math.abs(n));
        if (zero && !minus && width > s.length) s = sign + "0".repeat(width - s.length) + s.slice(sign.length);
        break;
      }
      case "f": case "F": case "g": case "G": {
        s = type === "f" || type === "F" ? pyFixed(Number(arg), prec ?? 6) : pyG(Number(arg), prec ?? 6);
        if (!s.startsWith("-") && (plus || space)) s = (plus ? "+" : " ") + s;
        if (zero && !minus && width > s.length) {
          const signLen = s.startsWith("-") || s.startsWith("+") || s.startsWith(" ") ? 1 : 0;
          s = s.slice(0, signLen) + "0".repeat(width - s.length) + s.slice(signLen);
        }
        break;
      }
      default: throw new Error(`pyPercent 不支持的格式 %${type}`);
    }
    if (width > s.length) {
      s = minus ? s + " ".repeat(width - s.length) : " ".repeat(width - s.length) + s;
    }
    out += s;
  }
  return out;
}

// Python "%.Nf" 兼容的便捷别名（与 red_team.mjs 里的 pyFixed1 同口径但更通用）
export function pyFixedN(x, prec) { return pyFixed(x, prec); }

// Python datetime.now().strftime('%Y-%m-%d %H:%M')（本地时间）
export function nowStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// ---------------------------------------------------------------- 字符处理

// Python str.strip(chars)：两端剥掉 chars 里出现的任意字符
export function pyStrip(s, chars) {
  const set = new Set(chars);
  const arr = Array.from(String(s));
  let a = 0, b = arr.length;
  while (a < b && set.has(arr[a])) a++;
  while (b > a && set.has(arr[b - 1])) b--;
  return arr.slice(a, b).join("");
}

// Python str.lstrip(chars)
export function pyLstrip(s, chars) {
  const set = new Set(chars);
  const arr = Array.from(String(s));
  let a = 0;
  while (a < arr.length && set.has(arr[a])) a++;
  return arr.slice(a).join("");
}

// Python len(s)：按码点计数（emoji 在 JS .length 里是 2，Python 是 1）
export function pyLen(s) { return Array.from(String(s)).length; }

// Python s[:n]：按码点切片
export function pySlice(s, n) { return Array.from(String(s)).slice(0, n).join(""); }

// ---------------------------------------------------------------- 日期（对齐 datetime 的日历算术）

// days_from_civil / civil_from_days（Howard Hinnant，整数运算，1970 纪元）
export function daysFromCivil(y, m, d) {
  y -= m <= 2 ? 1 : 0;
  const era = Math.floor(y / 400);
  const yoe = y - era * 400;
  const mp = (m + 9) % 12;
  const doy = Math.floor((153 * mp + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}

export function civilFromDays(z) {
  z += 719468;
  const era = Math.floor(z / 146097);
  const doe = z - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const y = yoe + era * 400;
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const m = mp < 10 ? mp + 3 : mp - 9;
  return [m <= 2 ? y + 1 : y, m, d];
}

// datetime(y, m, d) + timedelta(days=n) → 'YYYY-MM-DD'（对齐 strftime）
export function addDays(dateStr, n) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr).trim());
  if (!m) return null;
  const [y, mo, d] = civilFromDays(daysFromCivil(+m[1], +m[2], +m[3]) + n);
  return `${String(y).padStart(4, "0")}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

// dt - timedelta(hours=hours)：dt = {y, m, d, hh, mm, ss}（朴素本地时刻，对齐 timedelta 的
// days/seconds/microseconds 整数归一化；小时偏移含 0.5/0.25 增量时浮点精确）
export function shiftHours(dt, hours) {
  const base = daysFromCivil(dt.y, dt.m, dt.d) * 86400 + dt.hh * 3600 + dt.mm * 60 + dt.ss;
  const total = base - hours * 3600;
  const days = Math.floor(total / 86400);
  let rem = total - days * 86400;
  const hh = Math.floor(rem / 3600);
  rem -= hh * 3600;
  const mm = Math.floor(rem / 60);
  const ss = rem - mm * 60;
  const [y, m, d] = civilFromDays(days);
  return { y, m, d, hh, mm, ss };
}

// ---------------------------------------------------------------- JSON（对齐 json.dumps/loads）

// JS 只有 number 一种数值类型，而 Python 区分 int/float：json.dumps(12.0) -> "12.0"，
// json.dumps(12) -> "12"。要逐字节对齐，必须保留"这个字面量 originally 是 float"。
// PyFloat 就是这个标记：pyJsonLoads 把含 ./e 的字面量包成 PyFloat，
// 移植代码里凡 Python 语义产 float 的地方（round(x,n)、/、均值……）用 pyF() 包一下，
// pyJsonDumps 再按 Python 规则渲染。
export class PyFloat {
  constructor(v) { this.v = v; }
}
export function pyF(x) { return new PyFloat(x); }
export function isPyFloat(x) { return x instanceof PyFloat; }

// 超出 2^53 的 JSON 整数字面量：Python int 精确，JS number 会丢位。
// 保留原始字面量文本，dumps 时原样输出（不做算术）。
export class PyBigInt {
  constructor(raw) { this.raw = raw; }
}
// 参与运算时解包：PyFloat -> number，其余原样
export function num(v) { return v instanceof PyFloat ? v.v : v; }

// Python json.dumps(ensure_ascii=False) 的字符串编码：
// 转义 " \ 与 C0 控制符（\b \t \n \f \r 用简写，其余 \u00XX），其余原样输出。
export function pyJsonEncodeStr(s) {
  let out = '"';
  for (const ch of String(s)) {
    const c = ch.codePointAt(0);
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (ch === "\b") out += "\\b";
    else if (ch === "\t") out += "\\t";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\f") out += "\\f";
    else if (ch === "\r") out += "\\r";
    else if (c < 0x20) out += "\\u" + c.toString(16).padStart(4, "0");
    else out += ch;
  }
  return out + '"';
}

function pyJsonDumpsInner(o, indent, level, out) {
  const pad = " ".repeat(indent * (level + 1));
  const cur = " ".repeat(indent * level);
  if (o === null || o === undefined) { out.push("null"); return; }
  if (typeof o === "boolean") { out.push(o ? "true" : "false"); return; }
  if (o instanceof PyFloat) {
    // Python repr(float)：非有限值默认 allow_nan=True 输出 NaN/Infinity/-Infinity
    if (Number.isNaN(o.v)) out.push("NaN");
    else if (!Number.isFinite(o.v)) out.push(o.v < 0 ? "-Infinity" : "Infinity");
    else out.push(pyFloatRepr(o.v));
    return;
  }
  if (typeof o === "number") {
    // 移植代码里裸 number 视为 int 语义（json.load 的整数）；非整数值按 float repr
    if (Number.isInteger(o) && Math.abs(o) < 1e16) out.push(String(o));
    else if (!Number.isFinite(o)) out.push(Number.isNaN(o) ? "NaN" : (o < 0 ? "-Infinity" : "Infinity"));
    else out.push(pyFloatRepr(o));
    return;
  }
  if (o instanceof PyBigInt) { out.push(o.raw); return; }
  if (typeof o === "string") { out.push(pyJsonEncodeStr(o)); return; }
  if (Array.isArray(o)) {
    if (!o.length) { out.push("[]"); return; }
    out.push("[\n");
    o.forEach((v, i) => {
      out.push(pad);
      pyJsonDumpsInner(v, indent, level + 1, out);
      out.push(i === o.length - 1 ? "\n" : ",\n");
    });
    out.push(cur + "]");
    return;
  }
  if (typeof o === "object") {
    const keys = Object.keys(o);
    if (!keys.length) { out.push("{}"); return; }
    out.push("{\n");
    keys.forEach((k, i) => {
      out.push(pad + pyJsonEncodeStr(k) + ": ");
      pyJsonDumpsInner(o[k], indent, level + 1, out);
      out.push(i === keys.length - 1 ? "\n" : ",\n");
    });
    out.push(cur + "}");
    return;
  }
  out.push(pyJsonEncodeStr(String(o)));
}

// Python json.dumps(o, ensure_ascii=False, indent=2)
export function pyJsonDumps(o, indent = 2) {
  const out = [];
  pyJsonDumpsInner(o, indent, 0, out);
  return out.join("");
}

// 手写 JSON 解析器（只为把 float 字面量包成 PyFloat；语法对齐 json.loads）
export function pyJsonLoads(text) {
  const s = String(text);
  let i = 0;
  const err = (msg) => { throw new SyntaxError(msg + " (pos " + i + ")"); };
  function skipWs() { while (i < s.length && " \t\n\r".includes(s[i])) i++; }
  function parseString() {
    // s[i] === '"'
    i++;
    let out = "";
    while (i < s.length) {
      const ch = s[i];
      if (ch === '"') { i++; return out; }
      if (ch === "\\") {
        const e = s[i + 1];
        if (e === '"' || e === "\\" || e === "/") { out += e; i += 2; }
        else if (e === "b") { out += "\b"; i += 2; }
        else if (e === "f") { out += "\f"; i += 2; }
        else if (e === "n") { out += "\n"; i += 2; }
        else if (e === "r") { out += "\r"; i += 2; }
        else if (e === "t") { out += "\t"; i += 2; }
        else if (e === "u") {
          const hx = s.slice(i + 2, i + 6);
          if (!/^[0-9a-fA-F]{4}$/.test(hx)) err("bad \\u escape");
          out += String.fromCharCode(parseInt(hx, 16));
          i += 6;
        } else err("bad escape");
      } else {
        out += ch;
        i++;
      }
    }
    err("unterminated string");
  }
  function parseNumber() {
    const m = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(s.slice(i));
    if (!m) err("bad number");
    i += m[0].length;
    if (m[0].includes(".") || m[0].includes("e") || m[0].includes("E")) {
      return new PyFloat(parseFloat(m[0]));
    }
    const iv = parseInt(m[0], 10);
    if (!Number.isSafeInteger(iv)) return new PyBigInt(m[0]);
    return iv;
  }
  function parseValue() {
    skipWs();
    const ch = s[i];
    if (ch === '"') return parseString();
    if (ch === "{") {
      i++;
      const o = {};
      skipWs();
      if (s[i] === "}") { i++; return o; }
      while (true) {
        skipWs();
        if (s[i] !== '"') err("expected key");
        const k = parseString();
        skipWs();
        if (s[i] !== ":") err("expected colon");
        i++;
        o[k] = parseValue();
        skipWs();
        if (s[i] === ",") { i++; continue; }
        if (s[i] === "}") { i++; return o; }
        err("expected , or }");
      }
    }
    if (ch === "[") {
      i++;
      const a = [];
      skipWs();
      if (s[i] === "]") { i++; return a; }
      while (true) {
        a.push(parseValue());
        skipWs();
        if (s[i] === ",") { i++; continue; }
        if (s[i] === "]") { i++; return a; }
        err("expected , or ]");
      }
    }
    if (s.startsWith("true", i)) { i += 4; return true; }
    if (s.startsWith("false", i)) { i += 5; return false; }
    if (s.startsWith("null", i)) { i += 4; return null; }
    if (s.startsWith("NaN", i)) { i += 3; return new PyFloat(NaN); }
    if (s.startsWith("Infinity", i)) { i += 8; return new PyFloat(Infinity); }
    if (s.startsWith("-Infinity", i)) { i += 9; return new PyFloat(-Infinity); }
    if (ch === "-" || (ch >= "0" && ch <= "9")) return parseNumber();
    err("unexpected char " + JSON.stringify(ch));
  }
  const v = parseValue();
  skipWs();
  if (i < s.length) err("trailing data");
  return v;
}
