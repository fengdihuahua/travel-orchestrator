#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_tests.py · travel-orchestrator 回归测试

跑法：
    python tests/run_tests.py

覆盖十三件事：
  1. 干净的方案不该被红方挑出阻断项或严重项
  2. 故意写坏的方案必须被抓出对应的规则编号
  3. 赶海窗口的安全规则（RT-L01 / RT-L04）单独验证
  4. 渲染器产出的 HTML 必须是纯离线单文件，且章节齐全；待办不放假勾选框（状态静态呈现）
  5. 需求访谈协议齐备；问询记录属过程记录，不得出现在交付物里
  6. 上架元数据齐备：frontmatter 必填项、署名、版本号四处同口径
  7. 回归看门：使用须知跟着方案实际有的专项走、重名地名会告警、鸟种卡长句能拆
  8. 分片数据流：渲染/复核/校验直接读目录、拆开再合并等值、坏数据不得假装渲染成功
  9. 工具链不写脏仓库（文档口径：用例 10）：验收渲染出到临时目录、打包产物不落源码目录
 10. v4.8.0 顺滑度：报错定位到分片、只校验一次、数据结构文档齐备、一页速览去重与窄屏适配
 11. v4.9.0 地图动线：离线 SVG 示意图、标号与停靠点一致、跳外部地图、空条目不成行、标点不断行
 12. v4.10.0 坏数据防线：元素级类型校验、geo 写法容错、nan/inf 不进图、URL 参数不被地名切断、
     渲染器与红方都不假设上游校验过（坏数据最多少显示一条，不炸 traceback）

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
        # v4.7.0：待办不放假勾选框——离线单文件存不住勾选状态，能点的框是误导；
        # 状态是渲染时定死的静态标记，数据 status=done 才画 ✓
        check("待办不放假勾选框（存不住状态）", 'type="checkbox"' not in htm)
        check("待办是静态状态标记（数据驱动）",
              'class="mark' in htm and "已办" in htm)
        # done 状态必须真的画出来：改一份数据把第一项标成 done，重渲染后要看到 ✓
        import copy as _copy
        done_trip = _copy.deepcopy(load(GOOD))
        done_trip["itinerary"]["checklist"][0]["status"] = "done"
        p_done = os.path.join(tmp, "done_trip.json")
        with open(p_done, "w", encoding="utf-8") as f:
            __import__("json").dump(done_trip, f, ensure_ascii=False)
        with open(render_plan.write_outputs(p_done, out_dir=tmp)["html"],
                  encoding="utf-8") as f:
            h_done = f.read()
        check("status=done 渲染成已办标记（✓ + 划线）",
              'mark-done' in h_done and "is-done" in h_done)
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
        for key, label in (("一屏一层", "硬规则·一屏一层"),
                           ("每项最多 2 问", "L3 每项最多 2 问"),
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
    check("SKILL.md 要求一次一屏", "一次一屏" in skill)
    check("SKILL.md 明确一屏只问一层", "一屏只问" in skill)
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
        check("封面用一格表示往返", "<span>北京 → 威海</span>" in htm)
        # 只数速览这一节：天气节也用 .cell，全局数会数多。
        # 注意不能用 htm.index("sec-glance") —— 顶部导航里也有这个字符串，
        # 那样会切出一段空字符串，断言变成空过（这个坑踩过）
        cover = htm[htm.rindex('class="cover"'):htm.rindex('id="sec-glance"')]
        glance = htm[htm.rindex('id="sec-glance"'):htm.rindex('id="sec-weather"')]
        check("速览区间取到了格子", 'class="cell' in glance)
        units = (glance.count('class="cell"')
                 + 2 * glance.count('class="cell half"')
                 + 4 * glance.count('class="cell wide"'))
        check("速览网格行行填满（列宽合计是 4 的倍数）", units % 4 == 0, f"合计 {units} 列")
        # v4.8.0 去重：封面管定位（日期/天数/往返/同行），速览管决策（主题/预算/节奏/专项/约束）
        check("速览不再重复封面的往返与同行",
              "<b>往返</b>" not in glance and "<b>同行</b>" not in glance)
        check("封面不再重复专项标签（专项只在速览与专项节）",
              "<span>观鸟</span>" not in cover and "<span>赶海</span>" not in cover)
        n_cells = glance.count('class="cell')
        check("速览精简到 5 格以内（主题/预算/节奏/专项/约束）",
              n_cells <= 5, f"{n_cells} 格")
        check("速览用定死列数的网格", 'class="grid g-glance"' in htm)
        # v4.8.0 手机端：硬约束拆成小胶囊，不再是一条分号长句
        check("硬约束拆成胶囊", 'class="chip"' in glance)
        # 窄屏适配：2 列 / 1 列两档 + half 格子在单列时整行，CSS 内联进产物
        low = htm.replace(" ", "")
        check("速览 CSS 有两档窄屏规则",
              "@media(max-width:820px)" in low and "@media(max-width:480px)" in low)
        check("单列时 half 格子整行（不空半格）",
              ".grid.g-glance.cell.half{grid-column:1/-1}" in low)
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
                "version", "author", "category"):
        check("frontmatter 有 %s" % key, bool(field(key)), field(key)[:28])

    # 官方 Frontmatter 字段表把 category 标为必填，缺了上传解析会失败
    check("category 填了分类值", bool(field("category").strip()))

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

    # 取真实的赋值行：不能用 split("EXCLUDE_DIRS")[1]，
    # 文档字符串里也会出现这个词，会解析到注释上（原来那条断言就是这样假通过的）
    import re as _re
    m = _re.search(r"^EXCLUDE_DIRS\s*=\s*\{([^}]*)\}", pk, _re.M)
    dirs_line = m.group(1) if m else ""
    check("打包排除了源码目录里的 .workbuddy", '".workbuddy"' in dirs_line)
    # ---- ③ 开发工具不进上传包（与 install.py 的排除口径一致）。
    #         早先 pack.py 漏了 tools/，5 个开发脚本混进了上架包里
    check("打包排除 tools/（与安装口径一致）", '"tools"' in dirs_line)
    check("断言取到的是真实赋值行", "{".join(dirs_line.split("{")[:1]) == dirs_line)

    # ---- ④ 上架包必须符合平台结构「根目录 / 二级目录 / 文件」，
    #         即 skill 目录下最多一层子目录。examples/weihai-2d/x.json 是三层，
    #         所以 examples/ 与 tests/ 都得排除（曾经误以为"平铺分片"就能合规）
    check("打包排除 examples/（三层结构，平台会拒）", '"examples"' in dirs_line)
    check("打包排除 tests/（回归夹具，使用者用不到）", '"tests"' in dirs_line)
    # 包内最深只能一层
    check("打包把层级上限设为 1 层", "MAX_DEPTH = 1" in pk)

    # ---- ⑤ 截图与配图产物必须排除。generated-images/ 有 6.2 MB 的 PNG，
    #         漏排一次就把包顶到 6 MB+，直接超过平台 3 MB 上限（真的发生过）
    check("打包排除 generated-images/（6 MB 配图，漏排除就顶穿体积上限）",
          '"generated-images"' in dirs_line)
    check("打包排除 _shots/（截图产物）", '"_shots"' in dirs_line)

    # ---- ⑥ 排除清单是单一真源：install.py 直接 import pack 的那一份。
    #         两份各写一遍时漂过——pack 排除了 tools/ 而 install 没排
    ins = read(os.path.join("tools", "install.py"))
    check("安装脚本复用打包的排除清单（不再各写一份）",
          "from pack import EXCLUDE_DIRS" in ins)
    check("安装脚本不再自带一份排除清单",
          not _re.search(r"^EXCLUDE_DIRS\s*=", ins, _re.M))
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import pack as _pack
    import install as _install
    check("两处排除清单确实是同一个对象",
          _pack.EXCLUDE_DIRS is _install.EXCLUDE_DIRS)
    check("包内不含任何排除目录",
          not any(seg in _pack.EXCLUDE_DIRS
                  for f in _pack.collect()
                  for seg in os.path.relpath(f, ROOT).split(os.sep)))

    # ---- ⑦ 打包失败时不能留下半成品 zip（用户可能就拿去上传了）
    check("打包自检失败时清掉半成品 zip", "os.remove(zip_path)" in pk)


def test_smoothness():
    """v4.8.0 顺滑度改造看门。

    四件事，每一件都对应一次真实的"卡顿"：
      ① 结构校验的报错必须指到具体分片（否则要在十几个分片里盲翻）
      ② 一条命令只校验一次（build_trip render 与渲染器不重复收费）
      ③ 数据结构文档齐备且被 SKILL.md 引用（字段名写错是"生成成功但渲染空白"的根源）
      ④ 速览在窄屏有 2 列 / 1 列两档，half 格子在单列时不空半格
    """
    import json as _json
    import build_trip as bt
    print("\n用例 11 · 顺滑度（报错定位 / 单次校验 / 数据结构文档 / 窄屏）")

    # ---- ① 造一个「两个分片里 item_id 撞车」的最小方案，看报错落在哪一片
    tmp = tempfile.mkdtemp(prefix="to-smooth-")
    try:
        parts = os.path.join(tmp, "parts")
        os.makedirs(parts)
        head = {
            "trip_id": "smooth", "plan_version": 1, "status": "draft",
            "request": {"origin": {"name": "北京"}, "destination": {"name": "威海"},
                        "dates": {"start": "2026-10-01", "end": "2026-10-02"},
                        "companions": "2 成人",
                        "budget": {"amount_max": 2500, "currency": "CNY", "mode": "total"}},
            "itinerary": {"days+": [], "food": [], "checklist": [],
                          "budget": {"currency": "CNY",
                                     "categories": [{"name": "住宿", "min": 300, "max": 400}],
                                     "total": {"min": 300, "max": 400}}},
        }
        day = lambda i: {"itinerary": {"days+": [{
            "day_id": "D%d" % i, "date": "2026-10-0%d" % i,
            "items": [{"item_id": "same-id", "title": "撞车的条目"}]}]}}
        for name, obj in (("01_request.json", head),
                          ("10_days_D1.json", day(1)), ("11_days_D2.json", day(2))):
            with open(os.path.join(parts, name), "w", encoding="utf-8") as f:
                _json.dump(obj, f, ensure_ascii=False)

        trip, label, n, origin = bt.resolve_trip(tmp, quiet=True, with_origin=True)
        check("分片目录能给出字段来源映射", bool(origin.get("itinerary/days[1]")),
              str(origin.get("itinerary/days[1]")))
        ok, errors, _w = bt.validate(trip, label, quiet=True, origin=origin)
        dup = [e for e in errors if "item_id 重复" in e]
        check("重复 item_id 被拦下", bool(dup))
        check("报错指明是哪个分片",
              bool(dup) and "parts/11_days_D2.json" in dup[0],
              dup[0] if dup else "无")
        # 不带来源映射时行为不变（旧调用点不受影响）
        _ok2, errors_plain, _w2 = bt.validate(trip, label, quiet=True)
        check("不传来源映射时不加定位后缀",
              any(e.endswith("same-id") for e in errors_plain))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ② 校验只有一道门
    import inspect
    sig = inspect.signature(render_plan.write_outputs)
    check("渲染器支持跳过结构门（供已校验过的链路复用）",
          "skip_structure_gate" in sig.parameters)
    src_render = inspect.getsource(bt.cmd_render)
    check("build_trip render 通过后不再二次校验",
          "skip_structure_gate=True" in src_render)

    # ---- ③ 数据结构文档：三个高频坑必须写清，且被 SKILL.md 指到
    with io.open(os.path.join(ROOT, "references", "data-schema.md"), encoding="utf-8") as f:
        schema = f.read()
    check("数据结构文档含 `days+` 追加键坑", "days+" in schema and "覆盖" in schema)
    check("数据结构文档写明 companions 是字符串", "companions" in schema and "字符串" in schema)
    check("数据结构文档写明 item_id 全局唯一", "item_id" in schema and "全局唯一" in schema)
    with io.open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
        skill = f.read()
    check("SKILL.md 指向数据结构文档", "references/data-schema.md" in skill)
    check("S5 处也点了数据结构文档（不只写在 Resources）",
          skill.count("references/data-schema.md") >= 2)
    check("死指针「交付格式」一节已修掉", "「交付格式」" not in skill)

    # ---- ④ SKILL.md 不再内嵌变更史（历史归 CHANGELOG）
    check("SKILL.md 不再内嵌旧版本变更史",
          "v4.0.0 新增" not in skill and "v3.3.0 接口修复" not in skill)
    check("SKILL.md 指向 CHANGELOG", "CHANGELOG.md" in skill)
    # v4.9.0 把文件清单/仓库说明/四档状态/同类对比/S1 协议细节/专项操作手册外迁到 references，
    # 主文件从 419 行降到 295，这里钉住别再长回去
    check("SKILL.md 瘦身到位（< 310 行）",
          len(skill.splitlines()) < 310, "%d 行" % len(skill.splitlines()))
    check("SKILL.md 不再内嵌变更史正文",
          "顺滑度与实用度" not in skill)
    check("文件清单外迁后仍指得回仓库说明", "references/repo-layout.md" in skill)
    check("四档状态表已外迁（主文件只留指针）",
          "| `verified` |" not in skill and "references/data-schema.md" in skill)
    with io.open(os.path.join(ROOT, "references", "data-schema.md"), encoding="utf-8") as f:
        schema6 = f.read()
    check("数据结构文档补上了四档状态契约",
          "| `verified` |" in schema6 and "unknown" in schema6 and "30 天" in schema6)
    with io.open(os.path.join(ROOT, "references", "repo-layout.md"), encoding="utf-8") as f:
        layout = f.read()
    # 口径只有一种说法：「skill 目录下最多 1 层子目录」。早先文档里同时出现过
    # 「三层」「四层」指同一条路径，读者对不上平台规则，所以断言按统一后的口径写。
    check("仓库说明写清 examples/ 为何不进包",
          "最多 1 层子目录" in layout and "examples/" in layout)
    check("仓库说明列了打包排除项", "pack.py" in layout and "examples/" in layout)

    # ---- ⑤ 红方轮次封顶、交付门不再与共识重复
    check("红方轮次封顶（不再无上界重做）",
          "最多 2 轮" in skill and "该重做方案" not in skill)
    check("S8 不再重复确认共识已定的事",
          "别再确认一遍" in skill or "只有共识确认后需求又变过" in skill)


def test_routemap():
    """v4.9.0 地图动线看门。

    用户提的三件事都钉在这里：
      ① 「想看到整个路线图」→ 离线画 SVG 示意图 + 两个跳外部地图的按钮
      ② 「字段名对不上会渲染出空白行」→ 认不出的条目一律不占位
      ③ 「手机端『、』单独顶到第一行」→ 断行改成 break-word + line-break:strict
    """
    import json as _json
    print("\n用例 12 · 地图动线（投影 / 编号一致 / 空白行 / 标点换行）")

    # ---- ① 投影工具的边界：空、单点、重合点都不能崩
    check("空点集投影为空", nw.project_points([]) == [])
    one = nw.project_points([(122.42, 37.16, "A")])
    check("单点投影不除零", len(one) == 1)
    if one:
        x, y = one[0][0], one[0][1]
        check("单点落在画布正中附近",
              0 <= x <= 640 and 0 <= y <= 300, "%.1f,%.1f" % (x, y))
    same = nw.project_points([(122.4, 37.1, "A"), (122.4, 37.1, "B")])
    check("重合点投影到同一位置（重叠交给渲染层摊开）",
          len(same) == 2 and abs(same[0][0] - same[1][0]) < 1e-6)
    far = nw.project_points([(122.0, 37.0, "W"), (122.7, 37.4, "E")])
    check("两个点分居画布两侧（东西方向没搞反）", far[0][0] < far[1][0],
          "%.1f vs %.1f" % (far[0][0], far[1][0]))
    check("纬度大的画得更靠上（北在上）",
          nw.project_points([(122.0, 37.0, "S"), (122.0, 38.0, "N")])[0][1]
          > nw.project_points([(122.0, 37.0, "S"), (122.0, 38.0, "N")])[1][1])

    # ---- ② 跳外部地图的链接拼装
    check("少于两个点不拼路线链接", nw.route_url([(122.4, 37.1, "A")]) == "")
    check("空点集不拼标记链接", nw.markers_url([]) == "")
    r2 = nw.route_url([(122.4, 37.1, "A"), (122.5, 37.2, "B")])
    check("两点给得出路线链接",
          "uri.amap.com/navigation" in r2 and "from=" in r2 and "to=" in r2, r2[:70])
    r4 = nw.route_url([(1.0, 1.0, "A"), (2.0, 2.0, "B"),
                       (3.0, 3.0, "C"), (4.0, 4.0, "D")])
    check("多点只塞一个途经点（接口上限）", r4.count("via=") == 1, r4.count("via="))
    mk = nw.markers_url([(122.4, 37.1, "A"), (122.5, 37.2, "B")])
    check("多点标记链接用 | 连接", "uri.amap.com/marker" in mk and "%7C" in mk, mk[:70])

    # ---- ③ 渲染出的动线区块：图在、链接在、编号与列表对得上
    tmp = tempfile.mkdtemp(prefix="to-route-")
    try:
        out = render_plan.write_outputs(GOOD, out_dir=tmp)
        with open(out["html"], encoding="utf-8") as f:
            htm = f.read()
        with open(out["md"], encoding="utf-8") as f:
            md = f.read()
        check("HTML 有 SVG 动线示意图", 'class="rmap"' in htm and "<polyline" in htm)
        check("示意图带无障碍标签", 'aria-label="行程动线示意图"' in htm)
        check("图上每个点带名称 tooltip", htm.count("<title>") >= 5)
        check("给了「看全部点位」按钮", "uri.amap.com/marker" in htm and "看全部点位" in htm)
        check("给了「规划整条路线」按钮", "uri.amap.com/navigation" in htm and "规划整条路线" in htm)
        check("写明是示意、非导航路径", "非导航路径" in htm)
        check("MD 也有动线一览", "动线一览" in md and "uri.amap.com/marker" in md)

        import re as _re
        mapnums = _re.findall(r'<title>(\d+)\. ', htm)
        rownums = _re.findall(r'<i>(\d+)</i>', htm)
        check("地图标号都出现在下方停靠点里",
              set(mapnums) <= set(rownums), "地图 %s / 列表 %s" % (mapnums, rownums))
        check("停靠点按天去重且跨天连续编号",
              rownums == [str(i) for i in range(1, len(rownums) + 1)], str(rownums))
        # 重合点必须被摊开，否则两个数字叠在一起看不清。
        # 只在动线图那一段里数圆点——星图、潮汐图也有 <circle>，别把它们算进来。
        seg = htm[htm.find('class="rmap"'):]
        seg = seg[:seg.find("</svg>")]
        cxys = _re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)"', seg)
        check("序号点没有完全重合（重合点已摊开）",
              len(set(cxys)) == len(cxys) and len(cxys) >= 5,
              "%d 个点 / %d 个位置" % (len(cxys), len(set(cxys))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ④ 字段名写错的条目不得渲染成空白行
    tmp2 = tempfile.mkdtemp(prefix="to-blank-")
    try:
        trip = {
            "trip_id": "blank", "plan_version": 1, "status": "draft",
            "request": {"origin": {"name": "北京"}, "destination": {"name": "威海"},
                        "dates": {"start": "2026-10-01", "end": "2026-10-01"},
                        "companions": "2 成人",
                        "budget": {"amount_max": 2000, "currency": "CNY", "mode": "total"}},
            "itinerary": {
                "days": [{"day_id": "D1", "date": "2026-10-01", "items": [
                    {"titulo": "字段名全写错了", "local": "不认识的键"},
                    {"item_id": "ok", "time": "09:00", "title": "正常条目",
                     "kind": "visit", "place": "成山头景区"},
                ]}],
                "food": [{"nome": "只写了外文键"}, {"name": "正常店", "price": "80"}],
                "checklist": [], "budget": {"currency": "CNY", "categories": []},
            },
        }
        p = os.path.join(tmp2, "blank.json")
        with open(p, "w", encoding="utf-8") as f:
            _json.dump(trip, f, ensure_ascii=False)
        with open(render_plan.write_outputs(p, out_dir=tmp2,
                                            skip_structure_gate=True)["html"],
                  encoding="utf-8") as f:
            h2 = f.read()
        check("认不出的条目不留空 slot", '<div class="slot"></div>' not in h2)
        check("认不出的条目不留空标题", '<div class="ti"></div>' not in h2)
        check("认不出的条目整条消失（只渲染认识的那条）",
              h2.count('class="slot"') == 1, h2.count('class="slot"'))
        check("无名美食行不留空 <tr>", "<tr><td></td>" not in h2)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ---- ⑤ 标点不再被断到行首
    with io.open(os.path.join(ROOT, "assets", "handbook.css"), encoding="utf-8") as f:
        css = render_plan._strip_css_comments(f.read())   # 注释里提到关键字不算数
    block = css[css.find(".grid .cell span"):]
    block = block[:block.find("}") + 1]
    check("速览格子不再用 overflow-wrap:anywhere（会把标点顶到行首）",
          "anywhere" not in block, block.strip()[:80])
    check("速览格子改用 line-break:strict", "line-break:strict" in block)
    fcss = render_plan.FALLBACK_CSS
    fb = fcss[fcss.find(".grid .cell span"):]
    fb = fb[:fb.find("}") + 1]
    check("兜底样式同步修正", "anywhere" not in fb and "line-break:strict" in fb)


def test_hardening():
    """v4.10.0 坏数据防线（红方红队报告里的阻断/严重项逐条回归）。

    起因：`validate()` 只检查「容器是 list」，元素写成字符串照样放行，
    一路走到 `x.get("name")` 才抛 AttributeError —— 用户看到 traceback 而不是
    「哪个字段写错了」。同一批问题还有：geo 写成字符串、坐标是 nan/inf（能画进
    SVG 变成 cy="nan"）、地名带 `&` 把 URL 参数切断。这里逐条钉住。
    """
    import json as _json
    import build_trip as bt

    # ---- ① 元素级类型校验：元素写成字符串必须报错，且指到具体分片
    bad = {
        "trip_id": "hard", "plan_version": 1, "status": "draft",
        "request": {"origin": {"name": "北京"}, "destination": {"name": "威海"},
                    "dates": {"start": "2026-10-01", "end": "2026-10-01"},
                    "companions": "2 成人",
                    "interests": {"must": ["只写了字符串的专项"]},
                    "budget": {"amount_max": 2000, "currency": "CNY", "mode": "total"}},
        "itinerary": {
            "days": [{"day_id": "D1", "date": "2026-10-01", "items": []}],
            "food": ["这不是对象"], "checklist": [123],
            "budget": {"currency": "CNY", "categories": []},
        },
        "sources": ["也不是对象"],
    }
    ok, errs, _w = bt.validate(bad, "bad.json", quiet=True)
    joined = " ".join(errs)
    check("元素不是对象时校验不通过", not ok)
    check("校验指出是 food 的元素写错了", "itinerary.food[0]" in joined, joined[:90])
    check("校验指出是 checklist 的元素写错了", "itinerary.checklist[0]" in joined)
    check("校验指出是 sources 的元素写错了", "sources[0]" in joined)
    check("校验指出是 interests.must 的元素写错了",
          "request.interests.must[0]" in joined)

    # ---- ② 渲染器不再裸崩：同一份坏数据要么渲出来、要么退出码 4 + 人话
    # ---- ② 红方复核同样不许崩：坏数据下"挑刺"不能变成"报错"
    rc_r = red_team.run(bad)
    check("红方复核遇到坏数据不崩（照常出结论）",
          "findings" in rc_r and rc_r.get("verdict"), rc_r.get("verdict"))
    check("红方不再把 nan 预算当合规（比较恒 False 会静默放过）",
          red_team.as_num(float("nan")) is None
          and red_team.as_num(float("inf")) is None)

    tmp = tempfile.mkdtemp(prefix="to-hard-")
    try:
        p = os.path.join(tmp, "hard.json")
        with io.open(p, "w", encoding="utf-8") as f:
            _json.dump(bad, f, ensure_ascii=False)
        rc = render_plan.main([p, "--out-dir", tmp])
        # 结构门会拦住 → 退出码 3（"拒绝渲染"），不是 traceback、也不是 0
        check("坏数据被结构门拦住（退出码 3，不是 traceback）", rc == 3, rc)
        # 强制渲染：渲染器自己也不该崩
        rc2 = render_plan.main([p, "--out-dir", tmp, "--force"])
        check("--force 渲染坏数据也不崩（滤掉坏元素即可）", rc2 == 0, rc2)
        r = render_plan.write_outputs(p, out_dir=tmp, skip_structure_gate=True)
        check("绕过结构门时渲染器自己也不崩（不假设上游校验过）",
              os.path.getsize(r["html"]) > 0 and os.path.getsize(r["md"]) > 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ③ geo 写成字符串 / 数组时不得把动线图打崩
    it = {"days": [{"day_id": "D1", "date": "2026-10-01", "items": [
        {"title": "甲", "place": "成山头", "geo": "122.1,37.5"},
        {"title": "乙", "place": "樱花湖", "geo": [122.2, 37.4]},
    ]}]}
    check("geo 写成字符串不崩（当没坐标）", render_plan._flow(it) != "")
    check("geo 写成字符串时不给路线按钮",
          "规划整条路线" not in render_plan._flow(it))
    it["days"][0]["items"][0]["geo"] = {"lat": "x", "lng": None}
    check("geo 里坐标取不到时不崩", render_plan._flow(it) != "")

    # ---- ④ nan / inf 不许进 SVG，也不许进预算条
    check("nan 被 nw.num 挡掉", nw.num("nan") is None)
    check("inf 被 nw.num 挡掉", nw.num(float("inf")) is None)
    check("超大整数（转 float 会溢出）也被挡掉", nw.num(10 ** 400) is None)
    nan_it = {"days": [{"day_id": "D1", "date": "2026-10-01", "items": [
        {"title": "甲", "place": "成山头", "geo": {"lat": float("nan"), "lng": 122.1}},
        {"title": "乙", "place": "樱花湖", "geo": {"lat": 37.4, "lng": 122.2}},
    ]}]}
    check("坐标是 nan 时图上不出现 nan", "nan" not in render_plan._flow(nan_it))
    nan_budget = render_plan._budget(
        {"budget": {"currency": "CNY", "categories": [
            {"name": "住宿", "min": float("nan"), "max": float("inf")},
            {"name": "餐饮", "min": 100, "max": 200}]}},
        {"budget": {"amount_max": 800}})
    check("预算条不出现 nan/inf 宽度", "nan" not in nan_budget and "inf" not in nan_budget)

    # ---- ⑤ 地名里的结构分隔符不许把 URL 参数切断
    from urllib.parse import parse_qs, urlparse
    u = nw.route_url([(122.1, 37.5, "A&B 渔港"), (122.2, 37.4, "C,D 码头")])
    qs = parse_qs(urlparse(u).query)
    check("起点名里的 & 不再截断参数（起点名还在）",
          qs.get("from", [""])[0].endswith("A B 渔港"), qs.get("from"))
    check("终点名里的 , 不再截断参数",
          qs.get("to", [""])[0].endswith("C D 码头"), qs.get("to"))
    mk = nw.markers_url([(122.1, 37.5, "A|B"), (122.2, 37.4, "C")])
    check("多点标记里的 | 不再被当成第二段的分隔", mk.count("%7C") == 1, mk.count("%7C"))

    # ---- ⑥ 搜索词只有一处口径：所有区块都剥括号附注
    check("map_url 默认剥括号附注",
          "%E6%88%90%E5%B1%B1%E5%A4%B4" in nw.map_url("成山头（朝东无遮挡）"))
    check("map_url 不再把括号带进搜索词",
          "%EF%BC%88" not in nw.map_url("成山头（朝东无遮挡）"))

    # ---- ⑦ 标记点数上限只有一处真源
    many = [(120.0 + i / 100.0, 36.0, "P%d" % i) for i in range(40)]
    check("markers_url 默认截到 MARKER_LIMIT",
          nw.markers_url(many).count("%7C") == nw.MARKER_LIMIT - 1,
          nw.markers_url(many).count("%7C"))

    # ---- ⑧ 两产物一起落盘：MD 与 HTML 不会一新一旧
    tmp2 = tempfile.mkdtemp(prefix="to-pair-")
    try:
        out = render_plan.write_outputs(GOOD, out_dir=tmp2)
        check("MD 与 HTML 同时存在且非空",
              os.path.getsize(out["md"]) > 0 and os.path.getsize(out["html"]) > 0)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)


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
    test_smoothness()
    test_routemap()
    test_hardening()
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
