#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_tests.py · travel-orchestrator 回归测试

跑法：
    python tests/run_tests.py

覆盖九件事：
  1. 干净的方案不该被红方挑出阻断项或严重项
  2. 故意写坏的方案必须被抓出对应的规则编号
  3. 赶海窗口的安全规则（RT-L01 / RT-L04）单独验证
  4. 渲染器产出的 HTML 必须是纯离线单文件，且章节齐全
  5. 需求访谈协议齐备；问询记录属过程记录，不得出现在交付物里
  6. 上架元数据齐备：frontmatter 必填项、署名、版本号四处同口径
  7. 回归看门：使用须知跟着方案实际有的专项走、重名地名会告警、鸟种卡长句能拆
  8. 分片数据流：渲染/复核/校验直接读目录、拆开再合并等值、坏数据不得假装渲染成功
  9. 工具链不写脏仓库（文档口径：用例 10）：验收渲染出到临时目录、打包产物不落源码目录

不需要网络，不需要任何第三方库。
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import nature_forecast   # noqa: E402
import nature_widgets as nw   # noqa: E402
import red_team          # noqa: E402
import render_plan       # noqa: E402
from urllib.parse import quote   # noqa: E402

FIX = os.path.join(HERE, "fixtures")
# 示例方案是**分片目录**（parts/*.json），不是一份大 JSON ——
# 渲染器/复核器都直接读目录，测试也走同一条路
GOOD = os.path.join(ROOT, "examples", "weihai-2d")

SECTIONS = ["一页速览", "天气研判", "自然观察专项", "逐日行程", "美食推荐",
            "预算明细", "出发前待办", "踩坑指南", "依据索引"]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def load(path):
    """取一份方案数据：给目录就读其中的 parts/（与渲染器同一条路），给文件读文件。"""
    import build_trip as bt
    return bt.resolve_trip(path, quiet=True)[0]


def rules_of(path):
    return {f["rule_id"] for f in red_team.run(load(path))["findings"]}


# ---------------------------------------------------------------- 用例 1
def test_clean_plan():
    print("\n用例 1 · 干净方案应当通过红方")
    r = red_team.run(load(GOOD))
    check("无阻断项", r["summary"]["blocking"] == 0, f"blocking={r['summary']['blocking']}")
    check("无严重项", r["summary"]["major"] == 0, f"major={r['summary']['major']}")
    check("结论为通过", r["verdict"] == "通过", r["verdict"])


# ---------------------------------------------------------------- 用例 2
def test_bad_plan():
    print("\n用例 2 · 故意写坏的方案必须被抓出来")
    found = rules_of(os.path.join(FIX, "bad_plan.json"))
    expected = {
        "RT-S02": "重复的 item_id",
        "RT-S03": "行程时间重叠",
        "RT-S04": "无效时间窗 23:00-24:30",
        "RT-S05": "预算下限大于上限",
        "RT-F01": "标了已验证却没有来源",
        "RT-F05": "unknown 却写了具体值",
        "RT-L01": "赶海窗口没盖住干潮",
        "RT-L03": "满月夜照排观星",
        "RT-L06": "缺备选方案",
        "RT-L08": "踩坑指南为空",
        "RT-X01": "缺红方复核记录",
    }
    for rid, why in expected.items():
        check(f"{rid} 命中（{why}）", rid in found)
    r = red_team.run(load(os.path.join(FIX, "bad_plan.json")))
    check("存在阻断项时结论为需修改", r["verdict"] == "需修改", r["verdict"])


# ---------------------------------------------------------------- 用例 3
def test_tide_safety():
    print("\n用例 3 · 赶海窗口安全规则")
    r = red_team.run(load(os.path.join(FIX, "bad_tide.json")))
    ids = {f["rule_id"] for f in r["findings"]}
    check("RT-L04 命中（窗口拖过干潮 1.5 小时）", "RT-L04" in ids)
    hit = [f for f in r["findings"] if f["rule_id"] == "RT-L04"]
    detail = hit[0]["message"] if hit else ""
    check("提示里给出了干潮时刻与超时时长", "06:08" in detail and "小时" in detail, detail[:60])


# ---------------------------------------------------------------- 用例 4
def test_render():
    print("\n用例 4 · 渲染器产出")
    tmp = tempfile.mkdtemp(prefix="to-render-")
    try:
        out = render_plan.write_outputs(GOOD, out_dir=tmp)
        check("生成了 MD", os.path.exists(out["md"]))
        check("生成了 HTML", os.path.exists(out["html"]))
        with open(out["html"], encoding="utf-8") as f:
            htm = f.read()
        check("HTML 无 <script>", "<script" not in htm.lower())
        check("HTML 无远程资源加载",
              not any(p in htm.lower() for p in
                      ('src="http', "src='http", "<link ", "@import", "url(http")))
        missing = [s for s in SECTIONS if s not in htm]
        check("9 个章节齐全", not missing, f"缺少 {missing}" if missing else "")
        with open(out["md"], encoding="utf-8") as f:
            md = f.read()
        missing_md = [s for s in SECTIONS if f"## {s}" not in md]
        check("MD 章节与 HTML 对齐", not missing_md, f"缺少 {missing_md}" if missing_md else "")
        chk = render_plan.check_outputs(GOOD, out_dir=tmp)
        check("--check 一致性通过", chk["ok"], "; ".join(chk["problems"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 用例 5
def test_nature_widgets():
    """自然专项的交互件：鸟种卡、星图、天光条、潮汐点选图。

    这里既查产物里的痕迹，也直接查星图算法：北极星的地平高度必须接近观测地纬度
    （差 6° 以内），且 10 月 1 日晚上的心宿二必须已经落到地平线下。数字算错了这条会挂。
    """
    print("\n用例 5 · 自然专项交互件")
    import nature_widgets as nw
    from datetime import datetime, timedelta

    tmp = tempfile.mkdtemp(prefix="to-widgets-")
    try:
        out = render_plan.write_outputs(GOOD, out_dir=tmp)
        with open(out["html"], encoding="utf-8") as f:
            htm = f.read()
        n_sp = htm.count('<details class="sp')
        check("鸟种卡可展开（details.sp）", n_sp >= 1, f"count={n_sp}")
        check("鸟种卡带保护等级与查图外链",
              "sp-badge" in htm and htm.count('class="sp-link"') >= 4,
              f"links={htm.count('class=\"sp-link\"')}")
        n_star = htm.count('<circle class="st"')
        check("星图已绘制且含亮星", n_star >= 5, f"stars={n_star}")
        check("星图带悬停提示（高度/方位）", "｜高度" in htm and "｜方位" in htm)
        check("天光条有蓝调/黄金/图例",
              all(x in htm for x in ("sunband-svg", "b-blue", "b-gold", "sblegend")))
        radios = htm.count('class="tp" type="radio"')
        dots = htm.count('class="tpdot"')
        panels = htm.count('<div class="tp-panel">')
        check("潮汐点选：单选/圆点/面板一一对应",
              radios >= 3 and radios == dots == panels, f"{radios}/{dots}/{panels}")
        check("潮汐点选默认选中干潮", htm.count('" checked>') == 1,
              f"checked={htm.count(chr(34) + ' checked>')}")
        check("地点都变成可点链接", htm.count('<a class="map"') >= 6,
              f"count={htm.count('<a class=\"map\"')}")

        trip = load(GOOD)
        cfg = trip["itinerary"]["nature"]["stargazing"]["chart"]
        tz = cfg.get("tz", 8)
        jd = nw._jd_utc(datetime.strptime(cfg["at"], "%Y-%m-%dT%H:%M") - timedelta(hours=tz))
        lst = (nw._gmst_hours(jd) + cfg["lng"] / 15.0) % 24
        alt_polaris, _ = nw._altaz(*nw.STARS["北极星"][:2], cfg["lat"], lst)
        alt_antares, _ = nw._altaz(*nw.STARS["心宿二"][:2], cfg["lat"], lst)
        check("星图坐标自洽（北极星高度≈当地纬度）",
              abs(alt_polaris - cfg["lat"]) < 6, f"alt={alt_polaris:.1f} lat={cfg['lat']}")
        check("星图剔除了已落下的星（心宿二在地平线下）", alt_antares < 0,
              f"alt={alt_antares:.1f}")
        check("交互件里也没有脚本", "<script" not in htm.lower())

        # --- 排版返修（v4.3.1 / v4.3.2）---
        # 「观鸟点」是地名的一部分，剥掉就成「…自然保护区开放」，高德搜不出来
        kw = nw.search_keyword("荣成大天鹅国家级自然保护区开放观鸟点")
        check("地点搜索词不被剥残（保留「观鸟点」）",
              "开放观鸟点" in kw, kw)
        check("地点搜索词仍会剥掉括号附注与「备选」噪声",
              nw.search_keyword("成山头（朝东无遮挡）") == "成山头"
              and nw.search_keyword("樱花湖东岸 免费备选") == "樱花湖东岸")
        # 分隔符写成 HTML 文本节点的话，一行排不下就孤零零落在行尾
        check("地点串没有裸「、」卡在两项之间",
              '</span>、<span class="pl-item">' not in htm)
        n_wide = htm.count('class="kv wide"')
        check("地点行通栏（宽行 kv）", n_wide >= 4, f"wide={n_wide}")
        check("星图行两列，月相盘与目标清单同列",
              'class="skycol"' in htm and '<div class="tgs">' in htm
              and htm.index('class="skycol"') < htm.index('<div class="tgs">'))
        with open(os.path.join(ROOT, "assets", "handbook.css"), encoding="utf-8") as f:
            css = f.read()
        check("样式表有分隔符/通栏/两列三条规则",
              all(x in css for x in (".pl-item+.pl-item::before",
                                     ".kv.wide", ".skycol{")),
              f".pl 分隔符={'是' if '.pl-item+.pl-item::before' in css else '缺'}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)




# ---------------------------------------------------------------- 用例 6
def test_intake():
    """S1 需求访谈协议（v4.4.0）。

    两件事：协议文档与 SKILL.md 的接线是否齐全；以及**拍板记录不许出现在交付物里** ——
    访谈是生成过程，和红方复核同属过程记录，读者要的是行程不是问答流水。
    后面几条是反向断言，谁把问询记录渲染回产物都会挂在这里。
    """
    print("\n用例 6 · 需求访谈协议（问询记录不进交付物）")
    proto = os.path.join(ROOT, "references", "intake-interview.md")
    check("访谈协议文档存在", os.path.exists(proto), os.path.basename(proto))
    if os.path.exists(proto):
        with open(proto, encoding="utf-8") as f:
            p = f.read()
        for key, label in (("一次一问", "硬规则·一次一问"),
                           ("能查的不要问", "硬规则·能查的不问"),
                           ("共识确认前不写方案", "硬规则·共识前不写方案"),
                           ("五层决策树", "五层决策树"),
                           ("赶海", "L3 含自然专项四件"),
                           ("追问对照表", "追问对照表"),
                           ("需求共识", "收尾共识模板"),
                           ("反例清单", "反例清单")):
            check("协议含「%s」" % label, key in p)

    with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
        skill = f.read()
    check("SKILL.md S1 指向访谈协议", "references/intake-interview.md" in skill)
    check("SKILL.md 要求一次一问", "一次只问一个问题" in skill)
    check("SKILL.md 要求访谈收尾出共识", "需求共识" in skill)
    check("SKILL.md 把自然专项挂到访谈 L3", "L3 自然观察专项" in skill or "S1 访谈 L3" in skill)
    check("SKILL.md 写明问询记录不进交付物", "过程记录" in skill)

    trip = load(GOOD)
    dec = [d for d in (trip.get("decisions") or []) if isinstance(d, dict)]
    check("decisions 记录了访谈拍板", len(dec) >= 5, f"{len(dec)} 条")
    check("每条拍板都有理由", all(d.get("reason") for d in dec))
    check("被顶回过的决定留了 pushed_back", any(d.get("pushed_back") for d in dec))
    req = trip.get("request") or {}
    check("request.why 已填", bool(req.get("why")))
    check("request.constraints 已填", bool(req.get("constraints")))
    check("request.nature_caps 记了装备与作息", "gear" in (req.get("nature_caps") or {}))

    tmp = tempfile.mkdtemp(prefix="to-intake-")
    try:
        out = render_plan.write_outputs(GOOD, out_dir=tmp)
        with open(out["html"], encoding="utf-8") as f:
            htm = f.read()
        with open(out["md"], encoding="utf-8") as f:
            md = f.read()
        # 共识的结论要进产物：读者得知道这趟是什么口径
        check("速览显示这趟的主题", "这趟的主题" in htm)
        check("速览显示硬约束", "硬约束" in htm)
        check("长句整行通栏（cell.wide）", 'class="cell wide"' in htm)
        check("专项偏好并成一格、口径写在值里",
              "专项偏好" in htm and "必须玩到：" in htm and "能做则做：" in htm)
        check("不再有分不清的「必去专项/优先专项」双格",
              "必去专项" not in htm and "优先专项" not in htm)
        check("MD 同口径", "必须玩到：" in md and "能做则做：" in md)
        check("往返并成一格", "往返" in htm and "北京 → 威海" in htm)
        # 只数速览这一节：天气节也用 .cell，全局数会数多。
        # 注意不能用 htm.index("sec-glance") —— 顶部导航里也有这个字符串，
        # 那样会切出一段空字符串，断言变成空过（这个坑踩过）
        glance = htm[htm.rindex('id="sec-glance"'):htm.rindex('id="sec-weather"')]
        check("速览区间取到了格子", 'class="cell' in glance)
        units = (glance.count('class="cell"')
                 + 2 * glance.count('class="cell half"')
                 + 4 * glance.count('class="cell wide"'))
        check("速览网格行行填满（列宽合计是 4 的倍数）", units % 4 == 0, f"合计 {units} 列")
        check("专项偏好与硬约束各占两列并排",
              glance.count('class="cell half"') == 2,
              f"两列格 {glance.count('class=\"cell half\"')} 个")
        check("速览用定死列数的网格", 'class="grid g-glance"' in htm)
        # 过程记录不进产物
        for needle, label in (("访谈拍板记录", "产物无拍板记录标题"),
                              ("当时提醒过的顾虑", "产物无顶回顾虑"),
                              ("class=\"dlog\"", "产物无拍板记录块")):
            check(label, needle not in htm and needle not in md)
        check("产物仍无脚本", "<script" not in htm.lower())
        chk = render_plan.check_outputs(GOOD, out_dir=tmp)
        check("--check 一致性仍通过", chk["ok"], "; ".join(chk["problems"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 用例 7
def test_publish_meta():
    """上架元数据（v4.5.1）。

    开放平台要求 frontmatter 填 description / description_zh / description_en /
    version / author，缺一项上传的 zip 就解析失败。这几条是防漂移断言：
    SKILL.md、workbuddy.json、LICENSE、页脚版本号四处必须同口径。
    """
    import re
    print("\n用例 7 · 上架元数据（frontmatter 必填项与署名同步）")
    with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
        skill = f.read()
    m = re.match(r"^---\n(.*?)\n---", skill, re.DOTALL)
    check("SKILL.md 有 frontmatter", bool(m))
    if not m:
        return
    fm = m.group(1)

    def field(key):
        hit = re.search(r'^%s:\s*"?(.*?)"?\s*$' % key, fm, re.M)
        return hit.group(1) if hit else ""

    for key in ("name", "description", "description_zh", "description_en",
                "version", "author"):
        check("frontmatter 有 %s" % key, bool(field(key)), field(key)[:28])

    author = field("author")
    check("author 署项目名", author == "travel-orchestrator", author)
    desc = field("description")
    check("description 不含尖括号（官方校验器会拒）", "<" not in desc and ">" not in desc)

    with open(os.path.join(ROOT, "workbuddy.json"), encoding="utf-8") as f:
        wb = __import__("json").load(f)
    check("中文简介两处同步", wb.get("description_zh") == field("description_zh"))
    check("英文简介两处同步", wb.get("description_en") == field("description_en"))
    check("展示名两处同步", wb.get("display_name") == field("display_name"))

    foot = re.search(r"^\*v(\d+\.\d+\.\d+) \|", skill, re.M)
    check("页脚版本号与 frontmatter 一致",
          bool(foot) and foot.group(1) == field("version"),
          "%s / %s" % (field("version"), foot.group(1) if foot else "-"))

    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
        lic = f.read()
    check("LICENSE 著作权人与 author 同口径", "Copyright (c) 2026 " + author in lic)


# ---------------------------------------------------------------- 用例 8
def test_regressions():
    """回归看门（v4.5.2）：盯住大同那轮暴露的三个问题。

    这三处修复曾经只落在安装目录（`~/.workbuddy/skills/...`），源码里没有，
    一次从源码同步就被覆盖了。所以这里不测「文档里写了什么」，直接测代码行为。
    """
    import copy
    print("\n用例 8 · 回归看门（使用须知跟专项走 / 重名地名告警 / 鸟种卡长句）")

    # ---- ① MD「使用须知」跟着方案里实际有的专项走
    tmp = tempfile.mkdtemp(prefix="to-reg-")
    try:
        with open(render_plan.write_outputs(GOOD, out_dir=tmp)["md"], encoding="utf-8") as f:
            md_tide = f.read()
        check("有赶海的方案，须知里点出赶海潮汐", "赶海潮汐" in md_tide)

        no_tide = copy.deepcopy(load(GOOD))
        nat = no_tide["itinerary"]["nature"]
        for key in ("beachcombing", "sunrise"):
            nat.pop(key, None)
        no_tide["trip_id"] = "regression-no-tide"
        p2 = os.path.join(tmp, "no_tide.json")
        with open(p2, "w", encoding="utf-8") as f:
            __import__("json").dump(no_tide, f, ensure_ascii=False)
        with open(render_plan.write_outputs(p2, out_dir=tmp)["md"], encoding="utf-8") as f:
            md_no = f.read()
        check("没有赶海的方案，须知不提潮汐", "赶海潮汐" not in md_no)
        check("没有赶海的方案，须知仍提景区预约", "景区预约" in md_no)
        check("没有日出的方案，须知不提日出日落", "日出日落" not in md_no)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ② 重名地名：同名候选跨省必须说出来
    dup = [{"name": "大同", "admin1": "黑龙江", "latitude": 46.04, "longitude": 124.81},
           {"name": "大同", "admin1": "山西", "latitude": 40.08, "longitude": 113.30}]
    note = nature_forecast.place_ambiguity("大同", dup)
    check("同名跨省会告警", bool(note))
    check("告警列清两份候选", "山西" in note and "黑龙江" in note)
    check("告警指向 §6.2", "nature-apis.md §6.2" in note)
    check("只有一个候选时不吵", nature_forecast.place_ambiguity("威海", dup[:1]) == "")
    check("同省重复不算歧义",
          nature_forecast.place_ambiguity("大同", [dict(dup[0]), dict(dup[0])]) == "")

    # ---- ③ 鸟种卡：长句拆成「鸟名 + 说明」
    check("长句拆出鸟名", nw.split_species_text("大天鹅、灰鹤（未核实）")[0] == "大天鹅")
    check("拆出的说明原样保留",
          nw.split_species_text("大天鹅、灰鹤（未核实）")[1] == "灰鹤（未核实）")
    check("短鸟名不动", nw.split_species_text("黑嘴鸥") == ("黑嘴鸥", ""))
    wing = nw.species_list(["大天鹅、灰鹤（国家二级，未核实）"])
    check("降级胶囊留了说明位", 'class="sp-note"' in wing)
    check("名字位只剩鸟名", '<span class="sp-name">大天鹅</span>' in wing)
    check("外链只拿鸟名去搜",
          "q=" + quote("大天鹅") in wing and quote("大天鹅、灰鹤") not in wing)

    # ---- ④ 这轮补回的两条文档规则
    with open(os.path.join(ROOT, "references", "nature-apis.md"), encoding="utf-8") as f:
        na = f.read()
    check("nature-apis 补回了 §6.2", "§6.2" in na and "admin1" in na)
    with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
        sk = f.read()
    check("SKILL.md 有坐标优先硬规则", "--lat/--lng` 取，不要用 `--city" in sk)
    check("SKILL.md 写明纯字符串只写鸟名", "纯字符串只写鸟名" in sk)

    # ---- ⑤ 脚本版本号与 SKILL.md 里写的不再各说各话
    res = sk[sk.find("## Resources"):]

    def doc_slice(fname):
        i = res.find("`" + fname + "`")
        return res[i:i + 90] if i >= 0 else ""

    import build_trip as bt_mod
    for fname, ver in (("nature_forecast.py", nature_forecast.VERSION),
                       ("nature_widgets.py", nw.__version__),
                       ("render_plan.py", render_plan.VERSION),
                       ("build_trip.py", bt_mod.VERSION)):
        check("SKILL.md 里 %s 的版本号对得上" % fname, ("v" + ver) in doc_slice(fname), ver)


# ---------------------------------------------------------------- 用例 9
def test_split_flow():
    """分片数据流（v4.6.0）。

    起因是「生成慢、一生成就报错」：以前要把 20-40 KB 的 trip.json 一次写完，
    写坏一次就得临时手写脚本修。现在数据一直是小分片，渲染/复核/校验就地合并，
    中间不落大文件；同时渲染器前面补了一道结构门，坏数据不再"渲染成功"。
    """
    import json
    import build_trip as bt
    print("\n用例 9 · 分片数据流（目录直读 / 拆合等值 / 结构门拦截）")

    # ---- ① 示例就是分片形态，没有中间大文件，每片都小
    # 注意示例把分片**平铺在方案目录里**（不是 parts/ 子目录）：上架包限制目录不超过两层，
    # `examples/weihai-2d/parts/x.json` 会多一层。两种形态 resolve_trip 都认。
    check("示例目录没有中间 trip.json", not os.path.exists(os.path.join(GOOD, "trip.json")))
    jsons = [f for f in os.listdir(GOOD) if f.endswith(".json")]
    sizes = [os.path.getsize(os.path.join(GOOD, f)) for f in jsons]
    check("分片数量够多（按需拆开）", len(sizes) >= 8, "%d 片" % len(sizes))
    check("每片都很小，写不坏", max(sizes) < 12 * 1024, "最大 %d B" % max(sizes))
    check("上架包目录不超两层（示例不再套 parts/）",
          not os.path.isdir(os.path.join(GOOD, "parts")))

    # ---- ② 读目录能合并出完整数据，且直接过红方
    trip, label, n = bt.resolve_trip(GOOD, quiet=True)
    check("读目录能合并出数据", n >= 8 and isinstance(trip, dict), label)
    check("合并结果直接过红方",
          not red_team.run(trip)["summary"]["blocking"])

    # ---- ③ split 往返等值：拆开再合起来必须一模一样
    tmp = tempfile.mkdtemp(prefix="to-split-")
    try:
        src = os.path.join(tmp, "big.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(trip, f, ensure_ascii=False)
        try:
            rc = bt.main(["split", src, "--trip-dir", tmp])
        except SystemExit as e:
            rc = e.code
        check("split 命令成功", rc == 0, "rc=%s" % rc)
        back, _lbl, n2 = bt.resolve_trip(tmp, quiet=True)
        check("拆开再合并完全等值",
              json.dumps(back, ensure_ascii=False, sort_keys=True)
              == json.dumps(trip, ensure_ascii=False, sort_keys=True),
              "%d 片" % n2)
        want = [k for k in bt.TOP_ORDER if k in back]
        check("顶层键序固定（assemble 产物稳定、好 diff）",
              list(back.keys())[:len(want)] == want,
              "、".join(list(back.keys())[:5]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ④ 目录与文件两条路必须同源（内容哈希一致）
    tmp = tempfile.mkdtemp(prefix="to-same-")
    try:
        asm = os.path.join(tmp, "trip.json")
        try:
            rc = bt.main(["assemble", GOOD, "--out", asm])
        except SystemExit as e:
            rc = e.code
        check("assemble 仍可用（只在需要独立大 JSON 时用）", rc == 0, "rc=%s" % rc)
        a = render_plan.write_outputs(GOOD, out_dir=os.path.join(tmp, "by-dir"))
        b = render_plan.write_outputs(asm, out_dir=os.path.join(tmp, "by-file"))
        check("目录与文件两条路产物同源", a["content_hash"] == b["content_hash"])
        check("两条路都没落中间文件进示例目录",
              not os.path.exists(os.path.join(GOOD, "trip.json")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ⑤ 结构门：坏数据不得再"渲染成功"
    bad = os.path.join(FIX, "bad_plan.json")
    tmp = tempfile.mkdtemp(prefix="to-gate-")
    try:
        raised = False
        try:
            render_plan.write_outputs(bad, out_dir=tmp)
        except render_plan.TripDataError:
            raised = True
        check("坏数据被结构门拦住（不再假装成功）", raised)
        check("拦下时不留半成品产物", not os.listdir(tmp))
        forced = render_plan.write_outputs(bad, out_dir=tmp, force=True)
        check("--force 仍可强出（只留给排障）", os.path.exists(forced["html"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ⑥ 复核器也能直接吃目录
    try:
        rc = red_team.main(["check", GOOD])
    except SystemExit as e:
        rc = e.code
    check("复核器能直接吃目录", rc == 0, "rc=%s" % rc)


def test_tools():
    """验收与打包这两个工具，都不该在仓库里留下"每跑一次就变一次"的东西。"""
    def read(rel):
        with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    # ---- ① 验收渲染出到临时目录（早先是就地重渲染 examples/，
    #         产物头部带「生成于 <时间>」，于是每次验收都把仓库弄脏）
    va = read(os.path.join("tools", "verify_all.py"))
    check("验收渲染出到临时目录（不再就地重渲染示例产物）",
          "--out-dir" in va and "tempfile.mkdtemp" in va)
    check("示例产物比对忽略生成时间戳", "生成于" in va and "--refresh" in va)

    # ---- ② 打包产物落在工作区 .workbuddy/dist/，不是源码目录里面
    pk = read(os.path.join("tools", "pack.py"))
    check("打包输出指向工作区 .workbuddy/dist",
          'os.path.dirname(ROOT), ".workbuddy", "dist"' in pk)
    check("打包排除了源码目录里的 .workbuddy",
          '".workbuddy"' in pk.split("EXCLUDE_DIRS")[1].split("\n")[0])


def main():
    print("travel-orchestrator 回归测试")
    print("=" * 56)
    test_clean_plan()
    test_bad_plan()
    test_tide_safety()
    test_render()
    test_nature_widgets()
    test_intake()
    test_publish_meta()
    test_regressions()
    test_split_flow()
    test_tools()
    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 56)
    print(f"共 {len(results)} 项断言，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    if failed:
        for n in failed:
            print("  未通过：" + n)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
