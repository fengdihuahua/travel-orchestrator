# 仓库结构（维护者文档）

**运行时不必读这份。** 只有两类人会用到：改渲染器/规则引擎的人，和准备上架打包的人。
SKILL.md 只留一句指向这里，是为了不让文件清单占掉每次加载都付的 token。

## 顶层

```
travel-orchestrator/
├── SKILL.md              # 主流程 S0-S10（模型每次都会读，尽量瘦；frontmatter 只有 name+description）
├── adapters/             # 宿主专有 manifest（WorkBuddy workbuddy.json，打包时映射进包根）
├── README.md CHANGELOG.md  # 给人看的说明与变更史（USAGE.md 已撤出到本地 dev/usage/）
├── references/           # 领域文档，模型按需读
├── scripts/              # 确定性脚本（分片/渲染/复核/取数）
├── assets/handbook.css   # 交付页样式源
├── tools/                # 维护者工具，不进上架包
├── examples/ tests/      # 示例与回归测试，不进上架包
└── _shots/                     # 截图产物，不进上架包（generated-images/ 于 v4.14.0 删除）
```

## 上架包里有什么、没什么

`tools/pack.py` 打出的 zip **只含**：`SKILL.md`（注入 WorkBuddy 平台字段后的版本）、
`workbuddy.json`（由 `adapters/workbuddy/workbuddy.json` 映射到包根）、`references/`、
`scripts/`、`assets/`，以及 README/LICENSE 一类根目录文件。

**必须排除**（打包脚本里有对应断言，别改回去）：

| 目录 | 为什么排除 |
|---|---|
| `examples/` | 平台只支持「根目录 / 二级目录 / 文件」，即 **skill 目录下最多 1 层子目录**；`examples/weihai-2d/` 下是 2 层，超限会被拒 |
| `tests/` | 回归夹具，使用者用不到 |
| `tools/` | 与安装口径一致（安装后不需要维护者工具） |
| `adapters/` / `dev/` | 宿主 manifest 经 EXTRA_ROOT_FILES 映射进包（不以子目录进）；dev/ 是纯本地开发文档，不进仓库不进包 |
| `__pycache__` / `.workbuddy` / `.git` | 中间产物与本地状态 |

需要看示例或跑测试时，从[源码仓库](https://github.com/fengdihuahua/travel-orchestrator)取。

## examples/ 与 tests/

- `examples/weihai-2d/`：完整示例，13 个分片（`01_request.json` … `27_decisions.json`，含
  `facts`/`sources`/`red_team` 与访谈拍板记录）+ 渲染出的 MD + HTML。
  分片直接平铺在目录里（没有 `parts/` 子目录）——两种形态 `resolve_trip` 都认。
- `tests/run_tests.py`：规则引擎与渲染器的回归测试，含故意写坏的对照组（验证阻断项能被正确抓出）。
  跑法：`python tests/run_tests.py`。不需要网络、不需要第三方库。
- 改了 `red_team.py` 的规则或 `render_plan.py` 的输出，**必须同步更新示例产物**，
  否则 `verify_all.py` 的"示例同步"一步会失败。

## tools/（维护者用）

| 工具 | 作用 |
|---|---|
| `verify_all.py` | 验收流水线：编译 → 测试 → 红方 → 渲染 → 一致性 → 示例同步。`--refresh` 顺带刷新示例产物 |
| `install.py` | 源码 → 宿主 skill 目录增量同步（`--host workbuddy\|kimi`，默认 workbuddy；workbuddy 装注入版 SKILL.md，kimi 装源码最小集） |
| `pack.py` | 打包上架 zip（排除规则见上；SKILL.md 打包时注入 WorkBuddy 平台字段） |
| `shot.py` | 截图（`--measure` 量元素、`--offsets` 滚动到指定位置、`--dsf 2` 看清小字） |
| `probe.py` | 列出元素的盒模型/display，定位错位 |
| `icon.py` | 生成市场头像（`_shots/icon.png`，纯 PIL） |

**Chrome 命令行两个坑**：不吃相对路径（`--screenshot=` 必须给绝对路径，否则静默不写文件）；
不能选 print 媒体（`--print` 的做法是把 HTML 里的 `@media print` 临时改成 `@media all`）。
