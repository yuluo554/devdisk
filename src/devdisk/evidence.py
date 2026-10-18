# -*- coding: utf-8 -*-
"""证据提取（模块② 的下半层）。

**隐私红线（`data/README.md` §四 第 8 条 / `plan/02` §九）**：

- 只读**文件名**、`stat` 与白名单元数据文件（`package.json`）的顶层 JSON 键；
- **绝不打开任何其他文件正文**（不读源码、不读配置内容、不读文档）；
- 唯一允许的 `open()` 是 `_read_json_keys()`，它对文件名做白名单校验，
  非白名单文件直接拒绝（守门测试会记录运行期所有 `open` 调用）。

实现口径：

1. 每个候选目录**只遍历一次**（流式聚合，不缓存文件清单），因此大目录不会爆内存；
   遍历顺序固定（每层按名字排序）→ 证据与判定逐字节可复现。
2. 不跟随链接（与扫描层同口径）；链接既不计数也不进入。
3. 读不到的子目录会被记录，而不是静默忽略：分级层据此拒绝给出 `safe`。
4. 证据 `path` 是**相对路径**（位于候选内→相对候选；项目根内→相对项目根；
   候选之外→相对扫描根），避免把用户绝对路径写进理由字符串。
"""

import fnmatch
import json
import os
import stat

from . import model
from .scanner import _entry_stat, _is_reparse, fs_path

# 单条证据源最多回报多少条命中明细（判定只看"是否命中"，报告看前几条）。
MAX_HITS = 3

# 允许打开的文件名白名单（只解析顶层键，绝不读正文内容语义）。
JSON_KEY_FILES = ("package.json",)


class PatternGroupState(object):
    """一个模式组在**单个候选目录**遍历中的聚合结果（流式）。

    四种用途都由这一份聚合支撑：命中清单（本地状态/自定义 venv 配置）、
    命中与非命中的最大 mtime（手工文件晚于构建产物）、
    以及"全部文件都是某扩展名"（纯 .pyc）。
    """

    __slots__ = ("group", "hits", "hit_count", "max_mtime_hit", "max_mtime_other",
                 "file_count", "non_matching", "unreadable")

    def __init__(self, group):
        self.group = group
        self.hits = []              # [(rel_path, mtime_ns)]，最多 MAX_HITS 条
        self.hit_count = 0
        self.max_mtime_hit = None
        self.max_mtime_other = None
        self.file_count = 0
        self.non_matching = 0
        self.unreadable = 0

    def add(self, rel_path, name, mtime_ns):
        self.file_count += 1
        matched = (not self.group.is_excluded(rel_path, name)
                   and self.group.matches_name(name))
        if matched:
            self.hit_count += 1
            if len(self.hits) < MAX_HITS:
                self.hits.append((rel_path, mtime_ns))
            if self.max_mtime_hit is None or mtime_ns > self.max_mtime_hit:
                self.max_mtime_hit = mtime_ns
        else:
            self.non_matching += 1
            if self.max_mtime_other is None or mtime_ns > self.max_mtime_other:
                self.max_mtime_other = mtime_ns


class TreeFacts(object):
    """一次候选目录遍历的事实聚合（与扫描层口径一致，用于证据与理由）。"""

    __slots__ = ("path", "files", "dirs", "unreadable", "unreadable_dirs")

    def __init__(self, path):
        self.path = path
        self.files = 0
        self.dirs = 0
        self.unreadable = 0
        self.unreadable_dirs = []

    def to_evidence_detail(self):
        return "%d 个文件、%d 个子目录" % (self.files, self.dirs)


def _rel(root, path):
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:      # 不同盘符
        return model.display_path(path)
    rel = rel.replace(os.sep, "/")
    return "." if rel == "." else rel


def _iso(mtime_ns):
    return model.utc_iso_from_ns(mtime_ns) if mtime_ns else ""


class CandidateEvidence(object):
    """单个候选的证据采集器：首次需要目录事实时才遍历，之后复用同一份聚合。"""

    def __init__(self, candidate, groups):
        self.candidate = candidate
        self._groups = {}
        for group_id, group in groups.items():
            self._groups[group_id] = PatternGroupState(group)
        self._facts = None
        self._errors = []

    # -------------------------------------------------------------- 目录事实

    def facts(self):
        if self._facts is None:
            self._facts = TreeFacts(self.candidate.path)
            self._walk()
        return self._facts

    def group_state(self, group_id):
        self.facts()      # 保证遍历已完成
        return self._groups[group_id]

    def _walk(self):
        stack = [(self.candidate.path, "")]
        while stack:
            current, rel_dir = stack.pop()
            try:
                entries = sorted(os.scandir(fs_path(current)), key=lambda item: item.name)
            except OSError:
                self._facts.unreadable += 1
                self._facts.unreadable_dirs.append(_rel(self.candidate.path, current))
                continue
            for entry in entries:
                path = os.path.join(current, entry.name)
                rel_path = entry.name if not rel_dir else rel_dir + "/" + entry.name
                try:
                    st = _entry_stat(entry, path)
                except OSError:
                    self._facts.unreadable += 1
                    self._facts.unreadable_dirs.append(rel_path)
                    continue
                if _is_reparse(st):
                    continue                      # 不跟随，也不计数
                if stat.S_ISDIR(st.st_mode):
                    self._facts.dirs += 1
                    stack.append((path, rel_path))
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                self._facts.files += 1
                for state in self._groups.values():
                    state.add(rel_path, entry.name, st.st_mtime_ns)

    @property
    def errors(self):
        return list(self._errors)

    def note_error(self, message):
        if message not in self._errors:
            self._errors.append(message)

    # ------------------------------------------------------------ 证据源求值

    def evaluate(self, check, context):
        """求一条证据源的证据列表（空列表 = 未命中）。"""
        source = check.get("source")
        handler = _SOURCES[source]
        return handler(self, check, context)


# --------------------------------------------------------------------- 证据源
#
# 每个源返回 `[{"kind":…, "path":…, "detail":…}]`；空列表表示这条证据不成立。
# 词表定义在 `rules.GRADE_SOURCES`，两处必须一致（有测试锁住）。


def _evidence(check, path, detail):
    return {"kind": check.get("kind") or check["source"], "path": path, "detail": detail}


def _source_scan_root(state, check, context):
    candidate = state.candidate
    for root in context.roots:
        if os.path.normcase(os.path.abspath(candidate.path)) == \
                os.path.normcase(os.path.abspath(root)):
            return [_evidence(check, ".", "该条目就是本次扫描的扫描根")]
    return []


def _source_type_in(state, check, context):
    if state.candidate.type in (check.get("types") or ()):
        return [_evidence(check, ".", "主类型 %s" % state.candidate.type)]
    return []


def _source_path_in_keep(state, check, context):
    if context.is_kept(state.candidate.path):
        return [_evidence(check, ".", "命中显式保留列表")]
    return []


def _source_escapes_root(state, check, context):
    if not context.roots:
        return []
    if context.is_inside_any_root(state.candidate.path):
        return []
    return [_evidence(check, ".", "解析后位于所有扫描根之外")]


def _source_dir_files_glob(state, check, context):
    group_state = state.group_state(check["group"])
    items = []
    for rel_path, mtime_ns in group_state.hits:
        items.append(_evidence(
            check, rel_path, "%s %s（mtime %s）"
            % (group_state.group.label, rel_path, _iso(mtime_ns))))
    if group_state.hit_count > len(items):
        items.append(_evidence(
            check, items[0]["path"] if items else ".",
            "另有 %d 个同类文件" % (group_state.hit_count - len(items))))
    return items


def _source_manual_file_newer(state, check, context):
    group_state = state.group_state(check["group"])
    if not group_state.hit_count:
        return []
    path, mtime_ns = group_state.hits[0]
    if group_state.non_matching == 0:
        return [_evidence(check, path, "%s %s（mtime %s），目录内只有手工文件、无构建产物"
                                      % (group_state.group.label, path, _iso(mtime_ns)))]
    if group_state.max_mtime_hit > group_state.max_mtime_other:
        return [_evidence(
            check, path, "%s %s（mtime %s）晚于最新产物文件（mtime %s）"
            % (group_state.group.label, path, _iso(group_state.max_mtime_hit),
               _iso(group_state.max_mtime_other)))]
    return []


def _source_candidate_is_link(state, check, context):
    candidate = state.candidate
    if candidate.is_symlink:
        return [_evidence(check, ".",
                          "候选自身是链接（symlink_kind=%s），不跟随、不清理"
                          % candidate.symlink_kind)]
    return []


def _source_link_into_candidate(state, check, context):
    candidate = state.candidate
    items = []
    for link in context.links:
        if link["reason"] != model.SKIP_SYMLINK_NOT_FOLLOWED:
            continue
        target = link.get("link_target")
        if not target or not context.is_inside(target, candidate.path):
            continue
        referrer_root = context.project_root_of(link["path"])
        if _same_path(referrer_root, candidate.project_root):
            continue          # 同一项目内的自引用，不算"多项目共享"
        items.append(_evidence(
            check, context.rel_to_root(link["path"]),
            "被同根内链接 %s 引用；引用方项目根 %s，本目录项目根 %s"
            % (context.rel_to_root(link["path"]),
               context.rel_to_root(referrer_root) if referrer_root else "（无）",
               context.rel_to_root(candidate.project_root)
               if candidate.project_root else "（无）")))
    return items


def _source_extension_only(state, check, context):
    group_state = state.group_state(check["group"])
    if group_state.file_count < 1 or group_state.non_matching:
        return []
    extensions = "/".join(group_state.group.extensions)
    return [_evidence(check, ".", "目录内 %d 个文件，扩展名全部为 %s"
                                    % (group_state.file_count, extensions))]


def _source_dir_structure(state, check, context):
    facts = state.facts()
    if facts.files < 1 or facts.unreadable:
        return []
    return [_evidence(check, ".", "%s：%s"
                      % (state.candidate.type, facts.to_evidence_detail()))]


def _source_project_root_files(state, check, context):
    root = state.candidate.project_root
    if not root:
        return []
    patterns = tuple(check.get("patterns") or ())
    items = []
    try:
        entries = sorted(os.scandir(fs_path(root)), key=lambda item: item.name)
    except OSError:
        state.note_error("项目根不可读取：%s" % _rel(state.candidate.path, root))
        return []
    for entry in entries:
        if len(items) >= MAX_HITS:
            break
        if not any(fnmatch.fnmatchcase(entry.name, pattern) for pattern in patterns):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
        except OSError:
            continue
        items.append(_evidence(check, entry.name,
                               "%s %s" % (check.get("detail") or "项目根文件", entry.name)))
    return items


def _source_project_root_package_json_key(state, check, context):
    root = state.candidate.project_root
    if not root:
        return []
    keys, error = _read_json_keys(os.path.join(root, "package.json"))
    if error:
        state.note_error(error)
        return []
    present = [key for key in (check.get("keys") or ()) if key in keys]
    if not present:
        return []
    return [_evidence(check, "package.json",
                      "%s：%s" % (check.get("detail") or "package.json 顶层键",
                                  "、".join(present)))]


_SOURCES = {
    "scan_root": _source_scan_root,
    "type_in": _source_type_in,
    "path_in_keep": _source_path_in_keep,
    "escapes_root": _source_escapes_root,
    "dir_files_glob": _source_dir_files_glob,
    "manual_file_newer": _source_manual_file_newer,
    "link_into_candidate": _source_link_into_candidate,
    "candidate_is_link": _source_candidate_is_link,
    "extension_only": _source_extension_only,
    "dir_structure": _source_dir_structure,
    "project_root_files": _source_project_root_files,
    "project_root_package_json_key": _source_project_root_package_json_key,
}


def _read_json_keys(path):
    """读取白名单 JSON 文件的**顶层键名**，返回 (keys, error)。

    这是本模块唯一的 `open()`：非白名单文件名一律拒绝，且只 `json.load`
    顶层键，不解释任何字段语义。文件不存在不算错误（只是没有这条证据），
    但"存在却读不了/解析不了"必须记 `evidence_error`（`plan/03` 模块② 降级表）。
    """
    name = os.path.basename(path)
    if name not in JSON_KEY_FILES:
        return set(), "拒绝读取非白名单文件：%s" % name
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return set(), None
    except (OSError, ValueError) as exc:
        return set(), "%s 无法解析：%s" % (name, exc.__class__.__name__)
    if not isinstance(payload, dict):
        return set(), "%s 顶层不是对象" % name
    return set(payload.keys()), None


def _same_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


__all__ = ["CandidateEvidence", "PatternGroupState", "TreeFacts", "MAX_HITS",
           "JSON_KEY_FILES", "_SOURCES"]
