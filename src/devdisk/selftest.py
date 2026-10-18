# -*- coding: utf-8 -*-
"""自检：交付态可用性的一条命令。

`devdisk selftest` 必须可证伪——任一不变量被破坏即返回非 0。
M0 校验工程骨架不变量；M1 追加数据层不变量（类型/忽略规则表可加载、scan 已退役）；
M2 追加分级层不变量（分级规则表可加载、`.git` 必为禁止、无证据必为谨慎）。
"""

import os
import sys

from . import __version__
from . import exit_codes

# 骨架期必须注册的子命令（M1–M5 逐个落地时不得从该集合中消失）。
REQUIRED_COMMANDS = ("scan", "audit", "clean", "undo", "report")

REQUIRED_EXIT_CODES = ("OK", "REFUSED", "USAGE", "NOT_READY", "DEGRADED", "UNSAFE")

# 已退役 NOT_READY、必须真正可用的子命令（每棒追加）。
RETIRED_COMMANDS = ("scan", "audit")


def run_selftest(parser):
    """返回 (exit_code, 明细行列表)。"""
    problems = []
    lines = []

    lines.append("devdisk selftest")
    lines.append("版本：%s" % __version__)
    lines.append("Python：%s" % sys.version.split()[0])

    if not __version__ or not __version__.count(".") >= 1:
        problems.append("版本号形态异常：%r" % (__version__,))

    values = []
    for name in REQUIRED_EXIT_CODES:
        value = getattr(exit_codes, name, None)
        if not isinstance(value, int):
            problems.append("退出码常量缺失或非整数：%s" % name)
        else:
            values.append(value)
    if len(set(values)) != len(values):
        problems.append("退出码常量存在重复值：%r" % (values,))

    actions = _registered_commands(parser)
    for name in REQUIRED_COMMANDS:
        if name not in actions:
            problems.append("子命令未注册：%s" % name)
    lines.append("已注册子命令：%s" % ", ".join(sorted(actions)))

    problems.extend(_check_retired())
    problems.extend(_check_rules())
    problems.extend(_check_grade_rules())
    problems.extend(_check_audit_invariants())

    if problems:
        for problem in problems:
            lines.append("FAIL %s" % problem)
        return exit_codes.REFUSED, lines

    lines.append("OK 骨架不变量全部通过")
    return exit_codes.OK, lines


def _registered_commands(parser):
    names = set()
    for action in parser._actions:  # noqa: SLF001 - argparse 无公开枚举 API
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            names.update(choices.keys())
    return names


def _check_retired():
    """已退役的子命令不得再出现在 NOT_READY 表中。"""
    from . import cli

    problems = []
    for name in RETIRED_COMMANDS:
        if name in cli.NOT_READY_COMMANDS:
            problems.append("子命令 %s 已退役却仍在 NOT_READY_COMMANDS 中" % name)
    return problems


def _check_rules():
    """M1 不变量：类型规则表与忽略规则必须可加载且非空。"""
    from .rules import RulesError, load_rules

    problems = []
    try:
        types, ignore = load_rules()
    except RulesError as exc:
        return ["规则表不可加载：%s" % exc]

    if not types.types:
        problems.append("类型规则表为空")
    if types.match("node_modules") != "node_modules":
        problems.append("类型规则表未能识别 node_modules")
    if types.match(".git") != "git":
        problems.append("类型规则表未能识别 .git（只读样本必需）")
    # ignore 可为空集合；这里只验证对象可用。
    if ignore is None:
        problems.append("忽略规则对象缺失")
    return problems


def _check_grade_rules():
    """M2 不变量：分级规则表可加载，且两条底线成立（`.git` 禁止 / 无证据谨慎）。

    用"合成候选"直接问引擎，不依赖任何真实磁盘内容——自检必须能在干净 clone
    里离线跑通。候选路径刻意取不存在的位置，避免触碰真实 `.git`。
    """
    from . import model
    from .grader import Grader, make_context
    from .rules import RulesError, load_grade_rules

    problems = []
    try:
        table = load_grade_rules()
    except RulesError as exc:
        return ["分级规则表不可加载：%s" % exc]

    if table.grades != list(model.GRADES):
        problems.append("分级规则表 grades 与口径不一致：%r" % (table.grades,))
    if not table.rules:
        problems.append("分级规则表为空")

    grader = Grader(table, make_context(model.ScanResult([])))

    probe = os.path.join(os.path.abspath(os.curdir), "__devdisk_selftest_probe__")
    git_like = model.Candidate(path=os.path.join(probe, ".git"), type="git", bytes=1,
                               mtime_utc=None, project_root=None)
    grade, reason, evidence = grader.grade(git_like)
    if grade != model.GRADE_FORBIDDEN:
        problems.append("分级规则表未把 `git` 类型判为禁止（实际 %s）" % grade)
    if not reason or not evidence:
        problems.append(".git 的禁止结论缺少理由或证据")

    unknown = model.Candidate(path=os.path.join(probe, "unknown"), type="unknown-type",
                              bytes=1, mtime_utc=None, project_root=None)
    grade, reason, evidence = grader.grade(unknown)
    if grade != model.GRADE_CAUTION:
        problems.append("未命中任何规则时未落谨慎（实际 %s）" % grade)
    if not reason or not evidence:
        problems.append("证据不足结论缺少理由或证据")
    return problems


def _check_audit_invariants():
    """M3 不变量：Docker fixture 可解析、`.git` 统计只读、建议永不 applied。

    离线可跑：只读入仓 fixture，不要求 Docker 安装，也不触碰真实 `.git`。
    """
    import os as _os
    import tempfile

    from .audit import STATUS_DOCKER_PARSE, docker as docker_audit, git as git_audit

    problems = []

    fixture_dir = _os.path.join("fixtures", "docker_system_df")
    try:
        from .rules import resolve_data_dir

        fixture_dir = _os.path.join(resolve_data_dir(), fixture_dir)
    except Exception:  # noqa: BLE001 - 规则表问题由 _check_rules 报
        return problems

    standard = _os.path.join(fixture_dir, "v24_standard.txt")
    if not _os.path.isfile(standard):
        problems.append("Docker fixture 缺失：%s" % standard)
    else:
        status, payload = docker_audit.parse_fixture(standard)
        if status != "ok":
            problems.append("Docker fixture 解析失败：%s" % status)
        elif payload["images"]["total_bytes"] <= 0:
            problems.append("Docker fixture 解析出 0 体积（不得静默为空）")
        for suggestion in payload.get("suggestions") or []:
            if suggestion.get("applied") is not False:
                problems.append("Docker 建议不是 applied=false：%r" % (suggestion,))

    malformed = _os.path.join(fixture_dir, "malformed.txt")
    if _os.path.isfile(malformed):
        status, payload = docker_audit.parse_fixture(malformed)
        if status != STATUS_DOCKER_PARSE:
            problems.append("畸形 Docker fixture 未按 error:docker_parse 处理（实际 %s）" % status)
        if not payload.get("raw_excerpt"):
            problems.append("error:docker_parse 未附原文摘录")

    # 合成一个最小 `.git`：pack + loose + objects/info（后者不得计入）。
    with tempfile.TemporaryDirectory(prefix="devdisk-selftest-git-") as temp_dir:
        git_dir = _os.path.join(temp_dir, ".git")
        pack_dir = _os.path.join(git_dir, "objects", "pack")
        loose_dir = _os.path.join(git_dir, "objects", "ab")
        info_dir = _os.path.join(git_dir, "objects", "info")
        for directory in (pack_dir, loose_dir, info_dir):
            _os.makedirs(directory)
        _write_bytes(_os.path.join(pack_dir, "pack-0001.pack"), 1024)
        _write_bytes(_os.path.join(pack_dir, "pack-0001.idx"), 128)
        _write_bytes(_os.path.join(loose_dir, "cdef0123456789"), 256)
        _write_bytes(_os.path.join(info_dir, "packs"), 64)

        before = git_audit.snapshot(git_dir)
        stats = git_audit.measure(git_dir)
        after = git_audit.snapshot(git_dir)
        if before != after:
            problems.append("`.git` 审计改动了元数据（只读纪律被破坏）")
        if stats != {"objects_bytes": 1280, "pack_files": 1, "loose_objects": 1}:
            problems.append("`.git` 统计口径不符：%r" % (stats,))

        entry = git_audit.audit_one(git_dir, root=temp_dir,
                                    loose_threshold=0, bytes_threshold=0)
        if entry["status"] != "ok":
            problems.append("`.git` 审计状态异常：%s" % entry["status"])
        for suggestion in entry.get("suggestions") or []:
            if suggestion.get("applied") is not False:
                problems.append("Git 建议不是 applied=false：%r" % (suggestion,))
            if suggestion.get("command") != "git gc":
                problems.append("Git 建议命令异常：%r" % (suggestion.get("command"),))

    return problems


def _write_bytes(path, size):
    with open(path, "wb") as handle:
        handle.write(b"\0" * size)
