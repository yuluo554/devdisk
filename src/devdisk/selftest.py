# -*- coding: utf-8 -*-
"""自检：交付态可用性的一条命令。

`devdisk selftest` 必须可证伪——任一不变量被破坏即返回非 0。
M0 校验工程骨架不变量；M1 追加数据层不变量（类型/忽略规则表可加载、scan 已退役）；
M2 追加分级层不变量（分级规则表可加载、`.git` 必为禁止、无证据必为谨慎）。
"""

import os
import sys

from . import __version__
from . import exit_codes

# 骨架期必须注册的子命令（M1–M5 逐个落地时不得从该集合中消失）。
REQUIRED_COMMANDS = ("scan", "audit", "clean", "undo", "report")

REQUIRED_EXIT_CODES = ("OK", "REFUSED", "USAGE", "NOT_READY", "DEGRADED", "UNSAFE")

# 已退役 NOT_READY、必须真正可用的子命令（每棒追加）。
RETIRED_COMMANDS = ("scan", "audit", "clean", "undo")


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

    problems.extend(_check_retired())
    problems.extend(_check_rules())
    problems.extend(_check_grade_rules())
    problems.extend(_check_audit_invariants())
    problems.extend(_check_quarantine_invariants())

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


def _check_retired():
    """已退役的子命令不得再出现在 NOT_READY 表中。"""
    from . import cli

    problems = []
    for name in RETIRED_COMMANDS:
        if name in cli.NOT_READY_COMMANDS:
            problems.append("子命令 %s 已退役却仍在 NOT_READY_COMMANDS 中" % name)
    return problems


def _check_rules():
    """M1 不变量：类型规则表与忽略规则必须可加载且非空。"""
    from .rules import RulesError, load_rules

    problems = []
    try:
        types, ignore = load_rules()
    except RulesError as exc:
        return ["规则表不可加载：%s" % exc]

    if not types.types:
        problems.append("类型规则表为空")
    if types.match("node_modules") != "node_modules":
        problems.append("类型规则表未能识别 node_modules")
    if types.match(".git") != "git":
        problems.append("类型规则表未能识别 .git（只读样本必需）")
    # ignore 可为空集合；这里只验证对象可用。
    if ignore is None:
        problems.append("忽略规则对象缺失")
    return problems


def _check_grade_rules():
    """M2 不变量：分级规则表可加载，且两条底线成立（`.git` 禁止 / 无证据谨慎）。

    用"合成候选"直接问引擎，不依赖任何真实磁盘内容——自检必须能在干净 clone
    里离线跑通。候选路径刻意取不存在的位置，避免触碰真实 `.git`。
    """
    from . import model
    from .grader import Grader, make_context
    from .rules import RulesError, load_grade_rules

    problems = []
    try:
        table = load_grade_rules()
    except RulesError as exc:
        return ["分级规则表不可加载：%s" % exc]

    if table.grades != list(model.GRADES):
        problems.append("分级规则表 grades 与口径不一致：%r" % (table.grades,))
    if not table.rules:
        problems.append("分级规则表为空")

    grader = Grader(table, make_context(model.ScanResult([])))

    probe = os.path.join(os.path.abspath(os.curdir), "__devdisk_selftest_probe__")
    git_like = model.Candidate(path=os.path.join(probe, ".git"), type="git", bytes=1,
                               mtime_utc=None, project_root=None)
    grade, reason, evidence = grader.grade(git_like)
    if grade != model.GRADE_FORBIDDEN:
        problems.append("分级规则表未把 `git` 类型判为禁止（实际 %s）" % grade)
    if not reason or not evidence:
        problems.append(".git 的禁止结论缺少理由或证据")

    unknown = model.Candidate(path=os.path.join(probe, "unknown"), type="unknown-type",
                              bytes=1, mtime_utc=None, project_root=None)
    grade, reason, evidence = grader.grade(unknown)
    if grade != model.GRADE_CAUTION:
        problems.append("未命中任何规则时未落谨慎（实际 %s）" % grade)
    if not reason or not evidence:
        problems.append("证据不足结论缺少理由或证据")
    return problems


def _check_audit_invariants():
    """M3 不变量：Docker fixture 可解析、`.git` 统计只读、建议永不 applied。

    离线可跑：只读入仓 fixture，不要求 Docker 安装，也不触碰真实 `.git`。
    """
    import os as _os
    import tempfile

    from .audit import STATUS_DOCKER_PARSE, docker as docker_audit, git as git_audit

    problems = []

    fixture_dir = _os.path.join("fixtures", "docker_system_df")
    try:
        from .rules import resolve_data_dir

        fixture_dir = _os.path.join(resolve_data_dir(), fixture_dir)
    except Exception:  # noqa: BLE001 - 规则表问题由 _check_rules 报
        return problems

    standard = _os.path.join(fixture_dir, "v24_standard.txt")
    if not _os.path.isfile(standard):
        problems.append("Docker fixture 缺失：%s" % standard)
    else:
        status, payload = docker_audit.parse_fixture(standard)
        if status != "ok":
            problems.append("Docker fixture 解析失败：%s" % status)
        elif payload["images"]["total_bytes"] <= 0:
            problems.append("Docker fixture 解析出 0 体积（不得静默为空）")
        for suggestion in payload.get("suggestions") or []:
            if suggestion.get("applied") is not False:
                problems.append("Docker 建议不是 applied=false：%r" % (suggestion,))

    malformed = _os.path.join(fixture_dir, "malformed.txt")
    if _os.path.isfile(malformed):
        status, payload = docker_audit.parse_fixture(malformed)
        if status != STATUS_DOCKER_PARSE:
            problems.append("畸形 Docker fixture 未按 error:docker_parse 处理（实际 %s）" % status)
        if not payload.get("raw_excerpt"):
            problems.append("error:docker_parse 未附原文摘录")

    # 合成一个最小 `.git`：pack + loose + objects/info（后者不得计入）。
    with tempfile.TemporaryDirectory(prefix="devdisk-selftest-git-") as temp_dir:
        git_dir = _os.path.join(temp_dir, ".git")
        pack_dir = _os.path.join(git_dir, "objects", "pack")
        loose_dir = _os.path.join(git_dir, "objects", "ab")
        info_dir = _os.path.join(git_dir, "objects", "info")
        for directory in (pack_dir, loose_dir, info_dir):
            _os.makedirs(directory)
        _write_bytes(_os.path.join(pack_dir, "pack-0001.pack"), 1024)
        _write_bytes(_os.path.join(pack_dir, "pack-0001.idx"), 128)
        _write_bytes(_os.path.join(loose_dir, "cdef0123456789"), 256)
        _write_bytes(_os.path.join(info_dir, "packs"), 64)

        before = git_audit.snapshot(git_dir)
        stats = git_audit.measure(git_dir)
        after = git_audit.snapshot(git_dir)
        if before != after:
            problems.append("`.git` 审计改动了元数据（只读纪律被破坏）")
        if stats != {"objects_bytes": 1280, "pack_files": 1, "loose_objects": 1}:
            problems.append("`.git` 统计口径不符：%r" % (stats,))

        entry = git_audit.audit_one(git_dir, root=temp_dir,
                                    loose_threshold=0, bytes_threshold=0)
        if entry["status"] != "ok":
            problems.append("`.git` 审计状态异常：%s" % entry["status"])
        for suggestion in entry.get("suggestions") or []:
            if suggestion.get("applied") is not False:
                problems.append("Git 建议不是 applied=false：%r" % (suggestion,))
            if suggestion.get("command") != "git gc":
                problems.append("Git 建议命令异常：%r" % (suggestion.get("command"),))

    return problems


def _write_bytes(path, size):
    with open(path, "wb") as handle:
        handle.write(b"\0" * size)


def _check_quarantine_invariants():
    """M4 不变量：批次 ID 形态、digest 稳定、往返移动一致、选择必填。

    全部在临时目录内完成，不触碰真实用户目录；离线可跑。
    """
    import datetime
    import tempfile

    from . import digest as digest_mod
    from . import exit_codes as codes
    from . import manifest as manifest_mod
    from . import model as model_mod
    from . import pipeline as pipeline_mod
    from . import quarantine as quarantine_mod

    problems = []

    batch_id = quarantine_mod.make_batch_id(
        now=datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        suffix="a1b2c3")
    if batch_id != "20260101-120000-a1b2c3":
        problems.append("批次 ID 形态不符：%r" % (batch_id,))

    with tempfile.TemporaryDirectory(prefix="devdisk-selftest-clean-") as temp_dir:
        source_root = os.path.join(temp_dir, "root")
        target = os.path.join(source_root, "demo-web", "node_modules")
        os.makedirs(os.path.join(target, "pkg"))
        _write_bytes(os.path.join(target, "pkg", "index.js"), 128)
        _write_bytes(os.path.join(target, "package.json"), 32)

        before = digest_mod.tree_digest(target)
        trash_root = os.path.join(source_root, ".trash")
        os.makedirs(trash_root)
        batch_dir = os.path.join(trash_root, batch_id)
        os.makedirs(batch_dir)
        trash_path = os.path.join(batch_dir, "demo-web", "node_modules")

        entry = manifest_mod.new_entry(
            original_path=model_mod.display_path(target),
            trash_path=model_mod.display_path(trash_path),
            relative_path="demo-web/node_modules",
            bytes=160, grade=model_mod.GRADE_SAFE, reason="selftest",
            mtime_utc=None, tree_digest=before)
        payload = manifest_mod.new_manifest(
            batch_id, model_mod.display_path(trash_root),
            model_mod.display_path(source_root), "select", [entry])
        manifest_mod.save(batch_dir, payload)

        quarantine_mod.move(target, trash_path)
        after_move = digest_mod.tree_digest(trash_path)
        if not digest_mod.digests_match(before, after_move):
            problems.append("隔离区移动后 tree digest 变化")
        if os.path.lexists(target):
            problems.append("隔离区移动后原路径仍存在")

        quarantined = manifest_mod.load(batch_dir)
        manifest_mod.mark_entry(quarantined, quarantined["entries"][0],
                                manifest_mod.STATUS_MOVED, tree_digest=after_move)
        manifest_mod.save(batch_dir, quarantined)

        restored = pipeline_mod._restore_entries(batch_dir, quarantined)
        if restored["entries"][0]["status"] != manifest_mod.STATUS_RESTORED:
            problems.append("撤销未把条目恢复为 restored（实际 %s）"
                            % restored["entries"][0]["status"])
        if not os.path.isdir(target):
            problems.append("撤销后原路径不存在")
        elif not digest_mod.digests_match(digest_mod.tree_digest(target), before):
            problems.append("撤销后 tree digest 与移动前不一致")

        # 选择必填：未给 `--select` 必须拒绝（退出码 1，不得默认全选）。
        try:
            pipeline_mod.run_clean(out=os.path.join(temp_dir, "clean.json"))
        except pipeline_mod.ScanError as exc:
            if exc.exit_code != codes.REFUSED:
                problems.append("未选择时退出码不是 REFUSED（实际 %s）" % exc.exit_code)
        else:
            problems.append("未给选择却执行了 clean（禁止默认全选）")

        # dry-run 不得创建隔离区目录。
        dry_root = os.path.join(temp_dir, "dry")
        os.makedirs(os.path.join(dry_root, "demo-web", "node_modules"))
        _write_bytes(os.path.join(dry_root, "demo-web", "node_modules", "a.js"), 8)
        dry_trash = os.path.join(dry_root, ".trash")
        try:
            pipeline_mod.run_clean(
                select=[os.path.join(dry_root, "demo-web", "node_modules")],
                out=os.path.join(temp_dir, "dry.json"))
        except pipeline_mod.ScanError as exc:
            problems.append("dry-run 意外失败：%s" % exc)
        if os.path.lexists(dry_trash):
            problems.append("dry-run 创建了隔离区目录（应只读）")

    return problems
