#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pack.py · 打一个可直接上传到 SkillHub 的 zip

为什么要有这个脚本
------------------
上传包必须满足平台约束（包内要有 `SKILL.md`、体积有上限），而"哪些文件该进包"
这件事每次靠记忆手敲命令就会漏（上一版就漏了新加的 `.gitattributes`，
又多带了不该带的缓存）。所以把规则固定在这里，每次发版跑一次。

用法：
    python tools/pack.py                    # 默认输出到 <工作区>/.workbuddy/dist/
    python tools/pack.py --out <目录>        # 指定输出目录
    python tools/pack.py --list             # 只列出会进包的文件

包里**不含**开发用的东西：`.gitignore` / `.gitattributes` / `__pycache__` /
`*.pyc` / `*.bak` / `*.out` / `.DS_Store` / `tools/` / `SUBMISSION.md` / `tests/`。
**`examples/` 也必须排除**：平台只支持「根目录 / 二级目录 / 文件」，
而 `examples/weihai-2d/` 在 skill 目录下占 2 层子目录，上传会被拒。
（早先这里写"examples 要留在包里"是错的，与代码里的 EXCLUDE_DIRS 不一致，照那句话做会打包失败。）

排除清单**只有本文件这一份**，`install.py` 直接 import 过去用。早先两份各写一遍漂过：
pack.py 漏了 `tools/`（5 个开发脚本跟着上了架），install.py 漏了 `generated-images/`。

脚本会盯住三条平台约束：包内有 `SKILL.md`、体积不超 3 MB、**skill 目录下不超过
1 层子目录**。

关于"几层"的说法：平台原文是「根目录 / 二级目录 / 文件」，这个仓库统一按
**"skill 目录下的子目录层数"** 计数，上限 1——
`skill/SKILL.md` 是 0 层、`skill/references/a.md` 是 1 层（合规）、
`skill/examples/weihai-2d/x.json` 是 2 层（超限）。
早先文档里混用"三层/四层"（一个把文件算进去、一个不算），说的其实是同一条路径，
读的人容易以为是两个不同的限制，所以现在只用这一种口径。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_NAME = "travel-orchestrator"
# 产物放到**工作区**的 .workbuddy/dist/，不要落到源码目录里
# （源码目录里出现 .workbuddy/ 会被同步脚本当成项目文件，也会让人误以为包在仓库内）
DIST = os.path.join(os.path.dirname(ROOT), ".workbuddy", "dist")

# 平台对 zip 的体积上限是 3 MB，留点余量
SIZE_WARN = 2.5 * 1024 * 1024
SIZE_LIMIT = 3 * 1024 * 1024

# 目录排除清单。**这里是唯一的真源**，install.py 的 EXCLUDE_DIRS 必须与它逐项一致——
# 两份清单各改一份是上一版踩过的坑（pack 排除了 tools/，install 没排，装完目录里多出开发脚本）。
# 分四类：
#   ① 开发用：tools/ tests/ examples/（examples 还额外违反平台的层级限制，见下）
#   ② 本地状态：.git/ .workbuddy/ travel-plan/
#   ③ 截图与配图产物：_shots/ generated-images/（几 MB 的 PNG，一旦漏排就顶穿 3 MB 上限）
#   ④ 临时目录：_tmp/
# travel-plan/ 是跑出来的行程数据（含个人出行信息），.gitignore 里也排除了它；
# 它还是三层结构（travel-plan/<trip>/parts/*.json），漏排会直接违反平台层级限制。
EXCLUDE_DIRS = {"__pycache__", ".git", ".workbuddy", "_tmp", "_shots",
                "generated-images", "tools", "tests", "examples", "travel-plan"}
# SUBMISSION.md 是本地内部材料（活动文案、待办笔记），不是技能内容，不进包
EXCLUDE_FILES = {".gitignore", ".gitattributes", ".DS_Store", "SUBMISSION.md"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".bak", ".tmp", ".out")

# 平台结构约束（开放平台原文）：只支持「根目录 / 二级目录 / 文件」。
# 计数口径统一为「skill 目录下的子目录层数」，上限 1：
#   skill/SKILL.md                → 0 层，合规
#   skill/references/a.md         → 1 层，合规
#   skill/examples/weihai-2d/x.json → 2 层，超限
# 所以 examples/ 必须整体排除，不能靠"把分片平铺"绕过（平铺只减少一层，仍超）。
MAX_DEPTH = 1

# 开放平台要求 frontmatter 里的这几项，缺一项上传就解析失败
REQUIRED_FM = ("name", "description", "description_zh", "description_en",
               "version", "author", "category")


def collect():
    files = []
    for root, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for f in sorted(names):
            if f in EXCLUDE_FILES or f.endswith(EXCLUDE_SUFFIX):
                continue
            files.append(os.path.join(root, f))
    return files


def check_meta():
    """发版前最少要确认：SKILL.md 在、frontmatter 必填项齐。"""
    skill = os.path.join(ROOT, "SKILL.md")
    if not os.path.exists(skill):
        return False, ["SKILL.md 不存在"]
    with io.open(skill, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return False, ["SKILL.md 没有 YAML frontmatter"]
    fm = m.group(1)
    problems = [k for k in REQUIRED_FM if not re.search(r"^%s:" % k, fm, re.M)]
    return (not problems), ["frontmatter 缺 " + k for k in problems]


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="打包 " + SKILL_NAME + " 为可上传的 zip")
    ap.add_argument("--out", default=DIST,
                    help="输出目录，默认 <工作区>/.workbuddy/dist（不是源码目录里面）")
    ap.add_argument("--list", action="store_true", help="只列出会进包的文件")
    args = ap.parse_args(argv)

    ok, problems = check_meta()
    if not ok and not args.list:
        for p in problems:
            print("x " + p, file=sys.stderr)
        print("  先修好再打包（缺必填项上传会解析失败）", file=sys.stderr)
        return 1

    files = collect()
    if args.list:
        for p in files:
            print(os.path.relpath(p, ROOT).replace(os.sep, "/"))
        print("共 %d 个文件" % len(files))
        return 0

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, SKILL_NAME + ".zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            # 包内统一带一层 skill 目录，平台按目录名识别 skill
            z.write(p, os.path.join(SKILL_NAME, os.path.relpath(p, ROOT)))

    size = os.path.getsize(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bad = z.testzip()
        has_skill = (SKILL_NAME + "/SKILL.md") in names
        # 平台原文「只支持 根目录/二级目录/文件」，即 skill 目录下最多一层子目录
        max_folders = 0
        deep = []
        for n in names:
            inner = n.strip("/")[len(SKILL_NAME):].strip("/")
            folders = [p for p in inner.split("/")[:-1] if p]
            if len(folders) > max_folders:
                max_folders = len(folders)
            if len(folders) > MAX_DEPTH:
                deep.append(inner)

    print("已打包：" + zip_path)
    print("  文件 %d 个，skill 目录下最大层数 %d 层（上限 %d）"
          % (len(names), max_folders, MAX_DEPTH))
    print("  体积 %.0f KB" % (size / 1024.0))
    problems = []
    if max_folders > MAX_DEPTH:
        problems.append("目录层级 %d 层，超过平台的 %d 层限制（skill/二级目录/文件）：%s"
                        % (max_folders, MAX_DEPTH,
                           "、".join(deep[:5]) + ("…" if len(deep) > 5 else "")))
    if bad:
        problems.append("zip 损坏于 " + bad)
    if not has_skill:
        problems.append("包内缺 " + SKILL_NAME + "/SKILL.md")
    if size > SIZE_LIMIT:
        problems.append("超过平台 3 MB 上限")
    elif size > SIZE_WARN:
        print("  ! 体积接近上限，考虑精简示例或产物")
    if problems:
        for p in problems:
            print("x " + p, file=sys.stderr)
        # 自检不过就把包删掉。留在那儿最危险：文件名正常、大小正常，
        # 很容易被当成"已经打好了"直接传上去，然后在平台上被拒。
        try:
            os.remove(zip_path)
            print("  已删除这份不合规的包：" + zip_path, file=sys.stderr)
        except OSError:
            pass
        return 1
    print("  自检通过（SKILL.md 在、体积与目录层级未超限）。上传前建议再跑一遍：")
    print("    python tools/verify_all.py")
    print("    python tools/install.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
