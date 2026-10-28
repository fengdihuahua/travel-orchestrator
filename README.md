# travel-orchestrator · 旅行规划总编排

一个自带**红方复核**的旅行规划 Skill，遵循 [Agent Skills 开放标准](https://agentskills.io)
（`SKILL.md` 最小 frontmatter，任何标准宿主直接可用）。

通用 AI 旅行规划不知道潮汐会涨：赶海窗口只有干潮前后一个多小时，观星撞上满月银河不可见，
日出算错半小时到了地方太阳已经升完。这个 Skill 把潮汐、月落、日出当成**硬约束**——
先锁死这些改不了的时刻，再倒推整条动线；交付前再由质疑方逐项挑刺，阻断项清零才放行。
每个数字可溯源，查不到的标 `unknown`，不编。

- 零第三方依赖，纯 Python 标准库（无脚本运行时也有正式降级模式）
- 交付单文件离线 HTML 攻略，双击就开：能点、会动、可直接打印
- 所有实时数据走免费接口，不需要注册也不需要 API Key

## 安装

这是一个标准 Agent Skill：把整个目录复制到任意标准宿主的 skill 目录即可。

| 宿主 | 目录 |
|---|---|
| WorkBuddy | `%USERPROFILE%\.workbuddy\skills\travel-orchestrator` |
| Kimi Work | `%APPDATA%\kimi-desktop\daimon-share\daimon\skills\travel-orchestrator` |
| 其他标准宿主 | 手动把整个目录复制到它们的 `skills/` 目录 |

源码仓库用自带脚本同步（`--dest` 可装到任意位置）：

```bash
python tools/install.py                  # WorkBuddy（默认，SKILL.md 注入平台字段版）
python tools/install.py --host kimi      # Kimi Work（开放标准最小集版）
```

装完重启会话生效。**不需要装任何 Python 包**，标准库够用。

## 快速开始

对装了它的 agent 说一句就行：

> 帮我规划 10 月 1 日到 2 日去威海，两个人，预算 2500，想赶海和观鸟。

它会先逐层访谈问清关键项，然后锁死赶海窗口和日出时刻 → 排行程 → 红方复核挑刺 →
渲染成 Markdown + HTML 攻略页给出路径（AI 手绘地图海报是可选项，另外说）。

仓库 `examples/weihai-2d/` 是一份完整的真实案例（北京 → 威海 2 天）：
初稿里凭空写下的干潮时刻、错过涨潮的赶海窗口、无凭证的"已确认"车次，都是红方复核抓出来改的。
`tests/run_tests.py` 里有 300+ 项断言（含故意写坏的对照组），验证规则真的能抓问题。

## 目录结构

```
travel-orchestrator/
├── SKILL.md            # 主流程 S0-S10 + S6.5 红方复核（frontmatter 仅 name + description）
├── adapters/           # 宿主专有 manifest（WorkBuddy workbuddy.json，打包时映射进包根）
├── references/         # 领域文档（数据契约 / 访谈协议 / 红方清单 / 接口清单…），模型按需读
├── scripts/            # 确定性脚本（取数 / 分片 / 校验 / 渲染 / 复核），纯标准库
├── assets/handbook.css # 交付页样式源
└── tools/ examples/ tests/   # 维护者工具、示例、回归测试：仅仓库，不进上架包
```

维护与上架细节（打包、逐宿主校验、发布流程）见 `references/repo-layout.md` 与 `tools/`；
开发过程文档在本地 `dev/`（不进仓库）。

## 已知限制

诚实写在这里，不藏在文档深处。

1. **潮汐基准面是 MSL（平均海平面）**，与当地潮高基准面可能有系统性偏差。
   安全决策（尤其"涨潮前撤离"）必须对照国家海洋信息中心或当地潮汐表复核。
2. **潮位数据有效期约 10 天**，更远日期返回空并标 `partial: true`，不会硬给窗口。
3. **朝晚霞评分是自建模型**（原 Sunset Bot 已于 2026-09 下线），参考可以，别当成承诺。
4. **观鸟数据来自 iNaturalist 公民科学记录**，说的是"最近有人在哪看到鸟"，不等于到了一定能看到。
5. **城市名可能解析到别的省**（大同在山西和黑龙江都有），时刻敏感数据建议直接用经纬度取。
6. 纯 Web / 无本地运行时的环境走**降级模式**：只交 Markdown，自然专项查不到的一律标 `unknown`。

**赶海、涉水、登山、夜行等任何涉及人身安全的活动，以官方渠道发布的实时信息为准。**
本 Skill 产出的方案不能替代专业判断与现场评估。

## 协议与致谢

MIT License，见 `LICENSE`。

开发时参考了三个现有旅行 Skill 的方法论，取其经验，**但不依赖也不调用它们**：
[bys-travel-plan](https://github.com/qkgecn93/bys-travel-plan)（MIT）、travel-planner-wb、travel-planner-v2。
