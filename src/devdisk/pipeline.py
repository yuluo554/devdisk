# -*- coding: utf-8 -*-
"""编排层：CLI 与测试共用同一组公开函数。

M1 只落地 `run_scan()`；后续里程碑按 `plan/03` 追加 `run_grade()` / `run_audit()` 等。
纪律：本层不含分级判定（M2）也不做 HTML 渲染（M5），只做"校验输入 → 调用下层 →
组装契约 → 写盘"。
"""

import csv
import io
import os

from . import __version__
from . import config
from . import exit_codes
from . import model
from .rules import RulesError, load_rules
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
