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


def load_rules(data_dir=None):
    """加载规则表，返回 (TypeRules, IgnoreRules)。"""
    root = data_dir or resolve_data_dir()
    rules_dir = os.path.join(root, "rules")
    types = TypeRules.load(os.path.join(rules_dir, TYPES_FILENAME))
    ignore = IgnoreRules.load(os.path.join(rules_dir, IGNORE_FILENAME))
    return types, ignore


def is_project_marker(name):
    if name in PROJECT_MARKERS:
        return True
    for suffix in PROJECT_MARKER_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False
