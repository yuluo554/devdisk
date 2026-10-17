# -*- coding: utf-8 -*-
"""扫描层的数据模型。

所有字段都是"事实"，不含分级判定（分级属于 M2）。体积一律字节 int。
本模块只做值对象与序列化投影，不做任何文件系统访问。
"""

import datetime
import os

# 跳过原因枚举（单点定义）。新增原因必须同步 plan/03 模块① 的降级表。
SKIP_OUTSIDE_ROOT = "outside_root"
SKIP_SYMLINK_CYCLE = "symlink_cycle"
SKIP_SYMLINK_NOT_FOLLOWED = "symlink_not_followed"
SKIP_PERMISSION_DENIED = "permission_denied"
SKIP_DUPLICATE_INODE = "duplicate_inode"
SKIP_NOT_DIRECTORY = "not_directory"
SKIP_IGNORED = "ignored"
SKIP_IN_USE = "in_use"
SKIP_UNREADABLE = "unreadable"

SYMLINK_KIND_NONE = "none"
SYMLINK_KIND_SYMLINK = "symlink"
SYMLINK_KIND_JUNCTION = "junction"


def utc_iso_from_ns(mtime_ns):
    """把纳秒时间戳格式化为 UTC ISO-8601（秒精度，固定 Z 结尾）。

    秒精度是刻意的：Windows 与 Linux 对同一秒的亚秒表示不同，
    真值台账与两次扫描的逐字节一致都依赖这个截断。
    """
    seconds = int(mtime_ns) // 1000000000
    moment = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + \
        datetime.timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_from_epoch_seconds(seconds):
    """把 epoch 秒格式化为 UTC ISO-8601（供真值生成器复用同一口径）。"""
    return utc_iso_from_ns(int(seconds) * 1000000000)


def normalize_path(path):
    """统一为绝对、normpath、normcase 的路径。

    `normcase` 让 Windows 的大小写差异不产生两份候选；Linux 上是恒等变换。
    """
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def display_path(path):
    """输出用路径：绝对 + normpath，但保留大小写（用户要看得懂）。"""
    return os.path.normpath(os.path.abspath(path))


class Skip(object):
    """一条跳过记录。"""

    __slots__ = ("path", "reason", "detail")

    def __init__(self, path, reason, detail=None):
        self.path = path
        self.reason = reason
        self.detail = detail

    def to_dict(self):
        payload = {"path": display_path(self.path), "reason": self.reason}
        if self.detail:
            payload["detail"] = self.detail
        return payload


class Candidate(object):
    """一条候选目录（扫描层事实）。

    `grade` / `reason` / `evidence` 在 M1 恒为 None / 空：
    M1 只产出事实，M2 才填入分级结论。契约字段先占位，避免 M2 改契约。
    """

    __slots__ = ("path", "type", "bytes", "mtime_utc", "project_root",
                 "symlink_kind", "file_count", "directory_count",
                 "grade", "reason", "evidence")

    def __init__(self, path, type, bytes, mtime_utc, project_root,
                 symlink_kind=SYMLINK_KIND_NONE, file_count=0, directory_count=0):
        self.path = path
        self.type = type
        self.bytes = int(bytes)
        self.mtime_utc = mtime_utc
        self.project_root = project_root
        self.symlink_kind = symlink_kind
        self.file_count = int(file_count)
        self.directory_count = int(directory_count)
        self.grade = None
        self.reason = None
        self.evidence = []

    @property
    def is_symlink(self):
        """契约字段：候选路径自身是否是指向别处的链接（symlink 或 junction）。

        注意：目录**内部**的符号链接不跟随，因此不影响该字段。
        """
        return self.symlink_kind != SYMLINK_KIND_NONE

    def sort_key(self):
        """固定排序：体积降序，同体积按路径字典序（`data/README.md` §四 第 1 条）。"""
        return (-self.bytes, self.path)

    def to_dict(self):
        return {
            "path": display_path(self.path),
            "type": self.type,
            "bytes": self.bytes,
            "mtime_utc": self.mtime_utc,
            "project_root": display_path(self.project_root) if self.project_root else None,
            "is_symlink": self.is_symlink,
            "symlink_kind": self.symlink_kind,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "grade": self.grade,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


class ScanResult(object):
    """一次扫描的完整结果。"""

    __slots__ = ("roots", "candidates", "skipped", "ignored_count", "other_bytes",
                 "other_count", "errors", "protected")

    def __init__(self, roots):
        self.roots = list(roots)
        self.candidates = []
        self.skipped = []
        self.ignored_count = 0
        self.other_bytes = 0
        self.other_count = 0
        self.errors = []
        # 扫描根本身不进入候选，但要显式记录"为什么不是候选"（M2 分级要用）。
        self.protected = []

    def add_candidate(self, candidate):
        self.candidates.append(candidate)

    def add_skip(self, path, reason, detail=None):
        self.skipped.append(Skip(path, reason, detail))

    def finalize(self):
        """排序 + 计算 totals。必须在写盘前调用一次。"""
        self.candidates.sort(key=lambda item: item.sort_key())
        self.skipped.sort(key=lambda item: (item.path, item.reason))

    def totals(self):
        by_type = {}
        by_grade = {}
        candidate_bytes = 0
        for candidate in self.candidates:
            candidate_bytes += candidate.bytes
            by_type[candidate.type] = by_type.get(candidate.type, 0) + candidate.bytes
            if candidate.grade:
                by_grade[candidate.grade] = by_grade.get(candidate.grade, 0) + candidate.bytes
        return {
            "candidate_bytes": candidate_bytes,
            "candidate_count": len(self.candidates),
            "by_type": by_type,
            "by_grade": by_grade,
            "other_bytes": self.other_bytes,
            "other_count": self.other_count,
            "ignored_count": self.ignored_count,
            "skipped_count": len(self.skipped),
        }
