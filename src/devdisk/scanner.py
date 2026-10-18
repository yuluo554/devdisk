# -*- coding: utf-8 -*-
"""扫描与分类（模块①）。

实现纪律（`plan/03` 模块①、`data/README.md` §四）：

1. `os.scandir` 递归；**一律不跟随**符号链接 / junction / 其他 reparse point。
2. 物理对象去重：目录按 `(st_dev, st_ino)`；文件仅对 `st_nlink > 1` 的硬链接去重
   （单链接文件不可能重复，跳过集合可省大量内存）。
3. 体积 = 目录内普通文件 `st_size` 之和（`lstat`，不跟随）。
4. mtime = 目录内文件 mtime 的最大值（目录自身 mtime 不作为依据）。
5. 命中类型规则即成为候选，**不再向下递归**（避免嵌套重复计数）。
6. 候选阈值：低于 `min_bytes` 的命中目录记入 `other_*`，不进入候选清单。
7. 错误逐条记 `skipped:<reason>` 并继续，不中断整批。
"""

import os
import stat

from . import model
from .rules import is_project_marker

FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# Windows 长路径阈值：留出余量，避免在 260 边界上踩坑。
WINDOWS_LONG_PATH_THRESHOLD = 240

# 链接链最多跟随这么多跳；超过按环处理（防病态输入）。
MAX_LINK_HOPS = 40


def fs_path(path):
    """返回可安全传给 `os.*` 的路径。

    Windows 上绝对路径长度 ≥ 240 时加 `\\\\?\\` 前缀（仅限绝对路径）。
    其他平台原样返回。
    """
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if len(absolute) >= WINDOWS_LONG_PATH_THRESHOLD:
        return "\\\\?\\" + absolute
    return absolute


def _strip_extended(path):
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _lstat(path):
    return os.lstat(fs_path(path))


def _entry_stat(entry, path):
    """取条目自身的 stat（不跟随链接），并保证 inode 信息可用。

    Windows 上 `DirEntry.stat()` 由 `FindFirstFile` 数据填充，`st_ino`/`st_dev`/
    `st_nlink` 恒为 0（实测 Python 3.8 与 3.12 均如此）。若直接采信，
    硬链接去重与目录 inode 去重会在 Windows 上静默失效。因此 inode 缺失时
    退回 `os.lstat`——多一次系统调用，换正确性。
    """
    st = entry.stat(follow_symlinks=False)
    if st.st_ino == 0 or st.st_dev == 0:
        try:
            return _lstat(path)
        except OSError:
            return st
    return st


def _is_reparse(st):
    if stat.S_ISLNK(st.st_mode):
        return True
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _read_link_target(path):
    """读链接目标并归一为绝对路径；不是链接或读不到返回 None。"""
    try:
        target = os.readlink(fs_path(path))
    except (OSError, ValueError):
        return None
    target = _strip_extended(target)
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(os.path.abspath(path)), target)
    return os.path.normpath(target)


def _is_inside(path, root):
    """`path` 是否等于或位于 `root` 之下（大小写按平台归一）。"""
    try:
        candidate = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([candidate, base]) == base
    except ValueError:  # 不同盘符 / 不同驱动器
        return False


def classify_link(path, root):
    """判定一个链接条目的跳过原因。

    返回 `(reason, target)`：`target` 是解析到的最终目标（可能为 None）。
    判定顺序：环 → 根外 → 目标缺失 → 根内不跟随。
    """
    seen = set()
    current = os.path.abspath(path)
    seen.add(os.path.normcase(current))
    target = None
    for _ in range(MAX_LINK_HOPS):
        target = _read_link_target(current)
        if target is None:
            return model.SKIP_NOT_DIRECTORY, None
        key = os.path.normcase(target)
        if key in seen:
            return model.SKIP_SYMLINK_CYCLE, target
        seen.add(key)
        try:
            st = _lstat(target)
        except OSError:
            st = None
        if st is None or not _is_reparse(st):
            break
        current = target
    else:
        return model.SKIP_SYMLINK_CYCLE, target

    if target is None:
        return model.SKIP_NOT_DIRECTORY, None
    if not _is_inside(target, root):
        return model.SKIP_OUTSIDE_ROOT, target
    if not os.path.lexists(fs_path(target)):
        return model.SKIP_UNREADABLE, target
    return model.SKIP_SYMLINK_NOT_FOLLOWED, target


class Scanner(object):
    """一次扫描的状态机。实例可复用，但每次 `scan()` 会重置状态。"""

    def __init__(self, type_rules, ignore_rules, min_bytes=0):
        self.type_rules = type_rules
        self.ignore_rules = ignore_rules
        self.min_bytes = int(min_bytes)
        self._seen_dirs = set()
        self._seen_files = set()
        self._project_root_cache = {}

    def scan(self, roots):
        result = model.ScanResult([os.path.abspath(root) for root in roots])
        self._seen_dirs = set()
        self._seen_files = set()
        self._project_root_cache = {}

        for root in result.roots:
            result.protected.append({"path": model.display_path(root), "reason": "scan_root"})
            self._walk(root, root, "", result)
        result.finalize()
        return result

    # ------------------------------------------------------------------ 遍历

    def _walk(self, root, directory, rel_dir, result):
        try:
            entries = sorted(os.scandir(fs_path(directory)), key=lambda item: item.name)
        except PermissionError:
            result.add_skip(directory, model.SKIP_PERMISSION_DENIED)
            return
        except OSError as exc:
            result.add_skip(directory, _errno_reason(exc), str(exc))
            return

        for entry in entries:
            name = entry.name
            path = os.path.join(directory, name)
            rel_path = name if not rel_dir else rel_dir + "/" + name

            try:
                st = _entry_stat(entry, path)
            except PermissionError:
                result.add_skip(path, model.SKIP_PERMISSION_DENIED)
                continue
            except OSError as exc:
                result.add_skip(path, _errno_reason(exc), str(exc))
                continue

            if _is_reparse(st):
                reason, target = classify_link(path, root)
                result.add_skip(path, reason,
                                model.display_path(target) if target else None)
                continue

            if not stat.S_ISDIR(st.st_mode):
                continue

            if self.ignore_rules.matches(rel_path, name, True):
                result.ignored_count += 1
                continue

            type_name = self.type_rules.match(name)
            if type_name:
                candidate = self._build_candidate(root, path, type_name, st, result)
                if candidate is None:
                    continue
                if candidate.bytes >= self.min_bytes:
                    result.add_candidate(candidate)
                else:
                    result.other_bytes += candidate.bytes
                    result.other_count += 1
                continue

            inode = _inode_key(st)
            if inode is not None and inode in self._seen_dirs:
                result.add_skip(path, model.SKIP_DUPLICATE_INODE)
                continue
            if inode is not None:
                self._seen_dirs.add(inode)

            self._walk(root, path, rel_path, result)

    # ------------------------------------------------------------------ 度量

    def _build_candidate(self, root, path, type_name, st, result):
        """度量一个候选目录。

        根目录本身读不到 → 返回 None（记 skipped），**不产出 0 字节候选**：
        把"读不到"报成"0 字节"会让后续分级与报告得出错误结论。
        """
        try:
            measurement = self._measure(path, result, own_stat=st)
        except PermissionError:
            result.add_skip(path, model.SKIP_PERMISSION_DENIED)
            return None
        except OSError as exc:
            result.add_skip(path, _errno_reason(exc), str(exc))
            return None

        if measurement["unreadable_roots"]:
            result.add_skip(path, measurement["unreadable_roots"][0])
            return None

        return model.Candidate(
            path=path,
            type=type_name,
            bytes=measurement["bytes"],
            mtime_utc=measurement["mtime_utc"],
            project_root=self._find_project_root(root, path),
            symlink_kind=model.SYMLINK_KIND_NONE,
            file_count=measurement["files"],
            directory_count=measurement["dirs"],
        )

    def _measure(self, directory, result, own_stat=None):
        total = 0
        files = 0
        dirs = 0
        max_mtime_ns = 0
        unreadable_roots = []
        stack = [directory]

        while stack:
            current = stack.pop()
            try:
                entries = sorted(os.scandir(fs_path(current)), key=lambda item: item.name)
            except PermissionError:
                result.add_skip(current, model.SKIP_PERMISSION_DENIED)
                if current == directory:
                    unreadable_roots.append(model.SKIP_PERMISSION_DENIED)
                continue
            except OSError as exc:
                result.add_skip(current, _errno_reason(exc), str(exc))
                if current == directory:
                    unreadable_roots.append(_errno_reason(exc))
                continue

            for entry in entries:
                path = os.path.join(current, entry.name)
                try:
                    st = _entry_stat(entry, path)
                except PermissionError:
                    result.add_skip(path, model.SKIP_PERMISSION_DENIED)
                    continue
                except OSError as exc:
                    result.add_skip(path, _errno_reason(exc), str(exc))
                    continue

                if _is_reparse(st):
                    # 不跟随：链接自身不计入体积，也不进入其目标。
                    continue

                if stat.S_ISDIR(st.st_mode):
                    dirs += 1
                    stack.append(path)
                    continue

                if not stat.S_ISREG(st.st_mode):
                    continue

                inode = _inode_key(st)
                if inode is not None and st.st_nlink > 1:
                    if inode in self._seen_files:
                        continue
                    self._seen_files.add(inode)

                files += 1
                total += int(st.st_size)
                if st.st_mtime_ns > max_mtime_ns:
                    max_mtime_ns = st.st_mtime_ns

        return {
            "bytes": total,
            "files": files,
            "dirs": dirs,
            "unreadable_roots": unreadable_roots,
            "mtime_utc": model.utc_iso_from_ns(max_mtime_ns) if max_mtime_ns
            else model.utc_iso_from_ns(int(own_stat.st_mtime_ns) if own_stat else 0),
        }

    # -------------------------------------------------------------- 项目根

    def _find_project_root(self, root, path):
        """从候选目录向上找最近的标志文件所在目录，不越过扫描根。"""
        result = find_project_root(root, path, cache=self._project_root_cache)
        if result is not None:
            self._project_root_cache[result] = result
        return result


def find_project_root(root, path, cache=None):
    """从 `path` 所在目录向上找最近的标志文件所在目录，不越过 `root`；找不到返回 None。

    分级层（M2）要用同一口径判断"引用方项目根"，因此这里是单一实现点，
    `Scanner._find_project_root` 只是带缓存的薄封装。
    """
    directory = os.path.dirname(os.path.abspath(path))
    root = os.path.abspath(root)
    chain = []
    current = directory
    while True:
        chain.append(current)
        if os.path.normcase(current) == os.path.normcase(root):
            break
        parent = os.path.dirname(current)
        if parent == current or not _is_inside(parent, root):
            break
        current = parent

    for item in chain:
        if cache is not None:
            cached = cache.get(item)
            if cached is not None:
                return cached
        if _has_project_marker(item):
            if cache is not None:
                cache[item] = item
            return item
    return None


def _has_project_marker(directory):
    try:
        with os.scandir(fs_path(directory)) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False) and is_project_marker(entry.name):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _inode_key(st):
    if st.st_ino == 0:
        return None
    return (st.st_dev, st.st_ino)


def _errno_reason(exc):
    """把 OSError 映射为稳定的跳过原因字符串。"""
    import errno as _errno

    mapping = {
        _errno.EACCES: model.SKIP_PERMISSION_DENIED,
        _errno.EPERM: model.SKIP_PERMISSION_DENIED,
        _errno.ENOENT: model.SKIP_UNREADABLE,
        _errno.ELOOP: model.SKIP_SYMLINK_CYCLE,
        _errno.ENOTDIR: model.SKIP_NOT_DIRECTORY,
    }
    err = getattr(exc, "errno", None)
    if err in mapping:
        return mapping[err]
    if os.name == "nt":
        winerror = getattr(exc, "winerror", None)
        if winerror in (32, 33):  # ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION
            return model.SKIP_IN_USE
        if winerror in (5,):
            return model.SKIP_PERMISSION_DENIED
    return model.SKIP_UNREADABLE
