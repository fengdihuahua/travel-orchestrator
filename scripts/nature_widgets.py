#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nature_widgets.py · travel-orchestrator 自然专项的交互小部件（只用标准库）

给渲染器提供四件「能点、能看」的东西：
  1. species_list  —— 鸟种卡：展开看学名/科属/保护等级/简介，并给出查图与图鉴的外链
  2. star_section  —— 星图：真算地平坐标（儒略日 → 恒星时 → 时角 → 高度/方位），配月亮相位盘
  3. sun_section   —— 天光条（蓝调/黄金/日出日落整条天色）+ 霞光（火烧云）指数
  4. tide_section  —— 潮汐图：点曲线上的圆点，换该时刻的潮位与下滩建议

输出全是内联 SVG 与纯 HTML：没有 <script>，不引任何远程资源，双击 HTML 就能用。
交互用原生 <details> 和 <input type="radio"> + CSS 兄弟选择器实现，不写一行脚本。

星图的口径（重要，别把它当精密星历使）：
  星表是内置的 43 颗亮星（J2000），只画地平线以上的；
  算法含恒星时与地平坐标换算，但没有做岁差、大气折射、光污染与地形遮挡修正。
  所以图上标「示意星图」，精确星位请用 Stellarium 这类软件复核。
"""
from __future__ import annotations

import html
import math
import re
from datetime import datetime
from urllib.parse import quote

__version__ = "1.1.0"

__all__ = [
    "map_url", "map_link", "place_links", "species_list", "split_species_text",
    "star_section", "sun_section", "tide_section", "silhouette_svg",
]

AMAP = "https://uri.amap.com/search?keyword="

# ---------------------------------------------------------------- 基础工具


def _e(v):
    """HTML 转义。None 变空串，别把 None 打印到页面上。"""
    return html.escape("" if v is None else str(v), quote=True)


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _hm(text):
    """'05:45' / '日出 05:45' / '2026-10-01 06:08' -> 分钟数；取不到返回 None。"""
    m = re.search(r"(\d{1,2}):(\d{2})", str(text or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def _hm_txt(mins):
    mins = int(round(mins))
    return "%02d:%02d" % (mins // 60, mins % 60)


def _first_range(text):
    """'05:20-05:45' / '05:45-06:45（10/1）；05:46-06:46（10/2）' -> (325, 345)。"""
    m = re.search(r"(\d{1,2}):(\d{2})\s*[-~—－至]\s*(\d{1,2}):(\d{2})", str(text or ""))
    if not m:
        return None
    a, b = _hm(m.group(1) + ":" + m.group(2)), _hm(m.group(3) + ":" + m.group(4))
    if a is None or b is None or b <= a:
        return None
    return (a, b)


def _as_list(v):
    """字符串按常见分隔符切成列表；已是列表就原样。用于地点、鸟种这类字段。"""
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        return [x for x in v if x not in (None, "")]
    return [p.strip() for p in re.split(r"[、,，;；/|]+", str(v)) if p.strip()]


# ---------------------------------------------------------------- 地点链接


def map_url(place):
    """地点 → 高德搜索链接。只是拼字符串，页面本身不联网。"""
    return AMAP + quote(str(place))


def search_keyword(text):
    """从「成山头（朝东无遮挡）」这类写法里剥出搜索词：去掉括号附注和纯噪声尾缀。

    只剥「备选」这种对地图搜索毫无帮助的词。**不要剥「观鸟点 / 观景台」**——
    它们是地名的一部分（「…自然保护区开放观鸟点」剥掉就剩「…自然保护区开放」，
    搜不到东西）。剥完太短就退回原文，别把搜索词剥没了。
    """
    raw = str(text or "").strip()
    s = re.sub(r"[（(][^）)]*[）)]", "", raw).strip()
    s = re.sub(r"(为免费备选|免费备选|备选点?)$", "", s).strip(" ·-—")
    return s if len(s) >= 2 else raw


def map_link(place, keyword=None):
    if not place:
        return ""
    return ('<a class="map" href="' + _e(map_url(keyword or place)) + '" target="_blank" '
            'rel="noopener noreferrer">' + _e(place) + "</a>")


def _split_places(text):
    return [p.strip() for p in re.split(r"[、,，;；/|]+", str(text or "")) if p.strip()]


def place_links(value, sep=""):
    """支持三种写法：
         '成山头（朝东无遮挡）；樱花湖东岸'      —— 按分隔符切开，逐段给搜索链接
         ['荣成樱花湖', '桑沟湾滩涂']            —— 每个地点一个链接
         [{'name': '成山头', 'note': '朝东无遮挡'}] —— 带备注，备注排成小字
    搜索词会把括号里的说明剥掉，不然高德会拿整句话去搜。

    分隔符默认留空：由 CSS 的 .pl-item+.pl-item::before 画「、」。
    写在 HTML 里的话，一行排不下时「、」会孤零零落在行尾，很难看。
    """
    if not value:
        return ""
    items = value if isinstance(value, (list, tuple)) else _split_places(value)
    chunks = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("place") or ""
            note = item.get("note") or item.get("why") or ""
        else:
            name, note = str(item), ""
        if not name:
            continue
        one = map_link(name, search_keyword(name))
        if note:
            one += '<span class="pl-note">' + _e(note) + "</span>"
        chunks.append('<span class="pl-item">' + one + "</span>")
    return ('<span class="pl">' + sep.join(chunks) + "</span>") if chunks else ""


# ---------------------------------------------------------------- 鸟种剪影
# 全部用 currentColor 上色（fill 半透明 + stroke 实线），颜色由卡片的色系类决定。
_SIL = {
    "gull": (
        '<path d="M4 21.2c3.4-6.4 9.6-10.6 17-10.6 1.1 0 1.7.7 1.7 1.7 0 5.4-4.6 9.5-10.7 9.5H8.6'
        'A4.6 4.6 0 0 1 4 21.2z"/>'
        '<path d="M4.2 22.6 1.2 27.4l5.6-2.8z"/>'
        '<path d="M22.6 12.2l5.8 1.6-5.8 1.8z"/>'
        '<circle cx="20" cy="13" r="1.15" fill="currentColor" opacity=".9"/>'
    ),
    "wader": (
        '<path d="M7 18.6c2.2-4 6-6.5 10.6-6.5 3 0 4.9 1.5 4.9 3.8 0 3.7-3.7 6.3-9.1 6.3h-4'
        'c-2.5 0-4.1-1.4-2.4-3.6z"/>'
        '<circle cx="23.4" cy="12.6" r="2.9"/>'
        '<path d="M25.6 12.1 31.6 13l-6 1.7z"/>'
        '<path d="M12 22.2v6.4M17 22.2v6.4"/>'
        '<path d="M10.4 28.6h3.4M15.4 28.6h3.4"/>'
        '<circle cx="22.6" cy="12" r="1" fill="currentColor" opacity=".9"/>'
    ),
    "swan": (
        '<ellipse cx="12.6" cy="22" rx="9" ry="5.2"/>'
        '<path d="M8.2 19.8c2.6-1.5 5.8-1.7 8.4-.6"/>'
        '<path d="M16.2 18.4c.4-5.6 2.4-8.9 5.3-8.9 2.3 0 3.7 1.7 3.2 3.7" fill="none"/>'
        '<circle cx="22.9" cy="11.4" r="2.6"/>'
        '<path d="M25.1 11 30.6 12.4l-5.5 1.9z"/>'
        '<circle cx="22.2" cy="10.8" r=".95" fill="currentColor" opacity=".9"/>'
    ),
    "raptor": (
        '<path d="M16 20.4c-1.5-3.7-5.6-6.6-13.4-7.6 1.5 5.6 4.8 9.1 9.8 10.7z"/>'
        '<path d="M16 20.4c1.5-3.7 5.6-6.6 13.4-7.6-1.5 5.6-4.8 9.1-9.8 10.7z"/>'
        '<ellipse cx="16" cy="20.4" rx="4.2" ry="7.6"/>'
        '<circle cx="16" cy="9.8" r="3.5"/>'
        '<path d="M18.9 9.6c1.3.2 2.1 1 2 2.1-.1 1.1-1.2 1.7-2.6 1.5z"/>'
        '<circle cx="14.8" cy="9.2" r="1" fill="currentColor" opacity=".9"/>'
    ),
    "longneck": (
        '<path d="M13 16c-.5-4.6 1.4-7.9 4.6-8.7 2.5-.6 4.2.8 3.9 2.7" fill="none"/>'
        '<circle cx="18.8" cy="8.6" r="2.3"/>'
        '<path d="M20.7 8.6 27.6 9.7l-6.9 1.8z"/>'
        '<ellipse cx="10.6" cy="20" rx="7.8" ry="4.6"/>'
        '<path d="M6 23.6v5.8M13.6 23.6v5.8"/>'
        '<path d="M4.4 29.4h3.2M12 29.4h3.2"/>'
    ),
    "songbird": (
        '<ellipse cx="14" cy="18.2" rx="7.6" ry="6"/>'
        '<path d="M9.2 16c2.6-1.7 5.8-2.1 8.2-1.1"/>'
        '<circle cx="20.6" cy="10.9" r="4.2"/>'
        '<path d="M24.4 10.3 29.2 12l-4.8 1.6z"/>'
        '<path d="M8.4 22.8 2.6 28.4l7.2-1.9z"/>'
        '<circle cx="19.6" cy="10.4" r="1.05" fill="currentColor" opacity=".9"/>'
    ),
}

# 科名/中文名 -> 剪影。命中第一个即用。
_SHAPE_RULES = [
    (("laridae", "鸥"), "gull"),
    (("scolopacidae", "charadriidae", "鹬", "鸻"), "wader"),
    (("anatidae", "cygnus", "anser", "天鹅", "雁", "鸭"), "swan"),
    (("accipitridae", "falconidae", "鹰", "隼", "鸮", "雕", "鸢"), "raptor"),
    (("phalacrocoracidae", "gaviidae", "podicipedidae", "ardeidae", "gruidae",
      "鸬鹚", "潜鸟", "鹭", "鹤"), "longneck"),
]


def bird_shape(*texts):
    blob = " ".join(str(t or "") for t in texts).lower()
    for keys, shape in _SHAPE_RULES:
        if any(k.lower() in blob for k in keys):
            return shape
    return "songbird"


def silhouette_svg(shape):
    return ('<svg viewBox="0 0 32 32" fill="currentColor" fill-opacity=".2" '
            'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true">'
            + _SIL.get(shape, _SIL["songbird"]) + "</svg>")


# ---------------------------------------------------------------- 鸟种卡


# 保护等级代码 -> 中文；iNaturalist 返回的是各国红名录的原始代码，照抄不翻译会看不懂
_RANK_CN = {
    "EX": "灭绝", "EW": "野外灭绝", "CR": "极危", "EN": "濒危", "VU": "易危",
    "NT": "近危", "LC": "无危", "DD": "数据缺乏", "NE": "未评估",
}


def _rank_items(sp):
    """把 species.status 归一成 [{'rank','source'}]，兼容字符串写法。"""
    raw = sp.get("status") or sp.get("保护等级") or []
    out = []
    if isinstance(raw, str):
        raw = [raw]
    for item in raw:
        if isinstance(item, dict):
            out.append({"rank": str(item.get("rank") or item.get("评级") or "").strip(),
                        "source": str(item.get("source") or item.get("来源") or "").strip()})
        elif item:
            out.append({"rank": str(item).strip(), "source": ""})
    return [x for x in out if x["rank"]]


def _pick_rank(items):
    """挑一个最该被看见的评级：优先 IUCN，其次第一个能识别的代码。"""
    for it in items:
        if "iucn" in it["source"].lower():
            return it
    for it in items:
        if it["rank"].upper() in _RANK_CN:
            return it
    return items[0] if items else None


# 长句里可能出现的分隔标点：取第一个当断点
_PLAIN_BREAKS = "（(，,；;：:、"


def split_species_text(text):
    """把「鸟名 + 说明」的长句拆成 (鸟名, 说明)。

    `species[]` 的纯字符串只该写鸟名。写成一句话时，整句会被撑成一枚大胶囊，
    外链还会拿整句去搜图鉴（搜不到）。这里按首个标点拆一刀：左半当鸟名，
    右半当说明。拆不出边界且过长时，只在显示上截断，完整文本留在 title 里。
    """
    t = str(text or "").strip()
    if not t:
        return "", ""
    cut = len(t)
    for i, ch in enumerate(t):
        if ch in _PLAIN_BREAKS:
            cut = i
            break
    name = t[:cut].strip(" 　·．.。")
    note = t[cut:].lstrip(_PLAIN_BREAKS + " 　").strip()
    if not name:
        return t, note
    if len(name) > 12:
        note = note or t[len(name):]
        name = name[:12] + "…"
    return name, note


def _species_normalize(sp):
    if isinstance(sp, str):
        return {"name": sp.strip()}
    if isinstance(sp, dict):
        d = dict(sp)
        d["name"] = str(d.get("name") or d.get("中文名") or "").strip()
        d["sci"] = str(d.get("sci") or d.get("学名") or "").strip()
        return d
    return {}


def species_list(species):
    """鸟种卡列表。没有结构化信息时退回一枚小胶囊，绝不留空白。"""
    items = [_species_normalize(x) for x in (species or [])]
    items = [x for x in items if x.get("name")]
    if not items:
        return ""
    cards = []
    for i, sp in enumerate(items):
        name, dangling = split_species_text(sp["name"])
        if not name:
            name = sp["name"]
        sci = sp.get("sci") or ""
        tax = sp.get("family") or sp.get("分类") or ""
        ranks = _rank_items(sp)
        best = _pick_rank(ranks)
        is_plain = not any([sci, tax, ranks, sp.get("intro") or sp.get("简介")])
        shape = sp.get("shape") or bird_shape(name, tax)
        cls = "sp sp-c%d" % (i % 6 + 1)
        if is_plain:
            note_html = ""
            if dangling:
                note_html = ('<span class="sp-note" title="%s">%s</span>'
                             % (_e(dangling), _e(dangling)))
            cards.append('<div class="%s sp-plain"><i class="sp-ico">%s</i>'
                         '<span class="sp-name">%s</span>%s%s</div>'
                         % (cls, silhouette_svg(shape), _e(name), note_html,
                            _species_links(sp, name, compact=True)))
            continue

        if best:
            code = best["rank"].upper()
            cn = _RANK_CN.get(code, "")
            badge = "%s%s" % (code, "（%s）" % cn if cn else "")
            if "iucn" not in (best["source"] or "").lower():
                badge += "*"
            badge_html = '<span class="sp-badge">%s</span>' % _e(badge)
        else:
            badge_html = '<span class="sp-badge sp-badge-none">保护等级未查到</span>'

        meta = []
        if tax:
            meta.append(("分类", tax))
        if dangling:
            meta.append(("说明", dangling))
        if ranks:
            text = "；".join("%s%s" % (r["rank"], "（%s）" % r["source"] if r["source"] else "")
                            for r in ranks[:5])
            if len(ranks) > 5:
                text += "；等 %d 项" % len(ranks)
            meta.append(("保护等级", text))
        months = sp.get("best_months") or sp.get("最佳月份")
        if months:
            if isinstance(months, (list, tuple)):
                months = "、".join(str(m) for m in months)
            meta.append(("本地最佳月份", str(months)))
        if sp.get("global_records"):
            meta.append(("全球观测记录", str(sp["global_records"])))
        meta_html = "".join(
            '<div class="sp-m"><dt>%s</dt><dd>%s</dd></div>' % (_e(k), _e(v)) for k, v in meta)

        intro = sp.get("intro") or sp.get("简介") or ""
        intro_src = sp.get("intro_source") or sp.get("简介来源") or ""
        body = ['<div class="sp-links">' + _species_links(sp, name, compact=False) + "</div>"]
        if meta_html:
            body.append('<dl class="sp-metas">' + meta_html + "</dl>")
        if intro:
            body.append('<p class="sp-intro">' + _e(intro) + "</p>")
        if intro_src:
            src = ('<a class="src-link" href="' + _e(intro_src) + '" target="_blank" '
                   'rel="noopener noreferrer">' + _e(intro_src) + "</a>")
            body.append('<p class="sp-src">简介来源：' + src + "</p>")
        if any(r["source"] for r in ranks):
            body.append('<p class="sp-src">等级来源见上；iNaturalist 不含《国家重点保护野生'
                        '动物名录》，涉及拍摄许可或执法请人工核对官方名录。</p>')

        cards.append(
            '<details class="' + cls + '">'
            '<summary><i class="sp-ico">' + silhouette_svg(shape) + "</i>"
            '<span class="sp-name">' + _e(name) + "</span>"
            + ('<span class="sp-sci">' + _e(sci) + "</span>" if sci else "")
            + badge_html
            + '<span class="sp-more">查图 · 详情</span></summary>'
            '<div class="sp-body">' + "".join(body) + "</div></details>"
        )
    return '<div class="splist">' + "".join(cards) + "</div>"


def _species_links(sp, name, compact):
    """查图与图鉴外链。没有给 URL 就用搜索页兜底，保证「点得动」。"""
    q = quote(name)
    links = []
    taxa = sp.get("taxa_url") or sp.get("species_url") or sp.get("iNat")
    taxa = str(taxa or "https://www.inaturalist.org/search?q=" + q)
    links.append((taxa, "图鉴页"))
    links.append(("https://image.baidu.com/search/index?tn=baiduimage&word=" + q, "查图片"))
    links.append(("https://www.birdreport.cn/", "本地记录"))
    links.append(("https://ebird.org/explore", "全球地图"))
    if compact:
        links = links[:2]
    out = "".join(
        '<a class="sp-link" href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
        % (_e(u), _e(t)) for u, t in links)
    return out


# ---------------------------------------------------------------- 星图
# 内置亮星表：名称 -> (赤经 小时, 赤纬 度, 视星等)，J2000
STARS = {
    "北极星": (2.530, 89.264, 1.98),
    "天枢": (11.062, 61.751, 1.79),
    "天璇": (11.031, 56.382, 2.37),
    "天玑": (11.897, 53.695, 2.44),
    "天权": (12.257, 57.033, 3.31),
    "玉衡": (12.900, 55.960, 1.77),
    "开阳": (13.399, 54.925, 2.23),
    "摇光": (13.792, 49.313, 1.86),
    "王良四": (0.153, 59.150, 2.28),
    "王良一": (0.675, 56.537, 2.24),
    "策": (0.945, 60.717, 2.47),
    "阁道三": (1.430, 60.235, 2.68),
    "阁道二": (1.906, 63.670, 3.35),
    "室宿一": (23.079, 15.205, 2.49),
    "室宿二": (23.063, 28.083, 2.42),
    "壁宿一": (0.221, 15.184, 2.83),
    "壁宿二": (0.140, 29.091, 2.06),
    "天大将军一": (2.065, 42.330, 2.10),
    "奎宿九": (1.162, 35.620, 2.05),
    "织女星": (18.616, 38.784, 0.03),
    "牛郎星": (19.846, 8.868, 0.77),
    "天津四": (20.690, 45.280, 1.25),
    "心宿二": (16.490, -26.432, 1.09),
    "大角星": (14.261, 19.182, -0.05),
    "五车二": (5.278, 45.998, 0.08),
    "天狼星": (6.752, -16.716, -1.46),
    "南河三": (7.655, 5.225, 0.34),
    "北河三": (7.755, 28.026, 1.14),
    "北河二": (7.577, 31.888, 1.58),
    "参宿四": (5.919, 7.407, 0.50),
    "参宿七": (5.242, -8.202, 0.13),
    "参宿五": (5.418, 6.350, 1.64),
    "参宿六": (5.796, -9.670, 2.06),
    "参宿三": (5.533, -0.299, 2.23),
    "参宿二": (5.604, -1.202, 1.69),
    "参宿一": (5.679, -1.943, 1.77),
    "毕宿五": (4.599, 16.509, 0.87),
    "五车五": (5.438, 28.608, 1.65),
    "北落师门": (22.961, -29.622, 1.16),
    "角宿一": (13.420, -11.161, 1.04),
    "轩辕十四": (10.139, 11.967, 1.36),
    "娄宿三": (2.120, 23.462, 2.00),
    "天船三": (3.405, 49.861, 1.79),
}
# 星群连线：给几个一眼能认出来的，别贪多
ASTERISMS = [
    ("北斗七星", ["天枢", "天璇", "天玑", "天权", "玉衡", "开阳", "摇光"]),
    ("仙后座", ["王良四", "王良一", "策", "阁道三", "阁道二"]),
    ("秋季四边形", ["室宿一", "室宿二", "壁宿二", "壁宿一", "室宿一"]),
    ("夏季大三角", ["织女星", "天津四", "牛郎星", "织女星"]),
    ("猎户座", ["参宿四", "参宿五", "参宿七", "参宿六", "参宿四"]),
    ("猎户腰带", ["参宿三", "参宿二", "参宿一"]),
]


def _jd_utc(dt):
    """儒略日。dt 视为 UTC。"""
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _gmst_hours(jd):
    """格林尼治平恒星时（小时）。"""
    t = (jd - 2451545.0) / 36525.0
    deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
           + 0.000387933 * t * t - t * t * t / 38710000.0)
    return (deg % 360.0) / 15.0


def _altaz(ra_h, dec_d, lat_d, lst_h):
    """赤道坐标 -> 地平坐标。返回 (高度角, 方位角)，方位从正北起顺时针。"""
    ha = ((lst_h - ra_h) * 15.0 + 180.0) % 360.0 - 180.0
    ha_r, dec_r, lat_r = map(math.radians, (ha, dec_d, lat_d))
    sin_alt = math.sin(dec_r) * math.sin(lat_r) + math.cos(dec_r) * math.cos(lat_r) * math.cos(ha_r)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt)
    denom = math.cos(alt) * math.cos(lat_r)
    if abs(denom) < 1e-9:
        return math.degrees(alt), 0.0
    cos_az = max(-1.0, min(1.0, (math.sin(dec_r) - sin_alt * math.sin(lat_r)) / denom))
    az = math.degrees(math.acos(cos_az))
    if math.sin(ha_r) > 0:
        az = 360.0 - az
    return math.degrees(alt), az % 360.0


def _parse_at(value):
    """'2026-10-01T21:30' / '2026-10-01 21:30' -> datetime；解析不了返回 None。"""
    s = str(value or "").strip().replace("/", "-")
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            return None
    return None


# 星图几何：中心、半径
_SKY_W, _SKY_H, _SKY_CX, _SKY_CY, _SKY_R = 460, 420, 230, 198, 150


def star_map_svg(cfg):
    """把那一刻地平线以上的亮星画成一张极坐标星图（方位绕圈、半径表示高度）。"""
    lat = _num(cfg.get("lat"))
    lng = _num(cfg.get("lng"))
    when = _parse_at(cfg.get("at"))
    if lat is None or lng is None or when is None:
        return ""
    tz = _num(cfg.get("tz"))
    tz = 8.0 if tz is None else tz
    utc = when - __import__("datetime").timedelta(hours=tz)
    jd = _jd_utc(utc)
    lst = (_gmst_hours(jd) + lng / 15.0) % 24.0

    placed, lines, labels = [], [], []
    for name, (ra, dec, mag) in STARS.items():
        alt, az = _altaz(ra, dec, lat, lst)
        if alt <= 0.4:
            continue
        r = _SKY_R * (1.0 - alt / 90.0)
        x = _SKY_CX + r * math.sin(math.radians(az))
        y = _SKY_CY - r * math.cos(math.radians(az))
        placed.append((name, x, y, mag, alt, az))

    coords = {p[0]: (p[1], p[2]) for p in placed}
    labels, occupied = [], []   # occupied: (x, y, 最小距离平方) —— 标签之间互相躲开
    for cname, members in ASTERISMS:
        pts = [coords[m] for m in members if m in coords]
        if len(pts) < 2:
            continue
        d = "M" + " L".join("%.1f %.1f" % p for p in pts)
        lines.append('<path class="cst" d="%s"/>' % d)
        if len(pts) >= max(2, len(members) - 1):
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            # 星群名往图外推一点：中心那块挤满了高高度角的星，名字放中间必糊。
            # 但要压回圆环内侧，跑到浅色底上黄字就看不见了。
            vx, vy = cx - _SKY_CX, cy - _SKY_CY
            norm = math.hypot(vx, vy) or 1.0
            lx, ly = cx + vx / norm * 30, cy + vy / norm * 30 + 4
            dist = math.hypot(lx - _SKY_CX, ly - _SKY_CY)
            cap = _SKY_R * 0.84
            if dist > cap:
                lx = _SKY_CX + (lx - _SKY_CX) / dist * cap
                ly = _SKY_CY + (ly - _SKY_CY) / dist * cap
            labels.append('<text class="cstlbl" x="%.1f" y="%.1f">%s</text>'
                          % (lx, ly, _e(cname)))
            occupied.append((lx, ly, 1700.0))

    stars = []
    for name, x, y, mag, alt, az in sorted(placed, key=lambda p: p[3]):
        rad = max(1.0, 3.9 - 0.42 * mag)
        tip = "%s｜高度 %.0f°｜方位 %.0f°｜星等 %.2f" % (name, alt, az, mag)
        stars.append('<circle class="st" cx="%.1f" cy="%.1f" r="%.2f"><title>%s</title></circle>'
                     % (x, y, rad, _e(tip)))
        if mag <= 1.7 and alt > 7:
            if any((x - ox) ** 2 + (y - oy) ** 2 < d2 for ox, oy, d2 in occupied):
                continue
            anchor = "start" if x < _SKY_CX + 60 else "end"
            dx = 7 if anchor == "start" else -7
            lx, ly = x + dx, y + 3.4
            labels.append('<text class="stlbl" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
                          % (lx, ly, anchor, _e(name)))
            occupied.append((lx + (14 if anchor == "start" else -14), ly, 900.0))

    if not placed:
        return ""
    ring = ('<circle class="skygrid" cx="%d" cy="%d" r="%.1f"/>'
            '<circle class="skygrid" cx="%d" cy="%d" r="%.1f"/>'
            % (_SKY_CX, _SKY_CY, _SKY_R * 2 / 3.0, _SKY_CX, _SKY_CY, _SKY_R / 3.0))
    cross = ('<path class="skygrid" d="M%d %d V%d M%d %d H%d"/>'
             % (_SKY_CX, _SKY_CY - _SKY_R, _SKY_CY + _SKY_R,
                _SKY_CX - _SKY_R, _SKY_CY, _SKY_CX + _SKY_R))
    cards_txt = "".join(
        '<text class="cardi" x="%.1f" y="%.1f" text-anchor="middle">%s</text>'
        % (x, y, t) for x, y, t in (
            (_SKY_CX, _SKY_CY - _SKY_R - 12, "北"),
            (_SKY_CX + _SKY_R + 18, _SKY_CY + 4, "东"),
            (_SKY_CX, _SKY_CY + _SKY_R + 24, "南"),
            (_SKY_CX - _SKY_R - 18, _SKY_CY + 4, "西"),
        ))
    return ('<svg class="skychart" viewBox="0 0 %d %d" role="img" aria-label="当地当时的示意星图">'
            '<defs><clipPath id="to-skyclip"><circle cx="%d" cy="%d" r="%d"/></clipPath></defs>'
            '<circle class="sky-bg" cx="%d" cy="%d" r="%d"/>'
            '<g clip-path="url(#to-skyclip)">%s%s%s%s</g>'
            '<circle class="sky-ring" cx="%d" cy="%d" r="%d"/>%s</svg>'
            % (_SKY_W, _SKY_H, _SKY_CX, _SKY_CY, _SKY_R,
               _SKY_CX, _SKY_CY, _SKY_R,
               ring, cross, "".join(lines), "".join(stars),
               _SKY_CX, _SKY_CY, _SKY_R, "".join(labels) + cards_txt))


def moon_disc_svg(illum_pct, waxing=True, size=56):
    """按照度画出真实的亮面比例（新月到满月之间是椭圆终结线）。"""
    k = max(0.0, min(1.0, float(illum_pct) / 100.0))
    r = 22.0
    rx = r * abs(2 * k - 1)
    sweep = 1 if k < 0.5 else 0
    lit = ('<path d="M 0 %.1f A %.1f %.1f 0 0 1 0 %.1f A %.2f %.1f 0 0 %d 0 %.1f Z"/>'
           % (-r, r, r, r, rx, r, sweep, -r))
    flip = "" if waxing else ' transform="scale(-1,1)"'
    return ('<svg class="moondisc" viewBox="-30 -30 60 60" width="%d" height="%d" '
            'role="img" aria-label="月相示意，照度 %.0f%%">'
            '<circle r="%.1f" class="moon-dark"/>'
            '<g class="moon-lit"%s>%s</g>'
            '<circle r="%.1f" class="moon-edge"/></svg>'
            % (size, size, k * 100, r, flip, lit, r))


def _moon_illum(s):
    """从月相文本或 illumination_percent 取照度；返回 (百分比, 是否上弦趋向)。"""
    pct = _num(s.get("illumination_percent"))
    text = str(s.get("moon_phase") or "")
    if pct is None:
        m = re.search(r"(\d{1,3})\s*%", text)
        pct = float(m.group(1)) if m else None
    if pct is None:
        return None, True
    waxing = not any(k in text for k in ("亏", "下弦", "残月"))
    if any(k in text for k in ("新", "娥眉月", "上弦", "盈")):
        waxing = True
    return pct, waxing


def star_section(s):
    """观星专项的正文块：月亮相位盘 + 示意星图 + 目标清单 + 外链。"""
    out = []
    cfg = s.get("chart") or {}
    svg = star_map_svg(cfg) if cfg else ""
    pct, waxing = _moon_illum(s)

    # 右列：月相盘 + 今晚看什么。合成一列塞在星图右边，
    # 免得星图居中后两边各空一大片（原来月相盘只占 140px，撑不满）。
    right = []
    if pct is not None:
        right.append('<figure class="skymoon">%s<figcaption>月面照度 <b>%.0f%%</b><br>'
                     '<span>%s</span></figcaption></figure>'
                     % (moon_disc_svg(pct, waxing), pct,
                        _e("亏凸月（亮面在左）" if not waxing else "亮面在右")))
    targets = s.get("targets") or []
    if targets:
        chips = []
        for t in targets:
            if isinstance(t, dict):
                chips.append('<span class="tg"><b>%s</b>%s%s</span>' % (
                    _e(t.get("name")),
                    '<em>%s</em>' % _e(t.get("type")) if t.get("type") else "",
                    '<span class="tg-nt">%s</span>' % _e(t.get("note") or t.get("how")) if (t.get("note") or t.get("how")) else ""))
            else:
                chips.append('<span class="tg"><b>%s</b></span>' % _e(t))
        right.append('<div class="tgs"><span class="tgs-t">今晚看什么</span>'
                     + "".join(chips) + "</div>")

    left = ""
    if svg:
        cap = ("按 %s（%.2f°N %.2f°E）%s 当地时刻计算，只画地平线以上的亮星；"
               "未做大气折射与光污染修正，权当认星提纲，别当精密星历。"
               % (_e(cfg.get("place") or "观测点"),
                  _num(cfg.get("lat")) or 0.0, _num(cfg.get("lng")) or 0.0,
                  _e(cfg.get("at") or "")))
        left = ('<figure class="skywrap">' + svg
                + '<figcaption class="skycap">' + cap + "</figcaption></figure>")

    if left or right:
        out.append('<div class="skyrow">' + left
                   + ('<div class="skycol">' + "".join(right) + "</div>"
                      if right else "")
                   + "</div>")

    links = s.get("links") or {}
    links.setdefault("在线星图（Stellarium）", "https://stellarium-web.org/")
    links.setdefault("光污染地图", "https://www.lightpollutionmap.info/")
    out.append('<div class="sp-links">' + "".join(
        '<a class="sp-link" href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
        % (_e(u), _e(t)) for t, u in links.items()) + "</div>")
    return "".join(out)


# ---------------------------------------------------------------- 天光条 + 霞光


def _sky_color_at(mins, sr, ss):
    """给一条横向天色渐变挑停靠色：两端夜色、日出日落处金橙、白昼淡蓝。"""
    if sr is None and ss is None:
        return "#dfeaf6"
    if sr is not None and mins < sr:
        return "#2b3a67" if mins < sr - 70 else "#5d63a8"
    if ss is not None and mins > ss:
        return "#2b3a67" if mins > ss + 70 else "#5d63a8"
    return "#cfeaf7"


def sun_band_svg(r):
    """日出日落天光条：一条横轴把蓝调、黄金时刻、白昼、日落串起来。"""
    sr = _hm(r.get("sunrise"))
    ss = _hm(r.get("sunset"))
    blue = _first_range(r.get("blue_hour"))
    gold = _first_range(r.get("golden_hour"))
    anchors = [x for x in (sr, ss, blue and blue[0], blue and blue[1],
                           gold and gold[0], gold and gold[1]) if x is not None]
    if not anchors:
        return ""
    lo = (min(anchors) - 50) // 60 * 60
    hi = ((max(anchors) + 50) + 59) // 60 * 60
    hi = max(hi, lo + 120)
    span = float(hi - lo)

    def fx(m):
        return 34 + (m - lo) / span * (640 - 34 - 12)

    W, bar_y, bar_h = 640, 34, 44
    stops = [(0.0, _sky_color_at(lo, sr, ss))]
    for m in range(int(lo), int(hi) + 1, 15):
        stops.append(((m - lo) / span, _sky_color_at(m, sr, ss)))
    stops.append((1.0, _sky_color_at(hi, sr, ss)))
    grad = "".join('<stop offset="%.4f" stop-color="%s"/>' % (o, c) for o, c in stops)

    bands = []
    if blue:
        bands.append((blue, "蓝调", "b-blue"))
    if gold:
        bands.append((gold, "黄金", "b-gold"))
    if ss is not None:
        bands.append(((ss - 60, ss), "黄金", "b-gold"))
        bands.append(((ss, ss + 25), "蓝调", "b-blue"))
    band_html = []
    for (a, b), label, cls in bands:
        if b <= lo or a >= hi:
            continue
        a, b = max(a, lo), min(b, hi)
        w = fx(b) - fx(a)
        if w <= 0:
            continue
        band_html.append('<rect class="%s" x="%.1f" y="%d" width="%.1f" height="%d" rx="4"/>'
                         % (cls, fx(a), bar_y, w, bar_h))
        if w >= 34:
            band_html.append('<text class="bandlbl" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                             % (fx(a) + w / 2, bar_y - 6, _e(label)))
    ticks = []
    for m, label, cls in ((sr, "日出", "t-rise"), (ss, "日落", "t-set")):
        if m is None:
            continue
        x = fx(m)
        ticks.append('<path class="%s" d="M%.1f %d V%d"/>' % (cls, x, bar_y - 3, bar_y + bar_h + 3))
        ticks.append('<text class="ticklbl" x="%.1f" y="%d" text-anchor="middle">%s %s</text>'
                     % (x, bar_y + bar_h + 17, _e(label), _hm_txt(m)))
    axis = []
    step = 120 if span > 480 else 60
    m = int(lo + (step - lo % step) % step)
    while m <= hi:
        axis.append('<text class="axlbl" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                    % (fx(m), bar_y + bar_h + 34, _hm_txt(m)))
        m += step
    return ('<svg class="sunband-svg" viewBox="0 0 %d 118" role="img" '
            'aria-label="日出日落天光时间条">'
            '<defs><linearGradient id="to-sky" x1="0" y1="0" x2="1" y2="0">%s</linearGradient></defs>'
            '<rect x="34" y="%d" width="%d" height="%d" rx="10" fill="url(#to-sky)"/>%s%s%s</svg>'
            % (W, grad, bar_y, 640 - 34 - 12, bar_h, "".join(band_html), "".join(ticks),
               "".join(axis)))


def glow_meter(gl):
    """霞光（火烧云）指数：0-3 级点阵 + 判据 + 输入数据。"""
    if not gl:
        return ""
    score = _num(gl.get("score"))
    level = gl.get("level") or ""
    reasons = _as_list(gl.get("reasons")) or _as_list(gl.get("reason"))
    inputs = gl.get("inputs") or {}
    if score is None and not reasons:
        rating = gl.get("rating") or ""
        if not rating:
            return ""
        return ('<div class="glow"><div class="glow-head"><b>霞光指数</b>'
                '<span class="glow-lv">参考</span></div>'
                '<p class="glow-p">' + _e(rating) + "</p>"
                '<p class="glow-note">模型参考值，非官方预报，出发前结合实景判断。</p></div>')
    dots = ""
    if score is not None:
        n = int(max(0, min(3, round(score))))
        dots = "".join('<i class="%s"></i>' % ("on" if i < n else "") for i in range(3))
    in_labels = [("cloud_cover_low_pct", "低云"), ("cloud_cover_mid_pct", "中高云"),
                 ("cloud_cover_high_pct", "高云"), ("visibility_km", "能见度"),
                 ("relative_humidity_pct", "湿度"), ("aerosol_optical_depth", "气溶胶")]
    chips = []
    for k, label in in_labels:
        v = _num(inputs.get(k))
        if v is None:
            continue
        unit = {"visibility_km": " km", "aerosol_optical_depth": ""}.get(k, "%")
        chips.append('<span class="gd"><b>%s</b>%.0f%s</span>' % (_e(label), v, unit))
    return ('<div class="glow"><div class="glow-head"><b>霞光指数</b>'
            + ('<span class="glow-lv">' + _e(level) + "</span>" if level else "")
            + ('<span class="glow-dots">' + dots + "</span>" if dots else "")
            + ('<span class="glow-num">%d/3</span>' % int(round(score)) if score is not None else "")
            + "</div>"
            + ('<ul class="glow-why">' + "".join("<li>" + _e(x) + "</li>" for x in reasons[:4])
               + "</ul>" if reasons else "")
            + ('<div class="glow-data">' + "".join(chips) + "</div>" if chips else "")
            + '<p class="glow-note">按云量分层、通透度、水汽算出的参考值，不是官方预报；'
              "出门前扫一眼实景天色更靠谱。</p></div>")


def sun_section(r, glow=None):
    """日出日落专项正文：天光条 + 霞光数据（火烧云）合在一起看。"""
    out = []
    svg = sun_band_svg(r)
    if svg:
        legend = ('<div class="sblegend">'
                  '<span class="sb sb-blue">蓝调时刻</span>'
                  '<span class="sb sb-gold">黄金时刻</span>'
                  '<span class="sb sb-day">白昼</span>'
                  '<span class="sb sb-night">夜间</span></div>')
        cap = ("蓝调与黄金时刻取自接口；黄昏侧按同一套定义（日落前后）推算，未单独取数。"
               "条上窄到放不下字的时段看下方色块图例对位置。")
        out.append('<figure class="sunband">' + svg + legend
                   + '<figcaption class="skycap">' + cap + "</figcaption></figure>")
    if glow:
        out.append(glow_meter(glow))
    return "".join(out)


# ---------------------------------------------------------------- 潮汐点选图


def _tide_points(t):
    """把 curve / extreme 归一成一串 (分钟, 潮位, 类型) 并按时间排序。"""
    series = []
    for p in (t.get("curve") or []):
        if isinstance(p, dict):
            tm = _hm(p.get("t") or p.get("time"))
            h = _num(p.get("h") if p.get("h") is not None else p.get("height_m"))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            tm, h = _hm(p[0]), _num(p[1])
        else:
            continue
        if tm is None or h is None:
            continue
        series.append((tm, h, ""))
    for e in (t.get("extremes") or []):
        if isinstance(e, dict):
            tm = _hm(e.get("time"))
            h = _num(e.get("height_m") if e.get("height_m") is not None else e.get("h"))
            kind = str(e.get("type") or "")
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            tm, h, kind = _hm(e[0]), _num(e[1]), ""
        else:
            continue
        if tm is None or h is None:
            continue
        # 极值（满潮/干潮）几乎都不落在整点上（06:08 这种）。直接加一个点会和整点圆点叠住，
        # 所以把最近的那个小时点替换成极值本身：时刻、读数、类型都保住，图上也不挤。
        if series:
            near = min(range(len(series)), key=lambda i: abs(series[i][0] - tm))
            if abs(series[near][0] - tm) <= 25:
                series[near] = (tm, h, kind)
                continue
        series.append((tm, h, kind))
    series.sort(key=lambda x: x[0])
    return series


def _tide_advice(kind, trend, in_win, before_win):
    if kind == "干潮":
        return "干潮前后，滩涂裸露最广，正是下滩的时刻。"
    if kind == "满潮":
        return "满潮，水面最高，滩涂几乎全淹，这个时段别下滩。"
    if not in_win:
        if before_win:
            return "还没进窗口，潮水一般还在退，可以岸边准备装备。"
        return "已经出了推荐窗口，滩涂回水很快，岸边看海就好。"
    if trend is None:
        return "潮位基本持平。"
    if trend > 0.05:
        return "潮位在回涨，滩面开始回水，别再往深处走。"
    if trend < -0.05:
        return "潮水还在退，可以沿滩面向外走，注意脚下潮沟。"
    return "平潮阶段，水位变化很小。"


def tide_section(t, key="beachcombing"):
    """潮汐图：曲线 + 可点圆点 + 该时刻的潮位与安全建议。没有曲线数据就不渲染。"""
    pts = _tide_points(t)
    if len(pts) < 3:
        return ""
    win = _first_range(t.get("best_window"))
    unit = t.get("unit") or "m（MSL 基准）"
    lo = min(p[0] for p in pts)
    hi = max(p[0] for p in pts)
    x_lo = lo // 60 * 60
    x_hi = max(hi + 30, x_lo + 120)
    h_lo = min(p[1] for p in pts)
    h_hi = max(p[1] for p in pts)
    pad = max(0.15, (h_hi - h_lo) * 0.16)
    y_lo, y_hi = h_lo - pad, h_hi + pad

    W, H = 640, 214
    px0, px1, py0, py1 = 40, 630, 26, 168

    def fx(m):
        return px0 + (m - x_lo) / float(x_hi - x_lo) * (px1 - px0)

    def fy(h):
        return py1 - (h - y_lo) / (y_hi - y_lo) * (py1 - py0)

    # 逐时点抽稀到 32 个以内 —— CSS 里给点选预留的兄弟选择器就是这个上限
    if len(pts) > 32:
        step = int(math.ceil(len(pts) / 32.0))
        keep = pts[::step]
        if pts[-1] not in keep:
            keep.append(pts[-1])
        pts = keep

    line = " ".join("%.1f,%.1f" % (fx(m), fy(h)) for m, h, _ in pts)
    area = ("M%.1f %.1f L" % (fx(pts[0][0]), py1) + line.replace(" ", " L")
            + " L%.1f %.1f Z" % (fx(pts[-1][0]), py1))

    grid = []
    for frac in (0.0, 0.5, 1.0):
        h = y_lo + (y_hi - y_lo) * frac
        y = fy(h)
        grid.append('<path class="tgrid" d="M%d %.1f H%d"/>' % (px0, y, px1))
        grid.append('<text class="taxlbl" x="%d" y="%.1f" text-anchor="end">%.2f</text>'
                    % (px0 - 6, y + 3.6, h))
    for m in range(int(x_lo), int(x_hi) + 1, 120):
        grid.append('<text class="taxlbl" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                    % (fx(m), py1 + 22, _hm_txt(m)))

    band = ""
    if win:
        a, b = max(win[0], x_lo), min(win[1], x_hi)
        if b > a:
            band = ('<rect class="twindow" x="%.1f" y="%d" width="%.1f" height="%d" rx="8"/>'
                    '<text class="twlbl" x="%.1f" y="%d" text-anchor="middle">赶海窗口</text>'
                    % (fx(a), py0, fx(b) - fx(a), py1 - py0,
                       (fx(a) + fx(b)) / 2, py0 + 15))

    radios, marks, panels = [], [], []
    for i, (m, h, kind) in enumerate(pts):
        rid = "td-%s-%d" % (key, i)
        prev = pts[i - 1][1] if i else None
        trend = (h - prev) if prev is not None else None
        in_win = bool(win and win[0] <= m <= win[1])
        before = bool(win and m < win[0])
        checked = ""
        if kind == "干潮":
            checked = " checked"
        radios.append('<input class="tp" type="radio" name="td-%s" id="%s"%s>'
                      % (key, rid, checked))
        marks.append('<label class="tpdot" for="%s" style="left:%.2f%%;top:%.2f%%" '
                     'title="%s %s"> <i></i></label>'
                     % (rid, fx(m) / W * 100, fy(h) / H * 100, _hm_txt(m), kind or "潮位"))
        panels.append(
            '<div class="tp-panel"><span class="tp-t">%s</span>'
            '<b class="tp-h">%+.2f m</b>%s'
            '<span class="tp-tr">%s</span><p>%s</p></div>'
            % (_hm_txt(m), h,
               '<em class="tp-kind">%s</em>' % _e(kind) if kind else "",
               "回涨" if (trend or 0) > 0.05 else ("退潮" if (trend or 0) < -0.05 else "平潮"),
               _e(_tide_advice(kind, trend, in_win, before))))

    hint = "点曲线上的圆点，看那一刻的潮位和能不能下滩。"
    caption = ("逐时潮位来自 %s；曲线只画接口真实给到的时段，没数据的时段不补线。"
               % _e(t.get("source_id") or "潮汐接口"))
    return ('<div class="tide">' + "".join(radios)
            + '<div class="tide-plot"><svg class="tide-svg" viewBox="0 0 %d %d" role="img" '
              'aria-label="逐时潮位曲线，含赶海窗口">'
              '<path class="tarea" d="%s"/>%s<path class="tline" d="M%s"/>%s</svg>'
              '<div class="tp-marks">%s</div></div>'
              '<div class="tp-panels">%s</div>'
              '<p class="tide-hint">%s</p>'
              '<p class="skycap">%s</p></div>'
            % (W, H, area, band, line.replace(" ", " L"),
               "".join(grid), "".join(marks), "".join(panels), hint, caption))
