# -*- coding: utf-8 -*-
"""仓库纪律守门：隐私、安全、README 指令面可执行性。

这些断言在骨架期就要在位——它们防的是"后面几棒悄悄引入"的越界行为。
"""

import ast
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = os.path.join(REPO_ROOT, "src", "devdisk")
TOOLS_ROOT = os.path.join(REPO_ROOT, "tools")


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


# 唯一允许出现 `os.unlink` 的模块：确定性写盘的临时文件清理。
TEMP_CLEANUP_ALLOWLIST = {"serialize.py"}


def test_source_has_no_destructive_calls():
    """产品纪律：源码里不得出现删除候选目录的调用。

    `shutil.rmtree` 一律禁止；`os.remove` / `os.unlink` 只允许出现在
    临时文件清理单点（`serialize.py`）中。
    """
    hard_banned = {"rmtree", "rmdir"}
    soft_banned = {"remove", "unlink"}
    offenders = []
    for path in python_files(SRC_ROOT):
        basename = os.path.basename(path)
        tree = ast.parse(read_text(path), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in hard_banned:
                offenders.append("%s:%d %s" % (path, node.lineno, name))
            elif name in soft_banned and basename not in TEMP_CLEANUP_ALLOWLIST:
                offenders.append("%s:%d %s" % (path, node.lineno, name))
    assert offenders == []


def test_source_has_no_shell_destructive_strings():
    pattern = re.compile(r"rm\s+-rf|shutil\.rmtree|git\s+prune|git\s+gc\s+--")
    offenders = []
    for path in python_files(SRC_ROOT):
        for lineno, line in enumerate(read_text(path).splitlines(), 1):
            if pattern.search(line):
                offenders.append("%s:%d" % (path, lineno))
    assert offenders == []


def test_source_does_not_spawn_docker_prune_or_git_gc():
    """只读纪律：不得执行任何 prune / gc 写操作。"""
    banned = ("prune", "gc")
    offenders = []
    for path in python_files(SRC_ROOT):
        text = read_text(path)
        for match in re.finditer(r"subprocess\.(?:run|call|check_output|Popen)\((.*?)\)", text, re.S):
            call = match.group(1)
            for word in banned:
                if word in call:
                    offenders.append("%s: %s" % (path, word))
    assert offenders == []


def test_source_has_no_network_imports():
    """隐私红线：运行时代码不导入网络栈。"""
    banned = {"socket", "urllib", "http", "requests", "ftplib", "smtplib", "telnetlib", "xmlrpc"}
    offenders = []
    for path in python_files(SRC_ROOT):
        tree = ast.parse(read_text(path), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in banned:
                        offenders.append("%s:%d %s" % (path, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.split(".")[0] in banned:
                    offenders.append("%s:%d %s" % (path, node.lineno, module))
    assert offenders == []


def test_gitignore_covers_runtime_outputs():
    text = read_text(os.path.join(REPO_ROOT, ".gitignore"))
    for pattern in (".trash/", "devdisk-out/", "reports/"):
        assert pattern in text, pattern


def test_gitattributes_pins_lf():
    text = read_text(os.path.join(REPO_ROOT, ".gitattributes"))
    assert "eol=lf" in text


def test_license_file_exists_and_is_mit():
    text = read_text(os.path.join(REPO_ROOT, "LICENSE"))
    assert text.startswith("MIT License")


def test_readme_commands_exist_in_parser():
    """README 指令面：提到的子命令必须真在 CLI 里注册。"""
    from devdisk.cli import build_parser

    parser = build_parser()
    registered = set()
    for action in parser._actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            registered.update(choices.keys())

    readme = read_text(os.path.join(REPO_ROOT, "README.md"))
    used = set(re.findall(r"devdisk\s+([a-z][a-z0-9-]*)", readme))
    used -= {"--version", "--help"}
    assert used, "README 未给出任何可执行命令"
    assert used <= registered, "README 提到未注册的子命令：%s" % (used - registered)


def test_readme_does_not_claim_ci_green():
    """诚实口径：未接远端时不得声称 CI 已跑绿。"""
    readme = read_text(os.path.join(REPO_ROOT, "README.md"))
    assert "CI 绿" not in readme


def test_pyproject_declares_console_script():
    text = read_text(os.path.join(REPO_ROOT, "pyproject.toml"))
    assert "[project.scripts]" in text
    assert 'devdisk = "devdisk.cli:main"' in text


def test_no_absolute_local_paths_in_tracked_docs():
    """脱敏：仓库内文档不得出现本机绝对路径。"""
    pattern = re.compile(r"[A-Za-z]:[\\/](?:Users|ProgramData|Program)[\\/]"
                         r"|/home/[A-Za-z0-9._-]+/"
                         r"|/Users/[A-Za-z0-9._-]+/")
    offenders = []
    for name in ("README.md", "plan/00-README总览.md", "plan/01-题目文档.md",
                 "plan/02-架构与技术选型.md", "plan/03-模块详设.md",
                 "plan/04-数据计划.md", "plan/05-里程碑与发布.md",
                 "plan/06-交付对标与决策.md", "plan/HANDOFF-M1.md",
                 "data/README.md"):
        path = os.path.join(REPO_ROOT, name)
        if not os.path.isfile(path):
            continue
        if pattern.search(read_text(path)):
            offenders.append(name)
    assert offenders == []
