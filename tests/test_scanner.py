# -*- coding: utf-8 -*-
"""扫描层（模块①）测试。

测试纪律：期望值一律来自真值台账或由测试自己构造的**独立事实**，
不手抄引擎输出（`data/README.md` §四 第 15 条 / K12）。
"""

import json
import os
import subprocess
import sys

import pytest

from devdisk import exit_codes
from devdisk import model
from devdisk import pipeline
from devdisk import scanner as scanner_module

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------- 工具函数


def write(path, data=b"x"):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data)


def make_link(link_path, target):
    """真 symlink 优先；Windows 无权限时用 junction。"""
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


# ------------------------------------------------------------------ 基础契约


def test_scan_payload_matches_contract(tmp_path):
    root = tmp_path / "root"
    size = write(str(root / "demo-web" / "node_modules" / "pkg" / "index.js"), b"a" * 500)
    write(str(root / "demo-web" / "package.json"), b"{}")

    out = tmp_path / "scan.json"
    code, payload, lines = scan(root, out=str(out))
    assert code == exit_codes.OK
    assert lines and "候选目录" in lines[0] or any("候选目录" in line for line in lines)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["roots"] == [str(root.resolve())]
    assert data["generated_by"].startswith("devdisk ")
    assert set(data) >= {"schema_version", "roots", "generated_by", "candidates",
                         "skipped", "totals"}

    candidate = data["candidates"][0]
    assert candidate["type"] == "node_modules"
    assert candidate["bytes"] == size
    assert candidate["project_root"] == str((root / "demo-web").resolve())
    assert candidate["is_symlink"] is False
    assert candidate["grade"] is None  # M1 只出事实
    assert candidate["reason"] is None
    assert candidate["evidence"] == []
    assert data["totals"]["candidate_bytes"] == size
    assert data["totals"]["by_type"] == {"node_modules": size}


def test_scan_is_byte_identical_twice(tmp_path):
    root = tmp_path / "root"
    write(str(root / "demo-api" / ".venv" / "lib" / "a.py"), b"b" * 123)
    write(str(root / "demo-api" / "pyproject.toml"), b"[project]\n")

    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    scan(root, out=str(first))
    scan(root, out=str(second))
    assert first.read_bytes() == second.read_bytes()


def test_scan_output_is_deterministic_across_order(tmp_path):
    """同一棵树，两次扫描的排序必须完全一致（体积降序 + 路径字典序）。"""
    root = tmp_path / "root"
    write(str(root / "a" / "node_modules" / "x"), b"x" * 10)
    write(str(root / "b" / "node_modules" / "y"), b"y" * 10)
    write(str(root / "c" / "node_modules" / "z"), b"z" * 99)

    _, payload, _ = scan(root)
    paths = [candidate["path"] for candidate in payload["candidates"]]
    sizes = [candidate["bytes"] for candidate in payload["candidates"]]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] == 99
    # 同体积按路径字典序
    same_size = [path for path, size in zip(paths, sizes) if size == 10]
    assert same_size == sorted(same_size)


def test_missing_root_raises_usage(tmp_path):
    with pytest.raises(pipeline.ScanError) as info:
        pipeline.run_scan([str(tmp_path / "nope")], out=str(tmp_path / "x.json"), min_bytes=0)
    assert info.value.exit_code == exit_codes.USAGE
    assert "不存在" in str(info.value)


def test_root_that_is_a_file_raises_usage(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(pipeline.ScanError) as info:
        pipeline.run_scan([str(target)], out=str(tmp_path / "x.json"), min_bytes=0)
    assert info.value.exit_code == exit_codes.USAGE
    assert "不是目录" in str(info.value)


def test_duplicate_roots_are_collapsed(tmp_path):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "a"), b"x" * 10)
    _, payload, _ = pipeline.run_scan([str(root), str(root) + os.sep + "."],
                                      out=str(tmp_path / "o.json"), min_bytes=0)
    assert payload["roots"] == [str(root.resolve())]
    assert len(payload["candidates"]) == 1


# -------------------------------------------------------------- 规则与类型


def test_type_rule_order_decides_single_type(tmp_path):
    """同名目录只取规则表顺序里的第一个类型。"""
    from devdisk.rules import load_rules

    types, ignore = load_rules()
    assert types.match("node_modules") == "node_modules"
    assert types.match("cache-0042") == ".cache"
    assert types.match("not-a-cache") is None
    assert types.match(".git") == "git"


def test_ignore_rule_skips_directory(tmp_path):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "a"), b"x" * 10)
    write(str(root / "scratch.tmp" / "b"), b"y" * 10)

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    types = [candidate["type"] for candidate in payload["candidates"]]
    assert types == ["node_modules"]
    assert payload["totals"]["ignored_count"] == 1


def test_min_bytes_moves_small_dirs_to_other(tmp_path):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "big"), b"x" * 100)
    write(str(root / "small" / "node_modules" / "tiny"), b"y" * 10)

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"), min_bytes=50)
    assert len(payload["candidates"]) == 1
    assert payload["totals"]["other_count"] == 1
    assert payload["totals"]["other_bytes"] == 10


def test_nested_candidate_is_not_double_counted(tmp_path):
    """命中类型的目录不再向下展开，避免嵌套重复计数。"""
    root = tmp_path / "root"
    write(str(root / "node_modules" / "inner" / "node_modules" / "x"), b"x" * 100)

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["bytes"] == 100


# ------------------------------------------------------------------ 符号链接


def test_symlink_outside_root_is_skipped(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    write(str(outside / "payload.bin"), b"z" * 100)
    make_link(str(root / "link-outside"), str(outside))

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    reasons = {item["reason"] for item in payload["skipped"]}
    assert "outside_root" in reasons
    # 根外内容不得进入任何候选
    assert all("outside" not in candidate["path"] for candidate in payload["candidates"])


def test_symlink_inside_root_is_not_followed(tmp_path):
    root = tmp_path / "root"
    write(str(root / "real" / "node_modules" / "a"), b"x" * 100)
    write(str(root / "app" / "package.json"), b"{}")
    make_link(str(root / "app" / "node_modules"), str(root / "real" / "node_modules"))

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    candidates = {os.path.relpath(c["path"], str(root)).replace(os.sep, "/"): c
                  for c in payload["candidates"]}
    assert "real/node_modules" in candidates
    assert "app/node_modules" not in candidates
    assert candidates["real/node_modules"]["bytes"] == 100
    assert any(item["reason"] == "symlink_not_followed" for item in payload["skipped"])


def test_symlink_cycle_does_not_hang(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    a = str(root / "loop-a")
    b = str(root / "loop-b")
    make_link(a, b)
    make_link(b, a)

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    reasons = [item["reason"] for item in payload["skipped"]]
    assert reasons.count("symlink_cycle") == 2
    assert payload["candidates"] == []


def test_link_inside_directory_is_not_counted_in_bytes(tmp_path):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "real.bin"), b"x" * 40)
    outside = tmp_path / "outside"
    write(str(outside / "big.bin"), b"y" * 9999)
    make_link(str(root / "node_modules" / "escape"), str(outside))

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert payload["candidates"][0]["bytes"] == 40


# --------------------------------------------------------------------- 去重


def test_hardlink_counted_once(tmp_path):
    root = tmp_path / "root"
    payload_size = 100
    original = root / "node_modules" / "a.bin"
    write(str(original), b"q" * payload_size)
    duplicate = root / "node_modules" / "b.bin"
    try:
        os.link(str(original), str(duplicate))
    except (OSError, NotImplementedError):
        pytest.skip("文件系统不支持硬链接")

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert payload["candidates"][0]["bytes"] == payload_size
    assert payload["candidates"][0]["file_count"] == 1


def test_mtime_is_max_of_files_not_directory(tmp_path):
    root = tmp_path / "root"
    target = root / "node_modules"
    write(str(target / "old.bin"), b"x" * 10)
    write(str(target / "new.bin"), b"y" * 10)
    os.utime(str(target / "old.bin"), (1000000000, 1000000000))
    os.utime(str(target / "new.bin"), (1600000000, 1600000000))
    os.utime(str(target), (1700000000, 1700000000))  # 目录自身更晚，但不应被采用

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert payload["candidates"][0]["mtime_utc"] == model.iso_from_epoch_seconds(1600000000)


# ------------------------------------------------------------------ 权限拒绝


def _deny_scandir(monkeypatch, denied_path):
    """让 os.scandir 对指定目录抛 PermissionError（跨平台可测）。"""
    real_scandir = os.scandir
    denied = os.path.normcase(os.path.abspath(denied_path))

    def guarded(path="."):
        if os.path.normcase(os.path.abspath(str(path))) == denied:
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", guarded)


def test_permission_denied_is_recorded_and_scan_continues(tmp_path, monkeypatch):
    root = tmp_path / "root"
    write(str(root / "demo-api" / "node_modules" / "a"), b"x" * 100)
    write(str(root / "demo-api" / "locked" / "secret.bin"), b"y" * 50)
    write(str(root / "demo-web" / "node_modules" / "b"), b"z" * 200)

    _deny_scandir(monkeypatch, str(root / "demo-api" / "locked"))

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    reasons = [item["reason"] for item in payload["skipped"]]
    assert "permission_denied" in reasons
    # 继续扫描：两侧的候选都必须还在
    types = sorted(candidate["bytes"] for candidate in payload["candidates"])
    assert types == [100, 200]


def test_permission_denied_on_candidate_dir_skips_it(tmp_path, monkeypatch):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "a"), b"x" * 100)
    _deny_scandir(monkeypatch, str(root / "node_modules"))

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert payload["candidates"] == []
    assert any(item["reason"] == "permission_denied" for item in payload["skipped"])


# ------------------------------------------------------------------ 项目根


def test_project_root_stops_at_scan_root(tmp_path):
    root = tmp_path / "root"
    write(str(tmp_path / "package.json"), b"{}")  # 根外标志，不应被认领
    write(str(root / "demo-api" / "node_modules" / "a"), b"x" * 10)

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert payload["candidates"][0]["project_root"] is None


def test_project_root_uses_nearest_marker(tmp_path):
    root = tmp_path / "root"
    write(str(root / "demo-api" / "pyproject.toml"), b"[project]\n")
    write(str(root / "demo-api" / "services" / "worker" / "package.json"), b"{}")
    write(str(root / "demo-api" / "services" / "worker" / "node_modules" / "a"), b"x" * 10)

    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert payload["candidates"][0]["project_root"] == str(
        (root / "demo-api" / "services" / "worker").resolve())


def test_scan_root_is_not_a_candidate(tmp_path):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "a"), b"x" * 10)
    _, payload, _ = scan(root, out=str(tmp_path / "o.json"))
    assert all(candidate["path"] != str(root.resolve())
               for candidate in payload["candidates"])


# ------------------------------------------------------------------ CSV 形态


def test_csv_output_has_fixed_columns(tmp_path):
    root = tmp_path / "root"
    write(str(root / "node_modules" / "a"), b"x" * 10)
    out = tmp_path / "scan.csv"
    code, _, _ = scan(root, out=str(out), output_format="csv")
    assert code == exit_codes.OK

    raw = out.read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    header = text.splitlines()[0]
    assert header == "path,type,bytes,mtime_utc,project_root,is_symlink,grade,reason"


# ------------------------------------------------------------------ 规则表错误


def test_missing_rules_degrades(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(pipeline.ScanError) as info:
        pipeline.run_scan([str(root)], out=str(tmp_path / "o.json"),
                          data_dir=str(tmp_path / "no-data"))
    assert info.value.exit_code == exit_codes.DEGRADED


def test_broken_rules_degrades(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "rules").mkdir(parents=True)
    (data_dir / "rules" / "types.json").write_text("{not json", encoding="utf-8")
    (data_dir / "rules" / "ignore.txt").write_text("", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(pipeline.ScanError) as info:
        pipeline.run_scan([str(root)], out=str(tmp_path / "o.json"),
                          data_dir=str(data_dir))
    assert info.value.exit_code == exit_codes.DEGRADED


def test_rules_error_maps_to_cli_degraded(tmp_path):
    """CLI 层：规则表坏掉必须映射为退出码 4，而不是静默用内置默认。"""
    from devdisk import cli

    data_dir = tmp_path / "data"
    (data_dir / "rules").mkdir(parents=True)
    (data_dir / "rules" / "types.json").write_text("{not json", encoding="utf-8")
    (data_dir / "rules" / "ignore.txt").write_text("", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()

    import io
    from contextlib import redirect_stderr, redirect_stdout

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
    assert "规则" in err.getvalue()


# ------------------------------------------------------------------ 长路径前缀


def test_fs_path_adds_prefix_on_windows_long_paths(monkeypatch):
    long_path = os.path.join("C:" + os.sep, "x" * 250)
    if os.name == "nt":
        assert scanner_module.fs_path(long_path).startswith("\\\\?\\")
    else:
        assert scanner_module.fs_path(long_path) == long_path


# ------------------------------------------------------- 真值对账（端到端）


@pytest.fixture(scope="module")
def synth_tree(tmp_path_factory):
    """按仓库生成器的默认 seed 生成一棵树（模块级复用，避免重复 IO）。"""
    import importlib.util

    path = os.path.join(REPO_ROOT, "tools", "gen_synth_tree.py")
    spec = importlib.util.spec_from_file_location("gen_synth_tree", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tree = tmp_path_factory.mktemp("synth")
    module.build_tree(str(tree), module.DEFAULT_SEED)
    return tree


def test_synth_tree_matches_truth(synth_tree):
    truth = json.loads((synth_tree / "truth" / "manifest.json").read_text(encoding="utf-8"))
    out = synth_tree.parent / "scan.json"
    code, payload, _ = pipeline.run_scan([str(synth_tree)], out=str(out), min_bytes=0)
    assert code == exit_codes.OK

    actual = {os.path.relpath(c["path"], str(synth_tree)).replace(os.sep, "/"): c
              for c in payload["candidates"]}
    for sample in truth["samples"]:
        if sample["rel_path"] == "":
            continue
        candidate = actual.get(sample["rel_path"])
        assert candidate is not None, sample["rel_path"]
        assert candidate["type"] == sample["type"], sample["rel_path"]
        assert candidate["bytes"] == sample["expected_bytes"], sample["rel_path"]
        assert candidate["mtime_utc"] == sample["expected_mtime_utc"], sample["rel_path"]


def test_synth_scale_sample_is_complete(synth_tree):
    truth = json.loads((synth_tree / "truth" / "manifest.json").read_text(encoding="utf-8"))
    _, payload, _ = pipeline.run_scan([str(synth_tree)],
                                      out=str(synth_tree.parent / "scan-scale.json"),
                                      min_bytes=0)
    for scale in truth["scale_samples"]:
        matched = [c for c in payload["candidates"]
                   if os.path.relpath(c["path"], str(synth_tree)).replace(os.sep, "/")
                   .startswith(scale["prefix"])]
        assert len(matched) == scale["count"]
        assert sum(c["bytes"] for c in matched) == scale["expected_bytes_total"]


def test_synth_skips_match_expectations(synth_tree):
    truth = json.loads((synth_tree / "truth" / "manifest.json").read_text(encoding="utf-8"))
    _, payload, _ = pipeline.run_scan([str(synth_tree)],
                                      out=str(synth_tree.parent / "scan-skips.json"),
                                      min_bytes=0)
    actual = {}
    for item in payload["skipped"]:
        rel = os.path.relpath(item["path"], str(synth_tree)).replace(os.sep, "/")
        actual.setdefault(rel, set()).add(item["reason"])

    for expected in truth["skipped_expect"]:
        if expected.get("platform") == "posix" and os.name == "nt":
            continue
        assert expected["reason"] in actual.get(expected["rel_path"], set()), expected


def test_synth_scan_is_byte_identical(synth_tree):
    first = synth_tree.parent / "scan-1.json"
    second = synth_tree.parent / "scan-2.json"
    pipeline.run_scan([str(synth_tree)], out=str(first), min_bytes=0)
    pipeline.run_scan([str(synth_tree)], out=str(second), min_bytes=0)
    assert first.read_bytes() == second.read_bytes()


# ------------------------------------------------------------------ 生成器


def test_generator_check_passes_for_fresh_tree(synth_tree):
    import importlib.util

    path = os.path.join(REPO_ROOT, "tools", "gen_synth_tree.py")
    spec = importlib.util.spec_from_file_location("gen_synth_tree_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--out", str(synth_tree), "--seed", str(module.DEFAULT_SEED),
                        "--check"]) == 0


def test_generator_check_detects_drift(tmp_path):
    import importlib.util

    path = os.path.join(REPO_ROOT, "tools", "gen_synth_tree.py")
    spec = importlib.util.spec_from_file_location("gen_synth_tree_drift", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tree = tmp_path / "synth"
    module.build_tree(str(tree), module.DEFAULT_SEED)
    target = tree / "demo-web" / "node_modules" / "demo-pkg-a" / "index.js"
    with open(str(target), "ab") as handle:
        handle.write(b"drift")

    assert module.main(["--out", str(tree), "--seed", str(module.DEFAULT_SEED),
                        "--check"]) == 1


def test_generator_truth_has_no_observed_fields(synth_tree):
    truth = json.loads((synth_tree / "truth" / "manifest.json").read_text(encoding="utf-8"))
    text = json.dumps(truth, ensure_ascii=False)
    assert "observed_" not in text


def test_generator_is_seed_sensitive(tmp_path):
    import importlib.util

    path = os.path.join(REPO_ROOT, "tools", "gen_synth_tree.py")
    spec = importlib.util.spec_from_file_location("gen_synth_tree_seed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    left = tmp_path / "a"
    right = tmp_path / "b"
    module.build_tree(str(left), 1)
    module.build_tree(str(right), 2)

    left_file = left / "demo-web" / "node_modules" / "demo-pkg-a" / "index.js"
    right_file = right / "demo-web" / "node_modules" / "demo-pkg-a" / "index.js"
    assert left_file.read_bytes() != right_file.read_bytes()
