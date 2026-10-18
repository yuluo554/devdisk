#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分级基准：真值逐项对账（M2 的门）。

用法：
    py -X utf8 tools/bench_grade.py --tree devdisk-out/synth

门的定义（`plan/05` M2 DoD / `plan/03` 模块② 验收基准）：

- **分级一致**：真值中每个样本（扫描根本身除外）的 `grade` 与 `expected_grade`
  逐项一致；不一致逐条点名。
- **证据充分**：每条候选 `reason` 非空、`evidence >= 1`，每条证据含
  `kind`/`path`/`detail` 且 `detail` 非空（`data/README.md` §四 第 8 条）。
- **证据不足必落谨慎**：真值 `evidence_facts` 含 `insufficient_evidence`
  的样本必须是 `caution`。
- **禁止项不计入可回收**：`totals.by_grade` 三级键齐备，且
  `forbidden` 体积独立于 `safe`/`caution`（报告层口径由 M5 消费）。

退出码：0 全绿；1 任一门槛不达标；2 输入缺失（未生成合成树/扫描产物）。
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from devdisk import model  # noqa: E402
from devdisk import pipeline  # noqa: E402


def rel_of(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def load_truth(tree):
    path = os.path.join(tree, "truth", "manifest.json")
    if not os.path.isfile(path):
        raise IOError("真值缺失：%s。下一步：先运行 tools/gen_synth_tree.py。" % path)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def check_evidence(payload):
    """每条候选都要有理由与至少一条带明细的证据。"""
    problems = []
    for candidate in payload["candidates"]:
        label = rel_of(candidate["path"], payload["_root"])
        if not (candidate["reason"] or "").strip():
            problems.append("%s：reason 为空" % label)
        if candidate["grade"] not in model.GRADES:
            problems.append("%s：grade 非法（%r）" % (label, candidate["grade"]))
        evidence = candidate["evidence"] or []
        if len(evidence) < 1:
            problems.append("%s：evidence 少于 1 条" % label)
        for item in evidence:
            for field in ("kind", "path", "detail"):
                if not (item.get(field) or "").strip():
                    problems.append("%s：证据字段 %s 为空（%r）" % (label, field, item))
    return problems


def check_grades(tree, truth, payload):
    root = os.path.abspath(tree)
    actual = {rel_of(c["path"], root): c for c in payload["candidates"]}
    problems = []
    matched = 0
    expected_total = 0
    for sample in truth["samples"]:
        rel_path = sample["rel_path"]
        if rel_path == "":
            continue          # 扫描根本身不是候选（由 bench_scan 的跳过断言覆盖）
        expected_total += 1
        candidate = actual.get(rel_path)
        if candidate is None:
            problems.append("%s：未检出（无法核对分级）" % rel_path)
            continue
        if candidate["grade"] != sample["expected_grade"]:
            problems.append("%s：grade 期望 %s / 实际 %s（理由：%s）"
                            % (rel_path, sample["expected_grade"], candidate["grade"],
                               candidate["reason"]))
            continue
        matched += 1
    return problems, matched, expected_total


def check_insufficient(tree, truth, payload):
    root = os.path.abspath(tree)
    actual = {rel_of(c["path"], root): c for c in payload["candidates"]}
    problems = []
    for sample in truth["samples"]:
        if "insufficient_evidence" not in (sample.get("evidence_facts") or ()):
            continue
        candidate = actual.get(sample["rel_path"])
        if candidate is None:
            problems.append("%s：证据不足样本未检出" % sample["rel_path"])
            continue
        if candidate["grade"] != model.GRADE_CAUTION:
            problems.append("%s：证据不足样本期望谨慎 / 实际 %s"
                            % (sample["rel_path"], candidate["grade"]))
    return problems


def check_totals(payload):
    problems = []
    by_grade = payload["totals"].get("by_grade") or {}
    for grade in model.GRADES:
        if grade not in by_grade:
            problems.append("totals.by_grade 缺少 %s 键" % grade)
    if by_grade.get(model.GRADE_FORBIDDEN, 0) > 0:
        reclaimable = by_grade.get(model.GRADE_SAFE, 0) + by_grade.get(model.GRADE_CAUTION, 0)
        if reclaimable + by_grade.get(model.GRADE_FORBIDDEN, 0) != payload["totals"]["candidate_bytes"]:
            problems.append("totals 三级合计与 candidate_bytes 不闭合")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="分级基准：真值对账 + 证据门槛")
    parser.add_argument("--tree", default=os.path.join("devdisk-out", "synth"),
                        help="合成树目录（含 truth/manifest.json）")
    parser.add_argument("--scan", default=None,
                        help="已扫描产物（默认 devdisk-out/scan.json；给了就直接用，不重扫）")
    parser.add_argument("--json", default=None, help="可选：把明细写入该 JSON")
    args = parser.parse_args(argv)

    tree = os.path.abspath(args.tree)
    try:
        truth = load_truth(tree)
    except IOError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2

    scan_path = os.path.abspath(args.scan or os.path.join("devdisk-out", "scan.json"))
    if args.scan and os.path.isfile(scan_path):
        with open(scan_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        code, payload, _ = pipeline.run_scan([tree], out=None, min_bytes=0)
        if code != 0:
            sys.stderr.write("扫描失败，退出码 %d\n" % code)
            return 1
    payload = dict(payload)
    payload["_root"] = tree

    problems = []
    grade_problems, matched, expected_total = check_grades(tree, truth, payload)
    problems += grade_problems
    problems += check_insufficient(tree, truth, payload)
    problems += check_totals(payload)
    evidence_problems = check_evidence(payload)
    problems += evidence_problems

    print("分级基准：%s" % tree)
    print("分级一致：%d / %d" % (matched, expected_total))
    print("理由/证据缺失：%d" % len(evidence_problems))
    print("逐项差异：%d" % len(problems))
    for problem in problems:
        print("  - %s" % problem)

    passed = not problems and matched == expected_total and expected_total > 0

    if args.json:
        from devdisk.serialize import write_json_atomic

        write_json_atomic(args.json, {
            "tree": tree,
            "matched": matched,
            "expected_total": expected_total,
            "evidence_problems": len(evidence_problems),
            "problems": problems,
            "passed": passed,
        })

    print("结论：%s" % ("PASS（分级一致、证据充分、证据不足落谨慎）" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
