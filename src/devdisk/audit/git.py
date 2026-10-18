# -*- coding: utf-8 -*-
"""`.git` 只读审计（模块③，M3）。

统计口径（`data/README.md` §四 第 13 条）：

- `objects/pack/*.pack` → `objects_bytes` 的 pack 部分 + `pack_files` 计数
- `objects/??/*` → 松散对象数与字节和（`??` = 两位十六进制目录）
- **不统计** `objects/info/`，也不统计 `objects/pack` 里的 `.idx`
- `.git` 是文件（worktree/submodule，内容 `gitdir: ...`）→ `skipped:gitdir_file`，
  记目标路径但不跟随统计

只读纪律：本模块**不调用 git**（不论读写）。所有建议固定 `applied: false`。

审计前后对 `.git` 做 `(相对路径, size, mtime_ns)` 快照并断言不变；
不一致 → `error:git_mutated`（退出码 5）。快照只含相对路径，日志可复现且不含本机绝对路径。
"""

import os
import stat

from .. import config
from .. import model
from . import STATUS_GIT_MUTATED, STATUS_GITDIR_FILE, STATUS_OK, STATUS_PERMISSION_DENIED

GIT_DIRNAME = ".git"
OBJECTS_DIRNAME = "objects"
PACK_DIRNAME = "pack"
INFO_DIRNAME = "info"
PACK_SUFFIX = ".pack"
HEX_DIGITS = frozenset("0123456789abcdef")


class GitAuditError(Exception):
    """审计前后的 `.git` 元数据发生变化（只读纪律被破坏）。"""


def is_git_directory(path):
    return os.path.isdir(path)


def is_git_file(path):
    return os.path.isfile(path)


def _is_reparse(st):
    if stat.S_ISLNK(st.st_mode):
        return True
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _lstat(path):
    return os.lstat(path)


def snapshot(git_dir):
    """返回 `.git` 的元数据清单：`[(相对路径, size, mtime_ns), ...]`（已排序）。

    只记元数据，不打开任何文件。相对路径用 `/` 分隔，保证跨平台可复现。
    """
    root = os.path.abspath(git_dir)
    items = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name)
        except OSError:
            # 读不到就记一条哨兵：只要可读性变化，清单也会变化。
            items.append((_rel(root, current), -1, -1))
            continue
        for entry in entries:
            path = os.path.join(current, entry.name)
            rel = _rel(root, path)
            try:
                st = _lstat(path)
            except OSError:
                items.append((rel, -1, -1))
                continue
            if _is_reparse(st):
                items.append((rel, 0, 0))
                continue
            if stat.S_ISDIR(st.st_mode):
                items.append((rel, 0, st.st_mtime_ns))
                stack.append(path)
                continue
            items.append((rel, int(st.st_size), st.st_mtime_ns))
    items.sort()
    return items


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _read_gitdir_file(path):
    """读 `.git` 文件里的 `gitdir: <path>`，返回解析后的绝对路径（读不到返回 None）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(4096)
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.lower().startswith("gitdir:"):
            continue
        target = line.split(":", 1)[1].strip()
        if not target:
            return None
        if not os.path.isabs(target):
            target = os.path.join(os.path.dirname(os.path.abspath(path)), target)
        return os.path.normpath(target)
    return None


def _count_pack(objects_dir):
    """`objects/pack/*.pack` → (字节和, 文件数)。不统计 `.idx`。"""
    pack_dir = os.path.join(objects_dir, PACK_DIRNAME)
    total = 0
    count = 0
    try:
        entries = sorted(os.scandir(pack_dir), key=lambda item: item.name)
    except OSError:
        return 0, 0
    for entry in entries:
        if not entry.name.endswith(PACK_SUFFIX):
            continue
        try:
            st = _lstat(entry.path)
        except OSError:
            continue
        if _is_reparse(st) or not stat.S_ISREG(st.st_mode):
            continue
        total += int(st.st_size)
        count += 1
    return total, count


def _count_loose(objects_dir):
    """`objects/??/*` 的普通文件数与字节和。不进入 `info/` 与 `pack/`。"""
    count = 0
    total = 0
    try:
        entries = sorted(os.scandir(objects_dir), key=lambda item: item.name)
    except OSError:
        return 0, 0
    for entry in entries:
        name = entry.name
        if len(name) != 2 or not set(name.lower()) <= HEX_DIGITS:
            continue
        try:
            st = _lstat(entry.path)
        except OSError:
            continue
        if _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            continue
        try:
            files = sorted(os.scandir(entry.path), key=lambda item: item.name)
        except OSError:
            continue
        for item in files:
            try:
                file_st = _lstat(item.path)
            except OSError:
                continue
            if _is_reparse(file_st) or not stat.S_ISREG(file_st.st_mode):
                continue
            count += 1
            total += int(file_st.st_size)
    return count, total


def measure(git_dir):
    """统计一个 `.git` 目录。返回 `{objects_bytes, pack_files, loose_objects}`。"""
    objects_dir = os.path.join(git_dir, OBJECTS_DIRNAME)
    pack_bytes, pack_files = _count_pack(objects_dir)
    loose_objects, loose_bytes = _count_loose(objects_dir)
    return {
        "objects_bytes": pack_bytes + loose_bytes,
        "pack_files": pack_files,
        "loose_objects": loose_objects,
    }


def build_suggestions(stats, loose_threshold=None, bytes_threshold=None):
    """超阈值才建议 `git gc`；命令永远不执行，固定 `applied: false`。"""
    if loose_threshold is None:
        loose_threshold = config.DEFAULT_GIT_GC_LOOSE_THRESHOLD
    if bytes_threshold is None:
        bytes_threshold = config.DEFAULT_GIT_GC_BYTES_THRESHOLD

    reasons = []
    if stats["loose_objects"] > loose_threshold:
        reasons.append("松散对象 %d 个（阈值 %d）" % (stats["loose_objects"], loose_threshold))
    if stats["objects_bytes"] > bytes_threshold:
        reasons.append("对象体积 %s（阈值 %s）"
                       % (model.format_bytes(stats["objects_bytes"]),
                          model.format_bytes(bytes_threshold)))
    if not reasons:
        return []
    return [{
        "command": "git gc",
        "applied": False,
        "reason": "；".join(reasons) + "。本工具只生成建议，不执行 git gc。",
    }]


def audit_one(git_path, root=None, loose_threshold=None, bytes_threshold=None,
              snapshotter=None):
    """审计单个 `.git` 路径（目录或文件），返回契约里的仓库条目。

    目录形态：统计 + 前后快照断言；文件形态：记 `skipped:gitdir_file`。
    快照不一致抛 `GitAuditError`（由调用方映射为 `error:git_mutated` + 退出码 5）。
    """
    absolute = os.path.abspath(git_path)
    rel = _rel(os.path.abspath(root), absolute) if root else model.display_path(absolute)
    take_snapshot = snapshotter or snapshot

    if is_git_directory(absolute):
        before = take_snapshot(absolute)
        try:
            stats = measure(absolute)
        except PermissionError:
            return {
                "path": model.display_path(absolute),
                "relative_path": rel,
                "status": STATUS_PERMISSION_DENIED,
                "reason": "读取 .git 被拒绝。下一步：检查目录权限，或跳过该系统路径。",
                "objects_bytes": 0,
                "pack_files": 0,
                "loose_objects": 0,
                "suggestions": [],
            }
        after = take_snapshot(absolute)
        if before != after:
            raise GitAuditError(
                ".git 在审计前后发生变化（相对路径/size/mtime_ns 清单不一致）：%s" % rel)
        entry = {
            "path": model.display_path(absolute),
            "relative_path": rel,
            "status": STATUS_OK,
            "suggestions": build_suggestions(stats, loose_threshold, bytes_threshold),
        }
        entry.update(stats)
        return entry

    if is_git_file(absolute):
        target = _read_gitdir_file(absolute)
        target_label = None
        if target:
            target_label = _rel(os.path.abspath(root), target) if root else target
        return {
            "path": model.display_path(absolute),
            "relative_path": rel,
            "status": STATUS_GITDIR_FILE,
            "reason": "`.git` 是文件（worktree/submodule），指向 %s；本工具不跟随统计。"
                      % (target_label or "<无法解析>"),
            "gitdir": target,
            "objects_bytes": 0,
            "pack_files": 0,
            "loose_objects": 0,
            "suggestions": [],
        }

    return {
        "path": model.display_path(absolute),
        "relative_path": rel,
        "status": STATUS_PERMISSION_DENIED,
        "reason": "路径不存在或不可访问。下一步：确认仓库路径。",
        "objects_bytes": 0,
        "pack_files": 0,
        "loose_objects": 0,
        "suggestions": [],
    }


def discover(roots):
    """在扫描根下找 Git 仓库（含 `.git` 目录或文件的目录），返回 `.git` 路径列表。

    只下探不跟随链接；`.git` 命中即记录，不再进入其内部继续找。
    排序固定，保证输出可复现。
    """
    found = []
    seen = set()
    for root in roots:
        absolute_root = os.path.abspath(root)
        stack = [absolute_root]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda item: item.name)
            except OSError:
                continue
            for entry in entries:
                path = os.path.join(current, entry.name)
                try:
                    st = _lstat(path)
                except OSError:
                    continue
                if _is_reparse(st):
                    continue
                if entry.name == GIT_DIRNAME:
                    key = os.path.normcase(path)
                    if key not in seen:
                        seen.add(key)
                        found.append(path)
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    continue
                stack.append(path)
    found.sort()
    return found
