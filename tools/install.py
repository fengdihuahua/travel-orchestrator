#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install.py · 把本项目安装（或更新）到某个宿主的 skill 目录

项目目录（本仓库）是可编辑的源码；安装目录是运行时副本。
改代码在项目目录改，改完跑一次这个脚本同步过去。

支持的宿主（--host）：
- workbuddy：%USERPROFILE%\\.workbuddy\\skills\\travel-orchestrator
  （装的是**注入版** SKILL.md：frontmatter 带开放平台要求的 display_name/
   category/version 等字段，与上架 zip 同口径）
- kimi：     %APPDATA%\\kimi-desktop\\daimon-share\\daimon\\skills\\travel-orchestrator
  （Kimi Work 的托管 skill 目录；装完在新会话的技能索引里出现；
   装的是源码版 SKILL.md，开放标准最小集 frontmatter）

为什么是"增量同步"而不是"删掉重装"
------------------------------------
早先的实现是 `shutil.rmtree(安装目录)` 再整目录复制。实测有两个问题：
① 一次性删除几十上百个文件会触发宿主的安全删除保护，脚本会停在确认环节，
   现场表现就是"打包卡住了"，其实没报错；
② 目录被删空的那一瞬间如果中断，安装目录就废了。
所以改成：逐个文件比对内容，不同的才覆盖；源码里已删掉的文件才从安装目录移除。

用法：
    python tools/install.py                      # 装到 workbuddy 默认位置
    python tools/install.py --host kimi          # 装到 kimi 的 skill 目录
    python tools/install.py --dest <目录>         # 装到指定位置（注入口径仍跟 --host 走）
    python tools/install.py --dry-run             # 只看会做什么，不动文件
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_NAME = "travel-orchestrator"

# 排除清单**不在这里定义**，直接复用 pack.py 的那一份。
# 理由：安装目录的用途就是"本地看到的东西与上架包里的一致"，两份清单各维护一份
# 迟早会漂——上一版 pack 排除了 tools/ 而这里没排，装完目录里就多出 5 个开发脚本，
# 而没人会去比对两个文件。想改口径就改 pack.py，这里跟着走。
sys.path.insert(0, HERE)
from pack import EXCLUDE_DIRS, EXCLUDE_FILES, EXTRA_ROOT_FILES, EXCLUDE_SUFFIX  # noqa: E402
from pack import workbuddy_skill_md  # noqa: E402


def default_dest(host):
    if host == "kimi":
        # Kimi Work 桌面版（Windows）的托管 skill 根目录
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "kimi-desktop", "daimon-share", "daimon", "skills", SKILL_NAME)
    # workbuddy（默认）
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", SKILL_NAME)


def walk_sources():
    """[(源文件绝对路径, 安装目录相对路径)]。与 pack.collect() 同一口径。"""
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for f in sorted(files):
            if f in EXCLUDE_FILES or f.endswith(EXCLUDE_SUFFIX):
                continue
            full = os.path.join(root, f)
            yield full, os.path.relpath(full, ROOT)
    for src, dst in EXTRA_ROOT_FILES.items():
        full = os.path.join(ROOT, src)
        if os.path.exists(full):
            yield full, dst


def walk_dest(dest):
    for root, dirs, files in os.walk(dest):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for f in sorted(files):
            full = os.path.join(root, f)
            yield full, os.path.relpath(full, dest)


def prune_empty(dest, rel):
    """删掉文件后，把随之变空的目录一层层收掉（不越过 dest）。"""
    p = os.path.dirname(os.path.join(dest, rel))
    while p.startswith(dest) and p != dest and os.path.isdir(p) and not os.listdir(p):
        os.rmdir(p)
        p = os.path.dirname(p)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="安装 travel-orchestrator 到 skill 目录")
    ap.add_argument("--host", choices=("workbuddy", "kimi"), default="workbuddy",
                    help="目标宿主（决定默认安装位置与 SKILL.md 注入口径），默认 workbuddy")
    ap.add_argument("--dest", default=None, help="目标目录（给了就装这里；注入口径仍跟 --host 走）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    args = ap.parse_args(argv)

    dest = os.path.abspath(args.dest or default_dest(args.host))
    print(f"宿主: {args.host}")
    print(f"源  : {ROOT}")
    print(f"目标: {dest}")

    files = list(walk_sources())
    if not files:
        print("x 没找到任何文件，路径可能不对", file=sys.stderr)
        return 2

    # workbuddy 装的是"注入版" SKILL.md（开放平台 frontmatter 全字段），
    # 与上架 zip 同口径；kimi 装源码版（开放标准最小集）。
    inject = args.host == "workbuddy"
    if inject:
        files = [(full, rel) for full, rel in files if rel != "SKILL.md"]

    src_rels = {rel for _, rel in files}
    if inject:
        src_rels.add("SKILL.md")  # 注入版也算"源里有"，否则会被当 stale 删掉
    stale = [rel for _, rel in walk_dest(dest) if rel not in src_rels] if os.path.isdir(dest) else []

    changed, skipped = [], []
    if not args.dry_run:
        os.makedirs(dest, exist_ok=True)
        for full, rel in files:
            target = os.path.join(dest, rel)
            if os.path.exists(target) and filecmp.cmp(full, target, shallow=False):
                skipped.append(rel)
                continue
            os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
            shutil.copy2(full, target)
            changed.append(rel)
        if inject:
            with open(os.path.join(dest, "SKILL.md"), "wb") as f:
                f.write(workbuddy_skill_md())
            changed.append("SKILL.md（注入宿主 frontmatter）")
        for rel in stale:
            os.remove(os.path.join(dest, rel))
            prune_empty(dest, rel)
    else:
        print("（dry-run，不写文件）")

    verb = "将安装" if args.dry_run else "已安装"
    if args.dry_run:
        print(f"{verb} {len(files)} 个文件：")
        for _, rel in files:
            print("  " + rel.replace(os.sep, "/"))
        if inject:
            print("  SKILL.md（注入宿主 frontmatter，与上架包同口径）")
        if stale:
            print(f"将清理 {len(stale)} 个源码里已不存在的文件：")
            for rel in stale:
                print("  - " + rel.replace(os.sep, "/"))
        return 0

    print(f"{verb}：更新 {len(changed)} 个、未变 {len(skipped)} 个、清理 {len(stale)} 个")
    for rel in changed:
        print("  ~ " + rel.replace(os.sep, "/"))
    for rel in stale:
        print("  - " + rel.replace(os.sep, "/"))

    # 自检：同步后两边必须一致（SKILL.md 在注入目标比对注入字节）
    src_of = {rel: full for full, rel in files}
    bad = []
    for _, rel in walk_dest(dest):
        target = os.path.join(dest, rel)
        if rel not in src_rels:
            bad.append(rel)
        elif rel == "SKILL.md" and inject:
            with open(target, "rb") as f:
                if f.read() != workbuddy_skill_md():
                    bad.append(rel)
        elif not filecmp.cmp(src_of[rel], target, shallow=False):
            bad.append(rel)
    print()
    if bad:
        print("x 同步后仍不一致：" + "、".join(bad), file=sys.stderr)
        return 1
    print(f"完成，{len(files)} 个文件三方一致。若宿主已在运行，重启会话后生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
