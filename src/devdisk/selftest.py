# -*- coding: utf-8 -*-
"""自检：交付态可用性的一条命令。

`devdisk selftest` 必须可证伪——任一不变量被破坏即返回非 0。
M0 阶段校验的是工程骨架不变量，后续里程碑逐步补充数据与基准不变量。
"""

import sys

from . import __version__
from . import exit_codes

# 骨架期必须注册的子命令（M1–M5 逐个落地时不得从该集合中消失）。
REQUIRED_COMMANDS = ("scan", "audit", "clean", "undo", "report")

REQUIRED_EXIT_CODES = ("OK", "REFUSED", "USAGE", "NOT_READY", "DEGRADED", "UNSAFE")


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
