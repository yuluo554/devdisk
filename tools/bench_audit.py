#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只读审计基准：Docker fixture 解析 + 合成仓库 `.git` 统计（M3 的门）。

用法：
    py -X utf8 tools/bench_audit.py --tree devdisk-out/synth

门的定义（`plan/05` M3 DoD / `plan/03` 模块③ 验收基准）：

- **Docker 双边门**：`data/fixtures/docker_system_df/expect.json` 的手写真值与解析
  结果逐字段一致（四类 `total_bytes` / `reclaimable_bytes`、`status`、建议命令）；
  真值**手写在 fixture 旁**，不由解析器回填。
- **畸形输入门**：`malformed.txt` 必须是 `error:docker_parse` 且附原文摘录。
- **`.git` 双边门**：真值 `git_repos[]` 的 `objects_bytes` / `pack_files` /
  `loose_objects` 与审计结果逐项一致；`gitdir_files[]` 必须是 `skipped:gitdir_file`。
- **只读门**：审计前后 `.git` 元数据清单（相对路径 + size + mtime_ns）不变；
  所有建议 `applied == false`。
- **误判为 0**：审计发现的仓库不得多于真值（多出来的先点名再失败）。

退出码：0 全绿；1 任一门槛不达标；2 输入缺失（未生成合成树 / fixture 缺失）。
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from devdisk.audit import STATUS_DOCKER_PARSE, STATUS_GITDIR_FILE, STATUS_OK  # noqa: E402
from devdisk.audit import docker as docker_audit  # noqa: E402
from devdisk.audit import git as git_audit  # noqa: E402

FIXTURE_REL = os.path.join("data", "fixtures", "docker_system_df")
SECTIONS = ("images", "containers", "volumes", "build_cache")


def rel_of(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def check_docker(repo_root):
    """Docker fixture 逐文件对账。返回问题列表。"""
    fixture_dir = os.path.join(repo_root, FIXTURE_REL)
    expect_path = os.path.join(fixture_dir, "expect.json")
    if not os.path.isfile(expect_path):
        raise IOError("Docker 真值缺失：%s。下一步：补 data/fixtures/docker_system_df/expect.json。"
                      % expect_path)
    expect = load_json(expect_path)
    problems = []
    for case in expect["cases"]:
        fixture = os.path.join(fixture_dir, case["file"])
        if not os.path.isfile(fixture):
            problems.append("%s：fixture 缺失" % case["file"])
            continue
        status, payload = docker_audit.parse_fixture(fixture)
        if status != case["status"]:
            problems.append("%s：status 期望 %s / 实际 %s"
                            % (case["file"], case["status"], status))
            continue
        if case["status"] != STATUS_OK:
            excerpt = payload.get("raw_excerpt") or []
            limit = case.get("raw_excerpt_limit", 20)
            if not excerpt:
                problems.append("%s：错误路径未附原文摘录" % case["file"])
            if len(excerpt) > limit:
                problems.append("%s：原文摘录 %d 行超过上限 %d"
                                % (case["file"], len(excerpt), limit))
            continue
        for section in SECTIONS:
            for key in ("total_bytes", "reclaimable_bytes"):
                expected = case[section][key]
                actual = (payload.get(section) or {}).get(key)
                if actual != expected:
                    problems.append("%s：%s.%s 期望 %d / 实际 %r"
                                    % (case["file"], section, key, expected, actual))
        commands = [item["command"] for item in payload.get("suggestions") or []]
        if commands != case["suggestion_commands"]:
            problems.append("%s：建议命令期望 %r / 实际 %r"
                            % (case["file"], case["suggestion_commands"], commands))
        for item in payload.get("suggestions") or []:
            if item.get("applied") is not False:
                problems.append("%s：建议不是 applied=false（%r）"
                                % (case["file"], item))
    return problems


def check_git(tree, truth):
    """合成仓库统计对账 + 只读快照。返回问题列表。"""
    root = os.path.abspath(tree)
    problems = []
    expected = {repo["rel_path"]: repo for repo in truth.get("git_repos", [])}
    expected_gitdir = {item["rel_path"]: item for item in truth.get("gitdir_files", [])}

    discovered = git_audit.discover([root])
    actual_rel = {rel_of(path, root): path for path in discovered}

    extra = sorted(set(actual_rel) - set(expected) - set(expected_gitdir))
    for rel_path in extra:
        problems.append("%s：误判（真值中无此仓库）" % rel_path)

    for rel_path, sample in sorted(expected.items()):
        path = actual_rel.get(rel_path)
        if path is None:
            problems.append("%s：未发现（期望 pack=%d loose=%d objects=%d）"
                            % (rel_path, sample["expected_pack_files"],
                               sample["expected_loose_objects"],
                               sample["expected_objects_bytes"]))
            continue
        before = git_audit.snapshot(path)
        stats = git_audit.measure(path)
        after = git_audit.snapshot(path)
        if before != after:
            problems.append("%s：审计前后元数据发生变化（只读纪律）" % rel_path)
        for key in ("objects_bytes", "pack_files", "loose_objects"):
            expected_key = "expected_" + key
            if stats[key] != sample[expected_key]:
                problems.append("%s：%s 期望 %d / 实际 %d"
                                % (rel_path, key, sample[expected_key], stats[key]))

        entry = git_audit.audit_one(path, root=root, loose_threshold=0, bytes_threshold=0)
        if entry["status"] != STATUS_OK:
            problems.append("%s：审计状态期望 %s / 实际 %s"
                            % (rel_path, STATUS_OK, entry["status"]))
        for suggestion in entry.get("suggestions") or []:
            if suggestion.get("applied") is not False:
                problems.append("%s：建议不是 applied=false（%r）" % (rel_path, suggestion))
            if suggestion.get("command") != "git gc":
                problems.append("%s：建议命令异常（%r）"
                                % (rel_path, suggestion.get("command")))

    for rel_path, sample in sorted(expected_gitdir.items()):
        path = actual_rel.get(rel_path)
        if path is None:
            problems.append("%s：未发现 `.git` 文件" % rel_path)
            continue
        entry = git_audit.audit_one(path, root=root)
        if entry["status"] != STATUS_GITDIR_FILE:
            problems.append("%s：状态期望 %s / 实际 %s"
                            % (rel_path, STATUS_GITDIR_FILE, entry["status"]))
        if not entry.get("gitdir"):
            problems.append("%s：未记录 gitdir 目标" % rel_path)
        if entry["objects_bytes"] or entry["pack_files"] or entry["loose_objects"]:
            problems.append("%s：`.git` 文件不应被统计" % rel_path)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读审计基准：Docker fixture + 合成 .git")
    parser.add_argument("--tree", default=os.path.join("devdisk-out", "synth"),
                        help="合成树目录（含 truth/manifest.json）")
    parser.add_argument("--json", default=None, help="可选：把明细写入该 JSON")
    args = parser.parse_args(argv)

    tree = os.path.abspath(args.tree)
    truth_path = os.path.join(tree, "truth", "manifest.json")
    if not os.path.isfile(truth_path):
        sys.stderr.write("真值缺失：%s。下一步：先运行 tools/gen_synth_tree.py。\n"
                         % truth_path)
        return 2

    try:
        docker_problems = check_docker(REPO_ROOT)
    except IOError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2

    truth = load_json(truth_path)
    git_problems = check_git(tree, truth)

    problems = docker_problems + git_problems
    print("审计基准：%s" % tree)
    print("Docker fixture：%d 个用例，差异 %d" % (
        len(load_json(os.path.join(REPO_ROOT, FIXTURE_REL, "expect.json"))["cases"]),
        len(docker_problems)))
    print("Git 仓库：%d 个（真值），差异 %d"
          % (len(truth.get("git_repos", [])), len(git_problems)))
    for problem in problems:
        print("  - %s" % problem)

    passed = not problems

    if args.json:
        from devdisk.serialize import write_json_atomic

        write_json_atomic(args.json, {
            "tree": tree,
            "docker_problems": docker_problems,
            "git_problems": git_problems,
            "problems": problems,
            "passed": passed,
        })

    print("结论：%s" % ("PASS（fixture 解析正确、.git 统计精确、前后无变化、建议均未执行）"
                        if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
