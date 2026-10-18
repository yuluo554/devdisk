# -*- coding: utf-8 -*-
"""隔离区：批次 ID、同文件系统校验、移动与目标路径（模块④）。

口径（`data/README.md` §四 第 9 条、`plan/03` 模块④）：

- 批次 ID：`YYYYMMDD-HHMMSS-<6 位十六进制>`（UTC）；冲突重试最多 5 次；
- 隔离区根默认 `<source_root>/.trash`，可用 `--trash` 覆盖；
- 隔离区必须与源目录**同一文件系统**；跨设备直接拒绝（退出码 5），
  绝不用"复制后删除"绕过；
- 移动用 `shutil.move`，目标为 `trash_root/<batch-id>/<相对 source_root 的路径>`，
  保持相对路径结构。

本模块**不删除**任何东西：只有 `shutil.move` 与目录创建。删除类调用由
静态守门（`tests/test_repo_discipline.py`）禁止。
"""

import datetime
import os
import shutil

from . import config
from . import exit_codes
from . import model

BATCH_ID_ATTEMPTS = 5
BATCH_ID_RANDOM_BYTES = 3  # → 6 位十六进制


class QuarantineError(Exception):
    """批次/路径前置条件不满足；`exit_code` 由编排层直接映射。"""

    def __init__(self, message, exit_code=exit_codes.USAGE):
        Exception.__init__(self, message)
        self.exit_code = exit_code


def utc_batch_timestamp(now=None):
    """返回 UTC `YYYYMMDD-HHMMSS` 前缀（`now` 可注入，便于测试与基准）。"""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")


def random_suffix(length=BATCH_ID_RANDOM_BYTES):
    """6 位小写十六进制随机后缀。"""
    return os.urandom(length).hex()


def make_batch_id(now=None, suffix=None):
    """生成一个批次 ID（不检查冲突；冲突处理见 `create_batch_dir`）。"""
    return "%s-%s" % (utc_batch_timestamp(now),
                      suffix if suffix is not None else random_suffix())


def create_batch_dir(trash_root, now=None, suffix_factory=None, preferred_id=None):
    """在 `trash_root` 下创建唯一批次目录，返回 `(batch_id, batch_dir)`。

    首次尝试 `preferred_id`（dry-run 展示给用户的那个），冲突后重新生成；
    总共最多 5 次（`data/README.md` §四 第 9 条）。
    """
    factory = suffix_factory or random_suffix
    root = os.path.abspath(trash_root)
    attempts = []
    if preferred_id:
        attempts.append(preferred_id)
    future = BATCH_ID_ATTEMPTS - len(attempts)
    for _ in range(max(future, 0)):
        attempts.append(make_batch_id(now=now, suffix=factory()))
    for batch_id in attempts:
        batch_dir = os.path.join(root, batch_id)
        try:
            os.makedirs(batch_dir)
        except FileExistsError:
            continue
        return batch_id, batch_dir
    raise QuarantineError("无法生成唯一批次 ID（已重试 %d 次）：%s。下一步：稍后重试。"
                          % (BATCH_ID_ATTEMPTS, model.display_path(root)),
                          exit_code=exit_codes.REFUSED)


def resolve_trash_root(trash_root, source_root):
    """隔离区根绝对路径；缺省为 `<source_root>/.trash`（**不创建**）。"""
    if trash_root:
        return os.path.abspath(trash_root)
    return os.path.join(os.path.abspath(source_root), config.DEFAULT_TRASH_DIRNAME)


def ensure_trash_root(trash_root):
    """创建隔离区根（只在 `--apply` 路径调用）。"""
    root = os.path.abspath(trash_root)
    if not os.path.isdir(root):
        os.makedirs(root)
    return root


def is_inside(path, root):
    """`path` 是否等于或位于 `root` 之下（按平台归一大小写）。"""
    try:
        candidate = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([candidate, base]) == base
    except ValueError:
        return False


def relative_to_root(path, root):
    """相对路径（POSIX 分隔符）；越界 → 退出码 5。"""
    relative = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        raise QuarantineError("路径越出扫描根：%s（根：%s）"
                              % (model.display_path(path), model.display_path(root)),
                              exit_code=exit_codes.UNSAFE)
    return relative.replace(os.sep, "/")


def target_path(trash_root, batch_id, relative_path):
    """隔离目标绝对路径：`trash_root/<batch-id>/<relative>`。"""
    parts = [part for part in relative_path.split("/") if part not in ("", ".")]
    if not parts or any(part == os.pardir for part in parts):
        raise QuarantineError("非法的相对路径：%r" % (relative_path,),
                              exit_code=exit_codes.UNSAFE)
    return os.path.join(os.path.abspath(trash_root), batch_id, *parts)


def device_of(path):
    """`st_dev`；路径不存在 → `QuarantineError`（退出码 2）。"""
    try:
        return os.stat(path).st_dev
    except OSError as exc:
        raise QuarantineError("无法读取设备号：%s（%s）"
                              % (model.display_path(path), exc))


def device_of_existing(path):
    """`path` 或它最近的已存在祖先的 `st_dev`（dry-run 不创建目录时使用）。"""
    current = os.path.abspath(path)
    while True:
        try:
            return os.stat(current).st_dev
        except OSError:
            parent = os.path.dirname(current)
            if parent == current:
                raise QuarantineError("无法确定隔离区所在文件系统：%s"
                                      % model.display_path(path))
            current = parent


def ensure_same_device(source, trash_root):
    """跨设备直接拒绝：返回 `st_dev`，不一致抛 `QuarantineError`（退出码 5）。"""
    source_device = device_of(source)
    trash_device = device_of_existing(trash_root)
    if source_device != trash_device:
        raise QuarantineError(
            "error:cross_device：隔离区与源路径不在同一文件系统"
            "（source st_dev=%s / trash st_dev=%s）：%s → %s。"
            "下一步：用 `--trash` 指定同一磁盘上的目录；本工具不会用复制+删除绕过跨设备。"
            % (source_device, trash_device, model.display_path(source),
               model.display_path(trash_root)),
            exit_code=exit_codes.UNSAFE)
    return source_device


def ensure_isolated(source, trash_root):
    """隔离区与待移动条目的包含关系检查（两种方向都是安全拒绝）。"""
    if is_inside(trash_root, source):
        raise QuarantineError(
            "隔离区位于待移动条目内部：%s ⊂ %s。下一步：改用 `--trash` 指定条目之外的目录。"
            % (model.display_path(trash_root), model.display_path(source)),
            exit_code=exit_codes.UNSAFE)
    if is_inside(source, trash_root):
        raise QuarantineError(
            "待移动条目位于隔离区内：%s ⊂ %s。下一步：检查 `--select`。"
            % (model.display_path(source), model.display_path(trash_root)),
            exit_code=exit_codes.UNSAFE)


def ensure_not_nested(selected_paths):
    """选择项之间不得互相包含（父/子同时选中会重复移动）。"""
    normalized = [(path, os.path.normcase(os.path.abspath(path)))
                  for path in selected_paths]
    for path, key in normalized:
        for other, other_key in normalized:
            if path == other:
                continue
            if other_key != key and is_inside(other, path):
                raise QuarantineError(
                    "选择项存在嵌套：%s ⊂ %s。下一步：只选择父目录，或去掉父目录只选子项。"
                    % (model.display_path(other), model.display_path(path)))


def move(source, destination):
    """移动一个条目（目录或文件），保持内容原样。

    同一文件系统上 `shutil.move` 是重命名；跨设备已在上游拒绝。
    """
    parent = os.path.dirname(destination)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    shutil.move(source, destination)
    return destination
