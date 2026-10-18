# -*- coding: utf-8 -*-
"""模块④（隔离区清理与撤销）的验收测试。

门的定义见 `plan/05-里程碑与发布.md` M4 DoD 与 `plan/03` 模块④"验收基准"：

- 默认 dry-run 不写盘（隔离区目录不存在）；
- `--apply` 无确认 → 退出码 1；有 `--yes` → 移动；
- manifest 状态机 `pending → moving → moved`，中断可恢复；
- `undo` 后 tree digest 与移动前一致；
- 目标已存在 → `conflict`，不覆盖；
- 跨设备 → 退出码 5；
- `forbidden` → 退出码 5；
- 无 `--from` / `--select` → 退出码 1；
- `.trash/` 不入 git；
- 撤销时隔离区内容被改动 → `conflict`，不恢复。
"""

import datetime
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout

import pytest

from devdisk import cli
from devdisk import digest
from devdisk import exit_codes
from devdisk import manifest as manifest_mod
from devdisk import model
from devdisk import pipeline
from devdisk import quarantine
from devdisk.serialize import write_json_atomic

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- 夹具


def write_tree(root, files):
    for relative, data in files.items():
        path = os.path.join(str(root), *relative.split("/"))
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "wb") as handle:
            handle.write(data)


@pytest.fixture()
def project(tmp_path):
    """一棵最小工程：demo-web/node_modules + 锁文件；demo-web/dist 作为第二项。"""
    root = tmp_path / "root"
    write_tree(root, {
        "demo-web/package.json": b'{"name": "demo-web"}',
        "demo-web/package-lock.json": b"{}",
        "demo-web/node_modules/pkg/index.js": b"x" * 512,
        "demo-web/node_modules/pkg/package.json": b'{"name": "pkg"}',
        "demo-web/dist/app.js": b"y" * 256,
    })
    return root


def scan_root(root, tmp_path, min_bytes=0):
    out = tmp_path / "scan.json"
    code, payload, _ = pipeline.run_scan([str(root)], out=str(out), min_bytes=min_bytes)
    assert code == exit_codes.OK
    return payload


def clean_args(root, *names):
    return [str(root.joinpath(*name.split("/"))) for name in names]


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- 批次 ID


def test_batch_id_format_is_utc_and_six_hex():
    moment = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    batch_id = quarantine.make_batch_id(now=moment, suffix="a1b2c3")
    assert batch_id == "20260102-030405-a1b2c3"
    assert len(batch_id.rsplit("-", 1)[1]) == 6


def test_batch_id_retries_on_collision(tmp_path):
    trash = tmp_path / ".trash"
    trash.mkdir()
    moment = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    first = quarantine.make_batch_id(now=moment, suffix="aaaaaa")
    (trash / first).mkdir()

    suffixes = iter(["aaaaaa", "bbbbbb", "cccccc", "dddddd", "eeeeee"])
    batch_id, batch_dir = quarantine.create_batch_dir(
        str(trash), now=moment, suffix_factory=lambda: next(suffixes))
    assert batch_id == "20260102-030405-bbbbbb"
    assert os.path.isdir(batch_dir)


def test_batch_id_gives_up_after_five_attempts(tmp_path, monkeypatch):
    trash = str(tmp_path / ".trash")
    os.makedirs(trash)
    monkeypatch.setattr(quarantine, "BATCH_ID_ATTEMPTS", 1)
    fixed = quarantine.make_batch_id(
        now=datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
        suffix="aaaaaa")
    os.makedirs(os.path.join(trash, fixed))
    with pytest.raises(quarantine.QuarantineError):
        quarantine.create_batch_dir(
            trash, now=datetime.datetime(2026, 1, 2, 3, 4, 5,
                                         tzinfo=datetime.timezone.utc),
            suffix_factory=lambda: "aaaaaa")


# ---------------------------------------------------------------- digest


def test_tree_digest_detects_content_change(tmp_path):
    tree = tmp_path / "tree"
    write_tree(tree, {"a.txt": b"alpha", "sub/b.txt": b"beta"})
    before = digest.tree_digest(str(tree))
    assert before.startswith("sha256:")

    (tree / "sub" / "b.txt").write_bytes(b"Beta")
    assert digest.tree_digest(str(tree)) != before


def test_tree_digest_detects_same_size_change(tmp_path):
    tree = tmp_path / "tree"
    write_tree(tree, {"a.txt": b"aaaa"})
    before = digest.tree_digest(str(tree))
    (tree / "a.txt").write_bytes(b"bbbb")
    assert digest.tree_digest(str(tree)) != before


def test_tree_digest_is_stable_across_runs(tmp_path):
    tree = tmp_path / "tree"
    write_tree(tree, {"z.txt": b"1", "a/b.txt": b"2", "a/c.txt": b"3"})
    assert digest.tree_digest(str(tree)) == digest.tree_digest(str(tree))


def test_tree_digest_includes_empty_directories(tmp_path):
    tree = tmp_path / "tree"
    write_tree(tree, {"file.txt": b"x"})
    before = digest.tree_digest(str(tree))
    (tree / "empty").mkdir()
    assert digest.tree_digest(str(tree)) != before


def test_tree_digest_does_not_follow_links(tmp_path):
    tree = tmp_path / "tree"
    target = tmp_path / "outside"
    target.mkdir()
    (target / "secret.txt").write_bytes(b"secret")
    write_tree(tree, {"a.txt": b"a"})
    before = digest.tree_digest(str(tree))
    # 目标内容变化不应影响源树的 digest（链接不跟随）。
    (target / "secret.txt").write_bytes(b"changed")
    assert digest.tree_digest(str(tree)) == before


def test_measure_reports_bytes_and_mtime(tmp_path):
    tree = tmp_path / "tree"
    write_tree(tree, {"a.bin": b"x" * 10})
    stats = digest.measure(str(tree))
    assert stats["bytes"] == 10
    assert stats["files"] == 1
    assert stats["mtime_utc"].endswith("Z")


# ---------------------------------------------------------------- dry-run


def test_clean_without_selection_is_refused(project, tmp_path):
    scan_root(project, tmp_path)
    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"))
    assert excinfo.value.exit_code == exit_codes.REFUSED
    assert "全选" in str(excinfo.value)


def test_clean_without_selection_via_cli_returns_one(project, tmp_path):
    scan_root(project, tmp_path)
    code, _, err = run_cli(["clean", "--from", str(tmp_path / "scan.json")])
    assert code == exit_codes.REFUSED
    assert "全选" in err


def test_dry_run_writes_nothing(project, tmp_path):
    payload = scan_root(project, tmp_path)
    selected = clean_args(project, "demo-web/node_modules")
    code, out, lines = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"), select=selected)
    assert code == exit_codes.OK
    assert out["mode"] == "dry_run"
    assert out["totals"]["count"] == 1
    assert not os.path.lexists(str(project / ".trash"))
    assert project.joinpath("demo-web", "node_modules").is_dir()
    assert any("dry-run" in line for line in lines)


def test_dry_run_cli_writes_no_default_artifact(project, tmp_path, monkeypatch):
    """`--out` 缺省时 dry-run 也不得落盘（默认产物目录可能已存在）。"""
    monkeypatch.chdir(str(tmp_path))
    scan_root(project, tmp_path)
    code, stdout, _ = run_cli(["clean", "--from", str(tmp_path / "scan.json"),
                               "--select", str(project / "demo-web" / "node_modules")])
    assert code == exit_codes.OK
    assert "预演" in stdout
    assert not os.path.lexists(str(tmp_path / "devdisk-out" / "clean.json"))


def test_dry_run_reports_target_paths(project, tmp_path):
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules"))
    entry = payload["entries"][0]
    assert entry["relative_path"] == "demo-web/node_modules"
    assert entry["trash_path"].endswith(os.path.join(
        payload["batch_id"], "demo-web", "node_modules"))


# ---------------------------------------------------------------- apply


def test_apply_without_yes_is_refused(project, tmp_path):
    scan_root(project, tmp_path)
    selected = clean_args(project, "demo-web/node_modules")
    code, _, err = run_cli(["clean", "--from", str(tmp_path / "scan.json"),
                            "--select", selected[0], "--apply"])
    assert code == exit_codes.REFUSED
    assert "确认" in err
    assert project.joinpath("demo-web", "node_modules").is_dir()
    assert not os.path.lexists(str(project / ".trash"))


def test_apply_with_yes_moves_and_keeps_structure(project, tmp_path):
    scan_root(project, tmp_path)
    code, payload, lines = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules"),
        apply=True, yes=True)
    assert code == exit_codes.OK, lines
    assert payload["state"] == manifest_mod.STATE_COMPLETE
    assert payload["entries"][0]["status"] == manifest_mod.STATUS_MOVED

    original = project / "demo-web" / "node_modules"
    assert not os.path.lexists(str(original))
    trash_path = payload["entries"][0]["trash_path"]
    assert os.path.isfile(os.path.join(trash_path, "pkg", "index.js"))

    manifest_path = os.path.join(os.path.dirname(os.path.dirname(trash_path)), "manifest.json")
    stored = manifest_mod.load(os.path.dirname(manifest_path))
    assert stored["state"] == manifest_mod.STATE_COMPLETE
    assert stored["tree_digest"].startswith("sha256:")
    assert stored["entries"][0]["tree_digest"] == payload["entries"][0]["tree_digest"]


def test_apply_multiple_entries(project, tmp_path):
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules", "demo-web/dist"),
        apply=True, yes=True)
    assert code == exit_codes.OK
    assert len(payload["entries"]) == 2
    assert all(entry["status"] == manifest_mod.STATUS_MOVED for entry in payload["entries"])
    assert not os.path.lexists(str(project / "demo-web" / "dist"))


def test_forbidden_candidate_is_unsafe(tmp_path):
    root = tmp_path / "root"
    write_tree(root, {
        "demo-infra/.git/objects/pack/pack-0001.pack": b"p" * 64,
        "demo-infra/.git/config": b"[core]",
    })
    scan_root(root, tmp_path)
    selected = clean_args(root, "demo-infra/.git")
    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"), select=selected)
    assert excinfo.value.exit_code == exit_codes.UNSAFE
    assert ".git" in str(excinfo.value)

    code, _, err = run_cli(["clean", "--from", str(tmp_path / "scan.json"),
                            "--select", selected[0]])
    assert code == exit_codes.UNSAFE
    assert "禁止" in err


def test_cross_device_is_unsafe(project, tmp_path, monkeypatch):
    scan_root(project, tmp_path)
    selected = clean_args(project, "demo-web/node_modules")
    monkeypatch.setattr(quarantine, "device_of", lambda path: 111)
    monkeypatch.setattr(quarantine, "device_of_existing", lambda path: 222)

    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"), select=selected)
    assert excinfo.value.exit_code == exit_codes.UNSAFE
    assert "文件系统" in str(excinfo.value)
    assert not os.path.lexists(str(project / ".trash"))
    assert project.joinpath("demo-web", "node_modules").is_dir()


def test_cross_device_on_apply_does_not_copy(project, tmp_path, monkeypatch):
    """跨设备拒绝时不得复制：源目录原样保留、隔离区为空。"""
    scan_root(project, tmp_path)
    monkeypatch.setattr(quarantine, "device_of", lambda path: 111)
    monkeypatch.setattr(quarantine, "device_of_existing", lambda path: 222)
    with pytest.raises(pipeline.ScanError):
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"),
                           select=clean_args(project, "demo-web/node_modules"),
                           apply=True, yes=True)
    assert project.joinpath("demo-web", "node_modules", "pkg", "index.js").is_file()
    assert not os.path.lexists(str(project / ".trash"))


def test_trash_inside_selection_is_unsafe(project, tmp_path):
    scan_root(project, tmp_path)
    target = project / "demo-web" / "node_modules"
    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(select=[str(target)],
                           trash=str(target / "trash-inside"))
    assert excinfo.value.exit_code == exit_codes.UNSAFE


def test_trash_directory_name_must_be_ignored(project, tmp_path):
    """隔离区在扫描根内但目录名未被忽略 → 拒绝（否则重扫会把它当候选）。"""
    scan_root(project, tmp_path)
    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"),
                           select=clean_args(project, "demo-web/node_modules"),
                           trash=str(project / "quarantine-zone"))
    assert excinfo.value.exit_code == exit_codes.UNSAFE
    assert "忽略" in str(excinfo.value)


def test_nested_selection_is_refused(project, tmp_path):
    scan_root(project, tmp_path)
    parent = project / "demo-web" / "node_modules"
    child = parent / "pkg"
    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(select=[str(parent), str(child)])
    assert "嵌套" in str(excinfo.value)


def test_explicit_directory_outside_scan_is_caution(project, tmp_path):
    """未在候选清单里的显式目录：按谨慎处理，不假装可重建。"""
    extra = project / "demo-web" / "mystery-build"
    write_tree(extra, {"thing.bin": b"z" * 32})
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"), select=[str(extra)])
    assert code == exit_codes.OK
    entry = payload["entries"][0]
    assert entry["grade"] == model.GRADE_CAUTION
    assert "谨慎" in entry["reason"]


def test_keep_paths_are_refused(project, tmp_path):
    """配置里的 keep_paths 与 scan 同一条口径：显式保留的路径不得清理。"""
    scan_root(project, tmp_path)
    config = tmp_path / "devdisk.toml"
    config.write_text('[scan]\nkeep_paths = ["%s"]\n'
                      % str(project / "demo-web" / "node_modules"),
                      encoding="utf-8")
    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"),
                           select=clean_args(project, "demo-web/node_modules"),
                           config_path=str(config))
    assert excinfo.value.exit_code == exit_codes.UNSAFE
    assert "保留" in str(excinfo.value)


# ---------------------------------------------------------------- undo


def test_undo_restores_tree_digest(project, tmp_path):
    scan_root(project, tmp_path)
    original = project / "demo-web" / "node_modules"
    before = digest.tree_digest(str(original))

    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"), select=[str(original)],
        apply=True, yes=True)
    assert code == exit_codes.OK
    batch_id = payload["batch_id"]
    trash_root = payload["trash_root"]

    code, undone, lines = pipeline.run_undo(batch_id, trash=trash_root, yes=True)
    assert code == exit_codes.OK, lines
    assert undone["state"] == manifest_mod.STATE_UNDONE
    assert undone["entries"][0]["status"] == manifest_mod.STATUS_RESTORED
    assert digest.digests_match(digest.tree_digest(str(original)), before)


def test_undo_is_reverse_order(project, tmp_path, monkeypatch):
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules", "demo-web/dist"),
        apply=True, yes=True)
    assert code == exit_codes.OK

    order = []
    real_move = quarantine.move

    def recording_move(source, destination):
        order.append(os.path.basename(source.rstrip(os.sep)))
        return real_move(source, destination)

    monkeypatch.setattr(quarantine, "move", recording_move)
    pipeline.run_undo(payload["batch_id"], trash=payload["trash_root"], yes=True)
    # manifest 顺序是 [node_modules, dist]；撤销必须逆序 → [dist, node_modules]。
    assert order == ["dist", "node_modules"]


def test_undo_conflict_does_not_overwrite(project, tmp_path):
    scan_root(project, tmp_path)
    original = project / "demo-web" / "node_modules"
    before_digest = digest.tree_digest(str(original))

    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"), select=[str(original)],
        apply=True, yes=True)
    assert code == exit_codes.OK

    # 撤销前原路径被占用（内容故意不同）。
    write_tree(original, {"intruder.txt": b"do not overwrite"})

    code, undone, lines = pipeline.run_undo(
        payload["batch_id"], trash=payload["trash_root"], yes=True)
    assert code == exit_codes.REFUSED
    assert undone["entries"][0]["status"] == manifest_mod.STATUS_CONFLICT
    assert (original / "intruder.txt").read_bytes() == b"do not overwrite"
    assert any("冲突" in line for line in lines)
    # 未覆盖 → 隔离区里的原件仍在。
    assert os.path.isdir(undone["entries"][0]["trash_path"])


def test_undo_conflict_stops_by_default(project, tmp_path):
    """默认整体停止：第一条冲突后不再处理后续条目。"""
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules", "demo-web/dist"),
        apply=True, yes=True)
    assert code == exit_codes.OK

    # 逆序第一条是 dist；占住它。
    write_tree(project / "demo-web" / "dist", {"blocker.txt": b"x"})
    code, undone, _ = pipeline.run_undo(
        payload["batch_id"], trash=payload["trash_root"], yes=True)
    assert code == exit_codes.REFUSED
    statuses = [entry["status"] for entry in undone["entries"]]
    assert manifest_mod.STATUS_CONFLICT in statuses
    # node_modules 仍在隔离区（被停止策略挡住）。
    moved_entry = [entry for entry in undone["entries"]
                   if entry["relative_path"] == "demo-web/node_modules"][0]
    assert moved_entry["status"] == manifest_mod.STATUS_MOVED
    assert not os.path.lexists(str(project / "demo-web" / "node_modules"))


def test_undo_continue_on_conflict(project, tmp_path):
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules", "demo-web/dist"),
        apply=True, yes=True)
    assert code == exit_codes.OK
    write_tree(project / "demo-web" / "dist", {"blocker.txt": b"x"})

    code, undone, _ = pipeline.run_undo(
        payload["batch_id"], trash=payload["trash_root"], yes=True,
        stop_on_conflict=False)
    assert code == exit_codes.REFUSED
    by_path = dict((entry["relative_path"], entry) for entry in undone["entries"])
    assert by_path["demo-web/dist"]["status"] == manifest_mod.STATUS_CONFLICT
    assert by_path["demo-web/node_modules"]["status"] == manifest_mod.STATUS_RESTORED


def test_undo_detects_tampered_quarantine(project, tmp_path):
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules"),
        apply=True, yes=True)
    assert code == exit_codes.OK

    trash_target = payload["entries"][0]["trash_path"]
    with open(os.path.join(trash_target, "pkg", "index.js"), "wb") as handle:
        handle.write(b"tampered")

    code, undone, _ = pipeline.run_undo(
        payload["batch_id"], trash=payload["trash_root"], yes=True)
    assert code == exit_codes.REFUSED
    assert undone["entries"][0]["status"] == manifest_mod.STATUS_CONFLICT
    assert "digest" in (undone["entries"][0].get("detail") or "")
    assert not os.path.lexists(str(project / "demo-web" / "node_modules"))


def test_undo_missing_manifest_is_usage_error(tmp_path):
    code, _, err = run_cli(["undo", "20260101-000000-abcdef", "--trash", str(tmp_path)])
    assert code == exit_codes.USAGE
    assert "manifest" in err


def test_undo_corrupt_manifest_is_usage_error(tmp_path):
    batch_dir = tmp_path / "20260101-000000-abcdef"
    batch_dir.mkdir()
    (batch_dir / "manifest.json").write_text("{not json", encoding="utf-8")
    code, _, err = run_cli(["undo", batch_dir.name, "--trash", str(tmp_path)])
    assert code == exit_codes.USAGE
    assert "解析" in err


def test_undo_without_restorable_entries_is_usage_error(project, tmp_path):
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules"), apply=True, yes=True)
    assert code == exit_codes.OK
    pipeline.run_undo(payload["batch_id"], trash=payload["trash_root"], yes=True)

    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_undo(payload["batch_id"], trash=payload["trash_root"], yes=True)
    assert "可撤销" in str(excinfo.value)


# ---------------------------------------------------------------- 中断与恢复


def test_interrupted_move_can_resume(project, tmp_path, monkeypatch):
    """在第二条移动前模拟进程中断；manifest 留住 pending/moving，可续做。"""
    scan_root(project, tmp_path)
    selected = clean_args(project, "demo-web/node_modules", "demo-web/dist")

    calls = {"count": 0}
    real_move = quarantine.move

    def flaky_move(source, destination):
        calls["count"] += 1
        if calls["count"] == 2:
            raise KeyboardInterrupt("模拟 Ctrl-C")
        return real_move(source, destination)

    monkeypatch.setattr(quarantine, "move", flaky_move)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"), select=selected,
                           apply=True, yes=True)
    monkeypatch.setattr(quarantine, "move", real_move)

    trash_root = str(project / ".trash")
    batch_id = os.listdir(trash_root)[0]
    stored = manifest_mod.load(os.path.join(trash_root, batch_id))
    statuses = [entry["status"] for entry in stored["entries"]]
    assert manifest_mod.STATUS_MOVED in statuses
    assert statuses.count(manifest_mod.STATUS_MOVED) == 1
    assert manifest_mod.STATUS_MOVED not in statuses[1:]

    code, resumed, lines = pipeline.run_clean(
        resume_batch=batch_id, trash=trash_root, apply=True, yes=True)
    assert code == exit_codes.OK, lines
    assert resumed["state"] == manifest_mod.STATE_COMPLETE
    assert all(entry["status"] == manifest_mod.STATUS_MOVED
               for entry in resumed["entries"])
    assert not os.path.lexists(str(project / "demo-web" / "node_modules"))
    assert not os.path.lexists(str(project / "demo-web" / "dist"))


def test_interrupted_move_is_reported_by_resume_preview(project, tmp_path, monkeypatch):
    scan_root(project, tmp_path)
    selected = clean_args(project, "demo-web/node_modules", "demo-web/dist")
    real_move = quarantine.move
    calls = {"count": 0}

    def flaky_move(source, destination):
        calls["count"] += 1
        if calls["count"] == 2:
            raise KeyboardInterrupt("模拟中断")
        return real_move(source, destination)

    monkeypatch.setattr(quarantine, "move", flaky_move)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"), select=selected,
                           apply=True, yes=True)
    monkeypatch.setattr(quarantine, "move", real_move)

    trash_root = str(project / ".trash")
    batch_id = os.listdir(trash_root)[0]
    code, preview, lines = pipeline.run_clean(resume_batch=batch_id, trash=trash_root)
    assert code == exit_codes.OK
    assert preview["mode"] == "dry_run"
    assert any("预演" in line for line in lines)
    # 预演不继续移动：第二条仍在原位，隔离区里只有第一条。
    assert os.path.isdir(str(project / "demo-web" / "dist"))
    assert not [name for name in os.listdir(os.path.join(trash_root, batch_id))
                if name == "dist"]


def test_move_between_save_and_move_is_recovered(project, tmp_path, monkeypatch):
    """模拟"已移动但状态还没写成 moved"（硬中断）：续做时按 trash_path 补记。"""
    scan_root(project, tmp_path)
    selected = clean_args(project, "demo-web/node_modules")

    real_move = quarantine.move

    def move_then_die(source, destination):
        real_move(source, destination)
        raise KeyboardInterrupt("模拟写入 moved 之前断电")

    monkeypatch.setattr(quarantine, "move", move_then_die)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_clean(from_scan=str(tmp_path / "scan.json"), select=selected,
                           apply=True, yes=True)
    monkeypatch.setattr(quarantine, "move", real_move)

    trash_root = str(project / ".trash")
    batch_id = os.listdir(trash_root)[0]
    stored = manifest_mod.load(os.path.join(trash_root, batch_id))
    assert stored["entries"][0]["status"] == manifest_mod.STATUS_MOVING

    code, resumed, lines = pipeline.run_clean(
        resume_batch=batch_id, trash=trash_root, apply=True, yes=True)
    assert code == exit_codes.OK, lines
    assert resumed["entries"][0]["status"] == manifest_mod.STATUS_MOVED
    assert resumed["state"] == manifest_mod.STATE_COMPLETE


def test_resume_rejects_select(project, tmp_path):
    with pytest.raises(pipeline.ScanError) as excinfo:
        pipeline.run_clean(resume_batch="20260101-000000-abcdef",
                           select=[str(project)])
    assert "不能同时" in str(excinfo.value)


# ---------------------------------------------------------------- 契约与守门


def test_manifest_contract_fields(project, tmp_path):
    """`plan/02` §6.3 的字段逐项存在（含本轮新增的 `state` 与 `relative_path`）。"""
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules"), apply=True, yes=True)
    assert code == exit_codes.OK

    batch_dir = os.path.join(payload["trash_root"], payload["batch_id"])
    with open(os.path.join(batch_dir, "manifest.json"), "r", encoding="utf-8") as handle:
        stored = json.load(handle)

    for key in ("schema_version", "batch_id", "trash_root", "source_root",
                "created_from", "entries"):
        assert key in stored, key
    assert stored["schema_version"] == 1
    assert stored["created_from"] == {"mode": "select", "count": 1}
    for key in ("original_path", "trash_path", "relative_path", "bytes", "grade",
                "reason", "mtime_utc", "tree_digest", "status"):
        assert key in stored["entries"][0], key
    assert stored["entries"][0]["status"] == "moved"


def test_manifest_write_is_deterministic(project, tmp_path):
    """同一 manifest 连续写两次逐字节一致（确定性写盘单点）。"""
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules"), apply=True, yes=True)
    assert code == exit_codes.OK
    batch_dir = os.path.join(payload["trash_root"], payload["batch_id"])
    first = open(os.path.join(batch_dir, "manifest.json"), "rb").read()
    stored = manifest_mod.load(batch_dir)
    manifest_mod.save(batch_dir, stored)
    second = open(os.path.join(batch_dir, "manifest.json"), "rb").read()
    assert first == second
    assert b"\r\n" not in first


def test_clean_and_undo_are_retired_from_not_ready():
    assert "clean" not in cli.NOT_READY_COMMANDS
    assert "undo" not in cli.NOT_READY_COMMANDS
    from devdisk.selftest import RETIRED_COMMANDS

    assert "clean" in RETIRED_COMMANDS
    assert "undo" in RETIRED_COMMANDS


def test_cli_has_no_select_all_flag():
    parser = cli.build_parser()
    forbidden = {"all", "everything", "yes-all", "clean-all", "clean-everything"}
    for action in parser._actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for sub in choices.values():
            for sub_action in sub._actions:  # noqa: SLF001
                for bad in forbidden:
                    assert "--%s" % bad not in set(sub_action.option_strings)


def test_source_has_no_rmtree_or_remove_outside_serialize():
    """静态守门：隔离区模块不得出现删除调用。"""
    import ast

    for name in ("quarantine.py", "manifest.py", "digest.py", "pipeline.py"):
        path = os.path.join(REPO_ROOT, "src", "devdisk", name)
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                attr = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert attr not in ("rmtree", "rmdir"), (name, attr)
                assert attr not in ("remove", "unlink"), (name, attr)


def test_trash_is_gitignored():
    with open(os.path.join(REPO_ROOT, ".gitignore"), "r", encoding="utf-8") as handle:
        text = handle.read()
    assert ".trash/" in text
    # 运行时产物目录（批次摘要）同样不得入仓。
    assert "devdisk-out/" in text


def test_rescan_after_clean_does_not_see_trash(project, tmp_path):
    """清理后重扫：隔离区不得被当成候选（否则形成"清理→又发现"的假循环）。"""
    scan_root(project, tmp_path)
    code, payload, _ = pipeline.run_clean(
        from_scan=str(tmp_path / "scan.json"),
        select=clean_args(project, "demo-web/node_modules"), apply=True, yes=True)
    assert code == exit_codes.OK

    code, second, _ = pipeline.run_scan([str(project)],
                                        out=str(tmp_path / "scan2.json"), min_bytes=0)
    assert code == exit_codes.OK
    paths = [item["path"] for item in second["candidates"]]
    assert not any(".trash" in path.replace("\\", "/").split("/") for path in paths)
    assert second["totals"]["candidate_count"] == 1  # 只剩 dist


def test_clean_out_artifact_is_written_atomically(project, tmp_path):
    scan_root(project, tmp_path)
    out = tmp_path / "nested" / "clean.json"
    pipeline.run_clean(from_scan=str(tmp_path / "scan.json"),
                       select=clean_args(project, "demo-web/node_modules"),
                       out=str(out))
    raw = out.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    assert not [name for name in os.listdir(str(out.parent)) if name.endswith(".tmp")]
