# -*- coding: utf-8 -*-
"""编排层：CLI 与测试共用同一组公开函数。

`plan/03` 的模块间数据流：`roots --scan--> 候选清单 --grade--> 带分级的候选清单`；
`audit` 是独立的只读路径：`roots --audit--> audit.json`（Docker + `.git`）。
M1 落地 `run_scan()`，M2 追加 `run_grade()`，M3 追加 `run_audit()`。

纪律：本层不含分级/审计判定（在 `grader.py` / `audit/`），也不做 HTML 渲染（M5），
只做"校验输入 → 调用下层 → 组装契约 → 写盘"。
"""

import csv
import io
import os

from . import __version__
from . import config
from . import exit_codes
from . import model
from .audit import DEGRADED_STATUSES
from .audit import STATUS_GIT_MUTATED
from .audit import STATUS_NOT_REQUESTED
from .audit import STATUS_OK
from .audit import STATUS_DOCKER_PARSE
from .audit import STATUS_DOCKER_TIMEOUT
from .audit import STATUS_DOCKER_UNAVAILABLE
from .audit import docker as docker_audit
from .audit import git as git_audit
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


def default_audit_out_path():
    return os.path.join(config.default_out_dir(), "audit.json")


def build_audit_payload(docker_section, git_section):
    """把两个审计段投影为 `plan/02` §6.2 的契约 JSON。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "docker": docker_section,
        "git": git_section,
    }


def _docker_section(use_docker, fixture=None, timeout=None):
    if not use_docker:
        payload = {"status": STATUS_NOT_REQUESTED, "suggestions": []}
        for _, field in docker_audit.DOCKER_TYPES:
            payload[field] = dict(docker_audit.EMPTY_SECTION)
        return payload
    if fixture:
        if not os.path.isfile(fixture):
            raise ScanError("Docker fixture 不存在：%s。下一步：检查路径，或去掉 `--docker-fixture`。"
                            % os.path.abspath(fixture))
        _, payload = docker_audit.parse_fixture(fixture)
        return payload
    return docker_audit.run_docker(timeout=timeout)


def _git_section(enabled, roots, loose_threshold, bytes_threshold):
    if not enabled:
        return {"status": STATUS_NOT_REQUESTED, "repos": []}

    repos = []
    mutated = False
    for git_path in git_audit.discover(roots):
        try:
            repos.append(git_audit.audit_one(
                git_path, root=_root_for(git_path, roots),
                loose_threshold=loose_threshold, bytes_threshold=bytes_threshold))
        except git_audit.GitAuditError as exc:
            mutated = True
            repos.append({
                "path": model.display_path(git_path),
                "status": STATUS_GIT_MUTATED,
                "reason": str(exc) + "。下一步：停止其他 git 进程后重跑；本工具不会修改仓库。",
                "objects_bytes": 0,
                "pack_files": 0,
                "loose_objects": 0,
                "suggestions": [],
            })
    return {"status": STATUS_GIT_MUTATED if mutated else STATUS_OK, "repos": repos}


def _root_for(path, roots):
    """选一个包含 `path` 的扫描根，用于生成相对路径（没有就返回 None）。"""
    absolute = os.path.abspath(path)
    best = None
    for root in roots:
        root_abs = os.path.abspath(root)
        try:
            common = os.path.commonpath([absolute, root_abs])
        except ValueError:
            continue
        if os.path.normcase(common) == os.path.normcase(root_abs):
            if best is None or len(root_abs) > len(best):
                best = root_abs
    return best


def run_audit(roots=None, out=None, use_docker=False, use_git=False,
              docker_fixture=None, config_path=None, data_dir=None):
    """只读审计 Docker 与 `.git`，返回 `(exit_code, payload, summary_lines)`。

    退出码：0 正常；2 用法错误；4 Docker 降级；5 `.git` 审计前后被改动。
    `audit.json` 无论降级与否都会写盘（降级信息在 `status`/`reason` 里，不静默）。
    """
    roots = list(roots or [])
    if not use_docker and not use_git:
        raise ScanError("audit 至少需要 `--docker` 或 `--git` 之一。"
                        "下一步：例如 `devdisk audit --root . --git`。")

    try:
        settings = config.load_config(config_path)
    except config.ConfigError as exc:
        raise ScanError(str(exc))
    if data_dir is None:
        data_dir = config.data_dir(settings)

    normalized = []
    if use_git:
        if not roots:
            raise ScanError("`--git` 需要至少一个 `--root`。"
                            "下一步：例如 `devdisk audit --root . --git`。")
        normalized, problems = validate_roots(roots)
        if problems:
            raise ScanError("；".join(problems) +
                            "。下一步：检查路径拼写，或用 `devdisk audit --root . --git`。")

    docker_section = _docker_section(
        use_docker, fixture=docker_fixture,
        timeout=config.docker_timeout(settings))
    git_section = _git_section(
        use_git, normalized,
        config.git_gc_loose_threshold(settings),
        config.git_gc_bytes_threshold(settings))

    payload = build_audit_payload(docker_section, git_section)
    target = out or default_audit_out_path()
    write_json_atomic(target, payload)

    code = exit_codes.OK
    if git_section["status"] == STATUS_GIT_MUTATED:
        code = exit_codes.UNSAFE
    elif docker_section.get("status") in DEGRADED_STATUSES:
        code = exit_codes.DEGRADED

    payload = dict(payload)
    payload["_out"] = os.path.abspath(target)
    return code, payload, _audit_summary_lines(payload)


def _audit_summary_lines(payload):
    lines = []
    docker_section = payload["docker"]
    docker_status = docker_section.get("status")
    if docker_status == STATUS_NOT_REQUESTED:
        lines.append("Docker：未请求（未传 `--docker`）")
    elif docker_status == STATUS_OK:
        parts = []
        for label, field in docker_audit.DOCKER_TYPES:
            section = docker_section.get(field) or {}
            parts.append("%s %s（可回收 %s）"
                         % (label, format_bytes(section.get("total_bytes", 0)),
                            format_bytes(section.get("reclaimable_bytes", 0))))
        lines.append("Docker：" + "；".join(parts))
        suggestions = docker_section.get("suggestions") or []
        if suggestions:
            lines.append("Docker 建议（均未执行）：%s"
                         % "、".join(item["command"] for item in suggestions))
    else:
        lines.append("Docker：%s（%s）"
                     % (docker_status, docker_section.get("reason", "")))

    git_section = payload["git"]
    git_status = git_section.get("status")
    if git_status == STATUS_NOT_REQUESTED:
        lines.append("Git：未请求（未传 `--git`）")
    else:
        repos = git_section.get("repos") or []
        total_objects = sum(repo.get("objects_bytes", 0) for repo in repos)
        lines.append("Git：%d 个仓库，对象合计 %s" % (len(repos), format_bytes(total_objects)))
        for repo in repos:
            label = repo.get("relative_path") or repo.get("path")
            status = repo.get("status")
            if status == STATUS_OK:
                lines.append("  %s：对象 %s；pack %d 个；松散对象 %d 个"
                             % (label, format_bytes(repo.get("objects_bytes", 0)),
                                repo.get("pack_files", 0), repo.get("loose_objects", 0)))
                for suggestion in repo.get("suggestions") or []:
                    lines.append("  %s：建议 `%s`（未执行）"
                                 % (label, suggestion.get("command")))
            else:
                lines.append("  %s：%s（%s）" % (label, status, repo.get("reason", "")))
        if git_status == STATUS_GIT_MUTATED:
            lines.append("Git 审计失败：`.git` 在审计前后发生变化（只读纪律，退出码 5）")
    if payload.get("_out"):
        lines.append("已写出：%s" % payload["_out"])
    return lines


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
    """展示层：1024 进制、两位小数（`data/README.md` §四 第 1 条）。

    实现单点在 `model.format_bytes`；这里保留同名入口，避免调用方改 import。
    """
    return model.format_bytes(value)
