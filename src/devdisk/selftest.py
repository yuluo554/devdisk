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
RETIRED_COMMANDS = ("scan",)


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
