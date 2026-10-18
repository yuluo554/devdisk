# -*- coding: utf-8 -*-
"""编排层：CLI 与测试共用同一组公开函数。

`plan/03` 的模块间数据流：`roots --scan--> 候选清单 --grade--> 带分级的候选清单`；
`audit` 是独立的只读路径：`roots --audit--> audit.json`（Docker + `.git`）；
`clean` / `undo` 走隔离区：`scan.json --clean--> .trash/<batch>/manifest.json --undo--> 恢复`。
M1 落地 `run_scan()`，M2 追加 `run_grade()`，M3 追加 `run_audit()`，M4 追加
`run_clean()` / `run_undo()`，M5 追加 `run_report()`。

纪律：本层不含分级/审计判定（在 `grader.py` / `audit/`），也不做 HTML 渲染
（在 `report.py`），只做"校验输入 → 调用下层 → 组装契约 → 写盘"。
"""

import csv
import io
import json
import os
import sys

from . import __version__
from . import config
from . import digest
from . import exit_codes
from . import manifest as manifest_mod
from . import model
from . import quarantine
from . import report as report_mod
from .audit import DEGRADED_STATUSES
from .audit import STATUS_GIT_MUTATED
from .audit import STATUS_NOT_REQUESTED
from .audit import STATUS_OK
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


# ---------------------------------------------------------------- 模块④：隔离区
#
# 输入两种模式（`plan/03` 模块④）：
#   - `--from scan.json --select <path>`：候选清单里的条目，grade/reason 直接取用；
#   - `--select <path>`：显式路径，现场按同一套规则分级（绝不默认 safe）。
#
# 硬约束：
#   - 未给 `--select`（也没有 `--resume`）→ 退出码 1，**永不默认全选**；
#   - `--apply` 必须有 `--yes`，否则退出码 1（非 TTY 无交互输入）；
#   - `forbidden` 候选 / 跨设备 / 越界 → 退出码 5；
#   - 默认 dry-run：不创建隔离区、不移动任何东西。

CLEAN_SCHEMA_VERSION = 1


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _failure_reason(exc):
    """`failed:<reason>` 的 `<reason>`：优先 errno 名，其次异常类名。"""
    import errno

    number = getattr(exc, "errno", None)
    if number is not None:
        return errno.errorcode.get(number, "oserror").lower()
    return type(exc).__name__.lower()


def _load_scan_payload(path):
    """读入 `scan.json` 并做最小结构校验（损坏 → 退出码 2）。"""
    if not path:
        raise ScanError("缺少 `--from <scan.json>`。下一步：先运行 `devdisk scan`。")
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        raise ScanError("候选清单不存在：%s。下一步：先运行 `devdisk scan`，或改用 `--select`。"
                        % absolute)
    try:
        with open(absolute, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ScanError("候选清单无法解析：%s（%s）。下一步：重新扫描生成。" % (absolute, exc))
    if not isinstance(payload, dict):
        raise ScanError("候选清单不是 JSON 对象：%s" % absolute)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ScanError("候选清单 schema_version 不支持：%r（期望 %d）"
                        % (payload.get("schema_version"), SCHEMA_VERSION))
    if not isinstance(payload.get("roots"), list) or not payload["roots"]:
        raise ScanError("候选清单缺少 roots：%s" % absolute)
    if not isinstance(payload.get("candidates"), list):
        raise ScanError("候选清单缺少 candidates：%s" % absolute)
    return payload


def _load_settings(config_path, data_dir):
    try:
        settings = config.load_config(config_path)
    except config.ConfigError as exc:
        raise ScanError(str(exc))
    if data_dir is None:
        data_dir = config.data_dir(settings)
    return settings, data_dir


def _scan_and_grade(roots, min_bytes, keep_paths, data_dir):
    """扫描 + 分级（不写盘）；`run_scan()` 与 `clean` 的现场分级共用。"""
    try:
        type_rules, ignore_rules = load_rules(data_dir)
    except RulesError as exc:
        raise ScanError(str(exc), exit_code=exit_codes.DEGRADED)
    scanner = Scanner(type_rules, ignore_rules, min_bytes=min_bytes)
    result = scanner.scan(roots)
    table = _load_grade_table(data_dir)
    grade_result(result, table, keep_paths=keep_paths,
                 context=make_context(result, keep_paths=keep_paths))
    result.finalize()
    return result


def _candidate_index(payload):
    return dict((_norm(item["path"]), item) for item in payload["candidates"])


def _fallback_entry(path):
    """不在候选清单里的显式目录：按谨慎处理（不假装可重建）。"""
    measurement = digest.measure(path)
    return {
        "path": model.display_path(path),
        "bytes": measurement["bytes"],
        "grade": model.GRADE_CAUTION,
        "reason": "用户显式选择：该目录不在候选清单中，未识别到可重建证据，按谨慎处理。",
        "mtime_utc": measurement["mtime_utc"],
        "type": "explicit",
    }


def _entry_from_candidate(candidate):
    return {
        "path": candidate["path"],
        "bytes": int(candidate.get("bytes") or 0),
        "grade": candidate.get("grade") or model.GRADE_CAUTION,
        "reason": candidate.get("reason") or "",
        "mtime_utc": candidate.get("mtime_utc"),
        "type": candidate.get("type"),
    }


def _resolve_selection(select_paths, from_payload, data_dir, keep_paths=None):
    """把 `--select` 解析为带分级信息的条目，并确定唯一的源根。

    返回 `(source_root, mode, entries)`。
    - 候选清单命中：直接用它的 grade/reason/bytes；
    - 目录存在但不在清单：现场扫描其父目录后重新查找，仍找不到则按谨慎兜底。
    """
    if not select_paths:
        raise ScanError("未给出任何选择。本工具不提供\"全选\"；"
                        "下一步：用 `--select <path>`（可重复）逐条指定。",
                        exit_code=exit_codes.REFUSED)

    keep = [_norm(path) for path in (keep_paths or ())]
    candidates = _candidate_index(from_payload) if from_payload else {}
    entries = []
    seen = set()
    for raw in select_paths:
        absolute = os.path.abspath(raw)
        key = _norm(absolute)
        if key in seen:
            continue
        seen.add(key)
        if key in keep:
            raise ScanError(
                "选中路径在配置的保留列表（keep_paths）里：%s。"
                "下一步：从 `--select` 去掉它，或调整 `devdisk.toml`。" % absolute,
                exit_code=exit_codes.UNSAFE)

        candidate = candidates.get(key)
        if candidate is not None:
            entries.append(_entry_from_candidate(candidate))
            continue

        if not os.path.isdir(absolute):
            raise ScanError(
                "选中路径不在候选清单中，且不是目录：%s。"
                "下一步：检查路径拼写，或先 `devdisk scan` 确认它确实是候选。" % absolute)

        # 现场分级：以父目录为扫描根，保证项目根/证据与正常扫描口径一致。
        parent = os.path.dirname(absolute)
        result = _scan_and_grade([parent], 0, keep_paths, data_dir)
        match = None
        for item in result.candidates:
            if _norm(item.path) == key:
                match = item
                break
        if match is None:
            entries.append(_fallback_entry(absolute))
        else:
            entries.append({
                "path": model.display_path(match.path),
                "bytes": match.bytes,
                "grade": match.grade,
                "reason": match.reason,
                "mtime_utc": match.mtime_utc,
                "type": match.type,
            })

    forbidden = [entry for entry in entries if entry["grade"] == model.GRADE_FORBIDDEN]
    if forbidden:
        detail = "；".join("%s（%s）" % (entry["path"], entry["reason"] or "禁止")
                          for entry in forbidden)
        raise ScanError("选中的条目中包含禁止项，已拒绝清理：%s。下一步：用 `--select` 去掉它们；"
                        "`.git` 永远不参与清理。" % detail,
                        exit_code=exit_codes.UNSAFE)

    if from_payload:
        source_root = _choose_source_root(from_payload["roots"], entries)
        if source_root is None:
            raise ScanError("选中的条目跨越多个扫描根。下一步：一次 `clean` 只处理一个扫描根。",
                            exit_code=exit_codes.USAGE)
        mode = "select"
    else:
        # 没有候选清单时，以"选中项的公共父目录"为相对路径基准：
        # 单条选择 → relative_path 就是目录名；多条 → 保留它们在父级下的结构。
        parents = [os.path.dirname(os.path.abspath(entry["path"])) for entry in entries]
        source_root = os.path.commonpath(parents)
        mode = "select"

    return source_root, mode, entries


def _choose_source_root(roots, entries):
    """选一个包含全部选中项的扫描根；多个候选时取**最长**（最具体）的那个。

    与审计层的 `_root_for` 同一口径：相对路径越短越贴近用户的直觉，
    嵌套扫描根也不会把外层目录结构写进 manifest。
    """
    options = []
    for root in roots:
        absolute = os.path.abspath(root)
        if all(quarantine.is_inside(entry["path"], absolute) for entry in entries):
            options.append(absolute)
    if not options:
        return None
    options.sort(key=len, reverse=True)
    return options[0]


def _ensure_trash_ignored(source_root, trash_root, data_dir):
    """隔离区在扫描根内时，必须被忽略规则命中（否则重扫会把它当候选）。"""
    if not quarantine.is_inside(trash_root, source_root):
        return
    relative = os.path.relpath(os.path.abspath(trash_root), os.path.abspath(source_root))
    first = relative.replace(os.sep, "/").split("/")[0]
    try:
        _, ignore_rules = load_rules(data_dir)
    except RulesError as exc:
        raise ScanError(str(exc), exit_code=exit_codes.DEGRADED)
    if not ignore_rules.matches(first, first, True):
        raise ScanError(
            "隔离区位于扫描根内，但目录名 %r 未被忽略规则覆盖：%s。"
            "下一步：用默认的 `.trash`（已在 `data/rules/ignore.txt` 中），"
            "或用 `--trash` 指定扫描根之外的目录。"
            % (first, model.display_path(trash_root)),
            exit_code=exit_codes.UNSAFE)


def _clean_plan(source_root, trash_root, batch_id, entries):
    plan = []
    for entry in entries:
        relative = quarantine.relative_to_root(entry["path"], source_root)
        plan.append({
            "original_path": model.display_path(entry["path"]),
            "trash_path": quarantine.target_path(trash_root, batch_id, relative),
            "relative_path": relative,
            "bytes": entry["bytes"],
            "grade": entry["grade"],
            "reason": entry["reason"],
            "mtime_utc": entry["mtime_utc"],
            "type": entry["type"],
        })
    return plan


def _confirm_move(batch_id, confirm):
    """二次确认：必须输入 `MOVE <batch-id>`。

    非交互环境（stdin 不是 TTY）且调用方没有显式 `--yes` → 直接拒绝，
    不去 `input()` 读一个不存在的终端（否则在 CI/pytest 里会变成 OSError）。
    """
    if confirm is None and not sys.stdin.isatty():
        return False
    prompt = "即将把选中条目移入隔离区。确认请输入 `MOVE %s`：" % batch_id
    try:
        answer = (confirm or input)(prompt)
    except (EOFError, KeyboardInterrupt, OSError):
        return False
    return (answer or "").strip() == "MOVE %s" % batch_id


def _dry_run_payload(source_root, trash_root, batch_id, plan):
    return {
        "schema_version": CLEAN_SCHEMA_VERSION,
        "mode": "dry_run",
        "batch_id": batch_id,
        "source_root": model.display_path(source_root),
        "trash_root": model.display_path(trash_root),
        "entries": plan,
        "totals": {
            "count": len(plan),
            "bytes": sum(item["bytes"] for item in plan),
        },
    }


def _clean_summary_lines(payload):
    mode = payload["mode"]
    totals = payload.get("totals") or _entry_totals(payload.get("entries"))
    lines = []
    if mode == "dry_run":
        lines.append("清理预演（dry-run，未写盘、未移动任何文件）")
    else:
        lines.append("清理批次：%s" % payload["batch_id"])
    lines.append("源根：%s；隔离区：%s"
                 % (payload["source_root"], payload["trash_root"]))
    lines.append("选中条目：%d 个，合计 %s（%d 字节）"
                 % (totals["count"], format_bytes(totals["bytes"]), totals["bytes"]))
    for entry in payload.get("entries") or []:
        label = entry.get("relative_path") or entry.get("original_path")
        status = entry.get("status")
        suffix = "" if not status else " [%s]" % status
        lines.append("  %s → %s（%s）%s"
                     % (label, entry.get("trash_path"),
                        format_bytes(entry.get("bytes", 0)), suffix))
        if entry.get("detail"):
            lines.append("    说明：%s" % entry["detail"])
    if mode == "dry_run":
        lines.append("下一步：加 `--apply --yes` 真正移动；`devdisk undo <batch-id>` 可回滚。")
    else:
        lines.append("批次状态：%s" % payload.get("state"))
        lines.append("回滚：`devdisk undo %s --trash %s --yes`"
                     % (payload["batch_id"], payload["trash_root"]))
    return lines


def _entry_totals(entries):
    entries = entries or []
    return {
        "count": len(entries),
        "bytes": sum(int(entry.get("bytes") or 0) for entry in entries),
    }


def _write_clean_out(out, payload):
    """批次摘要只在调用方显式给 `--out` 时落盘。

    dry-run 的契约是"不写盘"（`plan/05` M4 DoD）：默认一个字节都不落。
    """
    if not out:
        return None
    target = os.path.abspath(out)
    write_json_atomic(target, payload)
    return target


def _move_entries(batch_dir, payload):
    """按 manifest 顺序逐条移动；每条先落 `moving`+digest，再移动，再落 `moved`。

    该顺序保证"已移动但无记录"不可能发生：中断时最坏状态是 `moving`，
    下次可凭 `trash_path` 是否存在判断该继续移动还是补记 `moved`。
    """
    failures = 0
    for entry in payload["entries"]:
        if entry.get("status") == manifest_mod.STATUS_MOVED:
            continue
        source = entry["original_path"]
        destination = entry["trash_path"]

        if os.path.lexists(destination) and not os.path.lexists(source):
            # 上次在 move 与写 moved 之间中断：目标已在，补记状态。
            try:
                actual = digest.tree_digest(destination)
            except digest.DigestError as exc:
                manifest_mod.mark_entry(
                    payload, entry, manifest_mod.FAILED_PREFIX + "digest",
                    detail=str(exc))
                failures += 1
                manifest_mod.save(batch_dir, payload)
                continue
            manifest_mod.mark_entry(payload, entry, manifest_mod.STATUS_MOVED,
                                    tree_digest=actual)
            manifest_mod.save(batch_dir, payload)
            continue

        if not os.path.lexists(source):
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + "source_missing")
            failures += 1
            manifest_mod.save(batch_dir, payload)
            continue

        manifest_mod.mark_entry(payload, entry, manifest_mod.STATUS_MOVING)
        manifest_mod.save(batch_dir, payload)

        try:
            entry["tree_digest"] = digest.tree_digest(source)
        except digest.DigestError as exc:
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + "digest",
                                    detail=str(exc))
            failures += 1
            manifest_mod.save(batch_dir, payload)
            continue
        except OSError as exc:
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + _failure_reason(exc),
                                    detail=str(exc))
            failures += 1
            manifest_mod.save(batch_dir, payload)
            continue

        manifest_mod.save(batch_dir, payload)
        try:
            quarantine.move(source, destination)
        except OSError as exc:
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + _failure_reason(exc),
                                    detail=str(exc))
            failures += 1
            manifest_mod.save(batch_dir, payload)
            continue

        manifest_mod.mark_entry(payload, entry, manifest_mod.STATUS_MOVED)
        manifest_mod.save(batch_dir, payload)

    if failures:
        payload["state"] = manifest_mod.STATE_PARTIAL
    else:
        payload["state"] = manifest_mod.STATE_COMPLETE
        payload["tree_digest"] = digest.combine_tree_digests(
            [(entry["relative_path"], entry["tree_digest"]) for entry in payload["entries"]])
    manifest_mod.save(batch_dir, payload)
    return payload


def _map_quarantine_errors(function):
    """把隔离层的 `QuarantineError` 映射为编排层的 `ScanError`（退出码原样带出）。

    隔离层不认识 CLI；编排层是唯一映射退出码的地方（`plan/02` §5.2）。
    """
    import functools

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except quarantine.QuarantineError as exc:
            raise ScanError(str(exc), exit_code=exc.exit_code)

    return wrapper


@_map_quarantine_errors
def run_clean(from_scan=None, select=None, apply=False, yes=False, trash=None,
              resume_batch=None, out=None, data_dir=None, config_path=None,
              now=None, confirm=None):
    """把选中项移入隔离区（默认 dry-run）。

    返回 `(exit_code, payload, summary_lines)`。
    退出码：0 预演/成功；1 未选择、未确认或有条目失败；2 用法错误；
    5 禁止项 / 跨设备 / 越界 / 隔离区未被忽略。
    """
    select_paths = list(select or [])
    settings, data_dir = _load_settings(config_path, data_dir)
    keep_paths = config.configured_keep_paths(settings)

    # ---- 恢复模式：按 manifest 续做，不接受新的 --select（避免语义漂移）。
    if resume_batch:
        if select_paths:
            raise ScanError("`--resume` 与 `--select` 不能同时使用。"
                            "下一步：恢复时只给 `--resume <batch-id>`。")
        trash_root = os.path.abspath(trash) if trash else \
            os.path.join(os.path.abspath(os.curdir), config.DEFAULT_TRASH_DIRNAME)
        batch_dir = os.path.join(trash_root, resume_batch)
        try:
            payload = manifest_mod.load(batch_dir)
        except manifest_mod.ManifestError as exc:
            raise ScanError(str(exc))
        payload["trash_root"] = model.display_path(trash_root)
        source_root = payload["source_root"]
        _ensure_trash_ignored(source_root, trash_root, data_dir)
        if not apply:
            return exit_codes.OK, _resume_preview(payload), \
                _resume_lines(payload, applied=False)
        if not yes and not _confirm_move(payload["batch_id"], confirm):
            raise ScanError("未得到确认（需要 `--yes` 或在交互终端输入 `MOVE <batch-id>`）。"
                            "下一步：确认后加 `--yes` 重跑。",
                            exit_code=exit_codes.REFUSED)
        payload = _move_entries(batch_dir, payload)
        payload["mode"] = "apply"
        return manifest_mod.exit_code_for_state(payload), payload, \
            _resume_lines(payload, applied=True)

    if not select_paths:
        raise ScanError("未给出任何选择。本工具不提供\"全选\"；"
                        "下一步：用 `--from scan.json --select <path>` 或 `--select <path>`。",
                        exit_code=exit_codes.REFUSED)

    from_payload = _load_scan_payload(from_scan) if from_scan else None
    source_root, mode, entries = _resolve_selection(
        select_paths, from_payload, data_dir, keep_paths=keep_paths)

    if not os.path.isdir(source_root):
        raise ScanError("源根不存在或不是目录：%s。下一步：检查 `--from` / `--select`。"
                        % source_root)

    quarantine.ensure_not_nested([entry["path"] for entry in entries])
    trash_root = quarantine.resolve_trash_root(trash, source_root)
    for entry in entries:
        quarantine.ensure_isolated(entry["path"], trash_root)
    _ensure_trash_ignored(source_root, trash_root, data_dir)

    # 同文件系统：dry-run 也要查（早失败优于"预演通过、真跑失败"）。
    for entry in entries:
        quarantine.ensure_same_device(entry["path"], trash_root)

    preview_id = quarantine.make_batch_id(now=now)
    plan = _clean_plan(source_root, trash_root, preview_id, entries)

    if not apply:
        payload = _dry_run_payload(source_root, trash_root, preview_id, plan)
        target = _write_clean_out(out, payload)
        payload = dict(payload)
        payload["_out"] = target
        return exit_codes.OK, payload, _clean_summary_lines(payload)

    if not yes and not _confirm_move(preview_id, confirm):
        raise ScanError("未得到确认（需要 `--yes` 或在交互终端输入 `MOVE <batch-id>`）。"
                        "下一步：确认后加 `--yes` 重跑。",
                        exit_code=exit_codes.REFUSED)

    quarantine.ensure_trash_root(trash_root)
    batch_id, batch_dir = quarantine.create_batch_dir(
        trash_root, now=now, preferred_id=preview_id)

    # 选中的可能是同一个父目录下的相对路径；批次 ID 以实际创建的为准。
    plan = _clean_plan(source_root, trash_root, batch_id, entries)
    manifest_entries = [
        manifest_mod.new_entry(
            original_path=item["original_path"],
            trash_path=item["trash_path"],
            relative_path=item["relative_path"],
            bytes=item["bytes"],
            grade=item["grade"],
            reason=item["reason"],
            mtime_utc=item["mtime_utc"],
        )
        for item in plan
    ]
    payload = manifest_mod.new_manifest(
        batch_id, model.display_path(trash_root),
        model.display_path(source_root), mode, manifest_entries)
    manifest_mod.save(batch_dir, payload)

    payload = _move_entries(batch_dir, payload)
    payload = dict(payload)
    payload["mode"] = "apply"
    payload["trash_root"] = model.display_path(trash_root)
    payload["source_root"] = model.display_path(source_root)
    target = _write_clean_out(out, payload)
    payload["_out"] = target
    return manifest_mod.exit_code_for_state(payload), payload, _clean_summary_lines(payload)


def _resume_preview(payload):
    return {
        "schema_version": CLEAN_SCHEMA_VERSION,
        "mode": "dry_run",
        "batch_id": payload["batch_id"],
        "source_root": payload["source_root"],
        "trash_root": payload["trash_root"],
        "entries": [
            {"original_path": entry["original_path"],
             "trash_path": entry["trash_path"],
             "relative_path": entry.get("relative_path"),
             "bytes": entry.get("bytes", 0),
             "grade": entry.get("grade"),
             "reason": entry.get("reason"),
             "mtime_utc": entry.get("mtime_utc"),
             "status": entry.get("status")}
            for entry in payload["entries"]
        ],
        "totals": {
            "count": len(payload["entries"]),
            "bytes": sum(entry.get("bytes", 0) for entry in payload["entries"]),
        },
    }


def _resume_lines(payload, applied):
    """续做批次的摘要：区分"预演"与"真跑"，并列出每条最终状态。"""
    entries = payload.get("entries") or []
    pending = [entry for entry in entries
               if entry.get("status") not in (manifest_mod.STATUS_MOVED,
                                              manifest_mod.STATUS_RESTORED)]
    lines = ["续做批次：%s（%s）"
             % (payload["batch_id"], "已执行" if applied else "预演，未移动任何文件")]
    lines.append("源根：%s；隔离区：%s"
                 % (payload.get("source_root", ""), payload.get("trash_root", "")))
    lines.append("条目：%d 个，待处理 %d 个，合计 %s"
                 % (len(entries), len(pending),
                    format_bytes(sum(entry.get("bytes", 0) for entry in entries))))
    for entry in entries:
        label = entry.get("relative_path") or entry["original_path"]
        lines.append("  %s → %s [%s]" % (label, entry["trash_path"],
                                        entry.get("status", "pending")))
        if entry.get("detail"):
            lines.append("    说明：%s" % entry["detail"])
    if applied:
        lines.append("批次状态：%s" % payload.get("state"))
    else:
        lines.append("下一步：确认后加 `--apply --yes` 继续移动。")
    return lines


def undo_default_trash():
    return os.path.join(os.path.abspath(os.curdir), config.DEFAULT_TRASH_DIRNAME)


def _restore_entries(batch_dir, payload, stop_on_conflict=True):
    """按 manifest **逆序**恢复；每条先校验 digest，目标存在即冲突且不覆盖。"""
    problems = 0
    for entry in reversed(payload["entries"]):
        status = entry.get("status")
        if status not in manifest_mod.RESTORABLE_STATES:
            continue

        original = entry["original_path"]
        trash_path = entry["trash_path"]

        if os.path.lexists(original):
            manifest_mod.mark_entry(payload, entry, manifest_mod.STATUS_CONFLICT,
                                    detail="目标路径已存在，未覆盖")
            problems += 1
            manifest_mod.save(batch_dir, payload)
            if stop_on_conflict:
                break
            continue

        if not os.path.lexists(trash_path):
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + "trash_missing")
            problems += 1
            manifest_mod.save(batch_dir, payload)
            continue

        manifest_mod.mark_entry(payload, entry, manifest_mod.STATUS_RESTORING)
        manifest_mod.save(batch_dir, payload)

        try:
            actual = digest.tree_digest(trash_path)
        except (digest.DigestError, OSError) as exc:
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + "digest",
                                    detail=str(exc))
            problems += 1
            manifest_mod.save(batch_dir, payload)
            continue

        if entry.get("tree_digest") and not digest.digests_match(actual, entry["tree_digest"]):
            manifest_mod.mark_entry(payload, entry, manifest_mod.STATUS_CONFLICT,
                                    detail="隔离区内容与 manifest 的 tree_digest 不一致")
            problems += 1
            manifest_mod.save(batch_dir, payload)
            if stop_on_conflict:
                break
            continue

        try:
            quarantine.move(trash_path, original)
        except OSError as exc:
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + _failure_reason(exc),
                                    detail=str(exc))
            problems += 1
            manifest_mod.save(batch_dir, payload)
            continue

        try:
            restored = digest.tree_digest(original)
        except (digest.DigestError, OSError) as exc:
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + "digest_after_restore",
                                    detail=str(exc))
            problems += 1
            manifest_mod.save(batch_dir, payload)
            continue

        if entry.get("tree_digest") and not digest.digests_match(restored, entry["tree_digest"]):
            manifest_mod.mark_entry(payload, entry,
                                    manifest_mod.FAILED_PREFIX + "digest_after_restore")
            problems += 1
            manifest_mod.save(batch_dir, payload)
            continue

        manifest_mod.mark_entry(payload, entry, manifest_mod.STATUS_RESTORED,
                                restored_digest=restored)
        manifest_mod.save(batch_dir, payload)

    payload["state"] = manifest_mod.STATE_UNDONE if not problems \
        else manifest_mod.STATE_UNDO_PARTIAL
    manifest_mod.save(batch_dir, payload)
    return payload


def run_undo(batch_id, trash=None, yes=False, out=None, stop_on_conflict=True):
    """按 manifest 逆序撤销一个批次。

    `--yes` 只是显式确认（恢复是保守动作：目标存在即冲突，不覆盖）。
    返回 `(exit_code, payload, summary_lines)`。
    退出码：0 正常；1 冲突/校验失败；2 manifest 缺失或损坏。
    """
    trash_root = os.path.abspath(trash) if trash else undo_default_trash()
    batch_dir = os.path.join(trash_root, batch_id)
    try:
        payload = manifest_mod.load(batch_dir)
        manifest_mod.check_manifest_usable(payload)
    except manifest_mod.ManifestError as exc:
        raise ScanError(str(exc))

    payload["trash_root"] = model.display_path(trash_root)
    payload = _restore_entries(batch_dir, payload, stop_on_conflict=stop_on_conflict)
    target = _write_clean_out(out, payload)
    payload = dict(payload)
    payload["_out"] = target
    return manifest_mod.exit_code_for_state(payload), payload, _undo_summary_lines(payload)


def _undo_summary_lines(payload):
    entries = payload.get("entries") or []
    lines = ["撤销批次：%s" % payload.get("batch_id")]
    lines.append("隔离区：%s；源根：%s"
                 % (payload.get("trash_root", ""), payload.get("source_root", "")))
    lines.append("条目：%d 个，合计 %s（%d 字节）"
                 % (len(entries), format_bytes(sum(entry.get("bytes", 0) for entry in entries)),
                    sum(entry.get("bytes", 0) for entry in entries)))
    for entry in entries:
        label = entry.get("relative_path") or entry["original_path"]
        status = entry.get("status") or "pending"
        lines.append("  %s ← %s [%s]" % (label, entry["trash_path"], status))
        if entry.get("detail"):
            lines.append("    说明：%s" % entry["detail"])
    conflicts = [entry for entry in entries
                 if entry.get("status") == manifest_mod.STATUS_CONFLICT]
    if conflicts:
        lines.append("冲突 %d 条：目标路径已存在或 digest 不一致，未覆盖；"
                     "已完成条目已保留。" % len(conflicts))
    lines.append("批次状态：%s" % payload.get("state"))
    return lines


# ---------------------------------------------------------------- 模块⑤：报告
#
# 报告层是**单一渲染源**（`plan/02` §6.5）：
#   scan.json（必需）+ audit.json（可选）+ 隔离批次 manifest（可选） → 一个 HTML。
# 本函数只做"读入 → 校验 → 交给 report 渲染 → 原子写"，不含任何判定。

REPORT_SCHEMA_VERSION = 1


def run_report(input_path, html_path, audit_path=None, top=None, as_of=None,
               batches=None, trash=None):
    """生成单文件 HTML 报告，返回 `(exit_code, stats, summary_lines)`。

    退出码：0 正常；2 输入缺失/损坏或输出不可写（`ScanError` 携带）。
    """
    if not html_path:
        raise ScanError("缺少 `--html <out.html>`。下一步：指定报告输出路径。")

    try:
        payload = report_mod.load_scan(input_path)
        audit, audit_note = report_mod.load_audit(audit_path)
        batch_payloads = report_mod.load_batches(batches or [], trash=trash)
        stats = report_mod.aggregate(payload, top=top)
        document = report_mod.render_html(
            payload, audit=audit, batches=batch_payloads, top=top, as_of=as_of,
            audit_note=audit_note,
            batch_note=("%d 个（来源：%s）" % (len(batch_payloads), trash)
                        if trash else None))
    except report_mod.ReportError as exc:
        raise ScanError(str(exc), exit_code=exc.exit_code)

    target = os.path.abspath(html_path)
    try:
        write_text_atomic(target, document)
    except OSError as exc:
        raise ScanError(
            "报告写盘失败：%s（%s）。下一步：确认输出目录可写，或用 `--html` 指定其他路径。"
            % (target, exc))

    lines = report_mod.summary_lines(
        stats, audit, target, top=top, batches=batch_payloads)
    return exit_codes.OK, stats, lines

