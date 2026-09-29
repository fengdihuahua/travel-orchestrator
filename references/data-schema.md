# 行程数据结构（数据分片写法）

> **S5 编排与 S8 交付之间唯一的数据契约。写分片之前先看这一页。**
>
> 数据以分片为常态：`travel-plan/<trip_id>/parts/*.json`（分片少时也可平铺在方案目录里）。
> 渲染、红方复核、校验都直接读目录、就地合并，不需要先组装大 JSON。
>
> 想不起来结构时，最快的办法是看骨架（`python scripts/build_trip.py skeleton <目录> --start YYYY-MM-DD --days N`）
> 或打印结构说明（`python scripts/build_trip.py schema`）。

---

## 1. 字段名必须和消费方一致

下面的字段名被 `render_plan.py`（渲染）、`red_team.py`（复核）、`build_trip.py`（校验）
**同时读**。写错一个名字，表现是"生成成功、渲染出来是空的"，排查成本极高。

| 路径 | 类型 | 说明 |
|---|---|---|
| `trip_id` | str | 唯一标识，一般与目录名同 |
| `plan_version` | int | 从 1 开始，S10 改方案时递增 |
| `status` | str | `draft` / `conditional` / `executable_as_of_check` |
| `status_note` | str | 为什么是这个状态（可空） |
| `request.dates.start` / `.end` | str | `YYYY-MM-DD`，**必填**，渲染与复核都依赖 |
| `request.companions` | str | 如 `"2 成人"`，⚠️ **字符串不是数组** |
| `request.budget.amount_max` | num/null | null 时预算超支检查（RT-S06）跳过 |
| `request.origin.name` / `request.destination.name` | str | 只给地名（`"威海"`），别写整句 |
| `request.pace` | str | 一句话节奏 |
| `request.interests.must` | `[{label}]` | 必须玩到；**对象数组**，不是字符串数组 |
| `request.interests.prefer` | `[str]` | 能做则做 |
| `request.why` | str | 这趟的主题（进"一页速览"） |
| `request.constraints` | `[str]` | 硬约束（进"一页速览"，渲染成胶囊） |
| `itinerary.days` | `[{day_id,date,weekday,title,items[],alternatives}]` | 逐日行程 |
| `itinerary.days[].items[].item_id` | str | **全局唯一**（跨所有天），阻断级 |
| `itinerary.days[].items[].geo` | `{lng,lat}` | 可选。给了才画动线图；**用高德坐标系**（见 §4.1） |
| `itinerary.days[].items[].booking` | `{kind,keyword}` | 可选。要预订的项目（门票/住宿/车票），渲染成平台入口 |
| `itinerary.food` | `[{name,search?,price,why,note}]` | 美食。`search` 是拿去搜的关键词（店名带括号备注时必填） |
| `itinerary.budget.categories` | `[{name,min,max}]` | 六分项，`min ≤ max` |
| `itinerary.checklist` | `[{task,priority,deadline,if_unresolved,status,booking?}]` | 待办；`booking` 同上 |
| `request.stay` | `{area,type,max,notes}` | 住哪一片 / 民宿还是酒店 / 价位档 / 硬要求（访谈落盘，不渲染成价格） |
| `request.dining` | `{style,must_try,avoid}` | 吃什么方向 / 必须吃到 / 忌口 |
| `itinerary.weather` | `{kind,summary,entries[]}` | `kind: seasonal/forecast` |
| `itinerary.nature` | `{birding,stargazing,sunrise,beachcombing,glow}` | 专项，见 §4 |
| `pitfalls` | `[{title,detail,avoid}]` | 踩坑指南 |
| `facts` | `[{fact_id,claim,value,status,source_ids[]}]` | `status` 四档 |
| `sources` | `[{source_id,name,kind,url,retrieved_at}]` | `facts.source_ids` 必须都能在这找到 |
| `red_team` | `{verdict,findings[]}` | 复核结论（不进交付物渲染） |
| `decisions` | `[{decision_id,question,choice,reason}]` | 访谈记录（**不进交付物**） |

缺失判定（`build_trip.py` 里的口径）：

- **错误（拦住渲染）**：`trip_id` / `plan_version` / `status` / `request` / `itinerary` 任一缺失或类型不对；
  `itinerary` 缺 `days` / `food` / `budget` / `checklist`；`days` 为空；`item_id` 重复；
  **列表元素的类型不对**；预算分项 `min > max`；`dates` 格式不对。
- **警告（不拦）**：`weekday` 与日期不符；某天没有 `items`；`weather.entries` 缺失；
  元素里认不出标志性字段（如 `food[0]` 没有 `name`）；可选字段
  （`pitfalls`/`facts`/`sources`/`red_team`/`status_note`/`title`）未填。

**列表必须是对象数组，元素不能图省事写成字符串。** 这几处的元素是对象：
`itinerary.days` / `days[].items` / `days[].alternatives` / `itinerary.food` /
`itinerary.checklist` / `itinerary.budget.categories` / `itinerary.weather.entries` /
`request.interests.must` / `facts` / `sources` / `pitfalls` / `decisions`。
写成字符串容错但不合规矩：`food` 写成 `["生蚝"]` 会让渲染器认不出店名，
渲染与红方复核都会跳过这一条（v4.10.0 起渲染器与复核器都不再崩，而 `validate` 会直接报错并指到分片）。

**坐标与数值**：`geo.lat` / `geo.lng` 必须是数，写字符串、数组或 `nan` / `inf`
一律按"没填坐标"处理——**不会报错，但图上也就不画这个点**。同理预算里的 `min` / `max`
是 `nan` / `inf` 时按"未查到"显示。数字要写就写干净的数字。

---

## 2. 三个高频坑（会返工的那种）

### 坑一：逐日行程必须写 `days+`，不能写 `days`

一天一片，每片都追加到同一个列表：

```json
// parts/10_days_D1.json
{"itinerary": {"days+": [ { "day_id": "D1", "date": "2026-10-01", "items": [] } ]}}

// parts/11_days_D2.json
{"itinerary": {"days+": [ { "day_id": "D2", "date": "2026-10-02", "items": [] } ]}}
```

合并后是 `itinerary.days = [D1, D2]`。

**写错成 `"days"` 会静默覆盖**：后一片把前面的天数整个顶掉，不报错，
直到渲染出来只剩最后一天、或者红方说"方案只有 1 天"才发现。
规则是：**带 `+` = 追加到列表，不带 = 覆盖**。同样的写法适用于任何需要跨片累积的列表。

### 坑二：`companions` 是字符串

```json
"companions": "2 成人 + 1 个 3 岁"     // ✅
"companions": ["2 成人", "1 个 3 岁"]  // ❌ 数组：渲染与复核按字符串消费
```

### 坑三：`item_id` 全局唯一

`d1-lunch` 只能出现一次；第二天再写一遍同名 id 就是阻断项。
命名建议 `<天>-<序号>-<事项>`（`d1-01-sunrise`），这样跨天不会撞。

**校验报错会指明分片文件**（`! item_id 重复：d1-tide（见 parts/11_days_D2.json）`），
直接改那一片即可，不用在十几个分片里翻。

---

## 3. 分片的切法（文件名即合并顺序）

```
parts/01_request.json        头信息 + request
parts/02_weather.json        itinerary.weather
parts/03_nature.json         itinerary.nature
parts/10_days_D1.json        itinerary.days+  ← 一天一片
parts/11_days_D2.json
parts/20_food.json           itinerary.food
parts/21_budget.json         itinerary.budget
parts/22_checklist.json      itinerary.checklist
parts/23_pitfalls.json       pitfalls
parts/24_facts.json          facts + sources
parts/25_sources.json        （也可与 facts 同片）
parts/26_red_team.json       red_team
parts/27_decisions.json      decisions
```

- **按文件名排序合并**，所以前缀数字决定顺序；`_` 开头的文件会被忽略（临时/草稿用这个命名）
- 每片几 KB：写到这个体量，单次写入不会失败（这也是分片存在的理由）
- 只写自己负责的那部分，不要复制整份数据进去

---

## 4. 自然专项的字段（渲染层要用）

专项的交互件依赖具体字段，写法不对会降级成纯文字（详见 SKILL.md 的专项表）：

| 专项 | 关键字段 |
|---|---|
| 观鸟 | `nature.birding.species[]` 写成**对象**（`sci`/`family`/`status[]`/`intro`/`best_months`/`taxa_url`）；纯字符串只能写鸟名 |
| 观星 | `nature.stargazing.chart{lat,lng,tz,at,place}`、`targets[]`、`spots` |
| 日出 | `nature.sunrise.sunrise/sunset/golden_hour/blue_hour`；霞光写 `nature.glow{score,level,reasons,inputs}` |
| 赶海 | `nature.beachcombing.curve[{t,h}]`、`extremes[{type,time,height_m}]`、`best_window`、`unit`、`source_id` |

地点类字段（`hotspots` / `spots` / `best_spot`）写成 `[{name, note}]` 或 `["A","B"]`，
渲染会变成可点的高德搜索链接。

### 4.1 坐标与动线图（`geo`）

交付页有一张「动线一览」：按行程顺序把停靠点连成示意图，并给两个跳外部地图的按钮。
它只在**停靠点带坐标**时画，所以想让它出现就得填 `geo`：

```json
{ "item_id": "d1-tide", "place": "桑沟湾滩涂",
  "geo": { "lng": 122.55, "lat": 37.13 } }
```

三条硬规矩：

1. **坐标要用高德坐标系（GCJ-02）**，别直接填 GPS 原始坐标（WGS-84）。
   两者在国内差几百米，填错了点会落到马路对面甚至水里。数据源给的是 WGS-84 时先转换。
2. **`place` 是给人看的，`geo` 是给图上点的**——名字里带"（朝东无遮挡）"这类备注不影响
   动线图（备注不参与绘图），但会影响地图搜索，所以搜索词会自动剥括号。
3. **没坐标不报错**：该点从图上省掉，序号留缺口，图下会写"另有 N 个点未录坐标"。
   所以宁可少填，也不要瞎编坐标——编的坐标画出来是错的，比不画更坏。

动线图只画**相对方位与顺序**，不是导航路径。真正的路径由「规划整条路线」按钮交给高德；
注意 `uri.amap.com/navigation` 的 `via` **只支持一个途经点**，所以链接最多体现 3 段，
多余的停靠点靠「看全部点位」按钮和上方示意图补。

### 4.2 预订入口（`booking`）

本 skill **不接要 Key 的平台接口**（美团/携程的开放接口都要商户资质），也不写死房价票价——
写死了就会过期。取而代之：凡是"要订的东西"（门票、住宿、车票），在数据里给一个 `booking`，
渲染器把关键词拼成平台的**搜索页**入口，用户点进去看当天报价与余票：

```json
{ "task": "买成山头门票", "priority": "must", "status": "todo",
  "booking": { "kind": "ticket", "keyword": "成山头景区门票" } }
```

- `kind` 取 `ticket` / `hotel` / `food`，只影响平台顺序，不改变链接形态。
- `keyword` 要写成**能搜到东西的词**（"成山头景区门票"，不是"门票"）。
- 美食条目想换搜索词时用 `search` 字段，默认拿 `name` 搜。
- 目前只上架**实测能打开**的大众点评与美团（见 `nature_widgets.py` 顶部注释）。
  加平台前先自己点一遍，别把 404 塞给用户。

---

## 5. 改完数据怎么验

```bash
python scripts/build_trip.py validate travel-plan/<trip_id>   # 结构（报错带分片文件名）
python scripts/red_team.py check    travel-plan/<trip_id>     # 23 条规则
python scripts/build_trip.py render travel-plan/<trip_id>     # 合并 + 校验 + 渲染（校验只做一次）
python scripts/render_plan.py       travel-plan/<trip_id> --check   # 产物一致性
```

改数据永远改 `parts/`；`trip.json` 是产物不是数据源（只有交付/存档才 `assemble`）。

---

## 6. 结论状态四档（`facts[].status`）

每个具体数字（时刻、价格、里程、鸟种、潮位）都要能追到 `sources` 里的某一条。
追不到不是"不写"，而是**降级标注**——状态决定这句能写成什么语气：

| 状态 | 含义 | 能不能当确定值写进正文 | 正文怎么写 |
|---|---|---|---|
| `verified` | 有权威来源且已核对 | 可以 | 直接写，附来源 |
| `estimated` | 有依据的推算，或只拿到部分数据 | 可以 | 必须加"估算/部分数据"，最好给误差范围 |
| `unverified` | 有说法但没核对过 | 可以 | 必须加"未核实" |
| `unknown` | 查不到 | **不可以** | 正文写"未查到"，`value` 留空/null |

三条别踩的线：

1. **`unknown` 不许写 0，也不许写"正常开放"**——那是拿想象填空。
2. **不许用"约""左右"把没依据的数字包装得像有依据**：没依据就降级成 `estimated`/`unverified` 并写明。
   反过来说，`estimated` 的数字也别裸写（比如"门票 80 元"），要写成"门票约 80 元（估算）"。
3. **超 30 天即过期**：潮汐/日出/月相/天气四类一天一变，`sources[].retrieved_at` 超 30 天必须重新取数，
   红方有对应规则会拦。这不是"文档要求"，是这套数据的物理属性。
