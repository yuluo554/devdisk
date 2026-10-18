# -*- coding: utf-8 -*-
"""隔离批次 manifest：状态机与确定性写盘（模块④）。

契约见 `plan/02-架构与技术选型.md` §6.3；状态机口径见 `data/README.md` §四 第 10 条：

- 移动：`pending → moving → moved`，失败为 `failed:<reason>`；
- 撤销：`moved → restoring → restored`，目标已存在或 digest 不匹配为 `conflict`；
- 批次状态：`moving`（进行中）→ `complete`（全部移动完成）/ `partial`（有失败）；
  撤销中 `undoing` → `undone` / `undo_partial`。

写盘单点复用 `serialize.write_json_atomic`（原子写、键排序、LF、结尾换行），
因此中断不会留下半个 manifest，也不会产生第二套 JSON 口径。
"""

import os

from . import exit_codes
from .serialize import write_json_atomic

SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

# 条目状态（单点定义）。
STATUS_PENDING = "pending"
STATUS_MOVING = "moving"
STATUS_MOVED = "moved"
STATUS_RESTORING = "restoring"
STATUS_RESTORED = "restored"
STATUS_CONFLICT = "conflict"
FAILED_PREFIX = "failed:"

# 批次状态。
STATE_MOVING = "moving"
STATE_COMPLETE = "complete"
STATE_PARTIAL = "partial"
STATE_UNDOING = "undoing"
STATE_UNDONE = "undone"
STATE_UNDO_PARTIAL = "undo_partial"

# 撤销流程会继续处理的状态（`restoring` 表示上一次撤销中断）。
RESTORABLE_STATES = (STATUS_MOVED, STATUS_RESTORING)


class ManifestError(Exception):
    """manifest 缺失、损坏或与批次目录不一致。"""


def manifest_path(batch_dir):
    return os.path.join(batch_dir, MANIFEST_FILENAME)


def new_entry(original_path, trash_path, relative_path, bytes, grade, reason,
              mtime_utc, tree_digest=None):
    """构造一条 manifest 条目（字段与 `plan/02` §6.3 对齐）。"""
    return {
        "original_path": original_path,
        "trash_path": trash_path,
        "relative_path": relative_path,
        "bytes": int(bytes),
        "grade": grade,
        "reason": reason,
        "mtime_utc": mtime_utc,
        "tree_digest": tree_digest,
        "status": STATUS_PENDING,
    }


def new_manifest(batch_id, trash_root, source_root, mode, entries):
    """构造批次 manifest 骨架（`state=moving`，全部条目 `pending`）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "trash_root": trash_root,
        "source_root": source_root,
        "created_from": {"mode": mode, "count": len(entries)},
        "state": STATE_MOVING,
        "entries": list(entries),
    }


def save(batch_dir, payload):
    """原子写 manifest（调用方持有 `batch_dir` 的已创建状态）。"""
    write_json_atomic(manifest_path(batch_dir), payload)
    return manifest_path(batch_dir)


def load(batch_dir):
    """读取并校验 manifest；缺失/损坏/批次不匹配 → `ManifestError`。"""
    import json

    path = manifest_path(batch_dir)
    if not os.path.isfile(path):
        raise ManifestError("manifest 不存在：%s。下一步：确认批次 ID 与 --trash 是否正确。"
                            % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ManifestError("manifest 无法解析：%s（%s）。下一步：检查文件是否被截断或手改。"
                            % (path, exc))

    if not isinstance(payload, dict):
        raise ManifestError("manifest 不是对象：%s。下一步：检查文件内容。" % path)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("manifest schema_version 不支持：%r（期望 %d）"
                            % (payload.get("schema_version"), SCHEMA_VERSION))
    if not isinstance(payload.get("entries"), list):
        raise ManifestError("manifest 缺少 entries 数组：%s" % path)
    if not payload.get("batch_id"):
        raise ManifestError("manifest 缺少 batch_id：%s" % path)
    if os.path.basename(os.path.normpath(batch_dir)) != payload["batch_id"]:
        raise ManifestError("manifest 的 batch_id 与目录名不一致：%r / %r"
                            % (payload["batch_id"], os.path.basename(batch_dir)))
    for entry in payload["entries"]:
        for key in ("original_path", "trash_path", "status"):
            if not entry.get(key):
                raise ManifestError("manifest 条目缺少 %s：%r" % (key, entry))
    return payload


def entry_by_original(payload):
    return dict((entry["original_path"], entry) for entry in payload["entries"])


def is_failed(status):
    return isinstance(status, str) and status.startswith(FAILED_PREFIX)


def failure_reason(status):
    return status[len(FAILED_PREFIX):] if is_failed(status) else status


def mark_entry(payload, entry, status, **fields):
    """就地更新条目状态并附加字段；返回条目本身。"""
    entry["status"] = status
    for key, value in fields.items():
        entry[key] = value
    return entry


def check_manifest_usable(payload):
    """撤销前的整体检查：批次里至少要有可恢复条目。"""
    restorable = [entry for entry in payload["entries"]
                  if entry.get("status") in RESTORABLE_STATES]
    if not restorable:
        raise ManifestError("批次 %s 没有可撤销的条目（当前状态：%s）"
                            % (payload.get("batch_id"),
                               "、".join(sorted(set(entry.get("status", "?")
                                                  for entry in payload["entries"])))))
    return restorable


def exit_code_for_state(payload):
    """批次状态 → 退出码（全部完成 0；有任何冲突/失败 1）。"""
    for entry in payload.get("entries", []):
        status = entry.get("status")
        if status == STATUS_CONFLICT or is_failed(status):
            return exit_codes.REFUSED
    return exit_codes.OK
