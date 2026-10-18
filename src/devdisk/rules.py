# -*- coding: utf-8 -*-
"""类型规则表与忽略规则的加载与匹配（单点定义）。

纪律：
- 规则是**数据**，不是代码：`data/rules/types.json` 与 `data/rules/ignore.txt` 是唯一来源。
- 命中顺序由规则表的 `order` 固定；同一路径只取一个主类型（`data/README.md` §四 第 3 条）。
- 规则表缺失或格式错 → 抛 `RulesError`，由 CLI 映射为退出码 4（DEGRADED），
  不静默退化成内置默认表。
"""

import fnmatch
import json
import os

from . import model

# 项目根标志文件（`plan/03` 模块① 第 6 条）。
PROJECT_MARKERS = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
)

PROJECT_MARKER_SUFFIXES = (".csproj",)

TYPES_FILENAME = "types.json"
IGNORE_FILENAME = "ignore.txt"
GRADES_FILENAME = "grades.json"

# 证据源**词表**（单点定义）：规则表里的 `source` 只能取这些名字。
# 词表是"能问什么问题"，规则表是"问哪些问题、按什么顺序、得出什么结论"——
# 判定规则本身全在 `data/rules/grades.json`，引擎不含任何具体规则。
GRADE_SOURCES = (
    "scan_root",
    "type_in",
    "path_in_keep",
    "escapes_root",
    "dir_files_glob",
    "manual_file_newer",
    "link_into_candidate",
    "candidate_is_link",
    "extension_only",
    "dir_structure",
    "project_root_files",
    "project_root_package_json_key",
)

# 需要 `dir_files_glob` / `manual_file_newer` / `extension_only` 的规则必须在
# `patterns` 段里声明同名模式组。
GROUP_SOURCES = ("dir_files_glob", "manual_file_newer", "extension_only")


class RulesError(Exception):
    """规则表缺失或格式错误。"""


def resolve_data_dir():
    """定位 `data/` 目录。

    查找顺序：环境变量 `DEVDISK_DATA_DIR` → 源码树向上查找 → 当前目录向上查找。
    找不到即报错，避免"用了另一套内置规则"这种不可审计的降级。
    """
    override = os.environ.get("DEVDISK_DATA_DIR")
    if override:
        candidate = os.path.abspath(override)
        if os.path.isfile(os.path.join(candidate, "rules", TYPES_FILENAME)):
            return candidate
        raise RulesError("环境变量 DEVDISK_DATA_DIR 指向的目录缺少 rules/%s：%s"
                         % (TYPES_FILENAME, candidate))

    starts = [os.path.dirname(os.path.abspath(__file__)), os.path.abspath(os.getcwd())]
    for start in starts:
        current = start
        while True:
            candidate = os.path.join(current, "data")
            if os.path.isfile(os.path.join(candidate, "rules", TYPES_FILENAME)):
                return candidate
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    raise RulesError(
        "找不到 data/rules/%s。下一步：确认仓库完整，或设置 DEVDISK_DATA_DIR 指向 data/ 目录。"
        % TYPES_FILENAME
    )


class TypeRule(object):
    __slots__ = ("type", "names", "prefixes", "suffixes", "note")

    def __init__(self, payload):
        self.type = payload["type"]
        self.names = tuple(payload.get("names") or ())
        self.prefixes = tuple(payload.get("prefixes") or ())
        self.suffixes = tuple(payload.get("suffixes") or ())
        self.note = payload.get("note") or ""
        if not (self.names or self.prefixes or self.suffixes):
            raise RulesError("类型规则 %r 既无 names 也无 prefixes/suffixes" % (self.type,))

    def matches(self, name):
        if name in self.names:
            return True
        for prefix in self.prefixes:
            if name.startswith(prefix):
                return True
        for suffix in self.suffixes:
            if name.endswith(suffix):
                return True
        return False


class TypeRules(object):
    """类型规则表：按 `order` 顺序匹配，首个命中即返回。"""

    def __init__(self, rules, order):
        self._rules = rules
        self._by_type = {rule.type: rule for rule in rules}
        self._order = order

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RulesError("类型规则表无法读取或解析：%s（%s）" % (path, exc))

        if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
            raise RulesError("类型规则表格式错误：缺少 rules 数组（%s）" % path)

        rules = [TypeRule(item) for item in payload["rules"]]
        order = payload.get("order") or [rule.type for rule in rules]

        by_type = {rule.type: rule for rule in rules}
        missing = [name for name in order if name not in by_type]
        if missing:
            raise RulesError("类型规则表 order 引用了不存在的类型：%s" % ", ".join(missing))
        ordered = [by_type[name] for name in order]

        duplicates = len(set(order)) != len(order)
        if duplicates:
            raise RulesError("类型规则表 order 存在重复类型")

        return cls(ordered, list(order))

    @property
    def types(self):
        return list(self._order)

    def match(self, name):
        for rule in self._rules:
            if rule.matches(name):
                return rule.type
        return None

    def get(self, type_name):
        return self._by_type.get(type_name)


class IgnoreRule(object):
    """一条忽略规则。支持字面名、`*` 前缀/后缀、`/` 锚定、结尾 `/` 仅目录。"""

    __slots__ = ("pattern", "anchored", "directory_only", "wildcard")

    def __init__(self, pattern):
        self.anchored = pattern.startswith("/")
        if self.anchored:
            pattern = pattern[1:]
        self.directory_only = pattern.endswith("/")
        if self.directory_only:
            pattern = pattern.rstrip("/")
        self.wildcard = "*" in pattern or "?" in pattern
        self.pattern = pattern

    def matches(self, rel_path, name, is_dir):
        if self.directory_only and not is_dir:
            return False
        subject = rel_path if self.anchored else name
        if self.wildcard:
            return fnmatch.fnmatchcase(subject, self.pattern)
        if self.anchored:
            return subject == self.pattern
        return name == self.pattern


class IgnoreRules(object):
    """忽略规则集合。空文件合法（表示不忽略任何东西）。"""

    def __init__(self, rules):
        self._rules = rules

    @classmethod
    def load(cls, path):
        if not os.path.isfile(path):
            raise RulesError("忽略规则文件缺失：%s" % path)
        rules = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            raise RulesError("忽略规则文件无法读取：%s（%s）" % (path, exc))

        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("!"):
                raise RulesError("忽略规则不支持取反（!）语法：%s" % text)
            rules.append(IgnoreRule(text))
        return cls(rules)

    def matches(self, rel_path, name, is_dir):
        for rule in self._rules:
            if rule.matches(rel_path, name, is_dir):
                return True
        return False


class PatternGroup(object):
    """一个文件模式组（`grades.json` 的 `patterns` 段）。

    只描述"哪些**文件名**算命中"，不做任何判定；判定由规则表决定。
    支持 `patterns`（fnmatch 作用于 basename）或 `extensions`（扩展名白名单），
    以及 `exclude_names` / `exclude_prefixes`（相对候选目录的路径前缀）。
    """

    __slots__ = ("id", "patterns", "extensions", "exclude_names",
                 "exclude_prefixes", "label", "note")

    def __init__(self, group_id, payload):
        self.id = group_id
        self.patterns = tuple(payload.get("patterns") or ())
        self.extensions = tuple(ext.lower() for ext in (payload.get("extensions") or ()))
        self.exclude_names = tuple(payload.get("exclude_names") or ())
        self.exclude_prefixes = tuple(payload.get("exclude_prefixes") or ())
        self.label = payload.get("label") or group_id
        self.note = payload.get("note") or ""
        if not (self.patterns or self.extensions):
            raise RulesError("模式组 %r 既无 patterns 也无 extensions" % (group_id,))

    def is_excluded(self, rel_path, name):
        if name in self.exclude_names:
            return True
        for prefix in self.exclude_prefixes:
            if rel_path.startswith(prefix):
                return True
        return False

    def matches_name(self, name):
        """按扩展名白名单或 fnmatch 模式判定；调用方负责先查排除项。"""
        if self.extensions:
            lowered = name.lower()
            return any(lowered.endswith(ext) for ext in self.extensions)
        for pattern in self.patterns:
            if fnmatch.fnmatchcase(name, pattern):
                return True
        return False


class GradeRule(object):
    """一条分级规则。`id` 形如 `<grade>:<name>`，与 `grade` 必须一致。"""

    __slots__ = ("id", "grade", "types", "checks", "veto", "reason", "note")

    def __init__(self, payload):
        self.id = payload.get("id")
        self.grade = payload.get("grade")
        if not self.id or not isinstance(self.id, str):
            raise RulesError("分级规则缺少 id：%r" % (payload,))
        if self.grade not in model.GRADES:
            raise RulesError("分级规则 %s 的 grade 非法：%r" % (self.id, self.grade))
        if not self.id.startswith(self.grade + ":"):
            raise RulesError("分级规则 id 必须以 `<grade>:` 开头：%s（grade=%s）"
                             % (self.id, self.grade))
        types = payload.get("types")
        if not isinstance(types, list) or not types:
            raise RulesError("分级规则 %s 的 types 必须是非空数组" % self.id)
        self.types = tuple(types)
        self.checks = tuple(payload.get("checks") or ())
        self.veto = tuple(payload.get("veto") or ())
        self.reason = payload.get("reason") or ""
        if not self.reason.strip():
            raise RulesError("分级规则 %s 缺少 reason 模板" % self.id)
        if "{evidence}" not in self.reason:
            raise RulesError("分级规则 %s 的 reason 模板必须含 {evidence}（证据摘要）" % self.id)
        self.note = payload.get("note") or ""

    def applies_to(self, type_name):
        return "*" in self.types or type_name in self.types


class GradeRules(object):
    """分级规则表：数组顺序即优先级（`data/README.md` §四 第 7 条）。"""

    def __init__(self, rules, order, grades, groups, sources):
        self._rules = rules
        self._order = order
        self._grades = grades
        self._groups = groups
        self._sources = sources

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RulesError("分级规则表无法读取或解析：%s（%s）" % (path, exc))

        if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
            raise RulesError("分级规则表格式错误：缺少 rules 数组（%s）" % path)
        if not payload["rules"]:
            raise RulesError("分级规则表为空：%s" % path)

        grades = payload.get("grades") or list(model.GRADES)
        if list(grades) != list(model.GRADES):
            raise RulesError("分级规则表 grades 与口径不一致：%r（应为 %r）"
                             % (grades, list(model.GRADES)))

        groups = {}
        raw_groups = payload.get("patterns") or {}
        if not isinstance(raw_groups, dict):
            raise RulesError("分级规则表 patterns 必须是对象（%s）" % path)
        for group_id, group_payload in raw_groups.items():
            groups[group_id] = PatternGroup(group_id, group_payload or {})

        sources = dict(payload.get("sources") or {})
        rules = [GradeRule(item) for item in payload["rules"]]

        by_id = {}
        for rule in rules:
            if rule.id in by_id:
                raise RulesError("分级规则 id 重复：%s" % rule.id)
            by_id[rule.id] = rule
            for check in list(rule.checks) + list(rule.veto):
                source = check.get("source")
                if source not in GRADE_SOURCES:
                    raise RulesError("分级规则 %s 引用了未知证据源：%r" % (rule.id, source))
                if source in GROUP_SOURCES and check.get("group") not in groups:
                    raise RulesError("分级规则 %s 引用了未声明的模式组：%r"
                                     % (rule.id, check.get("group")))
                if source == "project_root_files" and not check.get("patterns"):
                    raise RulesError("证据源 project_root_files 需要 patterns：%s" % rule.id)
                if source == "project_root_package_json_key" and not check.get("keys"):
                    raise RulesError("证据源 project_root_package_json_key 需要 keys：%s" % rule.id)

        order = payload.get("order")
        if order is None:
            order = [rule.id for rule in rules]
        order = list(order)
        missing = [name for name in order if name not in by_id]
        if missing:
            raise RulesError("分级规则表 order 引用了不存在的规则：%s" % ", ".join(missing))
        if len(set(order)) != len(order):
            raise RulesError("分级规则表 order 存在重复规则")
        if len(order) != len(rules):
            raise RulesError("分级规则表 order 未覆盖全部规则（order=%d，rules=%d）"
                             % (len(order), len(rules)))

        # 数组顺序即优先级，order 只是显式声明；两者必须一致，否则规则行为不可预期。
        if order != [rule.id for rule in rules]:
            raise RulesError("分级规则表 order 与 rules 数组顺序不一致（顺序即优先级）")

        ordered = [by_id[name] for name in order]
        for index in range(1, len(ordered)):
            previous = model.GRADES.index(ordered[index - 1].grade)
            current = model.GRADES.index(ordered[index].grade)
            if current < previous:
                raise RulesError("分级规则表顺序违反优先级：%s 排在 %s 之后"
                                 % (ordered[index].id, ordered[index - 1].id))
        return cls(ordered, order, list(grades), groups, sources)

    @property
    def rules(self):
        return list(self._rules)

    @property
    def order(self):
        return list(self._order)

    @property
    def grades(self):
        return list(self._grades)

    @property
    def groups(self):
        return dict(self._groups)

    @property
    def sources(self):
        return dict(self._sources)

    def group(self, group_id):
        return self._groups.get(group_id)


def load_rules(data_dir=None):
    """加载扫描层规则表，返回 (TypeRules, IgnoreRules)。"""
    root = data_dir or resolve_data_dir()
    rules_dir = os.path.join(root, "rules")
    types = TypeRules.load(os.path.join(rules_dir, TYPES_FILENAME))
    ignore = IgnoreRules.load(os.path.join(rules_dir, IGNORE_FILENAME))
    return types, ignore


def load_grade_rules(data_dir=None):
    """加载分级规则表（`data/rules/grades.json`）。"""
    root = data_dir or resolve_data_dir()
    return GradeRules.load(os.path.join(root, "rules", GRADES_FILENAME))


def is_project_marker(name):
    if name in PROJECT_MARKERS:
        return True
    for suffix in PROJECT_MARKER_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False
