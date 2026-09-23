# 使用文档

`README.md` 讲这个 Skill 是什么，这份文档讲怎么用它、怎么改它。

---

## 1. 它怎么工作

主流程十个模块，加上一个交付前的独立质量门：

```
S1  需求访谈 ⭐        一次一问逐层拷问（意图→硬约束→口味→自然专项→底线），先出「需求共识」
S2  灵感与目的地推荐    没定目的地时给 3-5 个有真实差异的候选
S3  实时调研          门票、预约、交通、政策，逐条记来源与日期
S4  天气研判          规划期气候 + 出发前逐日预报
S5  行程编排          八步规划：先锁专项时刻，再排其他
     └ 美食推荐 · 预算明细（六分项）
S6  核查与修正        规则引擎扫机械错误
S6.5 红方复核 ⭐       质疑方逐项挑刺，阻断项清零才放行
S7  待办提醒 + 踩坑指南
S8  成品交付          同源渲染 MD + 离线 HTML 攻略页
S9  出发前复核        重查过期信息，输出核对清单
S10 修改与版本存档     保留旧版本，可回退比较
```

**自然观察专项（观鸟 / 观星 / 日出 / 赶海，火烧云评分并入日出卡）嵌在 S1 访谈的 L3 环节里**：
拿到人和日期后**先跑一遍可行性核查**，再按三档如实告诉用户：条件好就问做不做、
条件一般就说清代价、**做不到就直说做不到并给替代**（目标鸟种不在季节、观测目标当天在地平线下、
那天没有潮汐窗口）。用户没兴趣的项跳过，不影响主流程。

---

## 2. 三种用法

### 2.1 对话（主要用法）

直接说需求。**它不会一次抛一串问题，而是像面试一样一次问一个**：每题给 2-4 个具体选项
（推荐项排第一，附上理由和代价），你点一下或自己写。能自己查到的事实（日出时刻、潮汐、月相、
票价、车次）它先去查，**把查到的数字嵌进问题里再问你**，所以问的是"04:30 出门你能接受吗"
而不是"你想几点起床"。

> **你**：帮我规划 11 月中旬去婺源，3 天，两个大人，预算 4000，想拍日出和星空。
>
> **Skill**：（先查到 11 月中旬婺源日出 06:33、满月落在 11/5）你那个日期月亮约 85% 亮，
> 银河基本没戏。
> **这趟最想带回来的是什么？**
> - 「一张晨雾里的石城日出」（推荐），住宿就锁在石城村里，省掉每天凌晨一小时盘山路
> - 「星空银河」，那得挪日期躲开满月，或者接受只看月亮和亮星
> - 「都不执念，就是走走」，那按普通秋游排，日出随缘

问的顺序是固定的五层：意图（为谁、为了什么）→ 硬约束（日期窗、同行人结构、预算口径、出发地）
→ 玩法口味（节奏、作息、钱花在哪）→ **自然观察专项**（先查可行性再问）→ 风险与底线
（安全底线、天气备选）。正常 8-12 个问题收敛。

访谈结束会给一段**「需求共识」**（主题、日期、同行、预算口径、必玩项、硬约束、每个专项的
可行性结论、我替你定的默认值）让你确认，**确认之后才开始排行程**。这时如果你发现哪个答案不对，
直接说，改完它会连带改掉受影响的排期和预算。

如果只说"帮我做份旅行方案"而没有目的地，它会先给候选：

> 想找个能看银河的地方，3 天左右。

赶时间的话说一句"十分钟给我个框架"，它会先给目的地/天数/预算档/节奏的框架，
细节标成默认值，再回头补访谈。

### 2.2 命令行

```bash
P="scripts"   # 在 skill 目录下执行

# 实时数据
python $P/nature_forecast.py tide --city 威海 --date 2026-10-01
python $P/nature_forecast.py sun  --city 威海 --date 2026-10-01
python $P/nature_forecast.py astro --lat 37.5091 --lng 122.1136
python $P/nature_forecast.py glow --city 威海 --event set
python $P/nature_forecast.py bird --region CN-37 --token $EBIRD_TOKEN
python $P/nature_forecast.py birdspot --city 威海 --token $EBIRD_TOKEN
python $P/nature_forecast.py selftest          # 一键接口自检

# 行程数据分片（数据以 parts/*.json 为常态）
python $P/build_trip.py skeleton travel-plan/<trip_id> --start 2026-10-01 --days 2
python $P/build_trip.py validate travel-plan/<trip_id>       # 目录或 trip.json 都行
python $P/build_trip.py render   travel-plan/<trip_id>       # 合并+校验+渲染一条龙
python $P/build_trip.py split    <旧的大 trip.json>          # 迁移：拆成分片（自动校验等值）
python $P/build_trip.py assemble travel-plan/<trip_id>       # 只在需要独立大 JSON 时用

# 红方复核
python $P/red_team.py check travel-plan/<trip_id>
python $P/red_team.py check <目录或 trip.json> --json
python $P/red_team.py rules                    # 列出全部规则

# 渲染
python $P/render_plan.py travel-plan/<trip_id>
python $P/render_plan.py <目录或 trip.json> --out-dir <目录>
python $P/render_plan.py <目录或 trip.json> --check   # 只做一致性检查
```

退出码约定：`red_team.py` 有阻断项时返回 1（可直接串进 CI）；
`render_plan.py` 结构校验不通过返回 3、读取失败返回 2；3 表示「数据没写对」，不是渲染器坏了。

### 2.3 把它当库用

```python
import sys
sys.path.insert(0, "scripts")
import red_team, render_plan

report = red_team.run(plan_dict)              # -> {findings, summary, verdict}
render_plan.write_outputs("travel-plan/<id>")  # 目录或 trip.json，-> 生成 md + html
render_plan.write_outputs("travel-plan/<id>", force=True)  # 结构不过也强出（排障用）
```

---

## 3. 行程数据结构（分片）

单一业务数据源。MD 和 HTML 都是它的表达层，不各自维护内容。

**数据以分片形态存在**：按文件名顺序合并，每片几 KB（写不坏），
渲染 / 复核 / 校验都直接读目录就地合并，不落中间大文件。
分片放哪都行，两种形态都认（`examples/weihai-2d/` 用的是下面第二种）：

```text
travel-plan/<trip_id>/                travel-plan/<trip_id>/
├── parts/                       或    ├── 01_request.json   基本信息 + 访谈结论
│   ├── 01_request.json               ├── 02_weather.json   天气
│   ├── 02_weather.json               ├── 03_nature.json    自然专项
│   ├── 03_nature.json                ├── 10_days_D1.json  逐日行程（一天一片，days+ 追加）
│   ├── 10_days_D1.json               ├── 11_days_D2.json
│   ├── 11_days_D2.json               ├── 20_food.json 21_budget.json 22_checklist.json
│   ├── 20_food.json …                ├── 23_pitfalls.json 24_facts.json 25_sources.json
│   └── 26_red_team.json …            └── 26_red_team.json 27_decisions.json
├── <trip_id>_vN.md                   ├── <trip_id>_vN.md   渲染产物（不要手改）
└── <trip_id>_vN.html                 └── <trip_id>_vN.html
```

下面这份结构说明写的是**合并后**的形态，也就是分片叠起来的样子。

列表追加的写法：`{"itinerary": {"days+": [ ...D1... ]}}` → 合并成 `itinerary.days`。
键名不带 `+` 就是覆盖。`assemble` 出来的 `trip.json` 是产物，不是数据源。

```jsonc
{
  "trip_id": "2026-10-01-weihai-2d",
  "plan_version": 2,
  "status": "conditional",            // draft | conditional | executable_as_of_check
  "status_note": "门票和车票未落实……",  // 标 conditional 时要说清在等什么

  // S1 访谈的结论。每个字段都由访谈里的某一个问题得来，不是默认值
  "request": {
    "origin": {"name": "北京"},
    "dates": {"start": "2026-10-01", "end": "2026-10-02"},
    "companions": "2 成人",
    "budget": {"amount_max": 2500, "currency": "CNY", "mode": "total"},
    "pace": "轻松休闲（D1 例外早起）",
    "why": "两天时间，重点放海边的日出、赶海和观鸟；景点数量不重要。",
    "interests": {"must": [{"label": "赶海"}], "prefer": ["观星", "日出"]},
    "constraints": ["不吃辣，海鲜以清蒸白灼为主", "回程必须 D2 晚上到北京"],
    "taste": {"nature": 5, "food": 4, "sightseeing": 2, "photo": 3},
    "risk": {"weather_backup": "必须有", "safety_bottom_line": "按窗口撤离，不追退潮"},
    "nature_caps": {"gear": ["双筒望远镜 8x42", "手机（无长焦）"],
                    "experience": {"birding": "入门", "astro": "能认北极星"},
                    "earliest_departure": "04:30", "latest_return": "23:00"}
  },

  // S1 访谈的问答流水。过程记录：供 S10 改方案时回看"当初是怎么定的"，
  // 与红方复核同理，不渲染进交付物
  "decisions": [
    {"decision_id": "dec-4",
     "question": "10/1 干潮 06:08，赶海窗口 04:38-07:38，意味着 04:30 出门 —— 接受吗？",
     "choice": "接受，两天里就这一次",
     "reason": "窗口由潮汐决定，不是想改就能改",
     "pushed_back": "提醒过 04:30 出门=3:50 起床，10 月海边清晨 10℃ 以下"}
  ],

  "itinerary": {
    "weather": {"kind": "forecast", "entries": [{"date": "...", "summary": "☀️ 16-22℃", "source_id": "s5"}]},
    "nature": {
      // 观鸟：species 写成对象数组，渲染器会生成可展开的鸟种卡
      "birding": {
        "species": [{"name": "黑嘴鸥", "sci": "Saundersilarus saundersi",
                     "status": "VU", "best_months": "10-3 月", "blurb": "", "source_id": "s6"}],
        "hotspots": "桑沟湾滩涂", "best_time": "退潮后 1 小时内", "notes": ""
      },
      // 观星：chart 是真实算星图的输入（纬度/经度/时区/时刻）
      "stargazing": {
        "moon_phase": "亏凸月 · 月照 82%", "moon_illum": 82, "moon_waxing": false,
        "dark_window": "22:40-04:30", "planets": [], "rating": "★★☆", "events": [],
        "chart": {"lat": 37.5091, "lng": 122.1136, "tz": 8,
                  "at": "2026-10-01 21:30", "place": "樱花湖东岸"},
        "targets": [{"name": "M31 仙女座星系", "note": "高挂天顶，肉眼可见核心"}]
      },
      // 日出：太阳轨迹带 + 火烧云（glow）
      "sunrise": {"sunrise": "05:45", "sunset": "17:37", "golden_hour": "05:20-06:10",
                  "blue_hour": "04:55-05:20", "best_spot": "樱花湖东岸"},
      // 赶海：curve 逐时潮高，渲染器画出可点选的潮汐曲线
      "beachcombing": {
        "tide_summary": "", "best_window": "04:38-07:38", "tide_phase": "大潮",
        "spots": "桑沟湾滩涂", "safety": "对照官方潮汐表",
        "curve": [{"time": "04:00", "height": -0.52}],
        "extremes": [{"time": "06:08", "height": -0.71, "kind": "low"}]
      },
      // 火烧云：0-3 分制，带成因与观察输入
      "glow": {"score": 2, "level": "较好",
               "reasons": ["中高云量适中"], "inputs": ["日落前 40 分钟云量 30-60%"]}
    },
    "days": [{
      "day_id": "D1", "date": "2026-10-01", "weekday": "周四", "title": "...",
      "items": [{"item_id": "d1-tide", "time": "04:30-07:20", "title": "赶海（干潮 06:08）",
                 "kind": "nature", "place": "桑沟湾滩涂",
                 "booking_status": "not_required", "notes": "07:20 开始往岸上撤"}],
      "alternatives": [{"title": "下雨改室内", "trigger": "日降水 > 5mm"}]
    }],
    "food": [{"name": "", "price": "¥120-180/人", "why": "", "note": ""}],
    "budget": {"currency": "CNY", "categories": [{"name": "大交通", "min": 700, "max": 900}]},
    "checklist": [{"task": "", "priority": "must", "status": "todo",
                   "deadline": "9/28 前", "if_unresolved": "改在樱花湖东岸看日出"}]
  },

  "pitfalls": [{"title": "", "detail": "", "when": "", "how": "", "level": "high"}],
  "facts":   [{"fact_id": "f1", "claim": "", "value": "", "status": "verified", "source_ids": ["s1"]}],
  "sources": [{"source_id": "s1", "name": "", "kind": "api", "url": "", "retrieved_at": "2026-09-22"}],
  "red_team": {"verdict": "通过", "open_blocking": 0, "rounds": []}
}
```

字段要点：

- `time` 用 `HH:MM-HH:MM` 或单点 `HH:MM`。**跨零点不要写 `23:00-24:30`**，
  要么拆两项，要么写 `23:00-00:30` 并标注跨日，规则会把它判为无效时间窗。
- `item_id` 全局唯一，版本比对靠它。
- `booking_status`：`confirmed` / `not_required` / `todo` / `unavailable`。
- `level`（pitfalls）：`high` / `medium`。
- `kind`（sources）：`api` / `official_site` / `web_search` / `model_estimate` / `user_input`。
  只有前三类会被时效性检查覆盖。

自然专项里几个**给交互件用的字段**，缺了不影响红方复核，但对应控件会降级：

| 字段 | 谁在用 | 缺了会怎样 |
|---|---|---|
| `birding.species[]` 写成 `{name, sci, status, ...}` | 鸟种卡 | 退化成一行纯文字药丸，不能展开 |
| `stargazing.chart{lat,lng,tz,at}` | 实时星图 + 月相盘 | 星图整块不渲染，只留文字要点 |
| `stargazing.targets[]` | 目标天体贴片 | 只显示 `planets` |
| `beachcombing.curve[]` + `extremes[]` | 潮汐点选图 | 退化成 `tide_summary` 一句话 |
| `glow{score,level,reasons,inputs}` | 火烧云指数表 | 只留 `rating` 文字 |

所有专项卡里的**地点名**（`place` / `spots` / `best_spot` / `hotspots`）都会变成
高德搜索链接；括号里的补充说明（如「（朝东无遮挡）」）不会进搜索词。

---

## 4. 红方复核

### 4.1 它是什么

规则引擎只能抓"时间重叠""预算超支"这种机械错误。
**"看着挺合理但其实不行"的问题，只有换个立场才看得出来。**

所以交付前由红方（质疑方）按六轮清单再过一遍：
**时间 → 地理 → 成本 → 安全 → 事实 → 体感**。清单在
`references/red-team-checklist.md`。

三条纪律：

1. 不许说"基本没问题"，挑不出就明确写"本轮无发现"
2. 每个质疑必须落到具体某一行，并给出证据
3. 质疑必须带替代动作，只提问题不给解法等于甩锅

安全相关**一律升为阻断级**。判断标准：出错的后果是"玩得不爽"还是"人回不来"。

### 4.2 规则清单（23 条）

```bash
python scripts/red_team.py rules
```

| 编号 | 级别 | 类别 | 检查什么 |
|---|---|---|---|
| RT-S01 | 提醒 | 结构 | 日期与标注的星期不符 |
| RT-S02 | 阻断 | 结构 | item_id 重复 |
| RT-S03 | 严重 | 结构 | 同一天行程时间窗相互重叠 |
| RT-S04 | 严重 | 结构 | 时间窗无效（结束早于开始或跨日） |
| RT-S05 | 严重 | 结构 | 预算分项 min > max |
| RT-S06 | 严重 | 预算 | 预算合计上限超出用户给的预算 |
| RT-S07 | 阻断 | 结构 | 缺少必填字段（日期/同行人） |
| RT-S08 | 提醒 | 结构 | 天气条目数与行程天数不一致 |
| RT-F01 | 严重 | 溯源 | 结论标了 verified 却没有来源 |
| RT-F02 | 严重 | 溯源 | 结论引用了不存在的来源 ID |
| RT-F03 | 提醒 | 溯源 | 来源缺少名称或取回日期 |
| RT-F04 | 严重 | 溯源 | 实时数据的来源已过期（超 30 天） |
| RT-F05 | 严重 | 溯源 | 值为 unknown 却写了具体数值 |
| RT-F06 | 提醒 | 溯源 | 自然专项数据没有对应结论可溯源 |
| RT-L01 | 阻断 | **安全** | 赶海窗口与干潮时刻对不上 |
| RT-L02 | 严重 | 逻辑 | 日出/日落活动时刻与天文时刻矛盾 |
| RT-L03 | 严重 | 逻辑 | 满月夜安排了观星且未提示 |
| RT-L04 | 阻断 | **安全** | 赶海窗口落在涨潮段 |
| RT-L05 | 提醒 | 逻辑 | 凌晨专项与次日清晨专项间隔不足 5 小时 |
| RT-L06 | 提醒 | 逻辑 | 主要景点没有雨天/满员备选 |
| RT-L07 | 提醒 | 结构 | 方案标了 conditional 但没写清条件 |
| RT-L08 | 提醒 | 逻辑 | 踩坑指南为空 |
| RT-X01 | 说明 | 复核 | 未执行红方复核（缺 red_team 字段） |

两条关于光线的规则值得单独说：

- **RT-L02**：会去比对方案里的"日出活动"时刻和 `nature.sunrise.sunrise`。
  活动窗必须覆盖日出前后各 1 小时，否则报错。
- **RT-L03**：月照超过 50% 还排观星就报警，**除非**你已经在标题或备注里
  写明改看"月面/亮星团"，或把深空目标挪到月落之后。这种"知情的取舍"是被允许的，
  红方只拦"没意识到问题"的情况。

### 4.3 复核记录写回

红方结论写进 `red_team`（即 `parts/*_red_team.json`），供 S10 改方案时回看当初为什么这么排。
**复核是生成过程，不渲染进交付物**（与 S1 访谈的问答流水同理）：读者要的是行程，不是挑刺记录。
复核中没解决、而读者必须知道的风险，改写进「踩坑指南」。

```json
"red_team": {
  "reviewer": "red-team",
  "verdict": "有条件通过",
  "open_blocking": 0,
  "rounds": [{
    "round": 1,
    "scope": "时间/地理/成本/安全/事实/体感",
    "findings": [{
      "finding_id": "rt-001",
      "severity": "blocking",          // blocking | major | minor | info
      "category": "safety",
      "target": "D1 赶海 08:30-10:30",
      "challenge": "干潮 06:08，窗口整段错位，出动时潮水已回涨",
      "evidence": "Open-Meteo Marine，2026-09-22 取数",
      "action": "窗口改为 04:38-07:38，并写明 07:20 开始撤离",
      "status": "resolved"             // resolved | accepted_risk | open
    }]
  }]
}
```

`open_blocking` 必须为 0，方案才能标 `executable_as_of_check`。
一般跑两轮就够：第一轮挑问题，第二轮确认没引入新问题。
第三轮还能源源不断挑出阻断项，说明方案该重做。

---

## 5. 反幻觉溯源

### 5.1 结论状态四档

| 状态 | 含义 | 能不能当确定值写进正文 |
|---|---|---|
| `verified` | 有权威来源且已核对 | 可以 |
| `estimated` | 有依据的推算，或数据是部分完整的 | 可以，但要标"估算/部分数据" |
| `unverified` | 有说法但没核对 | 可以，但要标"未核实" |
| `unknown` | 查不到 | 不可以。正文写"未查到"，`value` 必须留空 |

`unknown` 却填了数值 → RT-F05 直接报严重。

### 5.2 时效性

潮汐、日出、月相、天气这四类一天一变。来源类型为 `api` / `official_site` / `web_search`
且 `retrieved_at` 超过 **30 天**，RT-F04 报警：

```
[严重] RT-F04 结论 f2
      依据的 Open-Meteo Marine API 是 45 天前取的，实时数据早已过期。
      建议：重新取数后再交付，或在方案里标注已过期。
```

### 5.3 依据索引

交付物最后一节会列出全部结论与来源。这是"交底"，用户能自己判断哪条可信、
哪条存疑、哪条是估的。

```
| 编号 | 结论 | 状态 | 来源 | 取数日期 |
| f1 | 威海 2026-10-01 日出 05:45 | 已核实 | Sunrise-Sunset.org API | 2026-09-22 |
| f2 | 干潮 06:08（-0.71m），部分数据 | 估算 | Open-Meteo Marine API | 2026-09-22 |
```

---

## 6. 交付物

### 6.1 章节顺序（固定）

```
一页速览 → 天气研判 → 自然观察专项 → 逐日行程
→ 美食推荐 → 预算明细 → 出发前待办 → 踩坑指南
→ 依据索引（共 9 节）
```

顺序的逻辑：**先 30 秒看懂全局，再给可执行细节，最后交底。**
不把长声明放开头，也不把来源堆在最前面，那是交底不是开场。

### 6.2 HTML 攻略页

单文件、离线、无脚本、无 CDN，双击就开。包含：

- 粉→紫→蓝渐变封面（日期、天数、同行、专项一目了然），右上角贴几张小贴纸，底部一条横向飘过的**弹幕条**，写的是方案里最该先看到的事实
- 速览卡片、天气卡、**自然专项整行卡**（左侧场景动效 / 右侧要点两列），每节标题配一块描边小图标 + 一枚小贴图。
  四个专项各配一个**纯 CSS 交互件**（无脚本）：
  - 观鸟 → **鸟种卡**：点一下展开该鸟的剪影、分类、保护等级、最佳月份、简介、来源，
    外加「查图 / 详情 / 本地记录 / eBird 分布」四个外链
  - 观星 → **实时星图**：按 `chart` 的经纬度与时刻真算地平坐标，
    画北极星、北斗、仙后、夏季大三角等连线，只画地平线以上的星，带高度/方位浮签；旁边一枚**月相盘**
  - 日出日落 → **太阳轨迹带**：蓝调 / 黄金 / 白昼 / 夜间四段的色带 + 日出日落刻度 + 图例，下方**火烧云指数**（0-3 点 + 成因 + 观察输入）
  - 赶海 → **潮汐点选图**：潮高曲线 + 可作业窗口带 + 若干时间点，点任一点切换该时刻的潮高、趋势与出滩建议
- **逐日行程**：顶部日期锚点条 + 每天一张可折叠卡（默认展开，summary 显示条目数），条目带类型标签
- 预算区间条、可勾选待办、踩坑警示卡、依据索引表（窄列不折行，来源清单默认折叠）
- **可点**：顶部导航跳章节、日期锚点跳某天、每天折叠展开、**地点跳地图**、**来源网址打开出处**、待办勾选、右下角返回顶部（全部 HTML + CSS，无脚本）
- **会动**：满页会闪的小星星、封面珠光星点、小贴纸轻浮；观星有星光闪烁、赶海有海浪推移、观鸟有飞鸟掠过、日出有太阳与朝霞；系统开了「减少动态效果」就自动静止
- **打印样式**：直接 Ctrl+P 就是一份可读的纸质攻略（贴纸、星点、折叠按钮自动隐藏，折叠内容自动展开）

### 6.3 一致性检查

```bash
python scripts/render_plan.py <目录或 trip.json> --check
```

检查产物是否存在、是否有署名、是否含脚本或远程资源加载、关键数字有没有丢。
注意：正文里出现来源网址是正常的，不算远程依赖。

---

## 7. 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 潮汐返回空或标 `partial: true` | 目标日期超出约 10 天数据窗口 | 临近出行再查；安全决策对照官方潮汐表 |
| 观鸟返回 403 | 没提供 eBird Token | 到 ebird.org/api/keygen 免费申请，或降级用网上资料并标"未核实" |
| `selftest` 有接口挂掉 | 对方服务下线（已发生过两次） | 走降级方案，并更新 `references/nature-apis.md` 的状态表 |
| `red_team.py` 退出码 1 | 存在阻断项 | 按提示逐条改，改完重跑 |
| `--check` 报"缺少署名" | 手动改过产物文件 | 重新渲染，不要手改产物 |

**接口挂了怎么办：**不阻塞。改用 web 搜索对应数据，并在方案里标注数据源与日期。
但**必须同步更新 `nature-apis.md`**，把失效接口挪进「已停用接口」章节并写明替代方案，
文档里声明的"实时接口"必须实测，不能只凭资料转述。

---

## 8. 改这个 Skill

### 8.1 加一条红方规则

在 `scripts/red_team.py` 里找到对应的 `check_*` 函数，加一段 `rep.add(...)`，
然后在文件顶部的 `RULES` 列表里登记，最后在 `tests/run_tests.py` 加断言。

```python
rep.add("RT-L09", "major", "逻辑", "目标行",
        "说清问题是什么。",
        "给出可执行的替代动作。")
```

### 8.2 改需求访谈（S1）

提问口径全在 `references/intake-interview.md`，`SKILL.md` 的 S1 只是摘要：**要改问法改那个文件**，
不要往 SKILL.md 里堆问题清单。改的时候守住四条硬规则：一次一问、每题带推荐项、
能自己查到的事实不拿去问用户、共识确认前不写方案。

落盘与呈现的分工（容易改错的地方）：

| 内容 | 落在哪 | 进不进交付物 |
|---|---|---|
| 五层的问题与追问手法 | `references/intake-interview.md` |：|
| 访谈的结论（日期/同行/预算口径/节奏/专项） | `request.*` | ✅ 速览各格 |
| 这趟的主题 / 硬约束 | `request.why` / `request.constraints[]` | ✅ 速览独立两格（长句走 `.cell.wide` 通栏） |
| 一问一答的流水 | `decisions[]` | ❌ **不渲染**（同红方复核，属过程记录） |
| 装备与经验（专项目标怎么定） | `request.nature_caps` | ❌ 并入专项建议与待办清单 |

`tests/run_tests.py` 的 `test_intake()` 里有**反向断言**：产物里出现「访谈拍板记录」或
`class="dlog"` 就判失败。谁把问询记录渲染回产物，会在这里被拦住。

### 8.3 改界面

- **样式**：全部在 `assets/handbook.css`（v3.0.0，单一来源）。渲染时由 `render_plan.py`
  的 `load_css()` **内联**进 HTML，所以产物仍是纯离线单文件（无 `<link>`、无 `@import`、无远程 `url()`）。
  **改样式只改这个文件**，不要往 `render_plan.py` 里塞 CSS。
- **结构**：`render_plan.py` 的 `_cover` / `_nature` / `_nature_card` / `_days` / `_checklist` 等函数，
  小贴图与章节贴纸映射（`STICKER` / `SECTION_STICKER` / `NATURE_STICKER`）也在该文件里。
- **自然专项的四个交互件**：单独放在 `scripts/nature_widgets.py`（v1.0.1），
  `render_plan._nature()` 只是调用它。要改星图/潮汐图/鸟卡/火烧云表的样式和结构，
  改这个文件，不要塞回 `render_plan.py`。它对外只吐内联 SVG + HTML，**不含脚本**。
  - 星图/月相是真的天文计算（`_jd_utc` → `_gmst_hours` → `_altaz`），
    星星表 `STARS`（43 颗 J2000）与连线 `ASTERISMS` 也在里面，要加星就改这两张表。
  - 潮汐点选用的是**纯 CSS 单选面板**：`<input type="radio" class="tp">` +
    `.tp:nth-of-type(k):checked ~ .tp-plot .tpdot:nth-child(k)`。
    **必须写 `:nth-of-type(k):checked`**，只写 `:checked ~ ` 会匹配到「k 之前的任意被选中项」，
    结果是所有点选面板同时显示（这坑踩过一次）。
    点选序号要与 `.tpdot:nth-child(k)`、`.tp-panel:nth-child(k)` 严格对齐，数量变了三处一起改。
- **规范**：动手前先看 `references/design-system.md`（色板、圆体排版、贴纸清单、动效时长、交互对照、打印规则）。
- 产物是浅色网页风（要打印），不跟随 IDE 深色主题。
- 注意两个静默降级：`load_css()` 会先剥注释再判断远程引用；读不到样式表就退回内置 `FALLBACK_CSS`。
  **样式改坏了不会崩，但会静默退回兜底样式**，改完记得重新渲染并肉眼过一遍产物。

### 8.4 加一个自然专项

1. `references/nature-apis.md` 加接口与算法
2. `references/nature-knowledge.md` 加领域知识
3. `scripts/nature_forecast.py` 加子命令
4. `scripts/nature_widgets.py` 加对应的交互件函数（纯内联 SVG/HTML，不引脚本、不外链）
5. `render_plan.py` 的 `_nature()` 加一张卡，调上面的函数
6. `assets/handbook.css` 加该控件的样式
7. `red_team.py` 的 `NATURE_KEYS` 加溯源关键词
8. `tests/run_tests.py` 的 `test_nature_widgets()` 加断言（含「产物无 `<script>`」）

### 8.5 改完的检查顺序

```bash
python -m py_compile scripts/*.py      # 编译
python tests/run_tests.py              # 133 条断言
python scripts/red_team.py check <方案目录>            # 红方复核，有阻断项返回 1
python scripts/render_plan.py <方案目录> --check       # 产物一致性 + 无脚本/无远程资源
python scripts/nature_forecast.py selftest            # 接口健康度
python tools/install.py                # 同步到 skill 目录
```

前五步可以一条命令跑完：

```bash
python tools/verify_all.py             # 编译 → 测试 → 红方复核 → 渲染 → 一致性，逐项报结果
```

改完界面记得用**无头浏览器截一张图**肉眼过一遍，尤其星图和潮汐点选，
它们靠的是真算出来的坐标和纯 CSS 单选，语法错了不会报错，只会静静地画错。

`tools/` 下有两个验收小工具（都是开发用的，不进交付物）：

```bash
# 无头 Chrome 截图；--measure 先量出目标元素的 top/height，再按偏移截
python tools/shot.py <html> --measure ".nature,.skychart" --offsets 1000,2000
python tools/shot.py <html> --print --offsets 760          # 核对打印版式

# 列出容器内所有元素的盒模型 + display + 背景，用来定位「看着不对」的元素
python tools/probe.py <html> --root ".skyrow"
python tools/probe.py <html> --root ".nature" --filter y=1800,2400
```

两个坑：Chrome 命令行**不吃相对路径**（`--screenshot=` 必须给绝对路径），
也不支持选中 print 媒体：`--print` 是把 HTML 里的 `@media print` 临时换成
`@media all` 再看。

---

## 9. 完整案例走查

`examples/weihai-2d/` 是北京 → 威海 2 天 2 人的真实案例。
下面看红方第一轮到底挑出了什么。

**rt-001（阻断 · 安全）**：初稿写「干潮 09:12」，把赶海排在 08:30-10:30。
用 Open-Meteo Marine 实测：10/1 干潮是 **06:08（-0.71m）**，而且数据只覆盖到 07:00。
按原窗口出动时潮水已在回涨，滩涂深处有被困风险。
→ 窗口改为 04:38-07:38，行程项改为 04:30-07:20，写明"07:20 开始撤离"。

**rt-002（严重 · 事实）**：「09:12（0.4m）」在任何数据源里都查不到。
→ 替换为实测值，并标明 MSL 基准偏差与部分数据状态。

**rt-003（严重 · 预算）**：分项上限合计 2600，超出用户给的 2500。
→ 住宿压到 350-500，合计正好 2500。

**rt-004（严重 · 事实）**：原稿写死「G456 次 15:08 发车」并标为已确认，
但没有任何购票凭证。
→ 改为"车次待定"，购票状态改回待办。

**rt-005（严重 · 时间）**：赶海和观鸟分成两天排。实际上滩涂低潮时水鸟正在
同一片滩面觅食，合并反而省出一个清晨。
→ 合并到同一时段同一地点。

**rt-006 / rt-007（提醒）**：自然专项数据全都没有来源；D2 没有备选方案。
→ 补 facts/sources；补两条带触发条件的备选。

第二轮复核：**无发现**。结论为通过。

```bash
# 亲自跑一遍（示例目录里就是 parts/ 分片，直接喂目录）
python scripts/red_team.py check examples/weihai-2d
python scripts/render_plan.py examples/weihai-2d --out-dir /tmp/out
```

---

## 10. FAQ

**Q：一定要用红方吗？会不会太啰嗦？**
A：安全相关的必须用。其余部分，快速方案可以只跑规则引擎（`red_team.py check`），
跳过人工六轮。但带娃、赶海、高原、无人区这类场景，建议完整跑。

**Q：为什么潮汐不用官方数据？**
A：国家海洋信息中心的接口需要注册和签名认证。日常用 Open-Meteo Marine 够快够方便，
但**安全决策必须对照官方**。方案里每次都写了这句话。

**Q：能规划出境游吗？**
A：能。额外提醒签证、时差、插头、网络。但潮汐/天文/鸟况接口对境外的覆盖度不一，
偏远地区可能降级为 web 搜索。

**Q：产出的 HTML 会不会上传到网上？**
A：不会。渲染器只写本地文件，页面里不含任何脚本、CDN 或远程资源加载。
图片也不外链。行程里的个人信息只存在你本机。

**Q：eBird Token 会写进方案吗？**
A：不会。Token 只走环境变量，不落进任何数据分片或交付物。
