# -*- coding: utf-8 -*-
"""分级层（模块②）测试。

测试纪律：期望值一律来自**植入真值**或由测试自己构造的独立事实，
不手抄引擎输出（`data/README.md` §四 第 15 条 / K12）。

本文件同时承担 M2 的两条门：

- **常驻不变量门** `collect_grade_problems()`：每条候选理由非空、证据 ≥1、
  真值样本分级一致；变异测试与应用它的是同一个函数（红/绿同一把尺子）。
- **证据门槛变异门**：摘掉"证据不足降级"分支 → 上面这把尺子必须变红。
"""

import builtins
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

from devdisk import exit_codes
from devdisk import evidence as evidence_module
from devdisk import grader as grader_module
from devdisk import model
from devdisk import pipeline
from devdisk import rules as rules_module
from devdisk.rules import load_grade_rules, load_rules

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_ROOT = os.path.join(REPO_ROOT, "tools")


# ----------------------------------------------------------------- 工具函数


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write(path, data=b"x"):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data)


def make_link(link_path, target):
    try:
        os.symlink(target, link_path, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError, AttributeError):
        if os.name != "nt":
            raise
        result = subprocess.run(["cmd", "/c", "mklink", "/J", link_path, target],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            pytest.skip("本机既不能建 symlink 也不能建 junction")
        return "junction"


def scan(root, out=None, **kwargs):
    kwargs.setdefault("min_bytes", 0)
    return pipeline.run_scan([str(root)], out=out, **kwargs)


def load_tool(name):
    path = os.path.join(TOOLS_ROOT, name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rel_of(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def collect_grade_problems(payload, truth=None, root=None):
    """常驻不变量：理由非空、证据 ≥1、等级合法、（给了真值就）逐样本一致。"""
    problems = []
    for candidate in payload["candidates"]:
        label = candidate["path"]
        if candidate["grade"] not in model.GRADES:
            problems.append("%s：grade 非法（%r）" % (label, candidate["grade"]))
        if not (candidate["reason"] or "").strip():
            problems.append("%s：reason 为空" % label)
        evidence = candidate["evidence"] or []
        if len(evidence) < 1:
            problems.append("%s：evidence 少于 1 条" % label)
        for item in evidence:
            for field in ("kind", "path", "detail"):
                if not (item.get(field) or "").strip():
                    problems.append("%s：证据字段 %s 为空（%r）" % (label, field, item))
    if truth is not None:
        actual = {rel_of(c["path"], root): c for c in payload["candidates"]}
        for sample in truth["samples"]:
            if sample["rel_path"] == "":
                continue
            candidate = actual.get(sample["rel_path"])
            if candidate is None:
                problems.append("%s：未检出" % sample["rel_path"])
            elif candidate["grade"] != sample["expected_grade"]:
                problems.append("%s：grade 期望 %s / 实际 %s"
                                % (sample["rel_path"], sample["expected_grade"],
                                   candidate["grade"]))
    return problems


def copy_rule_tables(destination):
    """把仓库里的 types.json / ignore.txt 复制到临时数据目录。"""
    os.makedirs(os.path.join(destination, "rules"))
    for name in ("types.json", "ignore.txt"):
        shutil.copy(os.path.join(REPO_ROOT, "data", "rules", name),
                    os.path.join(destination, "rules", name))
    return destination


# ----------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def synth_tree(tmp_path_factory):
    path = os.path.join(REPO_ROOT, "tools", "gen_synth_tree.py")
    spec = importlib.util.spec_from_file_location("gen_synth_tree_grade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tree = tmp_path_factory.mktemp("synth-grade")
    module.build_tree(str(tree), module.DEFAULT_SEED)
    return tree


@pytest.fixture(scope="module")
def graded(synth_tree, tmp_path_factory):
    """整个模块只扫一次（含分级），各用例共用。"""
    out = tmp_path_factory.mktemp("graded") / "scan.json"
    code, payload, lines = scan(synth_tree, out=str(out))
    assert code == exit_codes.OK
    truth = json.loads((synth_tree / "truth" / "manifest.json").read_text(encoding="utf-8"))
    return {"payload": payload, "truth": truth, "tree": synth_tree,
            "out": out, "lines": lines}


# --------------------------------------------------------------- 契约与合计


def test_scan_output_carries_grade_reason_evidence(tmp_path):
    root = tmp_path / "root"
    write(str(root / "demo-web" / "package.json"), b'{"dependencies": {"demo-pkg-a": "1.0.0"}}')
    write(str(root / "demo-web" / "package-lock.json"), b'{"lockfileVersion": 3}')
    write(str(root / "demo-web" / "node_modules" / "pkg" / "index.js"), b"a" * 500)

    code, payload, lines = scan(root, out=str(tmp_path / "scan.json"))
    assert code == exit_codes.OK
    candidate = payload["candidates"][0]
    assert candidate["grade"] == "safe"
    assert candidate["reason"].startswith("安全可删：")
    assert candidate["evidence"][0]["kind"] == "lockfile"
    assert candidate["evidence"][0]["path"] == "package-lock.json"
    assert any(line.startswith("分级：") for line in lines)
    assert collect_grade_problems(payload) == []


def test_totals_by_grade_always_has_three_keys(tmp_path):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "a"), b"x" * 10)
    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert set(payload["totals"]["by_grade"]) == set(model.GRADES)


def test_forbidden_is_counted_separately_from_reclaimable(graded):
    totals = graded["payload"]["totals"]
    by_grade = totals["by_grade"]
    assert by_grade[model.GRADE_FORBIDDEN] > 0          # 合成树含 .git 样本
    closed = sum(by_grade[grade] for grade in model.GRADES)
    assert closed == totals["candidate_bytes"]
    assert by_grade[model.GRADE_SAFE] + by_grade[model.GRADE_CAUTION] != closed


# --------------------------------------------------------------- 真值对账


def test_grade_invariants_hold_on_synth(graded):
    """M2 常驻门：理由非空 + 证据 ≥1 + 真值逐样本一致。"""
    problems = collect_grade_problems(graded["payload"], graded["truth"], graded["tree"])
    assert problems == []


def test_synth_grades_match_truth(graded):
    tree = graded["tree"]
    truth = graded["truth"]
    actual = {rel_of(c["path"], tree): c for c in graded["payload"]["candidates"]}
    mismatched = []
    for sample in truth["samples"]:
        if sample["rel_path"] == "":
            continue
        candidate = actual.get(sample["rel_path"])
        if candidate is None or candidate["grade"] != sample["expected_grade"]:
            mismatched.append((sample["rel_path"], sample["expected_grade"],
                               candidate["grade"] if candidate else None))
    assert mismatched == []
    assert len([s for s in truth["samples"] if s["rel_path"]]) == 18


def test_insufficient_evidence_samples_land_caution(graded):
    tree = graded["tree"]
    actual = {rel_of(c["path"], tree): c for c in graded["payload"]["candidates"]}
    checked = 0
    for sample in graded["truth"]["samples"]:
        if "insufficient_evidence" not in (sample.get("evidence_facts") or ()):
            continue
        checked += 1
        candidate = actual[sample["rel_path"]]
        assert candidate["grade"] == model.GRADE_CAUTION, sample["rel_path"]
        assert "证据不足" in candidate["reason"]
    assert checked >= 1          # 合成树里确实有"证据不足"样本（out）


def test_git_candidate_is_forbidden(graded):
    tree = graded["tree"]
    actual = {rel_of(c["path"], tree): c for c in graded["payload"]["candidates"]}
    git_candidate = actual["demo-infra/.git"]
    assert git_candidate["grade"] == model.GRADE_FORBIDDEN
    assert git_candidate["evidence"][0]["kind"] == "git_metadata"


# ------------------------------------------------------------------ 确定性


def test_grade_is_byte_identical_twice(synth_tree):
    first = synth_tree.parent / "grade-1.json"
    second = synth_tree.parent / "grade-2.json"
    scan(synth_tree, out=str(first))
    scan(synth_tree, out=str(second))
    assert first.read_bytes() == second.read_bytes()


def test_run_grade_entry_point_is_shared_with_cli(synth_tree):
    """`run_grade()` 与 `run_scan()` 内的分级必须同源（同一函数）。"""
    from devdisk.scanner import Scanner

    type_rules, ignore_rules = load_rules()
    result = Scanner(type_rules, ignore_rules, min_bytes=0).scan([str(synth_tree)])
    code, stats, lines = pipeline.run_grade(result)
    assert code == exit_codes.OK
    assert stats["by_grade"][model.GRADE_SAFE] > 0
    assert any("安全可删" in line for line in lines)
    assert collect_grade_problems(pipeline.build_payload(result)) == []


# --------------------------------------------------------- 规则表：数据驱动


def test_grade_rules_contract():
    table = load_grade_rules()
    assert table.grades == list(model.GRADES)
    assert table.order == [rule.id for rule in table.rules]      # 顺序即优先级
    for rule in table.rules:
        assert rule.id.startswith(rule.grade + ":")
        assert "{evidence}" in rule.reason

    payload = json.loads(read_text(os.path.join(REPO_ROOT, "data", "rules", "grades.json")))
    assert payload.get("source"), "规则表必须自带来源字段"

    # 规则引用的类型必须都在类型规则表里
    types, _ = load_rules()
    for rule in table.rules:
        for type_name in rule.types:
            if type_name == "*":
                continue
            assert types.get(type_name) is not None, (rule.id, type_name)

    # 证据源词表三处一致：数据声明 / rules 词表 / evidence 实现
    assert set(payload["sources"]) == set(rules_module.GRADE_SOURCES)
    assert set(rules_module.GRADE_SOURCES) == set(evidence_module._SOURCES)


def test_engine_modules_have_no_hardcoded_rule_content():
    """引擎只解释规则：规则表里的类型名与模式串不得作为字面量出现在引擎里。"""
    import ast

    types_payload = json.loads(read_text(os.path.join(REPO_ROOT, "data", "rules", "types.json")))
    grades_payload = json.loads(read_text(os.path.join(REPO_ROOT, "data", "rules", "grades.json")))
    tokens = set(types_payload["order"])
    for group in grades_payload["patterns"].values():
        tokens.update(group.get("patterns") or ())
        tokens.update(group.get("extensions") or ())
    for rule in grades_payload["rules"]:
        for check in rule.get("checks") or ():
            tokens.update(check.get("patterns") or ())
    # `package.json` 属于"允许打开的文件名白名单"（隐私约束），不是分级规则。
    tokens -= set(evidence_module.JSON_KEY_FILES)

    offenders = []
    for name in ("grader.py", "evidence.py"):
        path = os.path.join(REPO_ROOT, "src", "devdisk", name)
        tree = ast.parse(read_text(path), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in tokens:
                    offenders.append("%s:%d %r" % (name, node.lineno, node.value))
    assert offenders == []


def test_patterns_come_from_the_rule_table(tmp_path):
    """把 `local_state` 模式组改成不含 `.env`，含 `.env` 的 node_modules 必须变 safe。

    这条用例证明"判定来自数据"：引擎不认识 `.env`，只认识规则表里的模式。
    """
    root = tmp_path / "root"
    write(str(root / "demo" / "package.json"), b'{"dependencies": {}}')
    write(str(root / "demo" / "package-lock.json"), b'{"lockfileVersion": 3}')
    write(str(root / "demo" / "node_modules" / "a.js"), b"x" * 10)
    write(str(root / "demo" / "node_modules" / ".env"), b"TOKEN=1\n")

    _, baseline, _ = scan(root, out=str(tmp_path / "a.json"))
    assert baseline["candidates"][0]["grade"] == model.GRADE_CAUTION

    data_dir = copy_rule_tables(str(tmp_path / "data"))
    payload = json.loads(read_text(os.path.join(REPO_ROOT, "data", "rules", "grades.json")))
    # 两个模式组都去掉 `.env`（用哪一组是规则表的安排，引擎不参与）
    for group_id in ("local_state", "manual_config"):
        payload["patterns"][group_id]["patterns"] = [
            pattern for pattern in payload["patterns"][group_id]["patterns"]
            if not pattern.startswith(".env")]
    with open(os.path.join(data_dir, "rules", "grades.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    _, mutated, _ = scan(root, out=str(tmp_path / "b.json"), data_dir=data_dir)
    assert mutated["candidates"][0]["grade"] == model.GRADE_SAFE
    assert collect_grade_problems(mutated) == []


def test_rule_table_order_is_validated(tmp_path):
    """顺序违反优先级（safe 排在 caution 之前）必须在加载期报错。"""
    data_dir = copy_rule_tables(str(tmp_path / "data"))
    payload = json.loads(read_text(os.path.join(REPO_ROOT, "data", "rules", "grades.json")))
    rules = payload["rules"]
    payload["rules"] = [rules[9]] + rules[:9] + rules[10:]
    payload["order"] = [rule["id"] for rule in payload["rules"]]
    with open(os.path.join(data_dir, "rules", "grades.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    with pytest.raises(rules_module.RulesError):
        rules_module.load_grade_rules(data_dir)


def test_missing_grade_rules_degrades(tmp_path):
    data_dir = copy_rule_tables(str(tmp_path / "data"))
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(pipeline.ScanError) as info:
        pipeline.run_scan([str(root)], out=str(tmp_path / "o.json"),
                          data_dir=data_dir, min_bytes=0)
    assert info.value.exit_code == exit_codes.DEGRADED


# ------------------------------------------------------- 证据门槛与变异验证


def _deny_scandir(monkeypatch, denied_path):
    real_scandir = os.scandir
    denied = os.path.normcase(os.path.abspath(denied_path))

    def guarded(path="."):
        if os.path.normcase(os.path.abspath(str(path))) == denied:
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(evidence_module.os, "scandir", guarded)


def _unreadable_node_modules_tree(tmp_path):
    root = tmp_path / "root"
    write(str(root / "demo" / "package.json"), b'{"dependencies": {"a": "1"}}')
    write(str(root / "demo" / "package-lock.json"), b'{"lockfileVersion": 3}')
    write(str(root / "demo" / "node_modules" / "a.js"), b"x" * 100)
    write(str(root / "demo" / "node_modules" / "locked" / "b.js"), b"y" * 100)
    return root


def test_unreadable_subtree_refuses_safe(tmp_path, monkeypatch):
    """子树读不全时不得给 safe：无法证明"纯依赖目录"，按谨慎处理。"""
    root = _unreadable_node_modules_tree(tmp_path)
    _deny_scandir(monkeypatch, str(root / "demo" / "node_modules" / "locked"))

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    candidate = payload["candidates"][0]
    assert candidate["grade"] == model.GRADE_CAUTION
    assert "unreadable_subtree" in [item["kind"] for item in candidate["evidence"]]
    assert "降级" in candidate["reason"] or "证据不足" in candidate["reason"]


def test_mutation_removing_unreadable_guard_changes_conclusion(tmp_path, monkeypatch):
    """变异验证：摘掉"读不全就不给 safe"这一分支，结论必须改变。"""
    root = _unreadable_node_modules_tree(tmp_path)
    _deny_scandir(monkeypatch, str(root / "demo" / "node_modules" / "locked"))
    _, payload, _ = scan(root, out=str(tmp_path / "a.json"))
    assert payload["candidates"][0]["grade"] == model.GRADE_CAUTION

    def identity(self, candidate, state, rule, grade, evidence):
        return grade, grader_module.render_reason(rule.reason, evidence), evidence

    monkeypatch.setattr(grader_module.Grader, "_finalize", identity)
    _, mutated, _ = scan(root, out=str(tmp_path / "b.json"))
    assert mutated["candidates"][0]["grade"] == model.GRADE_SAFE


def test_type_only_safe_rule_is_downgraded(tmp_path):
    """证据门槛：安全规则没有证据源时（规则表缺陷）必须降级为谨慎。"""
    data_dir = copy_rule_tables(str(tmp_path / "data"))
    table = {
        "schema_version": 1,
        "source": "测试夹具：一条没有证据源的安全规则",
        "grades": list(model.GRADES),
        "order": ["safe:type_only"],
        "sources": {},
        "patterns": {},
        "rules": [{
            "id": "safe:type_only",
            "grade": "safe",
            "types": ["*"],
            "checks": [],
            "reason": "安全可删：类型即证据（{evidence}）。",
        }],
    }
    with open(os.path.join(data_dir, "rules", "grades.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(table, ensure_ascii=False, indent=2) + "\n")

    root = tmp_path / "root"
    write(str(root / "cache-any" / "entry.bin"), b"x" * 10)
    _, payload, _ = scan(root, out=str(tmp_path / "o.json"), data_dir=data_dir)
    candidate = payload["candidates"][0]
    assert candidate["grade"] == model.GRADE_CAUTION
    assert "证据不足" in candidate["reason"]
    assert collect_grade_problems(payload) == []


def test_mutation_removing_evidence_gate_turns_red(tmp_path, monkeypatch):
    """变异验证（`plan/05` M2 DoD）：删掉"证据不足降级"分支 → 常驻门必红。"""
    data_dir = copy_rule_tables(str(tmp_path / "data"))
    table = {
        "schema_version": 1,
        "source": "测试夹具：一条没有证据源的安全规则",
        "grades": list(model.GRADES),
        "order": ["safe:type_only"],
        "sources": {},
        "patterns": {},
        "rules": [{
            "id": "safe:type_only",
            "grade": "safe",
            "types": ["*"],
            "checks": [],
            "reason": "安全可删：类型即证据（{evidence}）。",
        }],
    }
    with open(os.path.join(data_dir, "rules", "grades.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(table, ensure_ascii=False, indent=2) + "\n")

    root = tmp_path / "root"
    write(str(root / "cache-any" / "entry.bin"), b"x" * 10)

    # 正常实现：降级为谨慎，常驻门全绿
    _, healthy, _ = scan(root, out=str(tmp_path / "a.json"), data_dir=data_dir)
    assert collect_grade_problems(healthy) == []

    # 变异：把证据门槛改成"照原样返回"（等价于删掉降级分支）
    def identity(self, candidate, state, rule, grade, evidence):
        return grade, grader_module.render_reason(rule.reason, evidence), evidence

    monkeypatch.setattr(grader_module.Grader, "_finalize", identity)
    _, mutated, _ = scan(root, out=str(tmp_path / "b.json"), data_dir=data_dir)
    assert mutated["candidates"][0]["grade"] == model.GRADE_SAFE
    assert collect_grade_problems(mutated) != []      # 必红


# --------------------------------------------------------------- 隐私守门


def test_evidence_never_opens_non_whitelisted_files(synth_tree, tmp_path, monkeypatch):
    """只读白名单文件名与 stat：扫完合成树后，被打开的文件必须只有 package.json。"""
    opened = []
    real_open = builtins.open
    tree_key = os.path.normcase(os.path.abspath(str(synth_tree)))

    def recorder(file, *args, **kwargs):
        try:
            absolute = os.path.abspath(str(file))
        except (TypeError, ValueError):
            absolute = None
        if absolute:
            opened.append(absolute)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recorder)
    code, payload, _ = scan(synth_tree, out=str(tmp_path / "scan.json"))
    assert code == exit_codes.OK
    assert collect_grade_problems(payload) == []

    inside = [path for path in opened if os.path.normcase(path).startswith(tree_key)]
    assert inside, "没有记录到任何读取，断言会空转"
    offenders = [path for path in inside
                 if os.path.basename(path) not in evidence_module.JSON_KEY_FILES]
    assert offenders == []
    assert any(os.path.basename(path) == "package.json" for path in inside)


def test_reason_strings_do_not_leak_absolute_paths(graded):
    tree = str(graded["tree"])
    for candidate in graded["payload"]["candidates"]:
        assert tree not in candidate["reason"], candidate["path"]
        for item in candidate["evidence"]:
            assert tree not in item["detail"], candidate["path"]


def test_reason_strings_never_contain_file_body_content(graded):
    """理由/证据只由路径、体积、mtime、白名单文件名组成，不含任何文件正文片段。

    用合成树里几个**只在正文里出现**的标记串做反证：`.env` 的 `API_BASE`、
    `Makefile` 的 `cargo build`、锁文件的 `lockfileVersion`、`pyproject.toml` 的 `[project]`。
    它们一旦出现在理由里，就说明"读了正文"。
    """
    body_markers = ("API_BASE", "cargo build", "lockfileVersion", "[project]")
    for candidate in graded["payload"]["candidates"]:
        text = candidate["reason"] + "".join(
            item["detail"] + item["path"] for item in candidate["evidence"])
        for marker in body_markers:
            assert marker not in text, (candidate["path"], marker, text)


# ------------------------------------------------------------------ 工具门


def test_bench_grade_passes(synth_tree):
    module = load_tool("bench_grade.py")
    assert module.main(["--tree", str(synth_tree)]) == 0


def test_reference_grade_matches_engine(synth_tree, tmp_path):
    scan_out = tmp_path / "scan.json"
    scan(synth_tree, out=str(scan_out))
    module = load_tool("reference_grade.py")
    assert module.main(["--tree", str(synth_tree), "--scan", str(scan_out)]) == 0


def test_reference_grade_detects_engine_drift(synth_tree, tmp_path):
    scan_out = tmp_path / "scan.json"
    scan(synth_tree, out=str(scan_out))
    payload = json.loads(scan_out.read_text(encoding="utf-8"))
    for candidate in payload["candidates"]:
        if candidate["type"] == "dist":
            candidate["grade"] = "forbidden"
            break
    tampered = tmp_path / "tampered.json"
    from devdisk.serialize import write_json_atomic

    write_json_atomic(str(tampered), payload)

    module = load_tool("reference_grade.py")
    assert module.main(["--tree", str(synth_tree), "--scan", str(tampered)]) == 1


def test_reference_grade_does_not_import_production_modules():
    text = read_text(os.path.join(TOOLS_ROOT, "reference_grade.py"))
    assert "import devdisk" not in text
    assert "from devdisk" not in text


def test_bench_grade_detects_drift(synth_tree, tmp_path):
    """分级基准必须可证伪：把真值期望改错 → 必须红。"""
    module = load_tool("bench_grade.py")
    assert module.main(["--tree", str(synth_tree)]) == 0

    truth_path = synth_tree / "truth" / "manifest.json"
    backup = truth_path.read_bytes()
    try:
        truth = json.loads(backup.decode("utf-8"))
        for sample in truth["samples"]:
            if sample["expected_grade"] == "safe":
                sample["expected_grade"] = "forbidden"
                break
        truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2, sort_keys=True),
                             encoding="utf-8")
        assert module.main(["--tree", str(synth_tree)]) == 1
    finally:
        truth_path.write_bytes(backup)
