# 自然观察专项：实时数据接口清单

> **版本 v2.1.0（2026-09）**
> v2.0.0：原潮汐接口（cnss.com.cn tideJson）与火烧云接口（sunsetbot.top）**已失效**，
> 改由 Open-Meteo 系列替代。
> v2.1.0：**观鸟改为默认免注册**（iNaturalist 主源 + GBIF 备用），eBird 降级为可选增强
> 并补全完整支持（含 `ebirdcheck` 自检）。同批实测排除 3 个鸟鸣识别方案（见 §4.4）。
> 旧接口保留在文末「已停用接口」章节备查。

**原则：能用接口不用记忆，接口挂了用 web 搜索降级，所有结果标注数据源与日期。**
**安装后必须免注册即可用**，需要注册的能力只能作为可选增强，不能当前置条件。

---

## 0. 接口总览与可用性状态

| 专项 | 主接口 | 状态 | 需 Key | 有效期 |
|---|---|---|---|---|
| 🌊 潮汐（赶海） | Open-Meteo Marine | ✅ 可用 | 否 | 约 10 天 |
| 🌙 观星 | CycleCalcs | ✅ 可用 | 否 | 实时 |
| 🌅 日出日落 | Sunrise-Sunset.org | ✅ 可用 | 否 | 任意日期 |
| 🔥 火烧云（霞光） | Open-Meteo Forecast + Air Quality（自建模型） | ✅ 可用 | 否 | 16 天 |
| 🐦 观鸟（记录/名录/物候） | iNaturalist | ✅ 可用 | **否** | 近 1 年 + 历史全量 |
| 🐦 观鸟（备用源） | GBIF | ✅ 可用 | **否** | 近年 |
| 🐦 观鸟（可选增强） | eBird（康奈尔） | ✅ 可用（需免费 Token） | 是 | 近 30 天 |
| 📍 城市名解析 | Open-Meteo Geocoding | ✅ 可用 | 否 |：|
| 🔊 鸟鸣识别 |：| ❌ 无免 Key 方案 |：| 改用手机 App（§4.4） |

**一键自检**（确认当前哪些接口还活着）：

```bash
python scripts/nature_forecast.py selftest          # 全部数据源
python scripts/nature_forecast.py ebirdcheck        # 单独验证 eBird Token
```

**无需任何注册即可完成的功能**：潮汐、观星、日出日落、火烧云、观鸟记录、
当季鸟种名录、物种卡、观鸟黄金时段、观鸟地点推荐。

---

## 1. 🌊 赶海：潮汐预报

### 1.1 Open-Meteo Marine API（首选，免费无 Key，全球覆盖）

- **接口**：
  ```
  GET https://marine-api.open-meteo.com/v1/marine
      ?latitude={lat}&longitude={lng}
      &hourly=sea_level_height_msl
      &forecast_days={1-16}&timezone=Asia%2FShanghai
  ```
  例：`https://marine-api.open-meteo.com/v1/marine?latitude=37.509&longitude=122.114&hourly=sea_level_height_msl&forecast_days=10&timezone=Asia%2FShanghai`
- **返回**：`hourly.time[]`（本地时间字符串）+ `hourly.sea_level_height_msl[]`（逐小时潮位，单位米）
- **赶海窗口算法**（已内置于 `nature_forecast.py`）：
  1. 逐小时潮位曲线找**局部极值**（`v[i] >= v[i-1] && v[i] >= v[i+1]` 为满潮，反向为干潮）
  2. 用**三点抛物线插值**精化时刻与潮高（小时级数据不足以精确到分钟）
     `offset = 0.5*(v[i-1]-v[i+1]) / (v[i-1]-2*v[i]+v[i+1])`，限定在 ±0.75 小时内
  3. **推荐赶海窗口 = 干潮时刻前后各 1.5 小时**（滩涂裸露最广）

- ⚠️ **两个必须知道的坑**：
  1. **数据有效期约 10 天**：请求 `forecast_days=16` 时，`time` 数组会返回 16 天，
     但第 10 天之后的潮位值全为 `null`。脚本会自动检测并在 `data_valid_until` 标注，
     目标日期数据不完整时会标 `partial: true` 并降级提示，**不会给出误导性的窗口**。
  2. **基准面是 MSL（平均海平面），不是当地潮高基准面**：数值与当地潮汐表可能有
     系统性偏差。**安全决策（尤其"涨潮前撤离"）必须对照官方潮汐表复核。**

### 1.2 国家海洋信息中心官方接口（权威，需注册）

- **官网**：`http://service-tide.nmdis.org.cn/API/direction`
- **接口**：`POST http://service-tide.nmdis.org.cn:8080/api/v1/CoreData/GetPortTideData`
- **鉴权**：appid + appsecret（签名认证，需注册开发者账号）
- **参数**：`SiteCode`（站点代码，如 S003）、`Date`（日期 YYYY-MM-DD）
- **适用**：需要权威数据做最终核验时；日常推荐优先 1.1

### 1.3 降级方案

- web 搜索「目的地 + 潮汐表 + 日期」，优先潮汐表精灵（eisk.cn）等聚合页
- 潮汐表精灵：`https://www.eisk.cn/Tides/{id}.html`（数据来自国家海洋信息中心）
- 直接引用页面中的：满潮时刻、干潮时刻、潮高、推荐赶海时间、大潮/小潮

### 1.4 赶海判断规则（写入方案）

- **推荐赶海窗口 = 干潮（低潮）前后 1~2 小时**（滩涂裸露最广）
- 大潮（农历初一/十五前后 3 天）退得远、滩涂面积大，赶海收获多；小潮（初七/廿三前后）滩涂少
- 半日潮区域每天通常有 **2 次干潮 + 2 次满潮**；若只解析到 1 次，多半是数据不完整
- 赶海前必查：当天是否有台风/大风（海面风浪 > 5 级不建议）
- 安全红线：涨潮速度快，务必在涨潮前 1 小时撤到安全区；带孩子看管好防潮水回流

---

## 2. 🌙 观星：天文与光污染

### 2.1 CycleCalcs（免费，无 Key，首选）

- **官网**：`https://www.cyclecalcs.com/developers.html`
- **接口**：
  ```
  GET https://www.cyclecalcs.com/v2/today?lat={lat}&lon={lon}&at={ISO时间}
  ```
  例：`https://www.cyclecalcs.com/v2/today?lat=29.6&lon=106.5&at=2026-09-22T14:00Z`
- **返回**（实测字段，数据质量很高）：
  - `data.moon.phase`：`name` / `illumination_percent` / `age_days` / `waxing`
  - `data.moon.constellation`：月亮所在星座
  - `data.planets_up[]`：行星名称、星等、高度角、方位角、可见性描述、是否肉眼可见
  - `data.night`：`dark_start` / `dark_end` / `dark_minutes` /
    **`moonless_dark_minutes`（无月暗夜分钟数，观星最关键的指标）** / `verdict`（自然语言结论）
  - `data.next_events[]`：满月/新月/日月食等，含 `days_until`
- **注意**：返回 UTC 时间；`at` 参数用当前 UTC 时间

### 2.2 光污染地图（免费，静态查询）

- **Light Pollution Map**：`https://www.lightpollutionmap.info`（交互地图，人工查询后引用数值）
- **数据参考**：David J. Lorenz 光污染图集（djlorenz.github.io）
- **等级**：波特尔暗空分级（Bortle scale）1-9，1 级最佳；国内优质观星点通常 2-4 级

### 2.3 @pondlog/source-nightsky（本地计算 npm 包，无需 API）

- **安装**：`npm install @pondlog/source-nightsky`（Node 18+）
- **能力**：`getTonightsBriefing({coords})` 返回月相、暗夜窗口、行星位置、活跃流星雨、可见星座
- **适用**：需要离线/无网络快速计算时

### 2.4 其他付费/需注册备选

- Ouranos API（观星优化天气，需 Key）：`https://api.ouranos-app.com/recommendation_7days?apikey=...&latitude=..&longitude=..`
- WeatherAPI astronomy（需 Key）：`https://api.weatherapi.com/v1/astronomy.json?key=..&q=..&dt=..`

### 2.5 观星判断规则（写入方案）

- **优先看 `moonless_dark_minutes`**：这是"真正能看深空的时间"，比单纯月相更能说明问题
- **避开满月**：月照 > 50% 时银河和深空可见性大幅下降；新月前后 7 天最佳
- **银河可见期**：北半球 3-10 月，需月落后的暗夜窗口
- **流星雨**：英仙座（8 月中）、双子座（12 月中）、象限仪座（1 月初）；查当年 ZHR 与月相冲突
- **综合评分 = 月相（权重高）× 云量 × 光污染**，逐日给出 1-5 星

---

## 3. 🌅 日出日落：精确时刻

### 3.1 免费天文 API 组合（无 Key）

- **Sunrise-Sunset.org**（经典免费）：
  ```
  GET https://api.sunrise-sunset.org/json?lat={lat}&lng={lng}&date={YYYY-MM-DD}&formatted=0
  ```
  返回：`sunrise` / `sunset` / `civil_twilight_begin/end` / `nautical_twilight_*` /
  `astronomical_twilight_*` / `solar_noon` / `day_length`（均 UTC，需换算本地）
- **CycleCalcs**（同 2.1）：也可查日出日落与黄金时刻
- **APIFreaks Astronomy**（需 Key 备选）

### 3.2 日出判断规则（写入方案）

- **黄金时刻** = 日出后/日落前约 1 小时（光线柔和，摄影最佳）
- **蓝调时刻** = 日出前/日落后 20-40 分钟
- 云海日出：需前一天降雨 + 次日晨晴（湿度高、温差大）
- 提醒：日出前 30 分钟到达观景点占位；查头班索道/公交时间（部分山岳景区 4-5 点才开门）

---

## 4. 🐦 观鸟：物种、记录与识别

> **设计原则：默认免注册。** 游客装完 skill 就能查鸟，不该先被要求去某个网站注册账号。
> eBird 能力更全，但作为**可选增强**存在，不是前置条件。

### 4.1 iNaturalist API（主源，免费**无 Key**，首选）

- **Base URL**：`https://api.inaturalist.org/v1`，无需任何鉴权头
- **限流**：匿名约 60 次/分钟；本脚本单次命令 2-5 个请求，不会触碰
- **核心接口**：
  | 用途 | 接口 |
  |---|---|
  | 近期观测记录 | `GET /observations?taxon_id=3&lat=..&lng=..&radius=25&d1=..&d2=..` |
  | 当季鸟种名录 | `GET /observations/species_counts?taxon_id=3&lat=..&lng=..&d1=..&d2=..` |
  | 逐月物候直方图 | `GET /observations/histogram?taxon_id=..&interval=month&lat=..&lng=..` |
  | 中文名检索 → taxon id | `GET /taxa/autocomplete?q=黑尾鸥` |
  | 批量取中文名 | `GET /taxa?id=4370,6916&locale=zh-CN` |
  | 物种详情 | `GET /taxa/{id}` |
- **`taxon_id=3`** 即鸟纲（Aves），用它把范围锁在鸟类，避免混入植物昆虫
- **⚠️ 两个实测踩过的坑（照抄容易错）**：
  1. **`locale` 必须写 `zh-CN`**。写 `zh` 会返回**繁体**（黑尾鷗 / 黃嘴天鵝），
     写 `zh-CN` 才是简体（黑尾鸥 / 大天鹅）。`zh-Hans`、`zh-TW` 也都是繁体。
  2. **`/taxa?id=` 默认每页 30 条**。一次传超过 30 个 id 会被静默截断，
     部分物种静默回退成英文名（不报错，很难发现）。脚本已改为按 100 个一批 + 显式 `per_page`。
- **返回**：观测日期、物种学名/英文名、观察者填写的地点名、坐标（珍稀种已模糊化）、
  记录永久链接（`https://www.inaturalist.org/observations/{id}`）
- **优势**：免 Key、全球覆盖、含照片与位置、中文名规范；**劣势**：记录密度低于 eBird 在中国的部分热点

### 4.2 GBIF Occurrence API（备用，免费无 Key）

- **Base URL**：`https://api.gbif.org/v1`
- **查询**：`GET /occurrence/search?taxonKey=212&decimalLatitude=37.2,37.8&decimalLongitude=121.8,122.4&year=2025,2026&hasCoordinate=true`
  - `taxonKey=212` 为鸟纲（Aves）；`year` 用**逗号列表**（`2025,2026`），写成 `2025-2026` 会 400
- **定位**：iNaturalist 不可用时的兜底。数据以标本与科研调查记录为主，**时效性明显弱于公民科学平台**，
  用于"这个地区有分布"的佐证可以，用于"最近能看到什么"偏弱
- **物种名建议**：`GET /species/suggest?q=Grus japonensis&rank=SPECIES`

### 4.3 eBird API（可选增强，需免费 Token）

> 只有用户主动提供 Token 时才启用。不提供时**全部观鸟功能照常可用**。

- **申请**：`https://ebird.org/api/keygen`（免费，非商业约 1000 次/天）
- **传入**：`--token 你的Token`，或环境变量 `EBIRD_API_TOKEN`
- **验证**：`python scripts/nature_forecast.py ebirdcheck` ← 一条命令测 Token 与全部实际依赖的端点
- **请求头**：`X-eBirdApiToken: {token}`（**注意大小写**：`X-eBirdApiToken`）
- **本脚本实际使用的端点**：
  | 用途 | 接口 | 参数上限 |
  |---|---|---|
  | 近期观测（按坐标） | `GET /data/obs/geo/recent` | `dist` ≤ 50km，`back` ≤ 30 天 |
  | 珍稀鸟种 | `GET /data/obs/geo/recent/notable` | 同上 |
  | 观鸟热点 | `GET /ref/hotspot/geo?fmt=json` | `dist` ≤ 50km |
  | 区域近期观测 | `GET /data/obs/{regionCode}/recent` | `back` ≤ 30 天 |
- **eBird 独有能力**：**珍稀鸟种提醒**（notable），iNaturalist 没有等价端点；
  以及官方审核的**热点地点库**（地点名规范，不像自由填写的 `place_guess` 那样一地多名）
- **Token 带来的差异**：
  | | 无 Token（默认） | 有 Token |
  |---|---|---|
  | 近期观测 | iNaturalist | eBird + iNaturalist 合并 |
  | 珍稀鸟种提醒 | ✗ | ✓ |
  | 地点推荐 | 按记录密度排序 | eBird 官方热点库 |
  | 当季鸟种名录 | ✓ | ✓（仍来自 iNaturalist） |
- **403 的含义**：eBird 在鉴权层就拦，**连不存在的路径也返回 403**。
  所以无法用 403/404 判断端点是否有效，**能确认 Token 有效的唯一方法是真的调用一次**
  （这正是 `ebirdcheck` 的用途）
- **429**：超出每日配额。免费额度日常够用，批量生成多城市方案时才会碰到

### 4.4 鸟鸣识别：无免 Key 方案（诚实说明）

**结论：本 skill 不提供在线鸟鸣识别。** 三个候选全部实测排除：

| 方案 | 实测结果 |
|---|---|
| Xeno-canto API | v2 路径 404，v3 返回 **401 需 Key** |
| Macaulay Library（康奈尔） | 返回**机器人验证页**（Anubis 反爬），无法程序化调用 |
| BirdNET 在线 API | 无公开端点；官方为本地模型 |

- **降级建议（写入方案给用户）**：用 **Merlin Bird ID** 或 **BirdNET App** 手机端离线识别，
  现场录音即时出结果，比任何在线接口都快
- **不要**用模型记忆编造某鸟的鸣声描述当作"识别结果"；可以说特征（如"四声一度的哨音"），
  但必须标注为**野外手册常识**而非接口数据

### 4.5 中国本地数据源

- **中国观鸟记录中心**：`https://www.birdreport.cn/`（站点可用）
  - ⚠️ **无公开 API**：其前端调用的 `api.birdreport.cn` 后端确实存在（返回 Spring Boot 风格 JSON），
    但端点未公开、逐一探测全 404。**不要尝试逆向**，作为人工查询入口即可
  - 用法：让用户在站内搜索物种名，看本地记录与分布
- **中文鸟名录**：《中国鸟类野外手册》/ 中国观鸟年报
- ⚠️ **《国家重点保护野生动物名录》不在任何上述接口里**。
  方案的"保护级别"字段只输出 IUCN 评级（iNaturalist 提供），
  中国保护级别一律写"未查到，需人工核对官方名录"，**不允许编造**

### 4.6 观鸟判断规则（写入方案）

- **最佳时段**：`birdtime` 命令按当地日出日落算出，**晨窗 = 日出前 30 分钟至日出后 3 小时**，
  昏窗 = 日落前 2 小时至日落；正午鸟少
- **天气约束**（`birdtime` 的判定依据）：风速 ≥ 30km/h 判差（鸟躲藏且难观察）；
  降水概率 ≥ 60% 判差；能见度 < 5km 提示"远处水鸟看不清"
- **迁徙季**：春秋两季（3-5 月、9-11 月）过境鸟多；6-7 月繁殖季鸣唱密；冬季雁鸭鸥类集群
- **观察点选择**：优先 eBird 热点（有 Token 时）/ iNaturalist 记录密集点；保护区、湿地、河口滩涂
- **设备**：双筒望远镜（8×42 通用）、长焦（400mm+）、录音笔；穿素色/迷彩，避开红黄亮色
- **伦理**：不惊鸟、不诱拍、**不使用鸟鸣回放**、不靠近巢穴、不公开珍稀鸟精确坐标

### 4.7 数据诚实边界（务必写进方案）

- 观鸟记录只证明**"有人记录到"**，不等于**"你一定能看到"**，方案里不得把记录数当作观赏概率
- 记录数受观察者活跃度影响极大（周末、节假日记录明显偏多），不代表鸟的绝对数量
- iNaturalist 对珍稀物种的坐标已做模糊化；方案中不得原样输出精确坐标
- 部分观察者填写的地点名不规范（同一滩涂出现多种写法），`birdspot` 结果需人工归并

---

## 5. 🔥 火烧云 / 朝晚霞预报

### 5.1 自建评分模型（首选，免费无 Key，替代已下线的 Sunset Bot）

> ⚠️ **原接口 sunsetbot.top 已于 2026-09 下线**（域名 404 / SSL 525），不再可用。
> 现改用 Open-Meteo 公开气象数据**自建评分模型**（已内置于 `nature_forecast.py`）。

**取数接口（全部免费无 Key）**：

```
# 1. 日出日落时刻（作为评分中心时间）
GET https://api.sunrise-sunset.org/json?lat={lat}&lng={lng}&date={date}&formatted=0

# 2. 云量分层 + 能见度 + 湿度
GET https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}
    &hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,relative_humidity_2m
    &forecast_days={1-16}&timezone=UTC

# 3. 气溶胶光学厚度（可选增强，失败不阻塞）
GET https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lng}
    &hourly=aerosol_optical_depth&forecast_days={1-16}&timezone=UTC
```

**评分算法**（取日落/日出时刻前后各 1 小时的平均值）：

| 因子 | 理想条件 | 评分影响 |
|---|---|---|
| 中高云 `(mid+high)/2` | 20%，75% | 适中 +1（有可被照亮的云层）；<20% 说明云不够；>75% 说明云太厚遮光 |
| 低云 `low` | ≤30% | 通透 +1；30-60% 提示风险；>60% **-1**（霞光基本被挡） |
| 能见度 `visibility` | ≥15 km | +1；<8 km **-1**（有霾，色彩发灰） |
| 相对湿度 `rh` | 40%，85% | 适中不加分；>90% **-1**（易起雾/低云） |
| 气溶胶光学厚度 `aod` | 0.10-0.40 | +1（利于散射出红橙色）；>0.60 颜色发暗 |

最终得分 clamp 到 **0-3 级**：

| 分数 | 等级 | 含义 |
|---|---|---|
| 0 | 基本无霞光 | 条件不具备 |
| 1 | 一般 | 元素有但组合不佳 |
| 2 | 较好 | 值得专门去等 |
| 3 | 绝佳 | 多个有利因子同时满足 |

- **物理依据**：高空有中高云（承光层）+ 低空通透（阳光可直达云底）+ 水汽适中
  + 适度气溶胶（红橙散射）→ 霞光概率高；雨后初晴傍晚概率大
- ⚠️ 这是**参考模型，不是官方预报**；输出必须标注"非官方发布，出行前结合实景判断"

### 5.2 付费备选（更精细，需注册）

- **彩云天气朝晚霞 API**：`https://docs.caiyunapp.com/weather-api/v3/meteorology/glow.html`
  （企业套餐付费；返回 evening/morning 概率、质量、时段、图层 URL）
- **星图地球数据云火烧云预报**：`https://datacloud.geovisearth.com/support/meteorological/flameCloudForecast`
  （注册+开发者认证；按经纬度查未来 3 天日出/日落时刻火烧云等级，覆盖 15~55°N, 72~136°E）

### 5.3 判断规则（写入方案）

- 朝霞（日出）与晚霞（日落）分开看，海边/湖面/山顶视野开阔处效果佳
- 等级表述统一用：无 / 一般 / 较好 / 绝佳

---

## 6. 📍 城市名解析（地理编码）

### 6.1 Open-Meteo Geocoding（免费无 Key）

```
GET https://geocoding-api.open-meteo.com/v1/search?name={城市名}&count=5&language=zh&format=json
```

- 返回 `results[0]` 含 `latitude` / `longitude` / `name` / `admin1` / `country`
- 用于把用户输入的「威海」自动转成经纬度，避免手填坐标
- 取 `count=5`：一次取回同名候选，方便发现跨省重名（见 §6.2）
- ⚠️ **安全**：`nature_forecast.py` 已对城市名做白名单校验
  （只允许中英文/数字/空格/`-`/`.`/`'`，长度 ≤40），防止参数注入

### 6.2 ⚠️ 中文重名地名会解析到别的省（务必核对 `admin1`）

**中文重名地名极多，取到的那一条未必是你想的那个城市。** 用城市名查出来的天文与天气
数据会整体偏移，而且**不会报错**：脚本照常返回结果，只是地点标签写着 `城市名/省份`，
稍不注意就把错数据写进方案。

真实案例（2026-09 北京到大同自驾方案）：

| 输入 | `admin1` | 坐标 | 后果 |
|---|---|---|---|
| `--city 大同` | **黑龙江** | 46.04N, 124.81E | 日出算成 05:30 |
| 山西大同（正确） | 山西 | 40.08N, 113.30E | 日出实际 06:15 |

同一天的日出差了 **46 分钟**；朝霞评分也跟着从「绝佳」变成「基本无霞光」（低云分层
完全不同）。按错值交底，用户会在山顶多冻一小时，还会主动放过当天最好的霞光。

`nature_forecast.py` v2.3.0 起会自己查这件事：地理编码取 `count=5`，同名候选跨省时
在 stderr 与输出正文里都打一行 `[注意]`，列清「本次用的是哪个、还有哪些同名候选」。
但这只是提醒，**取数仍然取第一条**，换不换要人判断。

**规则：**

1. **能拿坐标就不要用城市名。** 涉及日出日落、月相、霞光这类时刻敏感的数据，
   一律走 `--lat/--lng`；城市名只当兜底。
2. **必须用城市名时，先看返回的 `admin1`。** 脚本输出里地点标签是 `城市名/省份`
   （如 `大同/黑龙江`），对不上就说明解析错了；有 `[注意]` 行时把候选逐条看一遍。
3. **坐标按实际观测点取，不按市中心取。** 观星与日出点离市区几十公里时，
   用观测点坐标（大同案例用的是火山群 40.10N 113.70E）。
4. 已知高危重名（出现频率高、跨省且坐标差得远）：**大同**（山西 / 黑龙江）、
   城关区与城关镇（多地）、新城（多地）、朝阳（北京 / 辽宁），以及榆林这类
   与同名乡镇并存的市名。

```bash
# 先确认解析到了哪个省（看 admin1），或直接看脚本打的 [注意] 行
curl -s "https://geocoding-api.open-meteo.com/v1/search?name=大同&count=5&language=zh&format=json"
# 确认无误再查数据；时刻敏感的一律改用坐标
python scripts/nature_forecast.py sun --lat 40.10 --lng 113.70 --date 2026-09-26
```

---

## 7. 统一降级规则

1. 接口失败（超时/空结果/403/404/SSL）→ 不阻塞，改用 web 搜索对应数据
2. 所有实时数据在方案中标注：**数据源 + 查询日期**
3. 潮汐/月相/日出属天文海洋预报，最终以官方（国家海洋信息中心、天文台）为准
4. 涉及 API Key 的接口：无 Key 时提示用户申请（给申请链接），有 Key 时直接调用
5. 数据不完整时**必须显式标注**（如潮汐 `partial: true`），不得静默输出残缺结果
6. 定期运行 `python scripts/nature_forecast.py selftest` 确认接口健康度

---

## 8. 已停用 / 不可用接口（备查，勿再使用）

| 接口 | 原用途 | 停用/排除时间 | 现象 | 替代方案 |
|---|---|---|---|---|
| `https://sunsetbot.top/?intend=select_city&...` | 火烧云预报 | 2026-09 | HTTPS 证书校验失败；HTTP 返回 nginx 404（域名已下线） | §5.1 自建模型 |
| `https://www.cnss.com.cn/u/cms/www/tideJson/{city}_{date}.json` | 潮汐预报 | 2026-09 | 全部城市代码返回 HTTP 404 | §1.1 Open-Meteo Marine |
| `https://xeno-canto.org/api/2/recordings` | 鸟鸣库 | 2026-09 | v2 路径 404；v3 返回 **401 需 Key** | §4.4 改用手机 App |
| `https://search.macaulaylibrary.org/api/v1/search` | 鸟类媒体库 | 2026-09 | 返回**机器人验证页**（Anubis 反爬） | §4.4 人工访问 |
| `https://api.birdreport.cn/*` | 中国观鸟记录中心数据 | 2026-09 | 后端存在，端点未公开，逐一探测全 404 | §4.5 站内人工查询 |
| `https://api.ebird.org/v2/ref/taxa/ebird` | eBird 分类名录 | 2026-09 | 返回 404 | 用 iNaturalist `/taxa` |

> 说明：前两项曾写在本文件旧版本中且未经实测；2026-09-22 实测确认失效后已替换。
> 后四项是 v2.1.0 为"观鸟免注册"做方案筛选时**逐个实测排除**的，
> 记录在此的目的不是备忘，是**防止后来者（或未来的自己）再试一遍**。
>
> **教训：文档里声明的"实时接口"必须实测，不能只凭资料转述。
> 而且要实测到"能不能真的拿到数据"，不是"域名能不能解析"。**

---

## 快速调用示例

```bash
P="scripts/nature_forecast.py"

# 接口健康自检（先跑这个）
python $P selftest

# 潮汐（赶海）——城市名自动解析
python $P tide --city 威海 --date 2026-09-25
python $P tide --lat 37.509 --lng 122.114 --days 5

# 日出日落
python $P sun --city 威海 --date 2026-09-25

# 观星（月相 / 无月暗夜 / 行星）
python $P astro --lat 29.6 --lng 106.5

# 火烧云 / 朝晚霞（set=晚霞 rise=朝霞）
python $P glow --city 威海 --event set

# ---- 观鸟：全部免注册 ----
python $P bird      --city 威海 --days 30              # 近期真实观测记录
python $P species   --city 威海 --month 10             # 当季鸟种名录（近 3 年同月）
python $P birdinfo  --name 黑尾鸥 --city 威海           # 物种卡 + 本地逐月物候
python $P birdtime  --city 威海 --date 2026-10-02      # 观鸟黄金时段（含天气判定）
python $P birdspot  --city 威海 --radius 60            # 附近观鸟地点

# ---- 观鸟：eBird 可选增强（有免费 Token 时） ----
python $P ebirdcheck --token YOUR_EBIRD_TOKEN          # 先验证 Token
python $P bird      --city 威海 --token YOUR_EBIRD_TOKEN        # eBird+iNat 合并 + 珍稀鸟种
python $P birdspot  --city 威海 --token YOUR_EBIRD_TOKEN        # 改用 eBird 官方热点库
python $P bird      --source ebird --region CN-37 --token YOUR_EBIRD_TOKEN  # 只用 eBird
# 也可以把 Token 放进环境变量，省掉 --token
#   Windows:      set EBIRD_API_TOKEN=xxx
#   macOS/Linux:  export EBIRD_API_TOKEN=xxx
```

```bash
# 直接 curl（调试用）
curl "https://marine-api.open-meteo.com/v1/marine?latitude=37.509&longitude=122.114&hourly=sea_level_height_msl&forecast_days=10&timezone=Asia%2FShanghai"
curl "https://www.cyclecalcs.com/v2/today?lat=29.6&lon=106.5"
curl "https://api.sunrise-sunset.org/json?lat=37.509&lng=122.114&date=2026-09-25&formatted=0"
curl "https://geocoding-api.open-meteo.com/v1/search?name=%E5%A8%81%E6%B5%B7&count=1&language=zh&format=json"

# 观鸟（免 Key）——注意 taxon_id=3 是鸟纲，locale 必须是 zh-CN（zh 会返回繁体）
curl "https://api.inaturalist.org/v1/observations?taxon_id=3&lat=37.509&lng=122.114&radius=25&per_page=3"
curl "https://api.inaturalist.org/v1/taxa/autocomplete?q=%E9%BB%91%E5%B0%BE%E9%B8%A5"
curl "https://api.inaturalist.org/v1/taxa?id=4370,6916&locale=zh-CN&per_page=2"

# eBird（需 Token；403 = 鉴权层拦截，不代表路径错误）
curl -H "X-eBirdApiToken: YOUR_TOKEN" "https://api.ebird.org/v2/data/obs/geo/recent?lat=37.509&lng=122.114&dist=25&back=14"
```

```bash
# 直接 curl（调试用）
curl "https://marine-api.open-meteo.com/v1/marine?latitude=37.509&longitude=122.114&hourly=sea_level_height_msl&forecast_days=10&timezone=Asia%2FShanghai"
curl "https://www.cyclecalcs.com/v2/today?lat=29.6&lon=106.5"
curl "https://api.sunrise-sunset.org/json?lat=37.509&lng=122.114&date=2026-09-25&formatted=0"
curl "https://geocoding-api.open-meteo.com/v1/search?name=%E5%A8%81%E6%B5%B7&count=1&language=zh&format=json"
curl -H "X-eBirdApiToken: YOUR_TOKEN" "https://api.ebird.org/v2/data/obs/CN-37/recent?back=7"
```
