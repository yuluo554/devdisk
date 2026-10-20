# -*- coding: utf-8 -*-
"""仓库纪律守门：隐私、安全、README 指令面可执行性。

这些断言在骨架期就要在位——它们防的是"后面几棒悄悄引入"的越界行为。
"""

import ast
import json
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


def test_git_audit_layer_never_spawns_git():
    """`.git` 审计只做文件系统统计：整个 audit 包不得调用 subprocess 之外的 git 命令。"""
    audit_root = os.path.join(SRC_ROOT, "audit")
    git_module = read_text(os.path.join(audit_root, "git.py"))
    assert "subprocess" not in git_module
    assert "git gc" in git_module          # 只是建议命令文本
    assert "applied" in git_module and "False" in git_module


def test_docker_audit_only_calls_system_df():
    """Docker 只调用 `docker system df`；prune 命令只作为数据存在。"""
    text = read_text(os.path.join(SRC_ROOT, "audit", "docker.py"))
    calls = re.findall(r"subprocess\.(?:run|call|check_output|Popen)\((.*?)\)", text, re.S)
    assert calls, "应当有且只有 `docker system df` 这一处调用"
    for call in calls:
        assert "DOCKER_COMMAND" in call
    assert 'DOCKER_COMMAND = ("docker", "system", "df")' in text


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


def test_readme_declares_known_environment_issues():
    """干净环境验证暴露过的坑必须写在 README，而不是只留在交接文档里。

    实测（M6 干净 clone + 全新 venv）：
    - 3.8 自带 pip 20.2.3 无法可编辑安装 pyproject-only 项目 → 需先升级 pip；
    - **v0.1.1 起规则表随 wheel 分发**：非 editable 安装从任意 CWD 都可跑，
      `DEVDISK_DATA_DIR` 退化为**覆盖**入口（旧缺陷"wheel 形态必须设环境变量"已修）；
    - Windows `py` 启动器会绕过 venv → 用 `python -m pip`。
    """
    readme = read_text(os.path.join(REPO_ROOT, "README.md"))
    assert "## 已知环境问题" in readme
    for needle in ("DEVDISK_DATA_DIR", "python -m pip install -U pip", "py` 启动器",
                   "规则表随包分发"):
        assert needle in readme, needle


def test_readme_no_longer_documents_wheel_degradation():
    """DEC2=A 的反向守门：README 不得再出现"wheel 装完跑不了"的旧缺陷描述。

    改前原文：`恰好这条描述**不是缺陷**而是披露，但如果只改代码不改文档/测试，
    就等于把旧缺陷钉在新版本上。`
    """
    readme = read_text(os.path.join(REPO_ROOT, "README.md"))
    for stale in ("不随 wheel 打包", "wheel 安装仅在设了该变量后可跑",
                  "非 editable 安装找不到规则表"):
        assert stale not in readme, stale


def test_pyproject_declares_console_script():
    text = read_text(os.path.join(REPO_ROOT, "pyproject.toml"))
    assert "[project.scripts]" in text
    assert 'devdisk = "devdisk.cli:main"' in text


def test_version_has_single_source():
    """版本号只在 `pyproject.toml` 声明一次（v0.1.1 起两处字面值，必须相等）。

    改前实测：`pyproject.toml` 是字面值，`src/devdisk/__init__.py` 另有 `__version__`——
    两处不同步就会让 `devdisk --version` 与发布物元数据对不上（诊断 D1 的同族风险）。
    """
    import sys

    pyproject = read_text(os.path.join(REPO_ROOT, "pyproject.toml"))
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert match, "pyproject.toml 缺少字面量 version"
    declared = match.group(1)

    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from devdisk import __version__

    assert __version__ == declared, (__version__, declared)


def test_readme_declares_release_artifact_is_handmade_source_package():
    """D2(b)：README 必须写清"发布物是手工源码包"，且与 MANIFEST.in 的事实一致。

    设计口径：`python -m build` 的 sdist **不是**发布形态；Release 附件由 `git archive <tag>` 打出。
    """
    readme = read_text(os.path.join(REPO_ROOT, "README.md"))
    assert "发布物是手工源码包" in readme
    assert "git archive" in readme


def test_sdist_manifest_collects_data_assets():
    """D2(a)：`MANIFEST.in` 必须把运行期数据与基准输入收进 sdist。

    依据：改前实测 `tar -tzf dist/*.tar.gz` 里 `data/rules` / `data/fixtures` / `tools/` / `plan/`
    的 `grep -c` 全为 0，即"走 sdist 渠道装完 `devdisk scan` 直接 exit 4"。
    本测试只断言清单**声明**了这些路径（真构建在门禁里跑，见 `update/01-迭代路线.md` 批 1）。
    """
    manifest = read_text(os.path.join(REPO_ROOT, "MANIFEST.in"))
    for needle in ("recursive-include data/rules", "recursive-include data/fixtures",
                   "recursive-include tools", "recursive-include plan"):
        assert needle in manifest, needle


def test_wheel_distributes_rule_tables():
    """DEC2=A：规则表必须随 wheel 分发（`pip install` 后任意 CWD 都能跑）。

    实测依据（改前）：只装 wheel 的干净 venv + 中立 CWD → `scan` 报"找不到 data/rules/types.json" exit 4。
    本测试钉住 packaging 配置这一层；"装完真能跑"由门禁里的 wheel-only venv 复验。
    """
    text = read_text(os.path.join(REPO_ROOT, "pyproject.toml"))
    assert "[tool.setuptools.package-data]" in text
    assert "rules/*" in text
    assert "fixtures/docker_system_df/*" in text


def test_wheel_install_is_usable_without_data_dir_override():
    """DEC2=A：`devdisk` 必须能在没有任何仓库树的情况下自解析规则表。

    `rules.resolve_data_dir()` 的查找顺序里必须包含**包内**数据目录，
    否则 wheel 形态仍然只能靠 `DEVDISK_DATA_DIR` 环境变量（旧缺陷）。
    """
    import sys

    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from devdisk import rules

    text = read_text(os.path.join(REPO_ROOT, "src", "devdisk", "rules.py"))
    assert "bundled_data_dir" in text, "resolve_data_dir 未包含包内数据目录这一层"
    assert hasattr(rules, "bundled_data_dir"), "包内数据目录的定位函数必须存在"

    # 顺序守门：包内数据目录必须在"当前目录向上查找"之前被尝试，
    # 否则 wheel 形态在仓库树外的 CWD 仍然只能靠 DEVDISK_DATA_DIR。
    body = text.split("def resolve_data_dir", 1)[1]
    assert body.index("bundled_data_dir()") < body.index("os.getcwd()")

    # 独立于 CWD：把 CWD 换到中立目录后解析仍须成功（editable 安装下指向源码树）。
    # 注意 Windows 不允许删除进程的当前目录，退出前必须先切回。
    import tempfile

    previous = os.getcwd()
    neutral = tempfile.mkdtemp(prefix="dd-neutral-")
    try:
        os.chdir(neutral)
        resolved = rules.resolve_data_dir()
    finally:
        os.chdir(previous)
        import shutil

        shutil.rmtree(neutral, ignore_errors=True)
    assert os.path.isfile(os.path.join(resolved, "rules", "types.json"))



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


# ------------------------------------------------------------------ M2 数据守门


def test_grade_rules_exist_and_are_registered():
    """分级规则表是数据资产：既要落盘，也要在 data/README 与 plan/04 登记来源。"""
    path = os.path.join(REPO_ROOT, "data", "rules", "grades.json")
    assert os.path.isfile(path), "data/rules/grades.json"

    payload = read_text(path)
    assert '"source"' in payload, "规则表必须自带来源字段"
    assert '"order"' in payload, "规则表必须显式声明顺序（顺序即优先级）"

    assert "data/rules/grades.json" in read_text(os.path.join(REPO_ROOT, "plan", "04-数据计划.md"))
    assert "data/rules/grades.json" in read_text(os.path.join(REPO_ROOT, "data", "README.md"))


def test_reference_implementation_stays_out_of_production():
    """分层纪律（`plan/02` §三 第 3 条）：参照实现不共享、也不被生产模块 import。"""
    for path in python_files(SRC_ROOT):
        assert "reference_grade" not in read_text(path), path

    tool = read_text(os.path.join(TOOLS_ROOT, "reference_grade.py"))
    assert "import devdisk" not in tool
    assert "from devdisk" not in tool


def test_grade_rules_do_not_embed_absolute_paths():
    """规则表是入仓数据：不得出现本机绝对路径或真实用户名。"""
    text = read_text(os.path.join(REPO_ROOT, "data", "rules", "grades.json"))
    pattern = re.compile(r"[A-Za-z]:[\\/]|/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/")
    assert not pattern.search(text)


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
    for name in ("gen_synth_tree.py", "bench_scan.py", "bench_grade.py",
                 "reference_grade.py", "bench_audit.py", "bench_clean.py",
                 "check_release.py"):
        path = os.path.join(TOOLS_ROOT, name)
        assert os.path.isfile(path), name
        text = read_text(path)
        assert "def main(" in text
        assert '__name__ == "__main__"' in text


def test_check_release_has_falsifiable_probes():
    """DEC5=B：发布门必须自带探针，否则"门本身没被验证过"。

    判据是性质不是痕迹：探针表里必须有**至少 8 组**变异，且每组都有自己的检查项码，
    并且 `--probe` 的返回码由"未命中数"决定（不是恒 0）。
    """
    text = read_text(os.path.join(TOOLS_ROOT, "check_release.py"))
    probes = re.findall(r'probe\("([a-z0-9_]+)",\s*"([A-Z_]+)"', text)
    assert len(probes) >= 8, probes
    codes = {code for _, code in probes}
    assert len(codes) >= 8, codes
    names = {name for name, _ in probes}
    assert len(names) >= 8, names
    assert "return 0 if failures == 0 else 1" in text


def test_check_release_never_weakens_to_text_only_checks():
    """本仓纪律（提示词第三/四节）：门要**真跑**，不能把已有 pytest/bench 抄成读文本。

    因此发布门必须真构建包形态（真读 zip/tar 内容）并真做 git archive 同源比对。
    """
    text = read_text(os.path.join(TOOLS_ROOT, "check_release.py"))
    for needle in ("zipfile.ZipFile(", "tarfile.open(", '"git", "-C", repo, "archive"',
                   "build_sdist", "build_wheel", "sha256_file("):
        assert needle in text, needle
    # 包形态必须读容器内容（真构建产物），而不是断言"某文件里出现过某串字"。
    assert "_tree_digest(" in text


# ------------------------------------------------------------------ M3 数据守门


def test_docker_fixtures_are_registered():
    """Docker fixture 是入仓数据：文件齐备，且在 data/README 与 plan/04 登记来源。"""
    fixture_root = os.path.join(REPO_ROOT, "data", "fixtures", "docker_system_df")
    for name in ("v24_standard.txt", "v20_standard.txt", "empty.txt",
                 "malformed.txt", "expect.json"):
        assert os.path.isfile(os.path.join(fixture_root, name)), name

    data_readme = read_text(os.path.join(REPO_ROOT, "data", "README.md"))
    plan04 = read_text(os.path.join(REPO_ROOT, "plan", "04-数据计划.md"))
    for text in (data_readme, plan04):
        assert "data/fixtures/docker_system_df" in text

    expect = read_text(os.path.join(fixture_root, "expect.json"))
    assert "source" in expect, "手写真值必须自带来源说明"


def test_docker_fixtures_contain_no_real_names_or_absolute_paths():
    """fixture 手写脱敏：不得出现本机绝对路径、真实镜像名/容器 ID 形态。"""
    fixture_root = os.path.join(REPO_ROOT, "data", "fixtures", "docker_system_df")
    path_pattern = re.compile(r"[A-Za-z]:[\\/]|/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/")
    for name in sorted(os.listdir(fixture_root)):
        text = read_text(os.path.join(fixture_root, name))
        assert not path_pattern.search(text), name


def test_generated_git_truth_never_contains_observed_fields():
    """真值只存输入与期望；`.git` 段同样不得出现 `observed_*`。"""
    truth_path = os.path.join(REPO_ROOT, "devdisk-out", "synth", "truth", "manifest.json")
    if not os.path.isfile(truth_path):
        pytest.skip("未生成 devdisk-out/synth（按需生成，不入仓）")
    payload = json.loads(read_text(truth_path))
    assert payload["git_repos"], "真值应登记合成仓库"
    for repo in payload["git_repos"]:
        for key in repo:
            assert not key.startswith("observed_"), key
        assert repo["expected_objects_bytes"] >= 0


# ------------------------------------------------------------------ M4 数据守门


def test_quarantine_never_deletes_and_never_copies():
    """产品纪律：隔离区只移动，不删除、不复制。

    - `src/` 里不得出现 `shutil.rmtree` / `os.remove` / `os.unlink`（临时文件单点除外）；
    - 隔离区模块不得出现 `shutil.copy*`（跨设备不得用"复制后删除"绕过）。
    """
    offenders = []
    copy_offenders = []
    for path in python_files(SRC_ROOT):
        text = read_text(path)
        basename = os.path.basename(path)
        for lineno, line in enumerate(text.splitlines(), 1):
            if re.search(r"shutil\.copy(?:file|tree|2)?\b", line):
                copy_offenders.append("%s:%d" % (path, lineno))
            if basename == "quarantine.py" and re.search(
                    r"shutil\.(?:move|copy)|os\.replace", line):
                if "shutil.move" not in line:
                    offenders.append("%s:%d %s" % (path, lineno, line.strip()))
    assert copy_offenders == []
    assert offenders == []


def test_quarantine_is_same_device_only():
    """跨设备必须拒绝：`quarantine.py` 必须比较 `st_dev` 且不提供复制回退。"""
    text = read_text(os.path.join(SRC_ROOT, "quarantine.py"))
    assert "st_dev" in text
    assert "ensure_same_device" in text
    assert "shutil.move" in text
    assert "shutil.copytree" not in text


def test_cli_has_no_select_all_for_clean():
    """CLI 反射守门：clean/undo 不得出现全选参数。"""
    import sys as _sys

    _sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from devdisk.cli import build_parser

    parser = build_parser()
    forbidden = {"all", "everything", "clean-all", "yes-all", "select-all"}
    seen = set()
    for action in parser._actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for name in ("clean", "undo"):
            sub = choices.get(name)
            if sub is None:
                continue
            for sub_action in sub._actions:  # noqa: SLF001
                for option in sub_action.option_strings:
                    seen.add(option)
                    for bad in forbidden:
                        assert "--%s" % bad != option, (name, option)
    assert "--select" in seen


def test_clean_requires_explicit_selection_in_source():
    """实现守门：`run_clean` 在无选择时必须走 REFUSED，不得回退到"全部候选"。"""
    text = read_text(os.path.join(SRC_ROOT, "pipeline.py"))
    assert "本工具不提供" in text and "全选" in text
    assert "exit_code=exit_codes.REFUSED" in text
    # 不得出现"没有选择就取全部候选"的写法。
    assert "candidates if not select" not in text
    assert "select or [candidate" not in text


def test_clean_and_undo_are_documented_in_plan():
    """文档口径：M4 落地后 `plan/05` 退役表与 README 状态行必须同步。"""
    plan05 = read_text(os.path.join(REPO_ROOT, "plan", "05-里程碑与发布.md"))
    assert "clean` | ✅ 已退役（M4）" in plan05 or "已退役（M4）" in plan05
    readme = read_text(os.path.join(REPO_ROOT, "README.md"))
    assert "M4" in readme
