#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描基准：真值逐项对账（双边门）。

用法：
    py -X utf8 tools/bench_scan.py --tree devdisk-out/synth

门的定义（`plan/05` M1 DoD / HANDOFF-M1 §二 第 8 条）：

- **误判为 0**：类型、体积、mtime、项目根与真值逐项一致；多出的候选也必须为 0。
- **检出率达标**：真值中的每个候选样本都必须出现在扫描结果里（100%）。
- **逐样本点名**：任何一项不达标，逐条打印 `相对路径：字段 期望 vs 实际`。

退出码：0 全绿；1 任一门槛不达标；2 输入缺失（未生成合成树）。
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from devdisk import pipeline  # noqa: E402

DETECTION_TARGET = 1.0
FALSE_POSITIVE_TARGET = 0


def rel_of(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def load_truth(tree):
    path = os.path.join(tree, "truth", "manifest.json")
    if not os.path.isfile(path):
        raise IOError("真值缺失：%s。下一步：先运行 tools/gen_synth_tree.py。" % path)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def compare(tree, truth, payload):
    """返回 (problems, detected, expected_total, false_positives)。"""
    root = os.path.abspath(tree)
    actual = {}
    for candidate in payload["candidates"]:
        actual[rel_of(candidate["path"], root)] = candidate

    expected = {}
    for sample in truth["samples"]:
        if sample["rel_path"] == "":
            continue  # 扫描根本身不是候选（样本 21 由 forbidden 断言覆盖）
        expected[sample["rel_path"]] = sample

    problems = []
    detected = 0
    for rel_path, sample in sorted(expected.items()):
        candidate = actual.get(rel_path)
        if candidate is None:
            problems.append("%s：未检出（期望 type=%s bytes=%d）"
                            % (rel_path, sample["type"], sample["expected_bytes"]))
            continue
        detected += 1
        if candidate["type"] != sample["type"]:
            problems.append("%s：type 期望 %s / 实际 %s"
                            % (rel_path, sample["type"], candidate["type"]))
        if candidate["bytes"] != sample["expected_bytes"]:
            problems.append("%s：bytes 期望 %d / 实际 %d"
                            % (rel_path, sample["expected_bytes"], candidate["bytes"]))
        if candidate["mtime_utc"] != sample["expected_mtime_utc"]:
            problems.append("%s：mtime_utc 期望 %s / 实际 %s"
                            % (rel_path, sample["expected_mtime_utc"],
                               candidate["mtime_utc"]))
        expected_project = sample.get("project_root_rel")
        actual_project = None
        if candidate["project_root"]:
            actual_project = rel_of(candidate["project_root"], root)
        if expected_project != actual_project:
            problems.append("%s：project_root 期望 %r / 实际 %r"
                            % (rel_path, expected_project, actual_project))
        if candidate["is_symlink"]:
            problems.append("%s：候选不应是符号链接（扫描不得跟随）" % rel_path)

    scale_prefixes = tuple(scale["prefix"] for scale in truth.get("scale_samples", []))
    extras = sorted(rel_path for rel_path in set(actual) - set(expected)
                    if not (scale_prefixes and rel_path.startswith(scale_prefixes)))
    for rel_path in extras:
        problems.append("%s：误判（真值中无此样本，实际 type=%s bytes=%d）"
                        % (rel_path, actual[rel_path]["type"], actual[rel_path]["bytes"]))

    return problems, detected, len(expected), len(extras)


def check_skips(tree, truth, payload):
    root = os.path.abspath(tree)
    actual = {}
    for item in payload["skipped"]:
        actual.setdefault(rel_of(item["path"], root), set()).add(item["reason"])

    problems = []
    for sample in truth.get("skipped_expect", []):
        platform = sample.get("platform")
        if platform == "posix" and os.name == "nt":
            continue
        rel_path = sample["rel_path"]
        reasons = actual.get(rel_path, set())
        if sample["reason"] not in reasons:
            problems.append("%s：跳过原因期望 %s / 实际 %s"
                            % (rel_path, sample["reason"],
                               sorted(reasons) or "未跳过"))
    return problems


def check_scale(tree, truth, payload):
    """规模样本：按前缀计数，确认 1000 个目录全部成为候选。"""
    root = os.path.abspath(tree)
    problems = []
    for scale in truth.get("scale_samples", []):
        prefix = scale["prefix"]
        matched = [c for c in payload["candidates"]
                   if rel_of(c["path"], root).startswith(prefix)]
        if len(matched) != scale["count"]:
            problems.append("%s*：数量期望 %d / 实际 %d"
                            % (prefix, scale["count"], len(matched)))
            continue
        total = sum(c["bytes"] for c in matched)
        if total != scale["expected_bytes_total"]:
            problems.append("%s*：体积合计期望 %d / 实际 %d"
                            % (prefix, scale["expected_bytes_total"], total))
        wrong_type = [c for c in matched if c["type"] != scale["type"]]
        if wrong_type:
            problems.append("%s*：%d 个目录类型不是 %s（首个 %s）"
                            % (prefix, len(wrong_type), scale["type"],
                               rel_of(wrong_type[0]["path"], root)))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="扫描基准：真值双边门")
    parser.add_argument("--tree", default=os.path.join("devdisk-out", "synth"),
                        help="合成树目录（含 truth/manifest.json）")
    parser.add_argument("--json", default=None, help="可选：把明细写入该 JSON")
    args = parser.parse_args(argv)

    tree = os.path.abspath(args.tree)
    try:
        truth = load_truth(tree)
    except IOError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2

    code, payload, _ = pipeline.run_scan([tree], out=None, min_bytes=0)
    if code != 0:
        sys.stderr.write("扫描失败，退出码 %d\n" % code)
        return 1

    problems, detected, expected_total, false_positives = compare(tree, truth, payload)
    problems += check_skips(tree, truth, payload)
    problems += check_scale(tree, truth, payload)

    detection_rate = (float(detected) / expected_total) if expected_total else 1.0

    print("扫描基准：%s" % tree)
    print("检出：%d / %d（%.1f%%）" % (detected, expected_total, detection_rate * 100))
    print("误判（真值外的候选）：%d" % false_positives)
    print("逐项差异：%d" % len(problems))
    for problem in problems:
        print("  - %s" % problem)

    passed = (detection_rate >= DETECTION_TARGET
              and false_positives <= FALSE_POSITIVE_TARGET
              and not problems)

    if args.json:
        from devdisk.serialize import write_json_atomic

        write_json_atomic(args.json, {
            "tree": tree,
            "detected": detected,
            "expected_total": expected_total,
            "detection_rate": detection_rate,
            "false_positives": false_positives,
            "problems": problems,
            "passed": passed,
        })

    print("结论：%s" % ("PASS（误判 0，检出率达标）" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
