# -*- coding: utf-8 -*-
"""安全分级（模块② 的上半层）：只解释规则表，不含任何具体规则。

纪律（`plan/03` 模块②、`data/README.md` §四 第 7/8 条）：

1. **规则是数据**：顺序、等级、类型、模式、理由模板全部来自
   `data/rules/grades.json`；本模块只提供证据源词表（`evidence.py`）与调度。
   同一候选命中**第一条**适用的规则即返回（顺序即优先级）。
2. **证据门槛**：`safe` 必须有可核查证据；命中安全规则却拿不到证据，
   或候选子树存在读不到的目录 → `caution`（`_finalize`）。
3. **理由模板**：`"<结论>：<证据摘要>"`；模板随规则表（数据）给出，
   本模块只保留兜底与降级模板（常量区），并且 **reason 只引用路径 / 体积 /
   mtime / 白名单文件名**，不引用任何文件正文内容。
4. **未命中任何规则** → 兜底 `caution`，理由"证据不足"（绝不默认 `safe`）。
"""

import os

from . import model
from .evidence import CandidateEvidence
from .scanner import find_project_root

# ---------------------------------------------------------------- 模板常量区
#
# 具体结论模板在 `data/rules/grades.json` 的每条规则里；这里只放两处
# **兜底**模板：没有任何规则命中，以及命中安全规则但过不了证据门槛。

FALLBACK_REASON = "证据不足：未识别到可重建标志（{evidence}），按谨慎处理。"
DOWNGRADE_REASON = "证据不足：命中安全规则但未取得可核验证据（{evidence}），降级为谨慎处理。"

FALLBACK_EVIDENCE_KIND = "insufficient_evidence"
DOWNGRADE_EVIDENCE_KIND = "unverified_claim"

# 证据摘要里证据条数上限（超出的用"等 N 条"概括，保证理由长度可控且确定）。
SUMMARY_LIMIT = 3


class GradeContext(object):
    """分级上下文：扫描根、保留列表、跳过记录（含链接目标）。

    只做路径归一与包含判断；不含任何规则知识。
    """

    def __init__(self, roots, keep_paths=None, skipped=None):
        self.roots = [os.path.abspath(root) for root in (roots or ())]
        self.keep_paths = [os.path.normcase(os.path.abspath(path))
                           for path in (keep_paths or ())]
        self.links = []
        for item in (skipped or ()):
            item = item.to_dict() if hasattr(item, "to_dict") else dict(item)
            self.links.append({
                "path": item.get("path"),
                "reason": item.get("reason"),
                "link_target": item.get("detail"),
            })
        self._project_root_cache = {}
        self._root_for_cache = {}

    # ------------------------------------------------------------ 路径工具

    def is_kept(self, path):
        return os.path.normcase(os.path.abspath(path)) in self.keep_paths

    def is_inside(self, path, directory):
        """`path` 是否等于或位于 `directory` 之内（大小写按平台归一）。"""
        try:
            candidate = os.path.normcase(os.path.abspath(path))
            base = os.path.normcase(os.path.abspath(directory))
            return os.path.commonpath([candidate, base]) == base
        except ValueError:
            return False

    def is_inside_any_root(self, path):
        return any(self.is_inside(path, root) for root in self.roots)

    def root_of(self, path):
        """返回包含 `path` 的扫描根（取最长匹配）；没有则 None。"""
        key = os.path.normcase(os.path.abspath(path))
        if key in self._root_for_cache:
            return self._root_for_cache[key]
        best = None
        for root in self.roots:
            if self.is_inside(path, root):
                if best is None or len(root) > len(best):
                    best = root
        self._root_for_cache[key] = best
        return best

    def project_root_of(self, path):
        """`path` 所在项目的项目根（与扫描层同一实现 `scanner.find_project_root`）。"""
        root = self.root_of(path)
        if root is None:
            return None
        cache_key = os.path.normcase(os.path.abspath(path))
        if cache_key in self._project_root_cache:
            return self._project_root_cache[cache_key]
        found = find_project_root(root, path, cache=self._project_root_cache)
        self._project_root_cache[cache_key] = found
        return found

    def rel_to_root(self, path):
        """相对扫描根的展示路径（根外或无根可用时退回绝对路径）。"""
        if not path:
            return ""
        root = self.root_of(path)
        if root is None:
            return model.display_path(path)
        return os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")


class Grader(object):
    """规则解释器：一次分级运行一个实例。"""

    def __init__(self, table, context):
        self.table = table
        self.context = context
        self.rules = table.rules
        self.groups = table.groups
        self._stats = {"by_grade": {}, "evidence_items": 0, "caution_fallback": 0,
                       "caution_downgraded": 0, "evidence_errors": []}

    # ---------------------------------------------------------------- 公开面

    def grade(self, candidate):
        """给单条候选填 `grade` / `reason` / `evidence`，返回 (grade, reason, evidence)。"""
        state = CandidateEvidence(candidate, self.groups)
        for rule in self.rules:
            if not rule.applies_to(candidate.type):
                continue
            evidence = self._collect(state, rule)
            if not evidence:
                if rule.checks:
                    continue          # 该规则不成立，继续试下一条
                return self._finalize(candidate, state, rule, rule.grade, evidence)
            if self._vetoed(state, rule):
                continue
            return self._finalize(candidate, state, rule, rule.grade, evidence)
        return self._fallback(candidate, state)

    def grade_result(self, result):
        """给整个 `ScanResult` 就地分级，返回统计 dict。"""
        self._stats = {"by_grade": dict((grade, 0) for grade in model.GRADES),
                       "evidence_items": 0, "caution_fallback": 0,
                       "caution_downgraded": 0, "evidence_errors": []}
        for candidate in result.candidates:
            grade, reason, evidence = self.grade(candidate)
            candidate.grade = grade
            candidate.reason = reason
            candidate.evidence = evidence
            self._stats["by_grade"][grade] = self._stats["by_grade"].get(grade, 0) + 1
            self._stats["evidence_items"] += len(evidence)
        return dict(self._stats)

    # ---------------------------------------------------------------- 内部

    def _collect(self, state, rule):
        items = []
        for check in rule.checks:
            items.extend(state.evaluate(check, self.context))
        for error in state.errors:
            if error not in self._stats["evidence_errors"]:
                self._stats["evidence_errors"].append(error)
        return items

    def _vetoed(self, state, rule):
        """规则自带的否决项（例如"缓存目录里混进了 .env"就不再是纯缓存）。"""
        for check in rule.veto:
            if state.evaluate(check, self.context):
                return True
        return False

    def _finalize(self, candidate, state, rule, grade, evidence):
        """证据门槛：安全结论必须有可核验证据，且候选子树必须能完整读取。"""
        facts = state.facts() if rule.grade == model.GRADE_SAFE else None
        unreadable = facts.unreadable if facts is not None else 0
        if rule.grade == model.GRADE_SAFE and (not evidence or unreadable):
            return self._downgrade(candidate, state, rule, evidence, unreadable)
        reason = render_reason(rule.reason, evidence)
        return grade, reason, evidence

    def _downgrade(self, candidate, state, rule, evidence, unreadable=0):
        """证据不足降级：安全规则命中但证据缺失/子树读不全 → 谨慎。"""
        items = list(evidence)
        if unreadable:
            items.append({"kind": "unreadable_subtree", "path": ".",
                          "detail": "目录子树内有 %d 处无法读取（%s）"
                                    % (unreadable, "、".join(state.facts().unreadable_dirs[:3]))})
        if not items:
            items.append({"kind": DOWNGRADE_EVIDENCE_KIND, "path": ".",
                          "detail": "安全规则 %s 未产出可核验证据" % rule.id})
        self._stats["caution_downgraded"] += 1
        return model.GRADE_CAUTION, render_reason(DOWNGRADE_REASON, items), items

    def _fallback(self, candidate, state):
        """兜底：没有任何规则命中 → 谨慎（绝不默认安全可删）。"""
        facts = state.facts()
        items = [{"kind": FALLBACK_EVIDENCE_KIND, "path": ".",
                  "detail": "%s，未命中任何分级规则" % facts.to_evidence_detail()}]
        for error in state.errors:
            items.append({"kind": "evidence_error", "path": ".", "detail": error})
        if facts.unreadable:
            items.append({"kind": "unreadable_subtree", "path": ".",
                          "detail": "目录子树内有 %d 处无法读取" % facts.unreadable})
        self._stats["caution_fallback"] += 1
        return model.GRADE_CAUTION, render_reason(FALLBACK_REASON, items), items


def render_reason(template, evidence):
    """把证据摘要填进理由模板：`<结论>：<证据摘要>`。"""
    details = [item.get("detail") or item.get("path") or "" for item in evidence]
    details = [detail for detail in details if detail]
    if len(details) > SUMMARY_LIMIT:
        summary = "；".join(details[:SUMMARY_LIMIT]) + "；等 %d 项" % len(details)
    else:
        summary = "；".join(details)
    return template.replace("{evidence}", summary)


def make_context(result, keep_paths=None):
    """从 `ScanResult` 构造分级上下文。"""
    return GradeContext(result.roots, keep_paths=keep_paths, skipped=result.skipped)


def grade_result(result, table, keep_paths=None, context=None):
    """便捷入口：对 `ScanResult` 就地分级，返回统计 dict。"""
    grader = Grader(table, context or make_context(result, keep_paths=keep_paths))
    return grader.grade_result(result)
