# -*- coding: utf-8 -*-
"""自检：交付态可用性的一条命令。

`devdisk selftest` 必须可证伪——任一不变量被破坏即返回非 0。
M0 阶段校验工程骨架不变量；M1 起追加数据层不变量（规则表可加载、scan 已退役 NOT_READY）。
"""

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
