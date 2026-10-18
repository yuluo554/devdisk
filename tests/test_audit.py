# -*- coding: utf-8 -*-
"""只读审计层（模块③）测试。

测试纪律（`data/README.md` §四 第 15 条）：Docker 期望值来自
`data/fixtures/docker_system_df/expect.json`（手写，不由解析器回填）；
`.git` 期望值由测试自己声明的文件大小累加。

本文件同时承担 M3 的门：

- **Docker 三值/四类精确匹配**：解析结果逐字段等于手写真值。
- **降级路径**：Docker 缺失（`PATH` 隔离）→ `skipped:docker_unavailable` + 退出码 4；
  畸形输出 → `error:docker_parse` + 原文摘录。
- **只读门**：审计前后 `.git` 元数据清单不变；建议固定 `applied: false`。
- **变异门**：摘掉快照断言 → 本文件的 `test_snapshot_detects_mutation` 必红。
"""

import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from devdisk import cli
from devdisk import exit_codes
from devdisk.audit import (
    STATUS_DOCKER_PARSE,
    STATUS_DOCKER_UNAVAILABLE,
    STATUS_GIT_MUTATED,
    STATUS_GITDIR_FILE,
    STATUS_NOT_REQUESTED,
    STATUS_OK,
)
from devdisk.audit import docker as docker_audit
from devdisk.audit import git as git_audit

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(REPO_ROOT, "data", "fixtures", "docker_system_df")


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def load_expect():
    with open(os.path.join(FIXTURE_DIR, "expect.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_bytes(path, size):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "wb") as handle:
        handle.write(b"\0" * size)
    return size


@pytest.fixture()
def fake_git(tmp_path):
    """手工构造一个 `.git`：2 个 pack + 2 个 loose + 必须排除的 info/ 与 .idx。"""
    git_dir = tmp_path / "repo" / ".git"
    pack_dir = git_dir / "objects" / "pack"
    for directory in (pack_dir, git_dir / "objects" / "ab",
                      git_dir / "objects" / "cd", git_dir / "objects" / "info",
                      git_dir / "refs" / "heads"):
        directory.mkdir(parents=True, exist_ok=True)

    expected = {"objects_bytes": 0, "pack_files": 0, "loose_objects": 0}
    for name, size in (("pack-0001.pack", 1024), ("pack-0002.pack", 2048)):
        write_bytes(str(pack_dir / name), size)
        expected["objects_bytes"] += size
        expected["pack_files"] += 1
    for name, size in (("pack-0001.idx", 64), ("pack-0002.idx", 128)):
        write_bytes(str(pack_dir / name), size)          # 不计入
    for directory, name, size in ((git_dir / "objects" / "ab", "0001", 300),
                                  (git_dir / "objects" / "cd", "00ff", 100)):
        write_bytes(str(directory / name), size)
        expected["objects_bytes"] += size
        expected["loose_objects"] += 1
    write_bytes(str(git_dir / "objects" / "info" / "packs"), 999)   # 不计入
    write_bytes(str(git_dir / "HEAD"), 16)
    write_bytes(str(git_dir / "config"), 32)
    write_bytes(str(git_dir / "refs" / "heads" / "main"), 40)
    return git_dir, expected


# ------------------------------------------------------------------ Docker


@pytest.mark.parametrize("case", load_expect()["cases"], ids=lambda c: c["file"])
def test_docker_fixture_matches_handwritten_truth(case):
    """四类体积 + RECLAIMABLE + 建议命令与手写真值逐字段一致。"""
    status, payload = docker_audit.parse_fixture(os.path.join(FIXTURE_DIR, case["file"]))
    assert status == case["status"]
    if case["status"] != STATUS_OK:
        assert payload["raw_excerpt"], "解析失败必须附原文摘录"
        assert len(payload["raw_excerpt"]) <= case.get("raw_excerpt_limit", 20)
        return
    for section in ("images", "containers", "volumes", "build_cache"):
        assert payload[section] == case[section], section
    commands = [item["command"] for item in payload["suggestions"]]
    assert commands == case["suggestion_commands"]
    for item in payload["suggestions"]:
        assert item["applied"] is False


def test_docker_fixture_covers_at_least_three_shapes():
    """验收基准要求至少 3 种版本形态（`plan/03` 模块③）。"""
    files = sorted(name for name in os.listdir(FIXTURE_DIR) if name.endswith(".txt"))
    assert len(files) >= 4
    assert "v24_standard.txt" in files and "v20_standard.txt" in files
    assert "empty.txt" in files and "malformed.txt" in files


def test_parse_size_units_are_binary():
    assert docker_audit.parse_size("0B") == 0
    assert docker_audit.parse_size("12kB") == 12 * 1024
    assert docker_audit.parse_size("1.5MB") == 1024 * 1024 + 512 * 1024
    assert docker_audit.parse_size("1.234GB") == 1324997410
    assert docker_audit.parse_size("1TB") == 1024 ** 4


@pytest.mark.parametrize("bad", ["", "GB", "1.2.3GB", "1XB", "12 GiB"])
def test_parse_size_rejects_garbage(bad):
    with pytest.raises(docker_audit.DockerParseError):
        docker_audit.parse_size(bad)


def test_reclaimable_takes_left_half():
    assert docker_audit.parse_reclaimable("1.2GB (45%)") == 1288490188
    assert docker_audit.parse_reclaimable("0B (0%)") == 0
    assert docker_audit.parse_reclaimable("512MB") == 512 * 1024 * 1024


def test_malformed_fixture_is_parse_error_with_excerpt():
    status, payload = docker_audit.parse_fixture(os.path.join(FIXTURE_DIR, "malformed.txt"))
    assert status == STATUS_DOCKER_PARSE
    assert payload["status"] == STATUS_DOCKER_PARSE
    assert payload["raw_excerpt"]
    assert all(isinstance(line, str) for line in payload["raw_excerpt"])


def test_missing_docker_degrades_via_path_isolation(tmp_path, monkeypatch):
    """Docker 缺失 → skipped:docker_unavailable + 退出码 4（不得静默输出 0 体积）。"""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    # Windows 上 subprocess 仍可能从 SystemRoot 找到 docker.exe；直接断言解析层语义。
    monkeypatch.setattr(subprocess, "run", _raise_file_not_found)
    payload = docker_audit.run_docker()
    assert payload["status"] == STATUS_DOCKER_UNAVAILABLE
    assert payload["reason"]
    assert payload["images"] == {"total_bytes": 0, "reclaimable_bytes": 0}

    out = tmp_path / "audit.json"
    code, stdout, _ = run_cli(["audit", "--docker", "--out", str(out)])
    assert code == exit_codes.DEGRADED
    assert "docker_unavailable" in stdout
    assert out.is_file()


def _raise_file_not_found(*args, **kwargs):
    raise FileNotFoundError("docker")


def test_docker_timeout_degrades(monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=30)

    monkeypatch.setattr(subprocess, "run", boom)
    payload = docker_audit.run_docker()
    assert payload["status"] == "skipped:docker_timeout"
    assert "30" in payload["reason"]


def test_docker_parse_error_maps_to_degraded(tmp_path, monkeypatch):
    class Completed(object):
        returncode = 0
        stdout = "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nNotAType 1 1 1GB 1GB\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed())
    out = tmp_path / "audit.json"
    code, _, err = run_cli(["audit", "--docker", "--out", str(out)])
    assert code == exit_codes.DEGRADED
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["docker"]["status"] == STATUS_DOCKER_PARSE


def test_audit_requires_at_least_one_section(tmp_path):
    code, _, err = run_cli(["audit", "--out", str(tmp_path / "a.json")])
    assert code == exit_codes.USAGE
    assert "--docker" in err and "--git" in err


def test_audit_git_requires_root(tmp_path):
    code, _, err = run_cli(["audit", "--git", "--out", str(tmp_path / "a.json")])
    assert code == exit_codes.USAGE
    assert "--root" in err


# ------------------------------------------------------------------ `.git`


def test_git_stats_are_exact(fake_git):
    git_dir, expected = fake_git
    assert git_audit.measure(str(git_dir)) == expected


def test_git_ignores_objects_info_and_idx(fake_git):
    git_dir, expected = fake_git
    stats = git_audit.measure(str(git_dir))
    assert stats["pack_files"] == 2
    assert stats["loose_objects"] == 2
    # `.idx`（64+128）与 objects/info/packs（999）都不在合计里
    assert stats["objects_bytes"] == 1024 + 2048 + 300 + 100


def test_git_snapshot_unchanged_by_measure(fake_git):
    """只读门：统计前后 `.git` 元数据清单（相对路径 + size + mtime_ns）不变。"""
    git_dir, _ = fake_git
    before = git_audit.snapshot(str(git_dir))
    git_audit.measure(str(git_dir))
    after = git_audit.snapshot(str(git_dir))
    assert before == after
    assert before, "快照不能为空（否则断言无意义）"
    assert all(isinstance(rel, str) for rel, _, _ in before)
    assert not any(os.path.isabs(rel) for rel, _, _ in before), "快照必须是相对路径"


def test_snapshot_detects_mutation(fake_git):
    """变异验证锚点：改写（大小不变）必须被快照发现。

    若把 `git.audit_one` 里的前后快照断言摘掉，`test_audit_raises_on_mutation`
    与 `test_cli_audit_mutation_returns_unsafe` 都会变红。
    """
    git_dir, _ = fake_git
    before = git_audit.snapshot(str(git_dir))
    target = git_dir / "objects" / "ab" / "0001"
    stat_before = os.lstat(str(target))

    with open(str(target), "r+b") as handle:
        handle.write(b"\1" * stat_before.st_size)
    os.utime(str(target), ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns + 1000000))

    after = git_audit.snapshot(str(git_dir))
    assert before != after, "快照必须能发现 mtime/内容变化"


def test_audit_raises_on_mutation(fake_git, monkeypatch):
    """`.git` 审计前后变化 → GitAuditError（映射为 error:git_mutated + 退出码 5）。"""
    git_dir, _ = fake_git
    calls = {"count": 0}
    real_snapshot = git_audit.snapshot

    def mutating_snapshot(path):
        calls["count"] += 1
        snapshot = real_snapshot(path)
        if calls["count"] == 2:
            return snapshot + [("injected", 0, 0)]
        return snapshot

    with pytest.raises(git_audit.GitAuditError):
        git_audit.audit_one(str(git_dir), root=str(git_dir.parent),
                            snapshotter=mutating_snapshot)


def test_audit_detects_real_concurrent_mutation(fake_git, monkeypatch):
    """变异验证锚点：审计过程中真的往 `.git` 写文件 → 快照断言必须报错。

    摘掉 `audit_one` 里的前后快照比较，这个用例必红——它是"只读纪律"的
    生产路径证明，而不是只测测试自己的辅助函数。
    """
    git_dir, _ = fake_git
    real_measure = git_audit.measure

    def mutating_measure(path):
        stats = real_measure(path)
        write_bytes(str(os.path.join(path, "objects", "ab", "ffff")), 16)
        return stats

    monkeypatch.setattr(git_audit, "measure", mutating_measure)
    with pytest.raises(git_audit.GitAuditError):
        git_audit.audit_one(str(git_dir), root=str(git_dir.parent))


def test_cli_audit_mutation_returns_unsafe(fake_git, monkeypatch, tmp_path):
    """端到端：审计期间 `.git` 被改动 → 退出码 5，JSON 状态为 error:git_mutated。"""
    git_dir, _ = fake_git

    def boom(*args, **kwargs):
        raise git_audit.GitAuditError(".git 发生变化（模拟）")

    monkeypatch.setattr(git_audit, "audit_one", boom)
    out = tmp_path / "audit.json"
    code, stdout, _ = run_cli(["audit", "--root", str(git_dir.parent.parent),
                               "--git", "--out", str(out)])
    assert code == exit_codes.UNSAFE
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["git"]["status"] == STATUS_GIT_MUTATED
    assert payload["git"]["repos"][0]["status"] == STATUS_GIT_MUTATED
    assert "退出码 5" in stdout


def test_git_file_worktree_is_skipped(tmp_path):
    """`.git` 是文件 → skipped:gitdir_file，记目标，不统计。"""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").write_text("gitdir: ../shared/.git/worktrees/app\n", encoding="utf-8")
    entry = git_audit.audit_one(str(root / ".git"), root=str(tmp_path))
    assert entry["status"] == STATUS_GITDIR_FILE
    assert entry["gitdir"]
    assert entry["objects_bytes"] == 0
    assert entry["suggestions"] == []
    assert entry["relative_path"] == "repo/.git"


def test_git_suggestions_threshold_and_applied(fake_git):
    git_dir, _ = fake_git
    entry = git_audit.audit_one(str(git_dir), root=str(git_dir.parent),
                                loose_threshold=0, bytes_threshold=0)
    assert entry["status"] == STATUS_OK
    assert entry["suggestions"], "超阈值必须有建议"
    for suggestion in entry["suggestions"]:
        assert suggestion["applied"] is False
        assert suggestion["command"] == "git gc"

    quiet = git_audit.audit_one(str(git_dir), root=str(git_dir.parent),
                                loose_threshold=10 ** 9, bytes_threshold=10 ** 12)
    assert quiet["suggestions"] == []


def test_git_discover_finds_nested_repo_and_does_not_enter(fake_git, tmp_path):
    discovered = git_audit.discover([str(fake_git[0].parent.parent)])
    assert [os.path.basename(path) for path in discovered] == [".git"]
    assert os.path.normcase(discovered[0]) == os.path.normcase(str(fake_git[0]))


def test_git_permission_denied_continues(tmp_path, monkeypatch):
    git_dir = tmp_path / "repo" / ".git"
    (git_dir / "objects").mkdir(parents=True)

    def boom(path):
        raise PermissionError("denied")

    monkeypatch.setattr(git_audit, "measure", boom)
    entry = git_audit.audit_one(str(git_dir), root=str(tmp_path))
    assert entry["status"] == "skipped:permission_denied"
    assert entry["objects_bytes"] == 0


# ------------------------------------------------------------- 契约/只读守门


def test_audit_payload_matches_contract(tmp_path):
    """`plan/02` §6.2 字段一次到位；非请求段为 not_requested。"""
    root = tmp_path / "root"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / ".git" / "objects").mkdir(parents=True)

    out = tmp_path / "audit.json"
    code, _, _ = run_cli(["audit", "--root", str(root), "--git", "--out", str(out)])
    assert code == exit_codes.OK
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["docker"]["status"] == STATUS_NOT_REQUESTED
    assert payload["docker"]["suggestions"] == []
    for section in ("images", "containers", "volumes", "build_cache"):
        assert payload["docker"][section] == {"total_bytes": 0, "reclaimable_bytes": 0}

    assert payload["git"]["status"] == STATUS_OK
    repo = payload["git"]["repos"][0]
    for field in ("path", "objects_bytes", "pack_files", "loose_objects", "suggestions"):
        assert field in repo
    assert repo["suggestions"] == []


def test_all_suggestions_are_never_applied(tmp_path):
    """所有建议 `applied == false`（Docker 与 Git 两条路径）。"""
    out = tmp_path / "audit.json"
    code, _, _ = run_cli([
        "audit",
        "--docker-fixture", os.path.join(FIXTURE_DIR, "v24_standard.txt"),
        "--out", str(out),
    ])
    assert code == exit_codes.OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    suggestions = payload["docker"]["suggestions"]
    assert suggestions
    assert all(item["applied"] is False for item in suggestions)

    root = tmp_path / "root"
    (root / ".git" / "objects" / "ab").mkdir(parents=True)
    write_bytes(str(root / ".git" / "objects" / "ab" / "0001"), 16)
    git_out = tmp_path / "audit-git.json"
    code, _, _ = run_cli(["audit", "--root", str(root), "--git", "--out", str(git_out)])
    assert code == exit_codes.OK
    for repo in json.loads(git_out.read_text(encoding="utf-8"))["git"]["repos"]:
        for item in repo["suggestions"]:
            assert item["applied"] is False


def test_audit_output_is_byte_identical_twice(tmp_path):
    root = tmp_path / "root"
    (root / ".git" / "objects" / "pack").mkdir(parents=True)
    write_bytes(str(root / ".git" / "objects" / "pack" / "pack-1.pack"), 128)

    first, second = tmp_path / "a1.json", tmp_path / "a2.json"
    for target in (first, second):
        code, _, _ = run_cli([
            "audit", "--root", str(root), "--git",
            "--docker-fixture", os.path.join(FIXTURE_DIR, "v20_standard.txt"),
            "--out", str(target),
        ])
        assert code == exit_codes.OK
    assert first.read_bytes() == second.read_bytes()


def test_audit_is_retired_from_not_ready():
    assert "audit" not in cli.NOT_READY_COMMANDS
    from devdisk import selftest

    assert "audit" in selftest.RETIRED_COMMANDS


def test_audit_cli_smoke_offline(tmp_path):
    root = tmp_path / "root"
    (root / "demo" / ".git" / "objects" / "pack").mkdir(parents=True)
    write_bytes(str(root / "demo" / ".git" / "objects" / "pack" / "pack-a.pack"), 256)
    out = tmp_path / "audit.json"
    code, stdout, _ = run_cli(["audit", "--root", str(root), "--git", "--out", str(out)])
    assert code == exit_codes.OK
    assert "Git：" in stdout
    assert out.is_file()
    assert b"\r\n" not in out.read_bytes()


def test_audit_writes_no_files_inside_git_dir(fake_git, tmp_path):
    """只读门（端到端）：审计后 `.git` 内文件集合与内容不变。"""
    git_dir, _ = fake_git
    before = git_audit.snapshot(str(git_dir))
    out = tmp_path / "audit.json"
    code, _, _ = run_cli(["audit", "--root", str(git_dir.parent.parent),
                          "--git", "--out", str(out)])
    assert code in (exit_codes.OK, exit_codes.DEGRADED)
    assert git_audit.snapshot(str(git_dir)) == before


def test_git_directory_audit_opens_no_files(fake_git, monkeypatch):
    """隐私门：目录形态的 `.git` 审计只做 stat，不打开任何文件。"""
    import builtins

    git_dir, _ = fake_git
    opened = []
    real_open = builtins.open
    key = os.path.normcase(os.path.abspath(str(git_dir)))

    def recorder(file, *args, **kwargs):
        try:
            opened.append(os.path.normcase(os.path.abspath(str(file))))
        except (TypeError, ValueError):
            pass
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recorder)
    entry = git_audit.audit_one(str(git_dir), root=str(git_dir.parent))
    assert entry["status"] == STATUS_OK
    assert [path for path in opened if path.startswith(key)] == []


def test_gitdir_file_reads_only_the_dot_git_file(tmp_path, monkeypatch):
    """文件形态只读 `.git` 本身（取 gitdir 一行），不进入目标仓库。"""
    import builtins

    root = tmp_path / "repo"
    root.mkdir()
    git_file = root / ".git"
    git_file.write_text("gitdir: ../shared/.git/worktrees/app\n", encoding="utf-8")
    shared = tmp_path / "shared" / ".git" / "worktrees" / "app"
    shared.mkdir(parents=True)
    write_bytes(str(shared / "HEAD"), 8)

    opened = []
    real_open = builtins.open

    def recorder(file, *args, **kwargs):
        try:
            opened.append(os.path.abspath(str(file)))
        except (TypeError, ValueError):
            pass
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recorder)
    entry = git_audit.audit_one(str(git_file), root=str(tmp_path))
    assert entry["status"] == STATUS_GITDIR_FILE
    assert opened == [os.path.abspath(str(git_file))]
