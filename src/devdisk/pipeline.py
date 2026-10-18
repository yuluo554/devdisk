# -*- coding: utf-8 -*-
"""编排层：CLI 与测试共用同一组公开函数。

`plan/03` 的模块间数据流：`roots --scan--> 候选清单 --grade--> 带分级的候选清单`。
M1 落地 `run_scan()`，M2 追加 `run_grade()` 并由 `run_scan()` 直接串联（CLI 一次
调用就拿到带分级的清单；测试也走同一入口，不存在第二份判定逻辑）。

纪律：本层不含分级判定（在 `grader.py`）也不做 HTML 渲染（M5），只做
"校验输入 → 调用下层 → 组装契约 → 写盘"。
"""

import csv
import io
import os

from . import __version__
from . import config
from . import exit_codes
from . import model
from .grader import grade_result, make_context
from .rules import RulesError, load_grade_rules, load_rules
from .scanner import Scanner
from .serialize import write_json_atomic, write_text_atomic

SCHEMA_VERSION = 1

CSV_COLUMNS = ("path", "type", "bytes", "mtime_utc", "project_root",
               "is_symlink", "grade", "reason")


class ScanError(Exception):
    """扫描前置校验失败（退出码由调用方映射）。"""

    def __init__(self, message, exit_code=exit_codes.USAGE):
        Exception.__init__(self, message)
        self.exit_code = exit_code


def default_scan_out_path():
    return os.path.join(config.default_out_dir(), "scan.json")


def validate_roots(roots):
    """绝对化 + 去重 + 必须存在且是目录。返回 (roots, problems)。"""
    problems = []
    normalized = []
    seen = set()
    for raw in roots:
        absolute = os.path.abspath(raw)
        if not os.path.exists(absolute):
            problems.append("扫描根不存在：%s" % absolute)
            continue
        if not os.path.isdir(absolute):
            problems.append("扫描根不是目录：%s" % absolute)
            continue
        # `abspath` 不消除 `root/.` 这类尾随形式；realpath 再归一一次
        # （不解析符号链接，只做纯路径归一）。
        canonical = os.path.normcase(os.path.normpath(os.path.realpath(absolute)))
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(absolute)
    return normalized, problems


def build_payload(result):
    """把 ScanResult 投影为 `plan/02` §6.1 的契约 JSON。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "devdisk %s" % __version__,
        "roots": [model.display_path(root) for root in result.roots],
        "candidates": [candidate.to_dict() for candidate in result.candidates],
        "skipped": [item.to_dict() for item in result.skipped],
        "totals": result.totals(),
    }


def render_csv(payload):
    """CSV 投影：列固定，体积为字节 int，行序与候选清单一致。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for candidate in payload["candidates"]:
        writer.writerow([
            candidate["path"],
            candidate["type"],
            candidate["bytes"],
            candidate["mtime_utc"] or "",
            candidate["project_root"] or "",
            "true" if candidate["is_symlink"] else "false",
            candidate["grade"] or "",
            candidate["reason"] or "",
        ])
    return buffer.getvalue()


def _load_grade_table(data_dir):
    """加载分级规则表；缺失/格式错 → 退出码 4（不静默用内置默认）。"""
    try:
        return load_grade_rules(data_dir)
    except RulesError as exc:
        raise ScanError(str(exc), exit_code=exit_codes.DEGRADED)


def run_grade(result, keep_paths=None, data_dir=None, config_path=None):
    """对已扫描的 `ScanResult` 就地分级，返回 (exit_code, stats, summary_lines)。

    这是"分级"这一步的公开入口（CLI 的 `scan` 内部也走同一函数）：
    `run_scan()` = `scanner.scan()` + `run_grade()` 的串联。
    """
    if config_path is not None and data_dir is None:
        try:
            settings = config.load_config(config_path)
        except config.ConfigError as exc:
            raise ScanError(str(exc))
        data_dir = config.data_dir(settings)

    table = _load_grade_table(data_dir)
    stats = grade_result(result, table, keep_paths=keep_paths,
                         context=make_context(result, keep_paths=keep_paths))
    result.finalize()
    return exit_codes.OK, stats, grade_summary_lines(result)


def grade_summary_lines(result):
    """分级摘要（中文、人类可读）：三级计数与体积。"""
    counts = {}
    bytes_by_grade = {}
    for candidate in result.candidates:
        counts[candidate.grade] = counts.get(candidate.grade, 0) + 1
        bytes_by_grade[candidate.grade] = \
            bytes_by_grade.get(candidate.grade, 0) + candidate.bytes
    return [_grade_line(counts, bytes_by_grade)]


def _grade_line(counts, bytes_by_grade):
    parts = []
    for grade in model.GRADES:
        parts.append("%s %d 个（%s）"
                     % (model.GRADE_LABELS[grade], counts.get(grade, 0),
                        format_bytes(bytes_by_grade.get(grade, 0))))
    return "分级：" + "；".join(parts) + "；禁止项不计入可回收体积"


def _grade_line_from_payload(payload):
    counts = {}
    for candidate in payload["candidates"]:
        counts[candidate["grade"]] = counts.get(candidate["grade"], 0) + 1
    return _grade_line(counts, payload["totals"].get("by_grade") or {})


def run_scan(roots, out=None, output_format="json", min_bytes=None,
             keep_paths=None, data_dir=None, config_path=None):
    """扫描并返回 (exit_code, payload, summary_lines)。

    `payload` 在 json 模式下就是契约 dict（附带 `_out` 表示落点）；
    csv 模式下只返回 `{"_out": ...}`，明细已在文件里。
    `out` 为 None 时落到默认路径 `devdisk-out/scan.json`（或同名 .csv）。
    """
    normalized, problems = validate_roots(roots)
    if problems:
        raise ScanError("；".join(problems) + "。下一步：检查路径拼写，或用 `devdisk scan .` 扫描当前目录。")

    try:
        settings = config.load_config(config_path)
    except config.ConfigError as exc:
        raise ScanError(str(exc))

    if data_dir is None:
        data_dir = config.data_dir(settings)

    try:
        type_rules, ignore_rules = load_rules(data_dir)
    except RulesError as exc:
        raise ScanError(str(exc), exit_code=exit_codes.DEGRADED)

    if min_bytes is None:
        threshold = config.candidate_min_bytes(settings)
    else:
        threshold = int(min_bytes)
    if threshold < 0:
        raise ScanError("--min-bytes 不能为负数。下一步：省略该参数使用默认阈值。")

    effective_keep = list(keep_paths or []) + config.configured_keep_paths(settings)

    scanner = Scanner(type_rules, ignore_rules, min_bytes=threshold)
    result = scanner.scan(normalized)
    # 扫描与分级在同一次调用里串起来（`plan/03` 模块间数据流）；
    # 分级失败的降级语义与规则表坏掉一致：退出码 4，不静默降级。
    table = _load_grade_table(data_dir)
    grade_result(result, table, keep_paths=effective_keep,
                 context=make_context(result, keep_paths=effective_keep))
    payload = build_payload(result)

    if effective_keep:
        payload["keep_paths"] = [model.display_path(path) for path in effective_keep]

    if output_format == "csv":
        text = render_csv(payload)
        target = out or default_scan_out_path().replace(".json", ".csv")
        _write_text_atomic(target, text)
        return exit_codes.OK, {"_out": os.path.abspath(target)}, \
            _summary_lines(payload, threshold, os.path.abspath(target))

    target = out or default_scan_out_path()
    write_json_atomic(target, payload)
    payload = dict(payload)
    payload["_out"] = os.path.abspath(target)
    return exit_codes.OK, payload, _summary_lines(payload, threshold, payload["_out"])


def _write_text_atomic(path, text):
    """CSV 也走同一写盘单点（`serialize.write_text_atomic`）。"""
    write_text_atomic(path, text)


def _summary_lines(payload, threshold, out=None):
    totals = payload["totals"]
    lines = ["扫描根：%s" % "、".join(payload["roots"])]
    lines.append("候选目录：%d 个，合计 %s（%d 字节）"
                 % (totals["candidate_count"], format_bytes(totals["candidate_bytes"]),
                    totals["candidate_bytes"]))
    lines.append(_grade_line_from_payload(payload))
    if totals["by_type"]:
        parts = ["%s %s" % (name, format_bytes(size))
                 for name, size in sorted(totals["by_type"].items())]
        lines.append("按类型：" + "；".join(parts))
    lines.append("低于阈值（%s）未入候选：%d 个，合计 %s"
                 % (format_bytes(threshold), totals["other_count"],
                    format_bytes(totals["other_bytes"])))
    if totals["skipped_count"]:
        reasons = {}
        for item in payload["skipped"]:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        detail = "、".join("%s=%d" % (name, count)
                          for name, count in sorted(reasons.items()))
        lines.append("跳过条目：%d（%s）" % (totals["skipped_count"], detail))
    if out:
        lines.append("已写出：%s" % out)
    return lines


def format_bytes(value):
    """展示层：1024 进制、两位小数（`data/README.md` §四 第 1 条）。"""
    value = int(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    index = 0
    while amount >= 1024.0 and index < len(units) - 1:
        amount /= 1024.0
        index += 1
    if index == 0:
        return "%d B" % value
    return "%.2f %s" % (amount, units[index])
