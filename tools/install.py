#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install.py · 把本项目安装（或更新）到 WorkBuddy 的 skill 目录

项目目录（本仓库）是可编辑的源码；安装目录是运行时副本。
改代码在项目目录改，改完跑一次这个脚本同步过去。

为什么是"增量同步"而不是"删掉重装"
------------------------------------
早先的实现是 `shutil.rmtree(安装目录)` 再整目录复制。实测有两个问题：
① 一次性删除几十上百个文件会触发宿主的安全删除保护，脚本会停在确认环节，
   现场表现就是"打包卡住了"，其实没报错；
② 目录被删空的那一瞬间如果中断，安装目录就废了。
所以改成：逐个文件比对内容，不同的才覆盖；源码里已删掉的文件才从安装目录移除。

用法：
    python tools/install.py                 # 装到默认位置
    python tools/install.py --dest <目录>    # 装到指定位置
    python tools/install.py --dry-run        # 只看会做什么，不动文件

默认目标：
    Windows  %USERPROFILE%\\.workbuddy\\skills\\travel-orchestrator
    macOS /
    Linux    ~/.workbuddy/skills/travel-orchestrator
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

# 这些是开发用的，不进安装目录
EXCLUDE_DIRS = {"__pycache__", ".git", "tools", "docs", ".workbuddy"}
EXCLUDE_FILES = {".gitignore", ".gitattributes", ".DS_Store"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".bak", ".tmp", ".out")


def default_dest():
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", SKILL_NAME)


def walk_sources():
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for f in sorted(files):
            if f in EXCLUDE_FILES or f.endswith(EXCLUDE_SUFFIX):
                continue
            full = os.path.join(root, f)
            yield full, os.path.relpath(full, ROOT)


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
    ap.add_argument("--dest", default=default_dest(), help="目标目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    args = ap.parse_args(argv)

    dest = os.path.abspath(args.dest)
    print(f"源  : {ROOT}")
    print(f"目标: {dest}")

    files = list(walk_sources())
    if not files:
        print("x 没找到任何文件，路径可能不对", file=sys.stderr)
        return 2

    src_rels = {rel for _, rel in files}
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

    # 自检：同步后两边必须逐字节一致
    bad = [rel for _, rel in walk_dest(dest)
           if rel not in src_rels
           or not filecmp.cmp(os.path.join(ROOT, rel), os.path.join(dest, rel), shallow=False)]
    print()
    if bad:
        print("x 同步后仍不一致：" + "、".join(bad), file=sys.stderr)
        return 1
    print(f"完成，{len(files)} 个文件三方一致。若 WorkBuddy 已在运行，重启会话后生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
