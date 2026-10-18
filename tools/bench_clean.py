#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""隔离区往返基准：移动 → 撤销 → digest 一致 + 中断恢复（M4 的门）。

用法：
    py -X utf8 tools/bench_clean.py --tree devdisk-out/synth

门的定义（`plan/05` M4 DoD / `plan/03` 模块④ 验收基准）：

- **往返门**：对合成树的真实候选执行 `clean --apply --yes` → `undo`，撤销后每个
  条目的 tree digest 与移动前**逐字节一致**；
- **中断恢复门**：在第 2 条移动时注入中断，manifest 必须留住第一条的 `moved`
  与其余条目的 `pending`；`--resume` 能补齐到 `complete`；
- **不写盘门**：dry-run 不创建隔离区；
- **选择必填门**：不给选择必须退出码 1；
- **隔离区不可见门**：清理后用同一扫描器重扫，`.trash/` 不得出现在候选里。

基准在临时目录的**副本**上跑，不动 `devdisk-out/synth` 本体。
退出码：0 全绿；1 任一门不达标；2 输入缺失或副本准备失败。
"""

import argparse
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from devdisk import digest  # noqa: E402
from devdisk import exit_codes  # noqa: E402
from devdisk import manifest as manifest_mod  # noqa: E402
from devdisk import model  # noqa: E402
from devdisk import pipeline  # noqa: E402
from devdisk import quarantine  # noqa: E402


def copy_tree(source, destination):
    """在临时目录里**重新生成**一份合成树。

    不直接 `copytree`：合成树含 junction / 环形链接，Windows 上复制会失败
    （`WinError 1921`）。生成器是确定性的，用同一 seed 重新生成得到等价副本。
    """
    import subprocess

    generator = os.path.join(REPO_ROOT, "tools", "gen_synth_tree.py")
    seed = _seed_of(source)
    result = subprocess.run(
        [sys.executable, generator, "--out", destination, "--seed", str(seed)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise IOError("重新生成合成树失败（退出码 %s）：%s"
                      % (result.returncode, result.stderr.strip()))


def _seed_of(tree):
    """从真值台账读 seed；读不到时退回默认 seed（与 README 一致）。"""
    path = os.path.join(tree, "truth", "manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return int(json.load(handle)["seed"])
    except (OSError, ValueError, KeyError):
        return 20261018


def check_selection_required(tmp_dir):
    problems = []
    try:
        pipeline.run_clean(out=os.path.join(tmp_dir, "never.json"))
    except pipeline.ScanError as exc:
        if exc.exit_code != exit_codes.REFUSED:
            problems.append("未选择时退出码期望 %d / 实际 %s"
                            % (exit_codes.REFUSED, exc.exit_code))
        if "全选" not in str(exc):
            problems.append("未选择时未提示\"不提供全选\"")
    else:
        problems.append("未给选择却执行了 clean")
    if os.path.lexists(os.path.join(tmp_dir, "never.json")):
        problems.append("拒绝路径也写了产物（不应写盘）")
    return problems


def pick_candidates(scan_payload, limit=3):
    """只挑 safe/caution 的目录型候选（`.git` 是 forbidden，不能参与）。"""
    picked = []
    for candidate in scan_payload["candidates"]:
        if candidate.get("grade") in (model.GRADE_SAFE, model.GRADE_CAUTION):
            absolute = candidate["path"]
            if os.path.isdir(absolute) and not os.path.islink(absolute):
                picked.append(candidate)
            if len(picked) >= limit:
                break
    return picked


def check_roundtrip(tmp_dir, copy_root, candidates):
    """清理 → 撤销；逐条比对 digest。"""
    problems = []
    if not candidates:
        return ["没有可用于往返的候选"]

    scan_path = os.path.join(tmp_dir, "scan.json")
    code, payload, _ = pipeline.run_scan([copy_root], out=scan_path, min_bytes=0)
    if code != exit_codes.OK:
        return ["副本扫描失败：退出码 %s" % code]

    scanned = dict((candidate["path"], candidate) for candidate in payload["candidates"])
    selected = []
    for candidate in candidates:
        if candidate["path"] not in scanned:
            problems.append("候选不在副本扫描结果中：%s" % candidate["path"])
            continue
        selected.append(candidate["path"])
    if not selected:
        return problems or ["没有可用于往返的候选"]

    before = dict((path, digest.tree_digest(path)) for path in selected)

    code, clean_payload, lines = pipeline.run_clean(
        from_scan=scan_path, select=selected, apply=True, yes=True,
        out=os.path.join(tmp_dir, "clean.json"))
    if code != exit_codes.OK:
        problems.append("清理失败：退出码 %s（%s）" % (code, "；".join(lines)))
        return problems

    trash_root = clean_payload["trash_root"]
    batch_id = clean_payload["batch_id"]
    for path in selected:
        if os.path.lexists(path):
            problems.append("清理后原路径仍存在：%s" % path)
    if clean_payload["state"] != manifest_mod.STATE_COMPLETE:
        problems.append("批次状态期望 complete / 实际 %s" % clean_payload["state"])
    for entry in clean_payload["entries"]:
        if entry["status"] != manifest_mod.STATUS_MOVED:
            problems.append("条目状态期望 moved / 实际 %s（%s）"
                            % (entry["status"], entry["original_path"]))

    code, undo_payload, lines = pipeline.run_undo(
        batch_id, trash=trash_root, yes=True,
        out=os.path.join(tmp_dir, "undo.json"))
    if code != exit_codes.OK:
        problems.append("撤销失败：退出码 %s（%s）" % (code, "；".join(lines)))
        return problems

    for path in selected:
        if not os.path.lexists(path):
            problems.append("撤销后原路径不存在：%s" % path)
            continue
        after = digest.tree_digest(path)
        if not digest.digests_match(before[path], after):
            problems.append("撤销后 tree digest 不一致：%s" % path)
    for entry in undo_payload["entries"]:
        if entry["status"] != manifest_mod.STATUS_RESTORED:
            problems.append("撤销状态期望 restored / 实际 %s（%s）"
                            % (entry["status"], entry["original_path"]))

    # 重扫不得把 `.trash/` 当候选。
    code, rescan, _ = pipeline.run_scan(
        [copy_root], out=os.path.join(tmp_dir, "rescan.json"), min_bytes=0)
    if code != exit_codes.OK:
        problems.append("重扫失败：退出码 %s" % code)
    else:
        leaked = [item["path"] for item in rescan["candidates"]
                  if ".trash" in item["path"].replace("\\", "/").split("/")]
        if leaked:
            problems.append("重扫把隔离区当成了候选：%s" % "、".join(leaked))
    return problems


def check_interrupted_resume(tmp_dir, copy_root, how_many=3):
    """注入中断（第 2 条移动时 Ctrl-C），断言状态机并完成续做。"""
    problems = []
    code, payload, _ = pipeline.run_scan(
        [copy_root], out=os.path.join(tmp_dir, "scan2.json"), min_bytes=0)
    if code != exit_codes.OK:
        return ["副本扫描失败：退出码 %s" % code]
    candidates = pick_candidates(payload, limit=how_many)
    if len(candidates) < 2:
        return ["可用候选不足 2 个，无法构造中断用例"]
    selected = [candidate["path"] for candidate in candidates]

    trash_root = os.path.join(copy_root, ".trash")
    before_batches = set(os.listdir(trash_root)) if os.path.isdir(trash_root) else set()

    real_move = quarantine.move
    calls = {"count": 0}

    def flaky_move(source, destination):
        calls["count"] += 1
        if calls["count"] == 2:
            raise KeyboardInterrupt("基准注入中断")
        return real_move(source, destination)

    quarantine.move = flaky_move
    try:
        pipeline.run_clean(from_scan=os.path.join(tmp_dir, "scan2.json"),
                           select=selected, apply=True, yes=True)
    except KeyboardInterrupt:
        pass
    else:
        problems.append("注入的中断没有生效")
    finally:
        quarantine.move = real_move

    if not os.path.isdir(trash_root):
        return problems + ["中断后没有隔离区目录"]
    new_batches = sorted(set(os.listdir(trash_root)) - before_batches)
    if not new_batches:
        return problems + ["中断后没有新的批次目录"]
    batch_id = new_batches[0]
    stored = manifest_mod.load(os.path.join(trash_root, batch_id))
    statuses = [entry["status"] for entry in stored["entries"]]
    if manifest_mod.STATUS_MOVED not in statuses:
        problems.append("中断后没有已移动条目（状态：%r）" % (statuses,))
    if not any(status in (manifest_mod.STATUS_PENDING, manifest_mod.STATUS_MOVING)
               for status in statuses):
        problems.append("中断后没有待处理条目（状态：%r）" % (statuses,))

    code, resumed, lines = pipeline.run_clean(
        resume_batch=batch_id, trash=trash_root, apply=True, yes=True,
        out=os.path.join(tmp_dir, "resume.json"))
    if code != exit_codes.OK:
        problems.append("续做失败：退出码 %s（%s）" % (code, "；".join(lines)))
    elif resumed["state"] != manifest_mod.STATE_COMPLETE:
        problems.append("续做后批次状态期望 complete / 实际 %s" % resumed["state"])
    for entry in resumed["entries"]:
        if entry["status"] != manifest_mod.STATUS_MOVED:
            problems.append("续做后条目状态期望 moved / 实际 %s（%s）"
                            % (entry["status"], entry["original_path"]))
    return problems


def check_dry_run_is_read_only(tmp_dir, copy_root):
    """dry-run 不得移动源目录，也不得创建新的批次目录。"""
    problems = []
    code, payload, _ = pipeline.run_scan(
        [copy_root], out=os.path.join(tmp_dir, "scan3.json"), min_bytes=0)
    if code != exit_codes.OK:
        return ["副本扫描失败：退出码 %s" % code]
    candidates = pick_candidates(payload, limit=1)
    if not candidates:
        return ["没有可用于 dry-run 的候选"]

    trash_root = os.path.join(copy_root, ".trash")
    before_batches = set(os.listdir(trash_root)) if os.path.isdir(trash_root) else set()

    code, dry_payload, _ = pipeline.run_clean(
        from_scan=os.path.join(tmp_dir, "scan3.json"),
        select=[candidates[0]["path"]],
        out=os.path.join(tmp_dir, "dry.json"))
    if code != exit_codes.OK:
        problems.append("dry-run 失败：退出码 %s" % code)
    if not os.path.isdir(candidates[0]["path"]):
        problems.append("dry-run 移动了源目录")
    if dry_payload.get("mode") != "dry_run":
        problems.append("dry-run 的 mode 字段不是 dry_run")

    after_batches = set(os.listdir(trash_root)) if os.path.isdir(trash_root) else set()
    created = after_batches - before_batches
    if created:
        problems.append("dry-run 创建了批次目录：%s" % "、".join(sorted(created)))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="隔离区往返基准（clean/undo）")
    parser.add_argument("--tree", default=os.path.join("devdisk-out", "synth"),
                        help="合成树目录")
    parser.add_argument("--json", default=None, help="可选：把明细写入该 JSON")
    args = parser.parse_args(argv)

    tree = os.path.abspath(args.tree)
    if not os.path.isdir(tree):
        sys.stderr.write("合成树不存在：%s。下一步：先运行 tools/gen_synth_tree.py。\n" % tree)
        return 2

    problems = []
    with tempfile.TemporaryDirectory(prefix="devdisk-bench-clean-") as temp_dir:
        copy_root = os.path.join(temp_dir, "synth")
        try:
            copy_tree(tree, copy_root)
        except OSError as exc:
            sys.stderr.write("复制合成树失败：%s\n" % exc)
            return 2

        scan_payload = {"_copy_root": copy_root}
        problems += check_selection_required(temp_dir)
        code, payload, _ = pipeline.run_scan(
            [copy_root], out=os.path.join(temp_dir, "scan.json"), min_bytes=0)
        if code != exit_codes.OK:
            sys.stderr.write("副本扫描失败：退出码 %s\n" % code)
            return 2
        candidates = pick_candidates(payload)
        if not candidates:
            sys.stderr.write("合成树没有可作为清理对象的候选（safe/caution）\n")
            return 2

        problems += check_roundtrip(temp_dir, copy_root, candidates)
        problems += check_dry_run_is_read_only(temp_dir, copy_root)
        problems += check_interrupted_resume(temp_dir, copy_root)

    print("隔离区基准：%s" % tree)
    print("候选样本：%d 个；问题 %d" % (len(candidates), len(problems)))
    for problem in problems:
        print("  - %s" % problem)

    passed = not problems
    if args.json:
        from devdisk.serialize import write_json_atomic

        write_json_atomic(args.json, {
            "tree": tree,
            "candidates": len(candidates),
            "problems": problems,
            "passed": passed,
        })
    print("结论：%s" % ("PASS（往返 digest 一致、中断可恢复、dry-run 只读、选择必填）"
                        if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
