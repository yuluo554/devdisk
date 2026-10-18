#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报告基准：报告数值对真值 + 零外链 + 双跑逐字节一致（M5 的门）。

用法：
    py -X utf8 tools/bench_report.py --tree devdisk-out/synth

门的定义（`plan/05` M5 DoD / `plan/03` 模块⑤ 验收基准）：

- **真值门**：`truth/report_expect.json`（生成器同轮产出，独立于报告实现）
  的 `candidate_count` / `candidate_bytes` / `reclaimable_bytes` / `by_grade` /
  `by_type` / `by_project` / `expected_top_order` 与报告 HTML 里的数值逐项一致；
- **零外链门**：生成物必须通过 `assert_no_external_resources`（不是"警告"）；
- **确定性门**：同一输入两次生成逐字节一致；
- **转义门**：路径/理由/证据里的 `<>&"` 不得原样出现在 HTML，也不得破坏区块；
- **降级门**：Docker 未审计时报告必须显式写"未审计"，且不得写 0 体积冒充。

报告的数值一律从**渲染后的 HTML** 读回（`data-metric` / `data-value` 锚点），
不从聚合函数直接取值——否则等于自己验自己。

退出码：0 全绿；1 任一门槛不达标；2 输入缺失（未生成合成树/候选清单）。
"""

import argparse
import html
import json
import os
import re
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from devdisk import exit_codes  # noqa: E402
from devdisk import pipeline  # noqa: E402
from devdisk import report  # noqa: E402

METRIC_RE = re.compile(r'data-metric="([^"]+)"\s+data-value="(-?\d+)"')
SECTION_RE = re.compile(r'<section id="(sec-[a-z]+)">')


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def metrics_of(text):
    """从渲染结果读回所有 `data-metric` 数值（同一键出现多次时保留首个）。"""
    values = {}
    for key, value in METRIC_RE.findall(text):
        values.setdefault(html.unescape(key), int(value))
    return values


def rel_paths_of(text):
    return [html.unescape(item) for item in re.findall(r'data-rel="([^"]*)"', text)]


def check_numbers(expect, text):
    problems = []
    values = metrics_of(text)
    totals = expect["expected_totals"]

    checks = (
        ("candidate-count", totals["candidate_count"], "候选总数"),
        ("candidate-bytes", totals["candidate_bytes"], "候选总体积"),
        ("reclaimable-bytes", totals["reclaimable_bytes"], "可删合计"),
    )
    for key, expected, label in checks:
        actual = values.get(key)
        if actual != expected:
            problems.append("%s：期望 %d / HTML 实际 %r（data-metric=%s）"
                            % (label, expected, actual, key))

    grade_counts = 0
    for grade, expected in totals["by_grade"].items():
        actual = values.get("grade-bytes-%s" % grade)
        if actual != expected:
            problems.append("分级 %s 体积：期望 %d / HTML 实际 %r"
                            % (grade, expected, actual))
        count = values.get("grade-count-%s" % grade)
        if count is None:
            problems.append("HTML 缺少分级 %s 的数量锚点" % grade)
        else:
            grade_counts += count
    if grade_counts != totals["candidate_count"]:
        problems.append("分级数量合计 %d 与候选总数 %d 不一致"
                        % (grade_counts, totals["candidate_count"]))

    for name, expected in totals["by_type"].items():
        key = "type-bytes:%s" % name
        actual = values.get(key)
        if actual != expected:
            problems.append("类型 %s 体积：期望 %d / HTML 实际 %r" % (name, expected, actual))

    for index, expected in enumerate(sorted(totals["by_project"].values(), reverse=True), 1):
        key = "project-bytes-%d" % index
        actual = values.get(key)
        if actual != expected:
            problems.append("项目排行第 %d 位体积：期望 %d / HTML 实际 %r"
                            % (index, expected, actual))

    ranking = rel_paths_of(text)
    expected_order = expect["expected_top_order"][:len(ranking)]
    if ranking != expected_order:
        for index, (actual, wanted) in enumerate(zip(ranking, expected_order), 1):
            if actual != wanted:
                problems.append("排行第 %d 位：期望 %s / HTML 实际 %s"
                                % (index, wanted, actual))
                break
        else:
            if len(ranking) != len(expected_order):
                problems.append("排行条数：期望 %d / HTML 实际 %d"
                                % (len(expected_order), len(ranking)))
    return problems


def check_sections(text, order):
    actual = SECTION_RE.findall(text)
    problems = []
    if list(actual) != list(order):
        problems.append("区块顺序不符：期望 %r / HTML 实际 %r" % (list(order), actual))
    for section in order:
        if 'id="%s"' % section not in text:
            problems.append("缺少区块：%s" % section)
    return problems


def check_escape_samples(text):
    """构造一条含 `<>&"` 的候选，确认转义后不破坏页面结构。"""
    problems = []
    hostile = 'evil <script>alert("x")</script> & "quote" <b>'
    payload = {
        "schema_version": 1,
        "generated_by": "devdisk bench",
        "roots": [hostile],
        "candidates": [{
            "path": hostile + "/node_modules",
            "type": "node_modules",
            "bytes": 4096,
            "mtime_utc": "1970-01-01T00:00:01Z",
            "project_root": hostile,
            "is_symlink": False,
            "symlink_kind": "none",
            "file_count": 1,
            "directory_count": 0,
            "grade": "safe",
            "reason": hostile,
            "evidence": [{"kind": "lockfile", "path": hostile,
                          "detail": hostile}],
        }],
        "skipped": [{"path": hostile, "reason": "outside_root", "detail": hostile}],
        "totals": {
            "candidate_bytes": 4096, "candidate_count": 1,
            "by_type": {"node_modules": 4096},
            "by_grade": {"safe": 4096, "caution": 0, "forbidden": 0},
            "other_bytes": 0, "other_count": 0, "ignored_count": 0,
            "skipped_count": 1,
        },
    }
    scan = report.validate_scan(payload, "<bench>")
    document = report.render_html(scan, top=10)
    if hostile in document:
        problems.append("转义失败：原始 `<>&\"` 样本原样出现在 HTML 中")
    if "<script>alert(" in document:
        problems.append("转义失败：script 标签未被 escape")
    if "&lt;script&gt;" not in document:
        problems.append("转义失败：没有出现 `&lt;script&gt;` 的转义形式")
    if document.count("<section") != len(report.SECTION_ORDER):
        problems.append("转义样本破坏了区块结构（<section> 数量不符）")
    return problems


def check_docker_degraded():
    """Docker 未审计 / 降级时必须显式说明，不得写 0 体积冒充。"""
    problems = []
    payload = {
        "schema_version": 1, "generated_by": "devdisk bench", "roots": ["."],
        "candidates": [], "skipped": [],
        "totals": {"candidate_bytes": 0, "candidate_count": 0, "by_type": {},
                   "by_grade": {"safe": 0, "caution": 0, "forbidden": 0},
                   "other_bytes": 0, "other_count": 0, "ignored_count": 0,
                   "skipped_count": 0},
    }
    scan = report.validate_scan(payload, "<bench>")

    without = report.render_html(scan, audit=None, top=10)
    if "未审计" not in without:
        problems.append("未提供 audit.json 时 Docker 段没有写“未审计”")
    docker_section = without.split('id="sec-docker"')[1].split("</section>")[0]
    if "docker-total:" in docker_section:
        problems.append("未提供 audit.json 时 Docker 段却输出了 0 体积指标")

    degraded = {
        "schema_version": 1,
        "docker": {"status": "skipped:docker_unavailable",
                   "reason": "docker CLI 不可用", "suggestions": []},
        "git": {"status": "not_requested", "repos": []},
    }
    with_degraded = report.render_html(scan, audit=degraded, top=10)
    if "skipped:docker_unavailable" not in with_degraded:
        problems.append("降级原因未在报告里显式展示")
    if "docker CLI 不可用" not in with_degraded:
        problems.append("降级说明缺少原始 reason")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="报告基准：真值对账 + 零外链 + 双跑一致")
    parser.add_argument("--tree", default=os.path.join("devdisk-out", "synth"),
                        help="合成树目录（含 truth/report_expect.json）")
    parser.add_argument("--scan", default=None,
                        help="候选清单（默认 devdisk-out/scan.json；缺失时现场扫描）")
    parser.add_argument("--json", default=None, help="可选：把明细写入该 JSON")
    args = parser.parse_args(argv)

    tree = os.path.abspath(args.tree)
    expect_path = os.path.join(tree, "truth", "report_expect.json")
    if not os.path.isfile(expect_path):
        sys.stderr.write("报告真值缺失：%s。下一步：重新运行 tools/gen_synth_tree.py。\n"
                         % expect_path)
        return 2
    expect = load_json(expect_path)

    scan_path = os.path.abspath(args.scan or os.path.join("devdisk-out", "scan.json"))
    if not os.path.isfile(scan_path):
        scan_path = os.path.join(tempfile.mkdtemp(prefix="devdisk-bench-report-"), "scan.json")
        code, _, _ = pipeline.run_scan([tree], out=scan_path, min_bytes=0)
        if code != exit_codes.OK:
            sys.stderr.write("现场扫描失败：退出码 %s\n" % code)
            return 2

    problems = []
    with tempfile.TemporaryDirectory(prefix="devdisk-bench-report-") as temp_dir:
        first = os.path.join(temp_dir, "first.html")
        second = os.path.join(temp_dir, "second.html")
        audit_path = os.path.join("devdisk-out", "audit.json")
        audit = audit_path if os.path.isfile(audit_path) else None
        for target in (first, second):
            try:
                code, _, _ = pipeline.run_report(scan_path, target, audit_path=audit,
                                                 top=50)
            except pipeline.ScanError as exc:
                sys.stderr.write("报告生成失败：%s\n" % exc)
                return 2
            if code != exit_codes.OK:
                sys.stderr.write("报告生成退出码异常：%s\n" % code)
                return 2

        with open(first, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(second, "r", encoding="utf-8") as handle:
            again = handle.read()

        if text != again:
            problems.append("同一输入两次生成的报告不是逐字节一致")
        try:
            report.assert_no_external_resources(text)
        except report.ReportError as exc:
            problems.append("零外链门失败：%s" % exc)

    problems += check_numbers(expect, text)
    problems += check_sections(text, report.SECTION_ORDER)
    problems += check_escape_samples(text)
    problems += check_docker_degraded()

    totals = expect["expected_totals"]
    print("报告基准：%s" % tree)
    print("候选：%d 个；总体积 %d 字节；可删合计 %d 字节"
          % (totals["candidate_count"], totals["candidate_bytes"],
             totals["reclaimable_bytes"]))
    print("报告大小：%d 字节" % len(text.encode("utf-8")))
    print("问题 %d" % len(problems))
    for problem in problems:
        print("  - %s" % problem)

    passed = not problems
    if args.json:
        from devdisk.serialize import write_json_atomic

        write_json_atomic(args.json, {
            "tree": tree,
            "scan": scan_path,
            "problems": problems,
            "passed": passed,
        })
    print("结论：%s" % ("PASS（数值对真值、零外链、双跑一致、转义与降级可见）"
                        if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
