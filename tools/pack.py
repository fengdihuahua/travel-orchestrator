#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pack.py · 按宿主打包（adapters/hosts.json 注册表驱动）

为什么要有这个脚本
------------------
"哪些文件该进包、哪个宿主需要什么 manifest、frontmatter 要不要注入"这些规则
每次靠记忆手敲命令就会漏（上一版就漏了新加的 `.gitattributes`，又多带了不该带的缓存）。
所以把规则固定在注册表（adapters/hosts.json）和这个脚本里，每次发版跑一次。

用法：
    python tools/pack.py                      # 给注册表里所有宿主各打一份，输出到 <工作区>/.workbuddy/dist/<宿主>/
    python tools/pack.py --host workbuddy     # 只打某一个宿主
    python tools/pack.py --out <目录>          # 指定输出根目录
    python tools/pack.py --list [--host X]     # 只列出会进包的文件

每种宿主的包形态由注册表的 "package" 字段决定：
  zip  → <宿主>/travel-orchestrator.zip（WorkBuddy 上传用，带平台自检）
  dir  → <宿主>/travel-orchestrator/     （整目录复制即装的形态，Claude Code / Cursor / Codex / Kimi 等）

包里**不含**开发用的东西：`.gitignore` / `.gitattributes` / `__pycache__` /
`*.pyc` / `*.bak` / `*.out` / `.DS_Store` / `tools/` / `tests/` / `SUBMISSION.md` /
`adapters/` / `dev/`。
**`examples/` 也必须排除**：WorkBuddy 平台只支持「根目录 / 二级目录 / 文件」，
而 `examples/weihai-2d/` 在 skill 目录下占 2 层子目录，上传会被拒。

`adapters/` 里的宿主专有 manifest 按注册表 "manifest" 映射进包（如 WorkBuddy 的
workbuddy.json → 包内根、Codex 的 openai.yaml → agents/openai.yaml）；
SKILL.md 的宿主专有 frontmatter 字段由 inject_skill_md() 在打包时注入——源码里的
SKILL.md 遵循开放标准最小集（只有 name + description），对标准宿主零污染。

排除清单**只有本文件这一份**，`install.py` 直接 import 过去用。早先两份各写一遍漂过：
pack.py 漏了 `tools/`（5 个开发脚本跟着上了架），install.py 漏了 `generated-images/`。

WorkBuddy 的 zip 会盯住三条平台约束：包内有 `SKILL.md`、体积不超 3 MB、
**skill 目录下不超过 1 层子目录**。

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
import json
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_NAME = "travel-orchestrator"
HOSTS_JSON = os.path.join(ROOT, "adapters", "hosts.json")
# 产物放到**工作区**的 .workbuddy/dist/，不要落到源码目录里
# （源码目录里出现 .workbuddy/ 会被同步脚本当成项目文件，也会让人误以为包在仓库内）
DIST = os.path.join(os.path.dirname(ROOT), ".workbuddy", "dist")

# WorkBuddy 平台对 zip 的体积上限是 3 MB，留点余量
SIZE_WARN = 2.5 * 1024 * 1024
SIZE_LIMIT = 3 * 1024 * 1024

# 目录排除清单。**这里是唯一的真源**，install.py 的 EXCLUDE_DIRS 必须与它逐项一致——
# 两份清单各改一份是上一版踩过的坑（pack 排除了 tools/，install 没排，装完目录里多出开发脚本）。
# 分五类：
#   ① 开发用：tools/ tests/ examples/（examples 还额外违反 WorkBuddy 的层级限制，见下）
#   ② 本地状态：.git/ .workbuddy/ travel-plan/
#   ③ 截图与配图产物：_shots/ generated-images/（几 MB 的 PNG，一旦漏排就顶穿 3 MB 上限）
#   ④ 临时目录：_tmp/
#   ⑤ 宿主适配与开发文档：adapters/ dev/（adapters/ 的内容按注册表 "manifest" 映射进包，
#      dev/ 是纯本地开发文档，不进包）
# travel-plan/ 是跑出来的行程数据（含个人出行信息），.gitignore 里也排除了它；
# 它还是三层结构（travel-plan/<trip>/parts/*.json），漏排会直接违反平台层级限制。
EXCLUDE_DIRS = {"__pycache__", ".git", ".workbuddy", "_tmp", "_shots",
                "generated-images", "tools", "tests", "examples", "travel-plan",
                "adapters", "dev"}
# SUBMISSION.md 是本地内部材料（活动文案、待办笔记），CHANGELOG.md 给人看的变更史，
# 都不是技能运行时内容，不进包
EXCLUDE_FILES = {".gitignore", ".gitattributes", ".DS_Store", "SUBMISSION.md", "CHANGELOG.md"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".bak", ".tmp", ".out")

# 平台结构约束（WorkBuddy 开放平台原文）：只支持「根目录 / 二级目录 / 文件」。
# 计数口径统一为「skill 目录下的子目录层数」，上限 1。dir 形态的包也按它检查：
# 目录形态虽然多数宿主不限层数，但统一口径能防"哪天又冒出来一个严格宿主"。
MAX_DEPTH = 1

# 仓库里 SKILL.md 的 frontmatter 遵循 Agent Skills 开放标准最小集（agentskills.io：
# 只有 name + description 是跨宿主公认字段，多出来的字段严格宿主可能拒载）。
REQUIRED_FM = ("name", "description")
DESC_LIMIT = 1024  # 规范上限


# ---------------------------------------------------------------- 宿主注册表

def load_hosts():
    """读 adapters/hosts.json；坏了就给出带定位的错误，不静默跑空。"""
    with io.open(HOSTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    hosts = data.get("hosts") or {}
    if not hosts:
        raise SystemExit("adapters/hosts.json 里没有 hosts，无法打包")
    return hosts


def expand_path(template):
    """注册表 skill_dir 展开：先环境变量（%APPDATA% 等）再 ~。"""
    return os.path.expanduser(os.path.expandvars(template))


def host_manifest(entry):
    """注册表 "manifest"：{仓库内路径: 包内/安装目录相对路径}，缺省空。"""
    return dict(entry.get("manifest") or {})


def host_frontmatter_fields(entry):
    """frontmatter 注入字段清单；min(inject 模式下缺清单/字段时兜底)。"""
    return list(entry.get("frontmatter_fields") or ())


# ---------------------------------------------------------------- 文件收集与注入

def collect(manifest=None):
    """返回 [(仓库内绝对路径, 包内相对路径)]。manifest 映射优先追加。"""
    files = []
    for root, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for f in sorted(names):
            if f in EXCLUDE_FILES or f.endswith(EXCLUDE_SUFFIX):
                continue
            full = os.path.join(root, f)
            files.append((full, os.path.relpath(full, ROOT)))
    for src, dst in (manifest or {}).items():
        full = os.path.join(ROOT, src)
        if os.path.exists(full):
            files.append((full, dst))
        else:
            raise SystemExit("注册表 manifest 指向的源文件不存在：" + src)
    return files


def inject_skill_md(meta, fields):
    """生成注入宿主专有 frontmatter 的 SKILL.md 字节。

    源码保持开放标准最小集；宿主额外要求的字段（如 WorkBuddy 开放平台的
    display_name/category/version…）来自该宿主 manifest 里的元数据，
    在打包/安装时拼接进去。
    """
    with io.open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        raise SystemExit("SKILL.md 没有 YAML frontmatter，无法注入宿主字段")
    missing = [k for k in fields if k not in meta]
    if missing:
        raise SystemExit("宿主 manifest 缺 frontmatter 字段：" + "、".join(missing))
    q = lambda s: json.dumps(s, ensure_ascii=False)  # YAML 双引号字符串，转义安全
    extra = "".join(
        "%s: %s\n" % (k, q(meta[k]) if k.startswith("description") else meta[k])
        for k in fields
    )
    injected = "---\n" + m.group(1).rstrip() + "\n" + extra + "---\n" + text[m.end():]
    return injected.encode("utf-8")


def skill_md_bytes(entry):
    """按注册表 frontmatter 模式给出包内 SKILL.md 内容。"""
    mode = entry.get("frontmatter", "minimal")
    if mode == "inject":
        src = next(iter(host_manifest(entry)), None)
        if not src:
            raise SystemExit("frontmatter=inject 的宿主必须在 manifest 里给元数据来源")
        with io.open(os.path.join(ROOT, src), encoding="utf-8") as f:
            meta = json.load(f)
        return inject_skill_md(meta, host_frontmatter_fields(entry))
    with io.open(os.path.join(ROOT, "SKILL.md"), "rb") as f:
        return f.read()


def check_meta():
    """发版前最少要确认：SKILL.md 在、开放标准必填项齐、description 合规。"""
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
    desc = re.search(r"^description:\s*(.*?)\s*$", fm, re.M)
    if desc:
        if len(desc.group(1)) > DESC_LIMIT:
            problems.append("description 超 %d 字符（规范上限）" % DESC_LIMIT)
        if "<" in desc.group(1) or ">" in desc.group(1):
            problems.append("description 含尖括号（官方校验器会拒）")
    return (not problems), problems


# ---------------------------------------------------------------- 打包

def rel_depth(rel):
    """skill 目录下的子目录层数（文件本身不算）。skill/SKILL.md → 0。"""
    return len([p for p in rel.replace(os.sep, "/").split("/")[:-1] if p])


def pack_zip(host, entry, files, skill_bytes, out_dir):
    """zip 形态（WorkBuddy 上传用），带平台自检，不过就删包。"""
    zip_path = os.path.join(out_dir, SKILL_NAME + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in files:
            arc = os.path.join(SKILL_NAME, rel)
            z.writestr(arc, skill_bytes) if rel == "SKILL.md" else z.write(full, arc)

    size = os.path.getsize(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bad = z.testzip()
        has_skill = (SKILL_NAME + "/SKILL.md") in names
        rels = [n.strip("/")[len(SKILL_NAME):].strip("/") for n in names]
    deep = [r for r in rels if rel_depth(r) > MAX_DEPTH]

    print("已打包：" + zip_path)
    print("  文件 %d 个，skill 目录下最大层数 %d 层（上限 %d）"
          % (len(names), max((rel_depth(r) for r in rels), default=0), MAX_DEPTH))
    print("  体积 %.0f KB" % (size / 1024.0))
    problems = []
    if deep:
        problems.append("目录层级超限（skill/二级目录/文件）：%s"
                        % ("、".join(deep[:5]) + ("…" if len(deep) > 5 else "")))
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
    return 0


def pack_dir(host, entry, files, skill_bytes, out_dir):
    """dir 形态：整目录复制即装。已存在则整目录换掉（dir 包是自包含产物）。"""
    dest = os.path.join(out_dir, SKILL_NAME)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for full, rel in files:
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
        if rel == "SKILL.md":
            with open(target, "wb") as f:
                f.write(skill_bytes)
        else:
            shutil.copy2(full, target)
    deep = [rel for _, rel in files if rel_depth(rel) > MAX_DEPTH]
    print("已打包：" + dest)
    print("  文件 %d 个" % len(files))
    if deep:
        for p in deep:
            print("x 目录层级超限：" + p, file=sys.stderr)
        shutil.rmtree(dest)
        print("  已删除这份不合规的包：" + dest, file=sys.stderr)
        return 1
    return 0


def pack_host(host, entry, out_root):
    files = collect(host_manifest(entry))
    skill_bytes = skill_md_bytes(entry)
    out_dir = os.path.join(out_root, host)
    os.makedirs(out_dir, exist_ok=True)
    if entry.get("package", "dir") == "zip":
        return pack_zip(host, entry, files, skill_bytes, out_dir)
    return pack_dir(host, entry, files, skill_bytes, out_dir)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    hosts = load_hosts()
    ap = argparse.ArgumentParser(description="按宿主打包 " + SKILL_NAME
                                 + "（注册表：adapters/hosts.json）")
    ap.add_argument("--host", choices=sorted(hosts), default=None,
                    help="只打某一个宿主；缺省打全部")
    ap.add_argument("--out", default=DIST,
                    help="输出根目录，默认 <工作区>/.workbuddy/dist（不是源码目录里面）")
    ap.add_argument("--list", action="store_true", help="只列出会进包的文件")
    args = ap.parse_args(argv)

    ok, problems = check_meta()
    if not ok and not args.list:
        for p in problems:
            print("x " + p, file=sys.stderr)
        print("  先修好再打包", file=sys.stderr)
        return 1

    targets = {args.host: hosts[args.host]} if args.host else hosts
    if args.list:
        for host, entry in targets.items():
            print("== %s（%s）==" % (host, entry.get("label", host)))
            for _, rel in collect(host_manifest(entry)):
                print("  " + rel.replace(os.sep, "/"))
        return 0

    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    bad = 0
    for host, entry in targets.items():
        print("== %s（%s）== frontmatter=%s package=%s"
              % (host, entry.get("label", host),
                 entry.get("frontmatter", "minimal"), entry.get("package", "dir")))
        bad += pack_host(host, entry, out_root)
        print()
    if bad:
        print("x %d 个宿主的包不合规" % bad, file=sys.stderr)
        return 1
    print("全部宿主打包完成。上传/安装前建议再跑一遍：")
    print("  python tools/verify_all.py")
    print("  python tools/install.py --host <宿主>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
