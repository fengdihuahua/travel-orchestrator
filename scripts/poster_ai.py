# -*- coding: utf-8 -*-
"""AI 一层绘制海报：行程数据 → ① 骨架草图 PNG ② 生图提示词 TXT。

设计原则：**通用、零人工微调**。所有东西都由数据推出——
  · 点位分布：分簇（同片地方保持真实相对几何、只等比放大）+ 画布适配椭圆环 +
              两两方位硬约束（每对点的东南西北必须与真实一致）
  · 角度容差：由「真实角间隔向均匀值平滑」解出，不写死任何角度
  · 小景选题：地点名关键词 → 场景描述（通用规则，兜底一句话）
  · 地形方位：海/湖/山的方位由「含水关键词的地点」的真实方位推出
  · 文字内容：编号 / 地名 / 时段 / 详情，全部取自 items
骨架里**不写任何文字**，文字与画法说明全部进 prompt。

用法：
    python scripts/poster_ai.py examples/weihai-2d --day 1 --out _tmp/ai
"""
from __future__ import annotations
import os
import re
import sys
import math
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_plan as rp                      # noqa: E402
import nature_widgets as nw                   # noqa: E402

VERSION = "1.7.5"

W, H = 1024, 1536
PAPER = (248, 243, 230)
INK = (108, 99, 86)
GREY = (150, 142, 128)
SEA = (226, 234, 239)
SAND = (238, 230, 212)
CIN = (176, 58, 40)
BLUE = (28, 100, 220)
REDD = (188, 62, 44)
FONT_CJK = "C:/Windows/Fonts/simhei.ttf"

# 一张图上最多画几个点。点一多，文字框互相挤、字越写越小，模型也画不准；
# 超了就按真位最近的一对合并（见 `_reduce_points`）。`--max-points 0` 可关掉上限。
MAX_POINTS = 8

# 含水关键词（海侧 / 湖侧）
SEA_KW = ["海", "湾", "滩", "港", "岛", "屿", "岸", "礁", "沙", "码头"]
LAKE_KW = ["湖", "池", "潭", "泉", "溪", "河", "江"]
# 专名里带「河 / 沙」但不该当水体画的（景区、保护区、博物馆名）
_NOT_WATER = ("黄河口", "黄河故道", "黄河三角洲", "三角洲", "景区", "旅游区",
              "保护区", "博物馆", "公园")
# **以这些后缀结尾的名字是「地方」，不是水体** —— 名字里带「岛/海/滩」也照样是陆地。
# 实测踩过：`河口区孤岛镇`（油田生活区）被「岛」命中、`荣成环海路`（沿海公路）被「海」命中，
# 都当成了水面，把海侧方位算歪。
_PLACE_SUFFIX = ("镇", "村", "乡", "街道", "社区", "市区", "城区", "新区",
                 "路", "街", "大道", "巷", "站", "机场", "码头边")
# 地点名 → 小景（通用规则，顺序即优先级）
SCENE_RULES = [
    (["滩", "潮", "涂"], "一片退潮的滩涂，滩面几道水痕，两三个弯腰赶海的小人影"),
    (["黄河口", "入海口"], "河水汇进大海，一条清浊分明的界线"),
    (["海堤", "堤坝"], "伸进海里的长堤，堤上一排采油树"),
    (["天鹅", "湿地", "保护区"], "湖面上一只曲颈天鹅，岸边几丛芦苇"),
    (["岛", "屿", "礁"], "海上一座小岛，礁石与几只飞鸟"),
    (["港", "码头", "渔船", "渔"], "港湾里停着三五只小渔船，桅杆错落"),
    (["海滩", "海滨", "浴场", "沙滩"], "一段浅滩海岸，几道浪线与远处小帆船"),
    (["湖", "池", "潭"], "一片平静湖面，岸边垂柳与小船"),
    (["樱花", "桃", "杏", "花海", "花田"], "一株开花的树，花瓣飘落水面"),
    (["寺", "庙", "塔", "庵", "宫"], "山间一座飞檐塔寺，松林环绕"),
    (["古城", "古镇", "老街", "村"], "几间灰瓦石墙老屋与一条石板小街"),
    (["山", "峰", "岭", "崖", "峡"], "层叠的远山，山腰几缕云雾"),
    (["林", "森", "竹", "园"], "一小片树林与山路"),
    (["温泉", "汤"], "山脚一处温泉，热气缭绕"),
    (["观星", "暗点", "星空"], "夜色海面上一弯月与几点星"),
    (["市区", "城区", "市中心", "广场", "街"], "两三间灰瓦民居与一条小街"),
]
SCENE_FALLBACK = "该处的一角小景：一两个当地特色的物象，笔触简略"


def _short(name: str, n: int = 10) -> str:
    return name if len(name) <= n else name[:n]


def scene_of(name: str) -> str:
    for kws, scene in SCENE_RULES:
        if any(k in name for k in kws):
            return scene
    return SCENE_FALLBACK


def _is_place(name: str) -> bool:
    """名字是「地方」而不是水体 —— 以 镇/村/路/站… 结尾的都算地方。

    「孤岛镇」「威海站」「环海路」里都有「岛/海」，但它们是油田生活区、火车站、沿海公路。
    """
    return any(name.endswith(sfx) for sfx in _PLACE_SUFFIX)


def is_water(name: str) -> bool:
    if any(k in name for k in _NOT_WATER) or _is_place(name):
        return False
    return any(k in name for k in SEA_KW)


def is_lake(name: str) -> bool:
    if any(k in name for k in _NOT_WATER) or _is_place(name):
        return False
    return any(k in name for k in LAKE_KW) and not any(k in name for k in SEA_KW)


# ── 数据抽取 ────────────────────────────────────────────────
_TOP_WORDS = ("鸟浪", "赶海", "日出", "日落", "夕阳", "星空", "观星", "游船",
              "黄蓝", "河海", "红地毯", "红滩", "观鸟", "天鹅", "丹顶鹤", "芦苇")


def _pick_rows(items, ks, nm, maxn=2):
    """一个地点最多留两行 —— 海报是导览，不是把行程流水抄一遍。

    **画面里不出现任何序号**：一个地点就把它的地名、时段、一句详情写下来，
    去了两次就写成两行，读者按地名对号，不靠编号。
    排序：含「鸟浪／赶海／日出／游船…」这类体验词的优先，其次是 nature/visit，
    再次是其他，同级按时间；纯赶路与休整排最后。
    """
    cand = []
    for k in ks:
        it = items[k - 1]
        ti = str(it.get("title") or "").split("（")[0].strip()
        kind = str(it.get("kind") or "")
        hot = 2 if any(w in ti for w in _TOP_WORDS) else (1 if kind in ("nature", "visit") else 0)
        weak = 0 if kind in ("transport", "rest", "checkin") else 1   # 纯赶路/休整排最后
        d = ti or _short(str(it.get("notes") or ""), 12)
        if nm and d.startswith(nm):                 # 详情里不再重复地名
            d = d[len(nm):].strip(" ·，,、")
        cand.append((hot, weak, k, {"idx": k, "time": str(it.get("time") or "").strip(),
                                    "detail": d or ti}))
    if any(c[0] > 0 for c in cand):
        cand = [c for c in cand if c[0] > 0]
    cand.sort(key=lambda c: (-c[0], -c[1], c[2]))
    return [c[3] for c in sorted(cand[:maxn], key=lambda c: c[2])]


def _geo_key(it):
    """这个 item 的坐标键（同坐标 = 同一个地点）；取不到或坏值返回 None。"""
    geo = it.get("geo") if isinstance(it.get("geo"), dict) else {}
    lat, lng = geo.get("lat"), geo.get("lng")
    if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float))):
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    return (round(float(lng), 5), round(float(lat), 5))


def _rep_name(members):
    """同一坐标的一组地名里挑**代表名**。

    优先「是其它所有名字前缀」的那个 —— 它就是这组里最通用的写法
    （「东营市区」是「东营市区东城」的前缀，「黄河口生态旅游区」是「…南门」的前缀）；
    没有前缀关系就取最短的（「东八路北端滩涂」优于「东八路湿地观景台」），
    再不成取先出现的。
    """
    ms = [m for m in (members or []) if m]
    if not ms:
        return ""
    for a in ms:
        if all(b.startswith(a) for b in ms):
            return a
    return sorted(ms, key=lambda s: (len(s), ms.index(s)))[0]


def _groups(items):
    """按坐标把 item 归组 → [(代表名, [序号...], {原名: 代表名}), ...]，保持出现顺序。

    **同一个地点在行程里写成不同名字是常态**（「东营市区」/「东营市区东城」、
    「黄河口生态旅游区南门」/「远望楼码头」），而它们的坐标是同一个。这里把它们并成
    一组；不并的话，`nw.project_route` 那道去重叠会把同坐标的两个名字推开成两个点，
    各画一个印、各占一个文字框（实测就是这个原因让编号糊成一团）。
    """
    raw, seat = [], {}
    for k, it in enumerate(items or [], 1):
        pl = str(it.get("place") or "").strip()
        if not pl:
            continue
        key = _geo_key(it)
        if key is not None and key in seat:
            g = seat[key]
            g[1].append(k)
            g[2].append(pl)
            continue
        g = [pl, [k], [pl]]
        if key is not None:
            seat[key] = g
        raw.append(g)
    return [(rep, ks, {m: rep for m in members})
            for pl, ks, members in raw
            for rep in (_rep_name(members),)]


def canon_names(items):
    """原始地名 → 归并后的规范地名。

    点位的归并做在 `collect` 里；题签、亮点句按地名取词，也得吃同一张表，
    否则同一个地点会在题签里以两个名字出现（「东八路湿地观景台」+「东八路北端滩涂」）。
    """
    m = {}
    for rep, _ks, alias in _groups(items):
        m.update(alias)
    return m


def collect(trip: dict, day: int = 1):
    """返回 (items, names, seqs_of, rows_of) —— 全部来自数据，不手工。

    同坐标的地名并成一个点、文字并到一起（见 `_groups`）；
    每个点最多两行文字，**不编号**（见 `_pick_rows`）。
    """
    days = trip["itinerary"]["days"]
    d = days[day - 1]
    items = d.get("items") or []
    order, seqs = [], {}
    for rep, ks, _alias in _groups(items):
        if rep not in seqs:
            order.append(rep)
            seqs[rep] = []
        seqs[rep].extend(ks)
    rows_of = {nm: _pick_rows(items, seqs[nm], nm) for nm in order}
    return d, items, order, seqs, rows_of


def _merge_rows(ra, rb, maxn=2):
    """两处文字并到一处：重要的在前，**仍最多两行**。"""
    out = list(ra)
    for r in rb:
        if len(out) >= maxn:
            break
        out.append(r)
    return out[:maxn]


def _importance(nm, seqs, rows_of):
    """一个点位的重要度：带体验词的条目数（鸟浪/赶海/日出…），其次是在当天出现的次数。"""
    rows = rows_of.get(nm) or []
    hot = sum(1 for r in rows if any(w in r["detail"] for w in _TOP_WORDS))
    return (hot, len(seqs.get(nm) or []))


def _reduce_points(keep, TRUE, seqs, rows_of, maxn=MAX_POINTS):
    """点位超过 maxn 就合并 —— 一张图上放不下太多点。

    点一多，文字框互相挤、字越写越小，模型也画不准。做法：每次挑**真位最近的一对**并成一个点
    （最近的一对本来就常是同一片地方，图上本来就分不开），名字留更重要的那个、文字并到一处
    （仍最多两行），坐标取两个成员的中点 —— 仍在真位上，方位自检照旧。
    返回 `(keep, TRUE, merged)`，`merged` 记下谁并进了谁，便于交代。`maxn<=0` 表示不设上限。
    """
    keep, TRUE = list(keep), list(TRUE)
    merged = {}
    while maxn and maxn > 0 and len(keep) > maxn:
        best = None
        for i in range(len(keep)):
            for j in range(i + 1, len(keep)):
                d = math.hypot(TRUE[i][0] - TRUE[j][0], TRUE[i][1] - TRUE[j][1])
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None:
            break
        _d, i, j = best
        a, b = keep[i], keep[j]
        if _importance(b, seqs, rows_of) > _importance(a, seqs, rows_of):
            i, j, a, b = j, i, b, a          # 名字留更重要的那个
        keep.pop(j)
        TRUE[i] = ((TRUE[i][0] + TRUE[j][0]) / 2, (TRUE[i][1] + TRUE[j][1]) / 2)
        TRUE.pop(j)
        seqs[a] = sorted(set(seqs.get(a) or []) | set(seqs.get(b) or []))
        rows_of[a] = _merge_rows(rows_of.get(a) or [], rows_of.get(b) or [])
        seqs.pop(b, None)
        rows_of.pop(b, None)
        merged.setdefault(a, []).append(b)
    return keep, TRUE, merged


def coords_of(trip: dict, names, day: int = 1):
    rows, _ = nw.route_stops(trip["itinerary"], detail=True)
    ll = {}
    for dd, (_did, stops) in enumerate(rows, 1):
        if dd != day:
            continue
        for s in stops:
            if s[2] is not None and s[3] is not None:
                ll[s[0]] = (s[3], s[2])          # (lng, lat)
    keep = [n for n in names if n in ll]
    norm = nw.project_route([(ll[n][0], ll[n][1], n) for n in keep])
    xs = [q[0] for q in norm]
    ys = [q[1] for q in norm]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    BX0, BY0, BX1, BY1 = 0.09, 0.11, 0.93, 0.86
    TRUE = [((BX0 + (q[0] - x0) / bw * (BX1 - BX0)) * W,
             (BY0 + (q[1] - y0) / bh * (BY1 - BY0)) * H) for q in norm]
    return keep, TRUE


# ── 点位排布（通用）────────────────────────────────────────
def arrange(TRUE, names):
    n = len(TRUE)
    CCX = sum(t[0] for t in TRUE) / n
    CCY = sum(t[1] for t in TRUE) / n
    # ① 分簇：真位很近的点并为一簇（同一片地方）
    THR = 0.085 * math.hypot(W, H)
    par = list(range(n))

    def _f(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a
    for a in range(n):
        for b in range(a + 1, n):
            if math.hypot(TRUE[a][0] - TRUE[b][0], TRUE[a][1] - TRUE[b][1]) < THR:
                ra, rb = _f(a), _f(b)
                if ra != rb:
                    par[rb] = ra
    G = {}
    for a in range(n):
        G.setdefault(_f(a), []).append(a)
    NODES = []
    for _r, mem in sorted(G.items()):
        cx = sum(TRUE[a][0] for a in mem) / len(mem)
        cy = sum(TRUE[a][1] for a in mem) / len(mem)
        off = [(TRUE[a][0] - cx, TRUE[a][1] - cy) for a in mem]
        mi = max([math.hypot(off[a][0] - off[b][0], off[a][1] - off[b][1])
                  for a in range(len(mem)) for b in range(a + 1, len(mem))] or [0])
        k = 1.0 if mi < 1 else min(3.0, 380.0 / mi)     # 放大倍数由簇内间距定
        Rc = max([math.hypot(o[0], o[1]) for o in off] or [0]) * k
        NODES.append({"mem": mem, "off": off, "k": k, "Rc": Rc,
                      "th": math.degrees(math.atan2(cy - CCY, cx - CCX)) % 360.0})
    # ② 节点铺开：角度 = 真实方位；半径 = 画布适配椭圆 − 节点半径
    X0, X1, Y0, Y1 = 140, W - 140, 204, 1302
    TIT = (0, 0, 620, 368)
    EX, EY = (X1 - X0) / 2.0, (Y1 - Y0) / 2.0
    ECX, ECY = (X0 + X1) / 2.0, (Y0 + Y1) / 2.0
    Q = []
    for nd in NODES:
        a = math.radians(nd["th"])
        rx = max(EX - nd["Rc"], 60.0) * 0.93
        ry = max(EY - nd["Rc"], 60.0) * 0.93
        Q.append([ECX + math.cos(a) * rx, ECY + math.sin(a) * ry])
    for _ in range(400):                               # 避开左上题签区
        fixed = True
        for q, nd in enumerate(NODES):
            if (Q[q][0] - nd["Rc"]) < TIT[2] and (Q[q][1] - nd["Rc"]) < TIT[3]:
                Q[q][1] = TIT[3] + nd["Rc"] + 8
                fixed = False
        if fixed:
            break
    for q, nd in enumerate(NODES):
        Q[q][0] = min(max(Q[q][0], X0 + nd["Rc"]), X1 - nd["Rc"])
        Q[q][1] = min(max(Q[q][1], Y0 + nd["Rc"]), Y1 - nd["Rc"])
    # ③ 展开成成员坐标
    P = [None] * n
    for q, nd in enumerate(NODES):
        for t, a in enumerate(nd["mem"]):
            P[a] = [Q[q][0] + nd["off"][t][0] * nd["k"], Q[q][1] + nd["off"][t][1] * nd["k"]]
    P = [[min(max(x, X0 + 34), X1 - 34), min(max(y, Y0 + 34), Y1 - 34)] for (x, y) in P]
    # ④ 两两方位硬约束：真实差距显著的轴上，东南西北符号必须一致
    for _ in range(600):
        fixed = True
        for a in range(n):
            for b in range(a + 1, n):
                for ax in (0, 1):
                    td = TRUE[b][ax] - TRUE[a][ax]
                    if abs(td) < 8.0:
                        continue
                    if td * (P[b][ax] - P[a][ax]) <= 0:
                        sg = 1.0 if td > 0 else -1.0
                        P[a][ax] -= sg * 7.0
                        P[b][ax] += sg * 7.0
                        fixed = False
        P = [[min(max(x, X0 + 34), X1 - 34), min(max(y, Y0 + 34), Y1 - 34)]
             for (x, y) in P]
        if fixed:
            break
    diag = {"clusters": [sorted(nd["mem"]) for nd in NODES],
            "kx": [round(nd["k"], 2) for nd in NODES]}
    return [(x, y) for (x, y) in P], diag


def check_compass(TRUE, P):
    """自检：真实差距显著的轴上，两两方位的东南西北符号是否全部一致。"""
    n = len(TRUE)
    tot = bad = 0
    for a in range(n):
        for b in range(a + 1, n):
            for ax in (0, 1):
                td = TRUE[b][ax] - TRUE[a][ax]
                if abs(td) < 2.0:
                    continue
                tot += 1
                if td * (P[b][ax] - P[a][ax]) <= 0:
                    bad += 1
    return tot, bad


# ── 骨架框位（纯几何，不碰 PIL）───────────────────────────
FW, FHT = 178, 130                                  # 小景位（蓝框）的宽高
SH = 56                                             # 地名位（朱红框）只写一行地名，比小景位矮
FW_MIN = 230                                        # 单元的最小宽度（够写下一行地名）
TITR = (24, 24, 566, 306)                           # 题签角


def name_font_size(text, box_w, measurer, hi=32, lo=13, pad=20):
    """地名框里放下这串字所需的最大字号。`measurer(text, size) -> 宽度`。

    抽成纯函数是为了能测：字太长就一路缩，缩到 `lo` 为止。
    """
    for size in range(hi, lo - 1, -1):
        if measurer(text, size) <= box_w - pad:
            return size
    return lo


def layout_units(names, P):
    """给每个地点挑一对框：**蓝框**画小景、**朱红框**写地名。返回 {地名: {...}}。

    🔴 框必须**紧贴自己的红点**：分圈放宽（300 → 460 → 650 → 1000 px），第一圈里能放下
    就一定贴着点放。这条是踩出来的 —— 早先只惩罚「离得远」，为了不和别的框打架，
    有的框被推到离自己的点很远的地方，模型就丢了「这个框属于哪个点」的对应关系，
    照着骨架画出来点位全错（实测 5 个点错了 3 个）。
    重合惩罚随重叠面积增长，所以挑出来的是「够近又不打架」的那个位置。
    """
    cand = [(gx, gy) for gx in range(60, W - 60, 44) for gy in range(60, H - 60, 44)]
    taken, units = [], {}
    for i, nm in enumerate(names):
        uw, uh = FW_MIN, FHT + 8 + SH
        x, y = P[i]
        box = None
        for cap in (300, 460, 650, 1000):
            best = None
            for gx, gy in cand:
                if math.hypot(gx - x, gy - y) > cap:
                    continue
                b = (gx - uw / 2, gy - uh / 2, gx + uw / 2, gy + uh / 2)
                pen = 0.0
                if b[0] < 10 or b[2] > W - 10 or b[1] < 10 or b[3] > H - 10:
                    pen += 8000
                if b[0] < TITR[2] and b[1] < TITR[3]:
                    pen += 8000
                hw, hh = uw / 2, uh / 2
                cc = (b[0] + hw, b[1] + hh)
                for d2 in P:
                    a1 = min(cc[0] + hw, d2[0] + 32) - max(cc[0] - hw, d2[0] - 32)
                    a2 = min(cc[1] + hh, d2[1] + 32) - max(cc[1] - hh, d2[1] - 32)
                    if a1 > 0 and a2 > 0:
                        pen += 1600.0
                for t in taken:
                    a1 = min(cc[0] + hw, t[2] + 14) - max(cc[0] - hw, t[0] - 14)
                    a2 = min(cc[1] + hh, t[3] + 14) - max(cc[1] - hh, t[1] - 14)
                    if a1 > 0 and a2 > 0:
                        pen += 900 + a1 * a2 / 700.0
                pen += math.hypot(gx - x, gy - y) * 0.5
                if best is None or pen < best:
                    best, box = pen, b
            if box is not None:
                break
        fr = (box[0], box[1], box[0] + FW, box[1] + FHT)
        st = (box[0], box[1] + FHT + 8, box[0] + uw, box[1] + FHT + 8 + SH)
        units[nm] = {"frame": [int(v) for v in fr], "strip": [int(v) for v in st],
                     "unit": [int(v) for v in box], "dot": i,
                     "offset": int(math.hypot(box[0] + uw / 2 - x, box[1] + uh / 2 - y))}
        taken.append(box)
    return units


BLK_W, BLK_H = 520, 342          # 行程文字块的估算尺寸（合成时按实际字号再对齐）


def _rect_gap(b, o):
    """矩形 b 到矩形 o 的间距；相交时返回负的重叠深度。"""
    ox = min(b[2], o[2]) - max(b[0], o[0])
    oy = min(b[3], o[3]) - max(b[1], o[1])
    if ox > 0 and oy > 0:
        return -min(ox, oy)
    return math.hypot(max(o[0] - b[2], b[0] - o[2], 0),
                      max(o[1] - b[3], b[1] - o[3], 0))


def route_corridor(P, pad=30):
    """路线走廊：把相邻两点之间的连线采样成一串小方块，当作障碍。

    底图上的路线走向与「点与点的连线」大致一致，所以用折线走廊近似它 ——
    文字块压在路上最难看，宁可让它避开整条走廊。
    """
    out = []
    for (x1, y1), (x2, y2) in zip(P, P[1:]):
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 24))
        for i in range(n + 1):
            t = i / float(n)
            x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            out.append((x - pad, y - pad, x + pad, y + pad))
    return out


def text_block_spot(units, P, size=(BLK_W, BLK_H)):
    """给「集中写行程那一块」找地方：离所有点、框、题签、**路线**都最远的空处。

    块是**有尺寸的矩形**，所以按「矩形到矩形」的间距算，而不是按点到点 ——
    否则算出来的"最空点"看着很空，块一摊开就压到点、框或路线上了。
    """
    hw, hh = size[0] / 2.0, size[1] / 2.0
    obs = []
    for p in P:
        obs.append((p[0] - 34, p[1] - 34, p[0] + 34, p[1] + 34))
    for u in units.values():
        f, s = u["frame"], u["strip"]
        obs.append((min(f[0], s[0]) - 12, min(f[1], s[1]) - 12,
                    max(f[2], s[2]) + 12, max(f[3], s[3]) + 12))
    obs.append((0, 0, TITR[2] + 40, TITR[3] + 40))
    obs += route_corridor(P)
    best, spot = -1e9, (W / 2, H / 2)
    for gx in range(int(hw) + 20, W - int(hw) - 20, 24):
        for gy in range(int(hh) + 20, H - int(hh) - 20, 24):
            b = (gx - hw, gy - hh, gx + hw, gy + hh)
            d = min(_rect_gap(b, o) for o in obs)
            if d > best:
                best, spot = d, (gx, gy)
    return spot, best


# ── 画骨架 ────────────────────────────────────────────────
def draw_sketch(names, P, TRUE, path, scene=None, day=None, title=None, seqs_of=None):
    # PIL 只在这一步需要：排布、归并、方位自检、提示词都不依赖它，
    # 所以放进函数内导入 —— 测试套件里那些纯逻辑断言才不用装第三方库
    from PIL import Image, ImageDraw, ImageFont
    n = len(names)
    scene = scene or {nm: scene_of(nm) for nm in names}
    img = Image.new("RGB", (W, H), PAPER)
    dr = ImageDraw.Draw(img)
    F = lambda s: ImageFont.truetype(FONT_CJK, s)          # noqa: E731

    def dash(p0, p1, col, w=3, d=12, g=9):
        L = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) or 1
        ux, uy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
        t = 0.0
        while t < L:
            e = min(t + d, L)
            dr.line([p0[0] + ux * t, p0[1] + uy * t, p0[0] + ux * e, p0[1] + uy * e],
                    fill=col, width=w)
            t = e + g

    def dashbox(b, col, w=3):
        dash((b[0], b[1]), (b[2], b[1]), col, w)
        dash((b[2], b[1]), (b[2], b[3]), col, w)
        dash((b[2], b[3]), (b[0], b[3]), col, w)
        dash((b[0], b[3]), (b[0], b[1]), col, w)

    def cr(pts, m=14):                                      # Catmull-Rom 平滑
        P_ = [pts[0]] + list(pts) + [pts[-1]]
        out = []
        for i in range(1, len(P_) - 2):
            p0, p1, p2, p3 = P_[i - 1], P_[i], P_[i + 1], P_[i + 2]
            for k in range(m):
                t = k / m
                t2, t3 = t * t, t * t * t
                out.append((0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                                   + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                                   + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                            0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                                   + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                                   + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)))
        out.append(pts[-1])
        return out

    # 地形：海侧由「含水地点」的真实方位推出；山在其对侧；湖画在湖点上
    Cx = sum(t[0] for t in P) / n                      # 一律用「摆好后」的重心
    Cy = sum(t[1] for t in P) / n
    sea_idx = [i for i, nm in enumerate(names) if is_water(nm)]
    if sea_idx:
        vx = sum(math.cos(math.atan2(P[i][1] - Cy, P[i][0] - Cx)) for i in sea_idx)
        vy = sum(math.sin(math.atan2(P[i][1] - Cy, P[i][0] - Cx)) for i in sea_idx)
        sb = math.atan2(vy, vx)
    else:
        sb = math.radians(90)
    ux, uy = math.cos(sb), math.sin(sb)                # 指向海
    nx, ny = -uy, ux                                   # 沿海岸线的横向
    D = math.hypot(W, H)
    # 岸线离重心多远：默认 0.24D；有水点时就贴着「最靠内的那个水点」再往外挪一点，
    # 免得滩涂、海堤这些水点落到岸线的内陆侧 —— 那画出来就自相矛盾了
    d0 = 0.24 * D
    if sea_idx:
        d0 = min(d0, min((P[i][0] - Cx) * ux + (P[i][1] - Cy) * uy for i in sea_idx) - 0.03 * D)
        d0 = max(d0, 0.10 * D)
    # 岸线要**横穿整幅**。原来横向只有 2.4 px（`t / 4.0`），沙滩画成一个圆点、海面细成一条缝，
    # 整张骨架上根本看不见海
    half = 0.55 * D
    coast_raw = [(Cx + ux * d0 + nx * (t / 5.0 * half), Cy + uy * d0 + ny * (t / 5.0 * half))
                 for t in range(-5, 6)]
    coast_raw = [(x + nx * 26 * math.sin(i * 1.7), y + ny * 26 * math.sin(i * 1.7))
                 for i, (x, y) in enumerate(coast_raw)]
    cs = cr(coast_raw)
    dr.line([(x - ux * 38, y - uy * 38) for (x, y) in cs], fill=SAND, width=52, joint="curve")
    dr.line([(x - ux * 16, y - uy * 16) for (x, y) in cs], fill=(232, 222, 200), width=22,
            joint="curve")
    # 陆地、海面两块都以岸线为界，正好拼满整幅。原来陆地是拿一个四角都在画布外的矩形去
    # clamp，两个角塌到同一个画布角上，边界成了一条 45° 的假斜线、左上留一大片空白纸
    far_sea = [(Cx + ux * D * 3 + nx * D * 2, Cy + uy * D * 3 + ny * D * 2),
               (Cx + ux * D * 3 - nx * D * 2, Cy + uy * D * 3 - ny * D * 2)]
    far_land = [(Cx - ux * D * 3 + nx * D * 2, Cy - uy * D * 3 + ny * D * 2),
                (Cx - ux * D * 3 - nx * D * 2, Cy - uy * D * 3 - ny * D * 2)]
    dr.polygon(cs + far_land, fill=(241, 235, 221))      # 整片陆地：淡淡的暖灰
    dr.polygon(cs + far_sea, fill=SEA)                   # 海：淡花青
    for k, col in enumerate([(216, 230, 238), (206, 224, 233), (196, 216, 228),
                             (188, 210, 224)]):
        dr.line([(x + ux * k * 30, y + uy * k * 30) for (x, y) in cs], fill=col, width=2,
                joint="curve")
    dr.line(cs, fill=(219, 210, 191), width=3, joint="curve")   # 岸线
    dr.rectangle([0, 0, 660, 330], fill=PAPER)           # 左上题签角留白透气
    # 两侧都要标字：**海侧标「海」**，陆侧标「山」「林」。
    # 以前只标「山」「林」两个字，整幅图里没有「海」，模型就自己猜海画在哪 —— 所以画错。
    # 位置从一组候选里挑：必须落在画布内、避开题签角，并且**确实在岸线的对应一侧**。
    fmk = F(48)
    cands = [(120, 400), (W - 120, 400), (120, H - 150), (W - 120, H - 150),
             (120, H // 2), (W - 120, H // 2)]
    sea_marks, land_marks = [], []
    for px, py in cands:
        if 60 < px < 700 and 60 < py < 370:                  # 题签角：让开
            continue
        s = (px - Cx) * ux + (py - Cy) * uy
        if s > d0 + 70:
            sea_marks.append((s, px, py))
        elif s < d0 - 70:
            land_marks.append((s, px, py))
    for _s, px, py in sorted(sea_marks, reverse=True)[:1]:    # 最靠海的那个角
        dr.text((px - 34, py - 44), "海", font=fmk, fill=(150, 176, 186))
    for i, (_s, px, py) in enumerate(sorted(land_marks)[:2]):  # 最靠内的两个角
        dr.text((px - 34, py - 44), "山林"[i], font=fmk, fill=(197, 186, 165))

    for i, nm in enumerate(names):                            # 湖
        if is_lake(nm):
            x, y = P[i]
            dr.ellipse([x - 64, y - 38, x + 64, y + 38], fill=SEA,
                       outline=(186, 206, 216), width=4)

    def bez(p0, p1, bow, m=20):                               # 路线
        mxx, myy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy) or 1
        ccx, ccy = mxx - dy / L * bow, myy + dx / L * bow
        return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ccx + t ** 2 * p1[0],
                 (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ccy + t ** 2 * p1[1])
                for t in (k / m for k in range(m + 1))]
    for i, (a, b) in enumerate(zip(P, P[1:])):
        bow = (1 if i % 2 == 0 else -1) * 0.10 * math.hypot(b[0] - a[0], b[1] - a[1])
        dr.line([c for q in bez(a, b, bow) for c in q], fill=(160, 150, 134), width=4,
                joint="curve")

    # 每点一个单元：蓝框（画小景）+ 朱红框（**只写地名**）+ 朱砂小红点（不带数字）。
    # 时段与详情不在这里写 —— 整块集中写到画面空着的那一大块里（text_block_spot）。
    UNITS = layout_units(names, P)
    for nm in names:
        u = UNITS[nm]
        fr, st = u["frame"], u["strip"]
        dr.rectangle(st, fill=(247, 244, 236))
        dashbox(fr, BLUE, 3)
        dashbox((st[0] - 9, st[1] - 7, st[2] + 9, st[3] + 7), REDD, 3)
        dr.rectangle(st, outline=(206, 186, 178), width=2)
        # 地名**直接淡墨写在骨架的红框里**：模型照抄，不会把「东八路北端滩涂」写成
        # 「东八路北滩镇」这种串字（实测过）。字长按框宽自动缩。
        ft = F(name_font_size(nm, st[2] - st[0],
                              lambda t, s: dr.textlength(t, font=F(s))))
        tw = dr.textlength(nm, font=ft)
        dr.text((st[0] + (st[2] - st[0] - tw) / 2,
                 st[1] + (st[3] - st[1]) / 2 - ft.size * 0.66), nm, font=ft,
                fill=(152, 138, 118))
        x, y = P[u["dot"]]
        ex, ey = min(max(x, fr[0]), fr[2]), min(max(y, fr[1]), fr[3])
        dash((ex, ey), (x, y), BLUE, 2, 10, 8)
    # 题签区（左上）：朱红虚线框，内容由提示词给出
    dashbox((30, 30, TITR[2] - 34, TITR[3] - 34), REDD, 3)
    for nm in names:                                     # 一个地点一枚朱砂小红点（序号只写在文字里）
        x, y = P[UNITS[nm]["dot"]]
        dr.ellipse([x - 18, y - 18, x + 18, y + 18], fill=CIN)
    img.save(path)
    return UNITS, scene


# ── 生成提示词 ────────────────────────────────────────────
def side_cn(deg: float) -> str:
    d = (deg + 360) % 360
    for lo, hi, s in [(-22, 22, "右（东）"), (22, 68, "右下（东南）"), (68, 112, "下（南）"),
                      (112, 158, "左下（西南）"), (158, 202, "左（西）"),
                      (202, 248, "左上（西北）"), (248, 292, "上（北）"),
                      (292, 338, "右上（东北）"), (338, 360, "右（东）")]:
        if lo <= d < hi:
            return s
    return "右"


def where_cn(P, i):
    xs = [q[0] for q in P]
    ys = [q[1] for q in P]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    a = math.degrees(math.atan2(P[i][1] - cy, P[i][0] - cx))
    ry = (P[i][1] - min(ys)) / max(max(ys) - min(ys), 1.0)
    v = "偏上" if ry < 0.34 else ("偏下" if ry > 0.66 else "居中")
    return "%s，%s" % (side_cn(a), v)


def _clip(t, n):
    """按标点截断，别把「建议 11:00 前到」切成「建议 11」。"""
    t = str(t or "").strip()
    if len(t) <= n:
        return t
    head = t[:n]
    for sep in ("，", "、", "；", "。", "（", "(", " "):
        k = head.rfind(sep)
        if k > 4:
            return head[:k]
    return head


_SKIP = ("早餐", "午餐", "晚餐", "夜宵", "下午茶", "休整", "补觉", "回住地", "入住",
         "退房", "返程", "出发", "出门", "开车", "驱车", "自驾", "返回", "回酒店",
         "早睡", "短睡", "上车", "下车", "赶路", "路上", "集合", "交通", "整理",
         "休息", "收拾", "前往", "抵达", "高铁", "机场", "车站", "送站", "接站")


def _hm(t):
    """从任意串里取第一个 HH:MM。"""
    m = re.search(r"\d{1,2}:\d{2}", str(t or ""))
    return m.group(0) if m else ""


def _clean_title(t):
    """把条目标题洗成亮点名：去括号里的话、去时间、去加号。"""
    t = re.sub(r"[（(][^）)]*[）)]", "", str(t or ""))
    t = re.sub(r"\d{1,2}:\d{2}(\s*-\s*\d{1,2}:\d{2})?", "", t)
    t = re.sub(r"\s*\+\s*", " · ", t)
    return t.strip(" ·，,。").strip()


_WHEN = ((6, "天亮前"), (9, "一早"), (12, "上午"), (14, "中午"),
         (17, "下午"), (19, "傍晚"), (24, "夜里"))

_TALK = (
    (("赶海", "滩涂", "潮"), "{w}去{p}赶海，退潮后最好走"),
    (("观鸟", "赏鸟", "水鸟", "候鸟"), "{w}在{p}看鸟，水鸟还在滩上"),
    (("日出",), "{w}到{p}等日出"),
    (("日落", "夕阳"), "{w}到{p}散步，等一场日落"),
    (("观星", "星空", "银河"), "{w}去{p}看月面与亮星团"),
    (("黄蓝交汇", "河海交汇", "分界线"), "{w}坐船去看河海交汇"),
    (("红地毯", "红滩", "碱蓬"), "{w}去看滩上那片红"),
    (("晚霞", "朝霞", "火烧云"), "{w}在{p}等一场晚霞"),
)


def _when_word(t):
    """把开始时刻说成「天亮前 / 上午 / 傍晚」这种话，不出数字。"""
    m = re.match(r"\s*(\d{1,2}):", str(t or ""))
    if not m:
        return "今天"
    h = int(m.group(1))
    for lim, word in _WHEN:
        if h < lim:
            return word
    return "夜里"


def _area_of(places):
    """这批地点共同的前缀（多半是区县名，如「荣成」）。过半地点都有才认。"""
    heads = {}
    for nm in places:
        nm = str(nm or "").strip()
        if len(nm) >= 4:
            heads[nm[:2]] = heads.get(nm[:2], 0) + 1
    if not heads:
        return ""
    head, cnt = max(heads.items(), key=lambda kv: kv[1])
    return head if cnt * 2 >= len(places) else ""


def _short_place(nm, dst, area=""):
    """地名去城市前缀、去共同区县前缀、去冗长后缀，留最认得出来的那几个字。"""
    s2 = str(nm or "").strip()
    for pre in (dst, area):
        if pre and s2.startswith(pre) and len(s2) > len(pre) + 2:
            s2 = s2[len(pre):]
    for suf in ("开放观鸟点", "开放观鸟", "国家级自然保护区", "自然保护区", "观鸟点"):
        if s2.endswith(suf) and len(s2) > len(suf) + 1:
            s2 = s2[:-len(suf)]
    return s2 or str(nm or "").strip()


def highlights_of(trip, d, maxn=4):
    """题签上的「本次行程亮点」：**像人说话那样的一句短句**。

    素材只取当天自然观察 / 观景类条目的地点与开始时段；口语化表达、**不罗列地名、
    不带时间与数字**，也不写成餐厅或注意事项。没有对应数据就不出这一行，不编。
    """
    req = trip.get("request") or {}
    dst = req.get("destination")
    if isinstance(dst, dict):
        dst = dst.get("name") or ""
    dst = str(dst or "").strip()
    items = d.get("items") or []
    # 地名先归并：同一坐标的两个名字要按同一个名字念，题签里才不会出现两个称呼
    cm = canon_names(items)
    places = [cm.get(str(x.get("place") or "").strip(), x.get("place")) for x in items]
    area = _area_of(places)

    def _p(x):
        raw = str(x.get("place") or "").strip()
        return _short_place(cm.get(raw, raw), dst, area)

    out = []
    for x in items:
        ttl = str(x.get("title") or "")
        if not ttl or any(w in ttl for w in _SKIP):
            continue
        hit = False
        for keys, tmpl in _TALK:
            if any(k in ttl for k in keys):
                out.append(_clip(tmpl.format(w=_when_word(x.get("time")), p=_p(x)), 14))
                hit = True
                break
        if not hit:                             # 模板没覆盖的活动（骑行/徒步/赶集…）走通用句
            act = _clean_title(ttl)
            p2 = _p(x)
            for pre in (p2, area, dst):
                if pre and act.startswith(pre):
                    act = act[len(pre):]
            act = act.strip(" ·")
            # 兜底句只接「事情」，不接动作：以动词开头、或太长/带并列符号的标题都不成句
            if (act and act[0] not in "去回返出赶到走驱乘上下开送"
                    and len(act) <= 8 and "+" not in act and "、" not in act):
                out.append(_clip("%s去%s%s" % (_when_word(x.get("time")), p2, act), 14))
        if len(out) >= maxn:
            break
    return out


def title_block(trip, d, day):
    """题签内容：主标题 / 副题 / 详情行 —— 全部来自数据，不手工。"""
    req = trip.get("request") or {}
    dst = req.get("destination")
    if isinstance(dst, dict):
        dst = dst.get("name") or ""
    dst = str(dst or "").strip()
    raw = str(d.get("title") or "").strip()
    main = _clip(re.split(r"[：:（(，,、+]", raw, 1)[0].strip(), 10)
    if not main:
        main = _short(str(trip.get("title") or "").strip(), 12)
    sub = ("%s · 第 %d 日" % (dst, day)) if dst else ("第 %d 日" % day)
    lines = highlights_of(trip, d)          # 题签写「本次行程亮点」，不写吃啥与注意事项
    return {"main": main, "sub": sub, "lines": lines[:4]}


_CELL9 = {(0, 0): "左上角", (0, 1): "上边中间", (0, 2): "右上角",
          (1, 0): "左边中间", (1, 1): "正中", (1, 2): "右边中间",
          (2, 0): "左下角", (2, 1): "下边中间", (2, 2): "右下角"}


def pos_lines(names, P):
    """每个点在画面的哪个位置 —— **明写在提示词里，不靠模型看骨架猜**。

    给两样：① 九宫格格子（把画面横竖各分三份）；② 与最近的两个点的相对方位
    （右＝东、下＝南）。模型对「骨架上的点该在哪」很不敏感，实测照骨架直接画会有
    5 个点错 3 个；把话说死之后才对得上。
    """
    out = []
    xs, ys = [q[0] for q in P], [q[1] for q in P]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    for i, nm in enumerate(names):
        fx = (P[i][0] - x0) / max(x1 - x0, 1e-6)
        fy = (P[i][1] - y0) / max(y1 - y0, 1e-6)
        cell = _CELL9[(0 if fy < 1 / 3 else (1 if fy < 2 / 3 else 2),
                       0 if fx < 1 / 3 else (1 if fx < 2 / 3 else 2))]
        near = sorted((j for j in range(len(P)) if j != i),
                      key=lambda j: math.hypot(P[j][0] - P[i][0], P[j][1] - P[i][1]))[:2]
        rel = []
        for j in near:
            dx, dy = P[i][0] - P[j][0], P[i][1] - P[j][1]
            d = []
            if abs(dx) > 70:
                d.append("右" if dx > 0 else "左")
            if abs(dy) > 70:
                d.append("下" if dy > 0 else "上")
            rel.append("在%s的%s方" % (names[j], "".join(d) or "原处"))
        out.append("· %s 画面**%s**%s" % (nm, cell, ("；" + "、".join(rel)) if rel else ""))
    return out


def _angle(P, spot):
    """spot 在点位重心看来是哪个方向 —— 给 `side_cn` 用。"""
    cx = sum(q[0] for q in P) / len(P)
    cy = sum(q[1] for q in P) / len(P)
    return math.degrees(math.atan2(spot[1] - cy, spot[0] - cx))


def spell_check_lines(names):
    """收尾的**逐字回读**清单 —— 放在提示词最后。

    模型对提示词的开头与结尾都更敏感，把地名在结尾再逐字排一遍，比只说一次管用。
    实测：光在中间写一次「孤东海堤」，出图会变成「孤岛海堤」「孤上海堤」——
    第二个字老被换掉。
    """
    out = ["· 地名逐字回读（有一个字不对就把那个名字重写）："]
    out.append("  " + " ／ ".join("、".join(nm) for nm in names))
    return out


_GENERIC_SUFFIX = ("国家级自然保护区", "生态旅游区", "自然保护区", "风景名胜区",
                   "风景区", "旅游区", "景区", "公园", "开发区")


def apply_display_names(names, rows_of, seqs):
    """把整批地名换成海报显示名；**重名就整体作废**（保持原名，别撞车）。

    在流水线里只换这一处，后面的方位说明、地名框、行程块、核对清单自动一致。
    """
    disps = [display_name(nm) for nm in names]
    if len(set(disps)) != len(disps):
        return list(names), rows_of, seqs, {}
    m = dict(zip(names, disps))
    return (disps,
            {m[k]: v for k, v in rows_of.items() if k in m},
            {m[k]: v for k, v in seqs.items() if k in m},
            {k: v for k, v in m.items() if k != v})


def route_chain(names):
    """路线顺序写成一条明确的链：`A → B → C`。

    原来只写「按上面顺序串起来」，模型会自己重排（画出来的走向和行程顺序对不上）。
    把链写死，它就没得发挥。顺序 = 当天各点**第一次出现**的先后（`collect` 的 order）。
    """
    return " → ".join("「%s」" % nm for nm in names)


def display_name(nm: str) -> str:
    """海报上的**显示名**：把「生态旅游区 / 自然保护区 / 景区」这类通用后缀去掉。

    字越多越容易写串 —— 实测 8 字的「黄河口生态旅游区」被写成「黄河口生态游游」，
    而 4 字的地名全对。地名太长就先削掉通用尾巴（剩 ≥2 字才削），
    全名仍保留在攻略页与 `meta.json` 里。
    """
    for sfx in _GENERIC_SUFFIX:
        if nm.endswith(sfx) and len(nm) - len(sfx) >= 2:
            return nm[: -len(sfx)]
    return nm


def checklist_text(names, tinfo, rows_of):
    """**逐字核对清单**：出图后拿它对着成品一个字一个字看，有一处不对就重生成。

    这是「地名/题签串字」这类模型级误差的通用出口 —— 提示词只能降低概率，
    真正的保证是**生成后逐字对比**（不需要 OCR，读图就能对）。
    """
    L = ["海报文字逐字核对清单（出图后对着成品逐个看，有一处不对就重生成）", ""]
    L.append("【地名】%d 个（写在各点旁）" % len(names))
    for nm in names:
        L.append("  「%s」= %s" % (nm, "、".join(nm)))
    if tinfo:
        L.append("")
        L.append("【题签】（左上角）")
        for tag, txt in (("标题", tinfo.get("main")), ("副题", tinfo.get("sub"))):
            if txt:
                L.append("  %s：%s = %s" % (tag, txt, "、".join(txt)))
        for ln in tinfo.get("lines") or []:
            L.append("  亮点：%s = %s" % (ln, "、".join(ln)))
    L.append("")
    rows = [(nm, r) for nm in names for r in (rows_of.get(nm) or [])]
    L.append("【行程】%d 行（集中那一块）" % len(rows))
    for nm, r in rows:
        L.append("  %s ｜ %s ｜ %s" % (nm, r["time"], r["detail"]))
    return "\n".join(L) + "\n"


def name_spell_lines(names):
    """每个地名**逐字列出来** —— 防模型把字写串。

    实测踩过：「孤东海堤」被写成「孤岛海堤」（借了「河口区孤岛镇」里的「岛」）。
    写成一整词时模型是当图案抄的，逐字列开它才会一个字一个字对。
    ⚠️ 只列**正确**的字，不要把写错的样子当反面例子写进去 —— 写了它会照抄。
    """
    return ["· 「%s」就是这 %d 个字：%s" % (nm, len(nm), "、".join(nm)) for nm in names]


def confusable_notes(names):
    """挑出**字面相近的地名对**，单独提醒别混。三条命中任一条就算：

    ① 共字 ≥2 个；② 首字相同；③ **共用一个在这批地名里只出现在这两个名字里的字**
    —— 第 ③ 条是为主角补的：「孤东海堤」和「河口区孤岛镇」只共一个「孤」，
    前两条都抓不到，而模型正是把这两个名字串成了「孤岛海堤」。
    """
    where = {}
    for nm in names:
        for ch in set(nm):
            where.setdefault(ch, set()).add(nm)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = set(a) & set(b)
            rare = [c for c in shared if where.get(c) == {a, b}]
            if len(shared) >= 2 or a[0] == b[0] or rare or a in b or b in a:
                # 两个名字**并排逐字摆出来** + 明说「别互相借字」。
                # 只说「字面像」不够 —— 实测模型会把「孤东海堤」写成「孤岛海堤」，
                # 就是借了「河口区孤岛镇」里的「岛」。
                out.append("· 「%s」= %s，与「%s」= %s，两串字各写各的，**别互相借字**。"
                           % (a, "、".join(a), b, "、".join(b)))
    return out


def build_prompt_plain(names, P, rows_of, scene, tinfo=None, block_spot=None):
    """**纯文字版提示词**：不传骨架图，位置全靠文字写死（甲方要的方向）。

    用来对比两条路：① 骨架 + img2img；② 只给文字。文字版把「时间 + 地点 + 描述 +
    真实经纬度推出来的位置关系」全写成话，模型按话构图 —— 没有虚线框和骨架上的字干扰它。
    实测（东营 D2，5 个点）：文字版位置全对、地名 4/5 对；骨架版位置错了 4/5。
    """
    Cx = sum(t[0] for t in P) / len(P)
    Cy = sum(t[1] for t in P) / len(P)
    if block_spot is None:
        block_spot, _d = text_block_spot(layout_units(names, P), P)
    flat_rows = sorted(((nm, r) for nm in names for r in (rows_of.get(nm) or [])),
                       key=lambda t: t[1].get("idx") or 0)
    sea = [i for i, nm in enumerate(names) if is_water(nm)]
    if sea:
        vx = sum(math.cos(math.atan2(P[i][1] - Cy, P[i][0] - Cx)) for i in sea)
        vy = sum(math.sin(math.atan2(P[i][1] - Cy, P[i][0] - Cx)) for i in sea)
        side = side_cn(math.degrees(math.atan2(vy, vx)))
    else:
        side = "右（东）"
    L = []
    A = L.append
    A("画一幅**中式水墨手绘旅行地图**，A4 竖幅（高大于宽），像国画家用毛笔在宣纸上画的"
      "一整幅写意山水地图。**不要参考任何图片，完全按下面的文字描述画。**")
    A("")
    A("【画面怎么分】**示意**地图，位置关系照下面写死的来（谁左谁右、谁上谁下绝不许变），"
      "距离与形状随意发挥。**画面%s是一带水面**（海），其余是陆地；海岸线斜贯整幅，"
      "由沙滩、浅滩沙洲渐变到深水，**不要画成一条硬边**。陆地上铺满水墨山水：山峦层叠三到四重、"
      "愈远愈淡，山脚点小树、坡上补疏林，云气流动。**整幅不许留大片空白纸面**；要大气，"
      "墨色分五层，近景最浓、远景极淡。" % side)
    A("")
    A("【点位 —— 共 %d 个，每个画一枚朱砂小红点；**点上和画面里都不许出现序号**】" % len(names))
    for ln in pos_lines(names, P):
        A(ln)
    A("· **路线照这个顺序连**（一跳都不许跳、先后不许调换）：%s；用一条**淡墨点线**（断续小墨点、"
      "疏密自然、由浓到淡，像古地图的「行旅路引」）依次串起来。**不要实线、不要等宽、不要箭头。**"
      % route_chain(names))
    A("")
    A("【每个点旁只写地名，不写时间与描述】都用中文引号「」框住，**逐字确认再写**，"
      "一个字不能改、不能少、不能多；字写大些、笔画清晰不粘连、写成标准简体汉字：")
    A("· " + " ／ ".join(names))
    A("")
    A("【行程文字 —— 全部集中成一块写在画面空着的地方，字号比地名小一点】")
    A("· 位置：**画面%s**那一大片空处，**不要压在小景、地名框、红点和路线上**；"
      "逐行写下这 %d 行（每行「地名 ｜ 时段 ｜ 详情」）："
      % (side_cn(_angle(P, block_spot)), len(flat_rows)))
    for nm, r in flat_rows:
        A("  %s ｜ %s ｜ %s" % (nm, r["time"], r["detail"]))
    A("· **不要给这些行编号**；这一块**只写一次**，不要拆开、不要贴到各个地名旁边；"
      "字可以小，但每一行都要看得清、笔画不粘连。")
    A("")
    if tinfo:
        A("【题签（左上角，留一小块空白透气）】")
        A("· 最大一行、毛笔大字作标题：%s" % tinfo["main"])
        A("· 第二行小一号：%s" % tinfo["sub"])
        if tinfo["lines"]:
            A("· **题签整体横排**（不要竖排），第三行起比副题再小一点、必须看得清，"
              "一行一句写下这些本次行程亮点（像人说话的一句短句、不罗列地名、不带时间数字）：")
            for ln in tinfo["lines"]:
                A("  %s" % ln)
        A("")
    A("【硬性】竖幅；像国画家用毛笔画成，不要电脑感；**不要序号数字、水印、logo、罗盘、"
      "图例、比例尺、边框**；所有中文字必须是**正确清晰可辨认的简体中文**，不许乱码、缺笔画、"
      "形近字替换、糊成一团的小字。")
    A("")
    A("【写完回读一遍】图上只应有：左上角题签、%d 个地名、那一块 %d 行行程，"
      "**别的字一个都不要有**。" % (len(names), len(flat_rows)))
    for ln in spell_check_lines(names):
        A(ln)
    A("· 这 %d 个名字**各写各的，别互相借字**。" % len(names))
    return "\n".join(L)


def build_prompt(names, P, TRUE, rows_of, scene, day_title, scene_side, tinfo=None,
                 block_spot=None):
    Cx = sum(t[0] for t in P) / len(P)
    Cy = sum(t[1] for t in P) / len(P)
    if block_spot is None:                      # 集中写行程的那一大块空处
        block_spot, _d = text_block_spot(layout_units(names, P), P)
    flat_rows = sorted(((nm, r) for nm in names for r in (rows_of.get(nm) or [])),
                       key=lambda t: t[1].get("idx") or 0)     # 按行程顺序排
    L = []
    A = L.append
    A("照下面这张**骨架草图**重绘成中式水墨手绘旅行地图（A4 竖幅），"
      "像国画家用毛笔在宣纸上画的写意山水地图。")
    A("")
    A("【三条铁律，违反即作废】")
    A("1）**不许把骨架上的虚线框画进成品**（那只是位置标记，一根框线都不留）；"
      "**画面里只准出现下面列出的文字**——骨架红框里已经用淡墨写着这个地点的名字，照它抄；"
      "**角上标方位的几个小字不要写**（**成品里不要写**）；**画面里不许出现任何序号数字**"
      "（1、2、3 都别写，时刻照抄）。")
    A("2）**红点与框的位置必须跟骨架一致**，不许挪位、不许对调；下面给了每个点的九宫格位置。"
      "图上文字只有三处：题签、每个地名、那一块行程小字。")
    A("")
    A("【骨架怎么读】小红点 = 地点；红点旁一对虚线框：**蓝色 = 画小景**、**朱红 = 写地名**；"
      "左上角那个朱红虚线框 = 题签区。暖灰一大片是陆地（整片画山峦密林），淡青一块是水，"
      "之间那条淡褐色曲线是海岸线。")
    A("")
    A("【每个点在画面的哪个位置 —— **硬要求**：画面横竖各分三份，照这个放，**放错了整张作废**】")
    for ln in pos_lines(names, P):
        A(ln)
    A("· 每个红点旁那个**朱红虚线框**只写那个地点的名字，**不要写时间、不要写任何描述**；"
      "只写这几个字：%s —— 用「」框住、**逐字确认再写**，不能改、不能少、不能多；"
      "**不许把两个地名对调。**" % " ／ ".join(names))
    A("")
    if tinfo:
        A("【题签（写左上角那个朱红虚线框里）】")
        A("· 最大一行、毛笔大字作标题：%s" % tinfo["main"])
        A("· 第二行小一号：%s" % tinfo["sub"])
        if tinfo["lines"]:
            A("· **整体横排**（不要竖排），第三行起比副题小一点、要看得清，一行一句写这些亮点"
              "（像人说话的一句短句、不列地名、不带时间数字）：")
            for ln in tinfo["lines"]:
                A("  %s" % ln)
    A("")
    A("【行程 —— **全部集中写在一处**，字号比地名小】")
    A("· 位置：**画面%s**那片空处（**别压在小景、地名框、红点和路线上**）；逐行写下这 %d 行，照抄："
      % (side_cn(_angle(P, block_spot)), len(flat_rows)))
    for nm, r in flat_rows:
        A("  %s ｜ %s ｜ %s" % (nm, r["time"], r["detail"]))
    A("· **不要编号**；这一块**只写一次，老老实实待在一处**，**不要拆开**；字小也要看得清。")
    A("")
    A("【蓝框里画什么小景】（统一笔触：松弛、有墨韵与飞白，%d 幅大小相当）" % len(names))
    for nm in names:
        A("· 「%s」那个红点旁的蓝框 → %s" % (nm, scene[nm]))
    A("")
    A("【地形】")
    si = [i for i, nm in enumerate(names) if is_water(nm)]
    if si:
        vx = sum(math.cos(math.atan2(P[i][1] - Cy, P[i][0] - Cx)) for i in si)
        vy = sum(math.sin(math.atan2(P[i][1] - Cy, P[i][0] - Cx)) for i in si)
        A("· **画面%s**是一带水面（海/江/湖），淡花青薄涂；**海岸线千万别画成一条硬边**，"
          "由沙滩、浅滩沙洲渐变到深水。" % side_cn(math.degrees(math.atan2(vy, vx))))
    lk = [nm for nm in names if is_lake(nm)]
    if lk:
        A("· 这几个点处各画一个小湖（淡花青水面）：%s。" % "、".join(_short(x, 8) for x in lk))
    A("· **水以外的整幅都由水墨山水铺满**（山峦层叠三到四重、愈远愈淡，铺到三边），"
      "**不许大片空白纸面**（只有左上角题签那小块留白）；山水是主体，要画大画满，别缩在中间一条带里。")
    A("")
    A("【路线】照骨架那条细曲线的走向，用**淡墨点线**（断续小墨点、由浓到淡，像古地图的"
      "「行旅路引」）按行程顺序串起那几处红点。**不要实线、不要箭头。**")
    A("")
    A("【硬性】竖幅；像国画家用毛笔画成，不要电脑感；**不要序号数字、水印、logo、罗盘、图例、"
      "比例尺、边框**；所有中文字必须是**正确清晰可辨认的简体中文**，不许乱码、缺笔画、形近字替换。")
    A("")
    A("【回读】图上只有：题签、%d 个地名、那一块行程小字，**别的字一个都不要有**。" % len(names))
    for ln in spell_check_lines(names):
        A(ln)
    A("· 这 %d 个名字**各写各的，别互相借字**。" % len(names))
    return "\n".join(L)


def build(job: str, out: str, day: int = 1, max_points: int = MAX_POINTS, plain: bool = False):
    trip = rp.read_trip_source(job)[0]
    d, items, order, seqs, rows_of = collect(trip, day)
    names, TRUE = coords_of(trip, order, day)
    names, TRUE, merged = _reduce_points(names, TRUE, seqs, rows_of, max_points)
    names, rows_of, seqs, renamed = apply_display_names(names, rows_of, seqs)
    P, diag = arrange(TRUE, names)
    os.makedirs(out, exist_ok=True)
    scene = {nm: scene_of(nm) for nm in names}
    sk = os.path.join(out, "sketch.png")
    UNITS, scene = draw_sketch(names, P, TRUE, sk, scene, day,
                               d.get("title") or "", seqs)
    nrows = sum(len(rs) for rs in rows_of.values())
    bsize = (BLK_W, 34 * nrows + 36)                       # 块高随行数伸缩（行距 ~34）
    block_spot, _spot_r = text_block_spot(UNITS, P, bsize)  # 行程文字集中写的那一大块空处
    tinfo = title_block(trip, d, day)
    if plain:                       # 纯文字版：不传骨架，位置全写在话里
        pr = build_prompt_plain(names, P, rows_of, scene, tinfo, block_spot)
    else:
        pr = build_prompt(names, P, TRUE, rows_of, scene, d.get("title") or "", None, tinfo,
                          block_spot)
    pt = os.path.join(out, "prompt.txt")
    open(pt, "w", encoding="utf-8").write(pr)
    ck = os.path.join(out, "checklist.txt")           # 出图后逐字核对用
    open(ck, "w", encoding="utf-8").write(checklist_text(names, tinfo, rows_of))
    tot, bad = check_compass(TRUE, P)
    meta = {"job": job, "day": day, "day_id": d.get("day_id"),
            "compass": {"total": tot, "bad": bad}, "max_points": max_points,
            "names": names, "dots": [[round(x), round(y)] for (x, y) in P],
            "units": UNITS, "rows_of": rows_of, "scene": scene, "diag": diag,
            "merged": merged, "title_block": tinfo, "text_block_spot": list(block_spot),
            "display_names": renamed,
            "plain": bool(plain)}
    json.dump(meta, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return sk, pt, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--out", default="_tmp/ai")
    ap.add_argument("--max-points", type=int, default=MAX_POINTS,
                    help="一张图最多几个点（默认 8；0 = 不设上限，超了按真位最近的一对合并）")
    ap.add_argument("--plain", action="store_true",
                    help="纯文字版：不传骨架图，位置全靠提示词写死（生图时只给 prompt）")
    a = ap.parse_args()
    sk, pt, meta = build(a.job, a.out, a.day, a.max_points, a.plain)
    print("骨架 →", sk)
    print("提示词 →", pt)
    print("逐字核对清单 →", os.path.join(os.path.dirname(pt), "checklist.txt"),
          "（出图后对着成品逐个字看，不对就重生成）")
    print("簇：", meta["diag"]["clusters"], " 放大：", meta["diag"]["kx"])
    print("  点位：")
    for i, nm in enumerate(meta["names"]):
        print("    %-22s (%4d,%4d)" % (_short(nm, 20), meta["dots"][i][0], meta["dots"][i][1]))


if __name__ == "__main__":
    main()
