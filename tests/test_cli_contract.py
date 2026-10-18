# -*- coding: utf-8 -*-
"""CLI 契约测试：退出码、子命令面、骨架期语义。"""

import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from devdisk import __version__
from devdisk import cli
from devdisk import exit_codes


def run_cli(argv):
    """在进程内调用 main()，返回 (退出码, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_version_returns_zero_and_prints_version():
    code, out, _ = run_cli(["--version"])
    assert code == exit_codes.OK
    assert __version__ in out


def test_help_lists_all_commands():
    code, out, _ = run_cli(["--help"])
    assert code == exit_codes.OK
    for name in ("scan", "audit", "clean", "undo", "report", "selftest"):
        assert name in out


def test_missing_command_is_usage_error():
    code, _, err = run_cli([])
    assert code == exit_codes.USAGE
    assert "缺少子命令" in err


def test_unknown_command_is_usage_error():
    code, _, _ = run_cli(["bogus-command"])
    assert code == exit_codes.USAGE


@pytest.mark.parametrize("command", sorted(cli.NOT_READY_COMMANDS))
def test_skeleton_commands_report_not_ready(command):
    argv = {"audit": ["audit"],
            "clean": ["clean"],
            "undo": ["undo", "20261018-120000-a1b2c3"],
            "report": ["report", "--input", "x.json", "--html", "x.html"]}[command]
    code, _, err = run_cli(argv)
    assert code == exit_codes.NOT_READY
    assert command in err
    assert "下一步" in err


def test_scan_is_retired_from_not_ready():
    """M1 退役：scan 不得再出现在 NOT_READY 表里。"""
    assert "scan" not in cli.NOT_READY_COMMANDS


def test_scan_runs_offline(tmp_path):
    root = tmp_path / "root"
    (root / "demo-web" / "node_modules").mkdir(parents=True)
    (root / "demo-web" / "package.json").write_text("{}", encoding="utf-8")
    (root / "demo-web" / "node_modules" / "a.js").write_bytes(b"x" * 64)
    out = tmp_path / "scan.json"

    code, stdout, stderr = run_cli(["scan", str(root), "--out", str(out), "--min-bytes", "0"])
    assert code == exit_codes.OK, stderr
    assert "候选目录" in stdout
    assert out.is_file()
    assert b"\r\n" not in out.read_bytes()


def test_scan_missing_root_returns_usage(tmp_path):
    code, _, err = run_cli(["scan", str(tmp_path / "nope"), "--out", str(tmp_path / "o.json")])
    assert code == exit_codes.USAGE
    assert "不存在" in err
    assert "下一步" in err


def test_scan_broken_grade_table_returns_degraded(tmp_path):
    """分级规则表坏掉 → 退出码 4（与类型规则表同一条降级语义，不静默用默认）。"""
    import shutil

    data_dir = tmp_path / "data"
    (data_dir / "rules").mkdir(parents=True)
    repo_rules = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "rules")
    for name in ("types.json", "ignore.txt"):
        shutil.copy(os.path.join(repo_rules, name), str(data_dir / "rules" / name))
    (data_dir / "rules" / "grades.json").write_text("{not json", encoding="utf-8")

    root = tmp_path / "root"
    (root / "node_modules").mkdir(parents=True)
    (root / "node_modules" / "a.js").write_bytes(b"x" * 16)

    out, err = io.StringIO(), io.StringIO()
    previous = os.environ.get("DEVDISK_DATA_DIR")
    os.environ["DEVDISK_DATA_DIR"] = str(data_dir)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["scan", str(root), "--out", str(tmp_path / "o.json"),
                             "--min-bytes", "0"])
    finally:
        if previous is None:
            os.environ.pop("DEVDISK_DATA_DIR", None)
        else:
            os.environ["DEVDISK_DATA_DIR"] = previous
    assert code == exit_codes.DEGRADED
    assert "分级" in err.getvalue()


def test_selftest_passes_on_skeleton():
    code, out, _ = run_cli(["selftest"])
    assert code == exit_codes.OK
    assert "OK" in out


def test_selftest_fails_when_a_command_is_missing(monkeypatch):
    """变异验证：从解析器里摘掉一个子命令，selftest 必须变红。"""
    parser = cli.build_parser()
    for action in parser._actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "scan" in choices:
            del choices["scan"]

    from devdisk.selftest import run_selftest

    code, lines = run_selftest(parser)
    assert code != exit_codes.OK
    assert any("scan" in line for line in lines)


def test_selftest_fails_when_exit_codes_collide(monkeypatch):
    """变异验证：把两个退出码改成同值，selftest 必须变红。"""
    from devdisk import selftest as selftest_module

    monkeypatch.setattr(selftest_module.exit_codes, "UNSAFE", exit_codes.USAGE)
    code, lines = selftest_module.run_selftest(cli.build_parser())
    assert code != exit_codes.OK
    assert any("重复" in line for line in lines)


def test_selftest_fails_when_retired_command_reappears(monkeypatch):
    """变异验证：把已退役的 scan 塞回 NOT_READY，selftest 必须变红。"""
    from devdisk import selftest as selftest_module

    monkeypatch.setitem(cli.NOT_READY_COMMANDS, "scan", "M1")
    code, lines = selftest_module.run_selftest(cli.build_parser())
    assert code != exit_codes.OK
    assert any("退役" in line for line in lines)


def test_selftest_fails_when_type_rule_table_is_broken(monkeypatch):
    """变异验证：类型规则表坏掉，selftest 必须变红。"""
    from devdisk import rules as rules_module
    from devdisk import selftest as selftest_module

    def boom():
        raise rules_module.RulesError("模拟规则表缺失")

    monkeypatch.setattr(rules_module, "load_rules", boom)
    code, lines = selftest_module.run_selftest(cli.build_parser())
    assert code != exit_codes.OK
    assert any("规则表" in line for line in lines)


def test_selftest_fails_when_git_type_missing(monkeypatch):
    """变异验证：类型表丢掉 .git，selftest 必须变红（只读样本失去输入）。"""
    from devdisk import rules as rules_module
    from devdisk import selftest as selftest_module

    class FakeRules(object):
        types = ["node_modules"]

        def match(self, name):
            return "node_modules" if name == "node_modules" else None

    monkeypatch.setattr(rules_module, "load_rules", lambda *a, **k: (FakeRules(), object()))
    code, lines = selftest_module.run_selftest(cli.build_parser())
    assert code != exit_codes.OK
    assert any(".git" in line for line in lines)


def test_no_select_all_flag():
    """产品纪律：CLI 不得提供全选/一键清理参数。"""
    parser = cli.build_parser()
    forbidden = {"all", "everything", "yes-all", "clean-all"}
    for action in parser._actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for name, sub in choices.items():
            for sub_action in sub._actions:  # noqa: SLF001
                option_strings = set(sub_action.option_strings)
                for bad in forbidden:
                    assert "--%s" % bad not in option_strings, (name, bad)


def test_module_entry_point_delegates_to_main():
    """`python -m devdisk` 走 __main__，必须与 main() 同语义。"""
    import devdisk.__main__ as entry

    assert entry.main is cli.main


def test_stdout_is_utf8_after_entry(monkeypatch):
    """入口守卫不应在 UTF-8 环境下破坏流。"""
    from devdisk.encoding import ensure_utf8_stdio

    ensure_utf8_stdio()
    assert sys.stdout is not None
