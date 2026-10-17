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
    for name in doc_files():
        path = os.path.join(REPO_ROOT, name)
        if not os.path.isfile(path):
            continue
        if pattern.search(read_text(path)):
            offenders.append(name)
    assert offenders == []


def doc_files():
    """仓库内需要脱敏扫查的文档（新文档落盘时要加进来）。"""
    names = ["README.md", "data/README.md"]
    names += sorted("plan/" + name for name in os.listdir(os.path.join(REPO_ROOT, "plan"))
                    if name.endswith(".md"))
    return names


# ------------------------------------------------------------------ M1 数据守门


def test_rule_tables_exist_and_are_registered():
    """规则表是数据资产：既要落盘，也要在 plan/04 资产总表里登记来源。"""
    for rel in ("data/rules/types.json", "data/rules/ignore.txt"):
        assert os.path.isfile(os.path.join(REPO_ROOT, rel)), rel

    plan04 = read_text(os.path.join(REPO_ROOT, "plan", "04-数据计划.md"))
    assert "data/rules/types.json" in plan04
    assert "data/rules/ignore.txt" in plan04

    payload = read_text(os.path.join(REPO_ROOT, "data/rules/types.json"))
    assert "source" in payload, "规则表必须自带来源字段"


def test_type_rules_cover_plan_04_samples():
    """样本清单里的类型必须都能在规则表里匹配到，避免样本被静默忽略。"""
    import json
    import sys

    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from devdisk.rules import load_rules

    types, _ = load_rules()
    payload = json.loads(read_text(os.path.join(REPO_ROOT, "data/rules/types.json")))
    declared = set(payload["order"])

    required = {"node_modules", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache",
                ".tox", ".next", ".cache", "dist", "build", "out", "target", "git"}
    assert required <= declared, required - declared
    for name in required:
        assert types.get(name) is not None, name


def test_generator_does_not_use_random_module():
    """确定性纪律：生成器必须自研 RNG，不得依赖 `random`（跨版本序列不保证）。"""
    text = read_text(os.path.join(TOOLS_ROOT, "gen_synth_tree.py"))
    assert "import random" not in text
    assert "from random" not in text
    assert "splitmix64" in text.lower() or "SplitMix64" in text


def test_generator_does_not_read_system_clock():
    """确定性纪律：不得用 time.time()/datetime.now() 作为数据内容或 mtime 来源。"""
    text = read_text(os.path.join(TOOLS_ROOT, "gen_synth_tree.py"))
    for banned in ("time.time(", "datetime.datetime.now", "datetime.now("):
        assert banned not in text, banned


def test_truth_files_never_contain_observed_fields():
    """K12：任何生成的真值文件都不得出现 `observed_*`。"""
    import json

    truth_dir = os.path.join(REPO_ROOT, "devdisk-out")
    if not os.path.isdir(truth_dir):
        pytest.skip("未生成 devdisk-out（按需生成，不入仓）")
    for dirpath, dirnames, filenames in os.walk(truth_dir):
        for name in filenames:
            if name != "manifest.json":
                continue
            payload = json.loads(read_text(os.path.join(dirpath, name)))
            assert "observed_" not in json.dumps(payload, ensure_ascii=False)


def test_tools_scripts_are_executable_entrypoints():
    """tools/ 下的脚本必须能被 `py tools/x.py --help` 调用（无 import 期副作用）。"""
    for name in ("gen_synth_tree.py", "bench_scan.py"):
        path = os.path.join(TOOLS_ROOT, name)
        assert os.path.isfile(path), name
        text = read_text(path)
        assert "def main(" in text
        assert '__name__ == "__main__"' in text
