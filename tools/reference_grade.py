#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分级的**独立参照实现**（M2）。

用途：拿另一条实现路径重算分级，与生产线（`src/devdisk/grader.py` +
`evidence.py`）的结论对账，防止"引擎与自己的测试同源"。

独立性的三条硬要求（`plan/02` §三 第 3 条）：

1. **不 import `devdisk`**（AST 守门测试 `test_reference_grade_is_independent`）；
2. 候选清单来自**植入真值**（`truth/manifest.json`）而不是扫描产物；
3. 规则逻辑在本文件里**从 `plan/03` 模块② 重新抄写一遍**，与规则表
   `data/rules/grades.json` 不共享代码；两者不一致即报错。

用法：
    py -X utf8 tools/reference_grade.py --tree devdisk-out/synth --scan devdisk-out/scan.json

退出码：0 逐样本一致；1 存在不一致；2 输入缺失。
"""

import argparse
import fnmatch
import json
import os
import stat
import sys

# --------------------------------------------------------------- 参照规则
#
# 刻意写成"另一种写法"：显式 if/elif 判定链、模式直接内联，
# 而不是像生产线那样由数据表驱动。

LOCAL_STATE_PATTERNS = (".env", ".env.*", "*.local", "*.pem", "*.key")
MANUAL_PATTERNS = (".env", ".env.*", "*.local", "*.local.*", "*.pem", "*.key", "config.*")

NODE_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
                  "pnpm-lock.yaml", "bun.lockb")
PY_LOCKFILES = ("poetry.lock", "Pipfile.lock", "uv.lock", "requirements.txt",
                "pyproject.toml", "Pipfile", "setup.py", "setup.cfg")
BUILD_MARKERS = ("Makefile", "makefile", "GNUmakefile", "pyproject.toml", "setup.py",
                 "vite.config.js", "vite.config.ts", "webpack.config.js",
                 "webpack.config.ts", "rollup.config.js", "esbuild.config.js",
                 "tsconfig.json", "build.gradle", "build.gradle.kts", "pom.xml",
                 "CMakeLists.txt", "meson.build")
NEXT_MARKERS = ("package.json", "next.config.js", "next.config.mjs", "next.config.ts")

CACHE_TYPES = ("__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".cache")
ARTIFACT_TYPES = ("dist", "build", "out", "target", ".next")
VENV_TYPES = (".venv", "venv")
PROJECT_MARKERS = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod",
                   "pom.xml", "build.gradle")


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def inside(path, directory):
    try:
        return os.path.commonpath([norm(path), norm(directory)]) == norm(directory)
    except ValueError:
        return False


def walk_files(root):
    """返回 [(rel_path, name, mtime_ns)]；不跟随链接（与扫描层同口径）。"""
    found = []
    stack = [(os.path.abspath(root), "")]
    while stack:
        current, rel_dir = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name)
        except OSError:
            continue
        for entry in entries:
            rel = entry.name if not rel_dir else rel_dir + "/" + entry.name
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            attributes = getattr(st, "st_file_attributes", 0)
            if stat.S_ISLNK(st.st_mode) or (attributes & 0x400):
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append((os.path.join(current, entry.name), rel))
            elif stat.S_ISREG(st.st_mode):
                found.append((rel, entry.name, st.st_mtime_ns))
    return found


def rel_entries(directory):
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def project_root_of(root, path):
    """向上找最近的标志文件目录，不越过 root（与扫描层同口径）。"""
    current = os.path.dirname(os.path.abspath(path))
    root = os.path.abspath(root)
    while True:
        for name in rel_entries(current):
            if name in PROJECT_MARKERS or name.endswith(".csproj"):
                if os.path.isfile(os.path.join(current, name)):
                    return current
        if norm(current) == norm(root):
            return None
        parent = os.path.dirname(current)
        if parent == current or not inside(parent, root):
            return None
        current = parent


def package_json_keys(project_root):
    if not project_root:
        return None
    path = os.path.join(project_root, "package.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return set(payload.keys()) if isinstance(payload, dict) else None


def match_any(name, patterns):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def reference_grade(tree, sample, shared_targets):
    """返回 (grade, reason_hint)；判定链与 plan/03 模块② 逐条对应。"""
    rel_path = sample["rel_path"]
    type_name = sample["type"]
    directory = os.path.join(os.path.abspath(tree), *rel_path.split("/"))

    if type_name == "git":
        return "forbidden", "git 元数据"

    files = walk_files(directory)
    names = [name for _, name, _ in files]

    # 规则 5：本地状态 / 密钥
    for name in names:
        if match_any(name, LOCAL_STATE_PATTERNS):
            return "caution", "本地状态文件 %s" % name

    # 规则 6：手工文件晚于构建产物
    if type_name in ARTIFACT_TYPES:
        manual = [mtime for _, name, mtime in files if match_any(name, MANUAL_PATTERNS)]
        others = [mtime for _, name, mtime in files if not match_any(name, MANUAL_PATTERNS)]
        if manual and (not others or max(manual) > max(others)):
            return "caution", "手工文件晚于构建产物"

    # 规则 7：monorepo 共享依赖
    for target, referrer_root in shared_targets:
        if norm(directory) == norm(target):
            expected_root = sample.get("project_root_rel")
            if expected_root is None:
                return "caution", "共享依赖（本目录无项目根）"
            expected_abs = os.path.join(os.path.abspath(tree), *expected_root.split("/"))
            if referrer_root is None or norm(referrer_root) != norm(expected_abs):
                return "caution", "被其他项目根共享引用"

    # 规则 8：自定义 venv 配置
    if type_name in VENV_TYPES:
        for rel, name, _ in files:
            if name == "pyvenv.cfg" or rel.startswith("bin/") or rel.startswith("Scripts/"):
                continue
            if match_any(name, ("*.pth", "*.cfg")):
                return "caution", "自定义 venv 配置 %s" % rel

    project_root = None
    if sample.get("project_root_rel"):
        project_root = os.path.join(os.path.abspath(tree),
                                    *sample["project_root_rel"].split("/"))
    keys = package_json_keys(project_root)
    root_names = rel_entries(project_root) if project_root else []

    # 规则 10：纯缓存 / 纯字节码
    if type_name in CACHE_TYPES:
        if type_name == "__pycache__":
            if files and all(name.endswith(".pyc") for _, name, _ in files):
                return "safe", "纯字节码缓存"
            return "caution", "证据不足"
        if files:
            return "safe", "纯缓存结构"
        return "caution", "证据不足"

    # 规则 11：node_modules 依赖
    if type_name == "node_modules":
        if any(name in NODE_LOCKFILES for name in root_names):
            return "safe", "项目根有锁文件"
        if keys and ({"dependencies", "devDependencies", "peerDependencies",
                      "optionalDependencies"} & keys):
            return "safe", "package.json 声明依赖"
        return "caution", "证据不足"

    # 规则 12：构建产物目录
    if type_name in ("dist", "build"):
        if any(name in BUILD_MARKERS for name in root_names):
            return "safe", "项目根有构建配置"
        if keys and "scripts" in keys:
            return "safe", "package.json 有构建脚本"
        return "caution", "证据不足"
    if type_name == "out":
        if keys and "scripts" in keys:
            return "safe", "package.json 有构建脚本"
        return "caution", "证据不足"
    if type_name == "target":
        if "Cargo.toml" in root_names:
            return "safe", "Cargo 项目"
        return "caution", "证据不足"
    if type_name == ".next":
        if any(name in NEXT_MARKERS for name in root_names):
            return "safe", "Next.js 构建配置"
        return "caution", "证据不足"

    # 规则 13：虚拟环境
    if type_name in VENV_TYPES:
        if any(name in PY_LOCKFILES for name in root_names):
            return "safe", "项目有依赖清单"
        return "caution", "证据不足"

    # 兜底：规则 9
    return "caution", "证据不足"


def collect_shared_targets(tree, truth):
    """从真值的跳过清单里取"根内链接"，独立解析其目标与引用方项目根。"""
    targets = []
    for entry in truth.get("skipped_expect", []):
        if entry.get("reason") != "symlink_not_followed":
            continue
        link_path = os.path.join(os.path.abspath(tree), *entry["rel_path"].split("/"))
        target = os.path.realpath(link_path)
        referrer_root = project_root_of(tree, link_path)
        targets.append((target, referrer_root))
    return targets


def rel_of(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def main(argv=None):
    parser = argparse.ArgumentParser(description="分级规则的独立参照实现")
    parser.add_argument("--tree", default=os.path.join("devdisk-out", "synth"))
    parser.add_argument("--scan", default=os.path.join("devdisk-out", "scan.json"))
    parser.add_argument("--json", default=None, help="可选：把明细写入该 JSON")
    args = parser.parse_args(argv)

    tree = os.path.abspath(args.tree)
    truth_path = os.path.join(tree, "truth", "manifest.json")
    if not os.path.isfile(truth_path):
        sys.stderr.write("真值缺失：%s。下一步：先运行 tools/gen_synth_tree.py。\n" % truth_path)
        return 2
    if not os.path.isfile(args.scan):
        sys.stderr.write("扫描产物缺失：%s。下一步：先运行 `devdisk scan`。\n" % args.scan)
        return 2

    with open(truth_path, "r", encoding="utf-8") as handle:
        truth = json.load(handle)
    with open(args.scan, "r", encoding="utf-8") as handle:
        scan = json.load(handle)

    engine = {}
    for candidate in scan["candidates"]:
        engine[rel_of(candidate["path"], tree)] = candidate

    shared_targets = collect_shared_targets(tree, truth)

    problems = []
    rows = []
    for sample in truth["samples"]:
        rel_path = sample["rel_path"]
        if rel_path == "":
            continue
        grade, hint = reference_grade(tree, sample, shared_targets)
        expected = sample["expected_grade"]
        if grade != expected:
            problems.append("%s：参照实现内部不一致（期望 %s / 参照 %s，%s）"
                            % (rel_path, expected, grade, hint))
        candidate = engine.get(rel_path)
        if candidate is None:
            problems.append("%s：扫描产物中未检出，无法对账" % rel_path)
            continue
        if candidate["grade"] != grade:
            problems.append("%s：引擎 %s / 参照 %s（引擎理由：%s）"
                            % (rel_path, candidate["grade"], grade, candidate["reason"]))
        rows.append((rel_path, expected, grade, candidate["grade"], hint))

    print("独立参照：%s（树 %s）" % (os.path.basename(__file__), tree))
    for rel_path, expected, reference, actual, hint in rows:
        print("  %-38s 真值 %-9s 参照 %-9s 引擎 %-9s %s"
              % (rel_path, expected, reference, actual, hint))
    print("对账样本：%d；不一致：%d" % (len(rows), len(problems)))
    for problem in problems:
        print("  - %s" % problem)
    print("结论：%s" % ("PASS（引擎与独立参照逐样本一致）" if not problems else "FAIL"))

    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({
                "tree": tree,
                "samples": [{"rel_path": row[0], "expected": row[1],
                             "reference": row[2], "engine": row[3]} for row in rows],
                "problems": problems,
                "passed": not problems,
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
