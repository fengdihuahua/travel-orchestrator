# 自然观察专项 · 执行手册

`SKILL.md` 只留触发规则和两条最常踩的坑。命令、降级规则、渲染层字段表都在这里。
触发时机：S1 访谈 L3 的一个环节，不是单独的问卷。

## 可行性三档

拿到人和日期后先跑一遍核查（`nature_forecast.py`），再照实说。

- 条件好：直接问做不做。
- 条件一般，比如月照强、潮位一般、鸟况平：说清代价，再问值不值得专门排。
- 做不了，比如鸟种不在季、目标在地平线下、没有潮汐窗口、不是流星雨季：直说做不了，给替代。

用户不感兴趣的项跳过，不影响主流程。结论并入 `facts`。
可行性数据一次并行查完（`tide`/`astro`/`sun`/`glow` 互相独立）。每个专项最多问 2 问。

## 五个命令

| 专项 | 命令 | 要点 |
|---|---|---|
| 观鸟 | `nature_forecast.py bird --city 城市`、`birdspot` 查 25km 内热点 | 全程免注册：iNaturalist 主源，失败回落 GBIF。识别鸟用 BirdNET，无录音也能 `predict(纬,经,week)` |
| 观星 | `astro --lat .. --lng ..` | 输出月相照度、暗夜窗口、流星雨和逐日评分。月照超过 50% 不排深空 |
| 日出日落 | `sun --lat .. --lng .. --date ..` | 精确到分钟，含黄金、蓝调、晨昏蒙影。交付页要写机位朝向和占位时间 |
| 赶海 | `tide --lat .. --lng .. --date ..` | 推荐窗口是干潮前后 1.5 小时。潮位是 MSL 基准、有效期约 10 天；脚本标 `partial` 时必须对照官方潮汐表 |
| 朝晚霞 | `glow --lat .. --lng .. --event set\|rise` | 自建 0-3 级评分。参考值不是官方预报，交付时要标注 |

算法、降级规则、已停用清单见 `references/nature-apis.md`。鸟种季节、波特尔分级、滩涂类型见 `nature-knowledge.md`。

## 渲染层要的字段

攻略页上每一项都是可点的交互件，纯 CSS 无脚本。

| 专项 | 交互件 | 字段 |
|---|---|---|
| 观鸟 | 鸟种卡：学名、科属、保护等级、简介、图鉴外链 | `species[]` 写成对象，含 `sci`/`family`/`status[]`/`intro`/`intro_source`/`best_months`/`taxa_url` |
| 观星 | 示意星图，真算地平坐标，带月相盘和「今晚看什么」 | `chart{lat,lng,tz,at,place}`、`targets[]`、`spots` |
| 日出日落 | 天光条加霞光指数点阵 | `sunrise`/`sunset`/`golden_hour`/`blue_hour`；霞光写进 `nature.glow` |
| 赶海 | 潮汐图点选，点圆点换该时刻潮位和下滩建议 | `curve[{t,h}]`、`extremes[{type,time,height_m}]`、`best_window`/`unit`/`source_id` |
| 动线 | 逐日停靠点连成 SVG，可跳高德看全部点位 | 每个停靠点给 `geo{lng,lat}`，用高德坐标系 |

四件事：

- 地点字段（`hotspots`/`spots`/`best_spot`）写成 `[{name, note}]` 或 `["A","B"]`，渲染时变成可点的高德搜索链接。
- `species[]` 里的纯字符串只写鸟名，说明写进 `nature.birding.notes`。渲染层按首个标点拆句子，拆出来的鸟名未必对。
- 潮汐图只画接口真实给到的时段，没数据的不补线。星图要写明是「按 X 坐标 Y 时刻算的示意星图，未做大气折射与光污染修正」。
- 价格不写死。要订的门票、住宿、车票在数据里给 `booking{kind,keyword}`，渲染成搜索入口，让用户看当天报价。
