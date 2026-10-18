# -*- coding: utf-8 -*-
"""模块⑤：单文件 HTML 报告（M5）。

纪律（`plan/03` 模块⑤、`plan/02` §6.5）：

- **单一渲染源**：只读入 `scan.json`（必需）、`audit.json`（可选）与隔离批次
  manifest（可选），聚合值全部由输入算出。本模块**不重新分级、不改写体积**；
  页面内 JS 只做筛选/排序，不重算任何合计。
- **零外链**：不写 `<link href>` / `<script src>` / CSS `url(http...)` / `@import`；
  每次渲染结束都调用 `assert_no_external_resources`，命中即抛错。
- **全量转义**：路径、理由、证据、状态、原因等一切插值都过 `html.escape`。
- **确定性**：不写生成时刻、主机名、用户名；`--as-of` 是唯一的展示时间来源。
  同一输入两次渲染逐字节一致（`serialize.write_text_atomic` 保证 LF 与结尾换行）。

页面区块顺序固定为 10 段（`SECTION_ORDER`），测试按 `id` 断言顺序。
"""

import html
import json
import os
import re

from . import exit_codes
from . import model
from .audit import STATUS_NOT_REQUESTED, STATUS_OK
from .audit import docker as docker_audit

SCHEMA_VERSION = 1

# 固定区块顺序（`plan/03` 模块⑤"页面结构"）。
SECTION_ORDER = (
    "sec-source",
    "sec-overview",
    "sec-ranking",
    "sec-aggregates",
    "sec-grades",
    "sec-candidates",
    "sec-docker",
    "sec-git",
    "sec-batches",
    "sec-disclaimer",
)

# 零外链守门：任何命中都视为渲染缺陷（不是"警告"）。
EXTERNAL_PATTERNS = (
    (re.compile(r"<link\b[^>]*\bhref\s*=", re.I), "<link href>"),
    (re.compile(r"<script\b[^>]*\bsrc\s*=", re.I), "<script src>"),
    (re.compile(r"url\(\s*['\"]?\s*(?:https?:)?//", re.I), "CSS url(...)"),
    (re.compile(r"@import\b", re.I), "CSS @import"),
    (re.compile(r"<(?:img|iframe|video|audio|source|embed|object)\b[^>]*\b(?:src|data)\s*="
                r"\s*['\"]?(?:https?:)?//", re.I), "内嵌外部资源"),
    # 零网络：报告里不得出现任何绝对 URL（含 JS 字符串常量里的 CDN 地址）。
    (re.compile(r"https?://", re.I), "外部 URL"),
)

# 文案守门：报告不得承诺"删除后必然安全"。
PROMISE_PATTERNS = (
    re.compile(r"(?:必然|一定|绝对|100%)\s*安全"),
    re.compile(r"保证\s*(?:删除|清理)?\s*(?:后)?\s*安全"),
    re.compile(r"删(?:除|掉)?\s*(?:后)?\s*(?:必然|一定|绝对)\s*没问题"),
)

DOCKER_LABELS = (
    ("Images", "images"),
    ("Containers", "containers"),
    ("Local Volumes", "volumes"),
    ("Build Cache", "build_cache"),
)


class ReportError(Exception):
    """报告输入或渲染失败（退出码由编排层映射，默认 2=用法错误）。"""

    def __init__(self, message, exit_code=exit_codes.USAGE):
        Exception.__init__(self, message)
        self.exit_code = exit_code


def _esc(value):
    """唯一转义入口：所有插值都必须经过它。"""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _int(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportError("scan.json 字段 %s 不是整数：%r" % (field, value))
    if value < 0:
        raise ReportError("scan.json 字段 %s 为负数：%r" % (field, value))
    return value


def default_top():
    return 50


# ------------------------------------------------------------------ 输入


def load_scan(path):
    """读入并校验 `scan.json`（缺失/坏/自相矛盾 → 退出码 2）。"""
    if not path:
        raise ReportError("缺少 `--input <scan.json>`。下一步：先运行 `devdisk scan`。")
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        raise ReportError("候选清单不存在：%s。下一步：先运行 `devdisk scan` 生成候选清单。"
                          % absolute)
    try:
        with open(absolute, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ReportError("候选清单无法解析：%s（%s）。下一步：重新扫描生成。" % (absolute, exc))
    return validate_scan(payload, absolute)


def validate_scan(payload, source="<scan.json>"):
    """最小结构校验 + 汇总自洽校验（只校验一致性，不重新判定）。"""
    if not isinstance(payload, dict):
        raise ReportError("候选清单不是 JSON 对象：%s" % source)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ReportError("候选清单 schema_version 不支持：%r（期望 %d）"
                          % (payload.get("schema_version"), SCHEMA_VERSION))
    roots = payload.get("roots")
    if not isinstance(roots, list) or not roots or \
            not all(isinstance(item, str) and item for item in roots):
        raise ReportError("候选清单缺少有效的 roots：%s" % source)
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ReportError("候选清单缺少 candidates：%s" % source)
    skipped = payload.get("skipped")
    if skipped is None:
        skipped = []
        payload = dict(payload)
        payload["skipped"] = skipped
    if not isinstance(skipped, list):
        raise ReportError("候选清单的 skipped 不是数组：%s" % source)

    by_grade = dict((grade, {"count": 0, "bytes": 0}) for grade in model.GRADES)
    by_type = {}
    total_bytes = 0
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ReportError("候选清单第 %d 项不是对象：%s" % (index, source))
        for field in ("path", "type", "grade"):
            if not isinstance(candidate.get(field), str) or not candidate.get(field):
                raise ReportError("候选清单第 %d 项缺少 %s：%s" % (index, field, source))
        size = _int(candidate.get("bytes"), "candidates[%d].bytes" % index)
        grade = candidate["grade"]
        if grade not in by_grade:
            raise ReportError("候选清单第 %d 项 grade 非法（%r）。"
                              "下一步：用 M2 之后生成的 scan.json（含分级）。"
                              % (index, grade))
        total_bytes += size
        by_grade[grade]["count"] += 1
        by_grade[grade]["bytes"] += size
        slot = by_type.setdefault(candidate["type"], {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += size

    totals = payload.get("totals")
    if not isinstance(totals, dict):
        raise ReportError("候选清单缺少 totals：%s" % source)
    if totals.get("candidate_count") != len(candidates):
        raise ReportError("scan.json 汇总与明细不一致：candidate_count=%r / 明细 %d"
                          % (totals.get("candidate_count"), len(candidates)))
    if totals.get("candidate_bytes") != total_bytes:
        raise ReportError("scan.json 汇总与明细不一致：candidate_bytes=%r / 明细合计 %d"
                          % (totals.get("candidate_bytes"), total_bytes))

    declared_grades = totals.get("by_grade") or {}
    for grade in model.GRADES:
        declared = declared_grades.get(grade, 0)
        if declared != by_grade[grade]["bytes"]:
            raise ReportError("scan.json by_grade[%s] 与明细不一致：%r / %d"
                              % (grade, declared, by_grade[grade]["bytes"]))
    declared_types = totals.get("by_type") or {}
    for name, slot in by_type.items():
        if declared_types.get(name, 0) != slot["bytes"]:
            raise ReportError("scan.json by_type[%s] 与明细不一致：%r / %d"
                              % (name, declared_types.get(name), slot["bytes"]))
    return payload


def load_audit(path):
    """读入可选 `audit.json`。

    返回 `(payload, note)`：
    - 未提供或路径不存在 → `(None, 说明)`，报告正常生成，Docker/.git 段显示"未审计"；
    - 文件存在但无法解析 / 结构不对 → `ReportError`（退出码 2）。
    """
    if not path:
        return None, "未提供"
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        return None, "未提供（%s 不存在）" % absolute
    try:
        with open(absolute, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ReportError("审计结果无法解析：%s（%s）。下一步：重新运行 `devdisk audit`。"
                          % (absolute, exc))
    if not isinstance(payload, dict):
        raise ReportError("审计结果不是 JSON 对象：%s" % absolute)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ReportError("审计结果 schema_version 不支持：%r（期望 %d）"
                          % (payload.get("schema_version"), SCHEMA_VERSION))
    for section in ("docker", "git"):
        if not isinstance(payload.get(section), dict):
            raise ReportError("审计结果缺少 %s 段：%s" % (section, absolute))
    return payload, absolute


def load_batches(paths=None, trash=None):
    """读入隔离批次 manifest 清单（可选）。

    `--batch <manifest.json>` 可重复；`--trash <dir>` 读取 `<dir>/*/manifest.json`。
    两种来源都按批次 ID 排序，保证同一输入输出一致。
    """
    candidates = []
    for raw in paths or []:
        candidates.append(os.path.abspath(raw))
    if trash:
        root = os.path.abspath(trash)
        if not os.path.isdir(root):
            raise ReportError("隔离区目录不存在：%s。下一步：检查 `--trash` 路径。" % root)
        try:
            names = sorted(os.listdir(root))
        except OSError as exc:
            raise ReportError("隔离区目录无法读取：%s（%s）" % (root, exc))
        for name in names:
            manifest = os.path.join(root, name, "manifest.json")
            if os.path.isfile(manifest):
                candidates.append(manifest)

    seen = set()
    batches = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not os.path.isfile(path):
            raise ReportError("批次 manifest 不存在：%s。下一步：检查 `--batch` 路径。" % path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ReportError("批次 manifest 无法解析：%s（%s）" % (path, exc))
        if not isinstance(payload, dict):
            raise ReportError("批次 manifest 不是对象：%s" % path)
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ReportError("批次 manifest schema_version 不支持：%r（%s）"
                              % (payload.get("schema_version"), path))
        if not isinstance(payload.get("entries"), list) or not payload.get("batch_id"):
            raise ReportError("批次 manifest 缺少 batch_id 或 entries：%s" % path)
        payload = dict(payload)
        payload["_source"] = path
        batches.append(payload)

    batches.sort(key=lambda item: (item["batch_id"], item["_source"]))
    return batches


# ------------------------------------------------------------------ 聚合


def aggregate(payload, top=None):
    """由输入计算聚合值。**不做任何分级判定**，只累加与排序。"""
    top = default_top() if top is None else int(top)
    if top < 0:
        raise ReportError("`--top` 不能为负数。下一步：省略该参数使用默认值。")

    candidates = payload["candidates"]
    roots = payload["roots"]

    candidate_bytes = 0
    by_grade = dict((grade, {"count": 0, "bytes": 0}) for grade in model.GRADES)
    by_type = {}
    by_project = {}
    for candidate in candidates:
        size = int(candidate["bytes"])
        candidate_bytes += size
        grade = candidate["grade"]
        by_grade[grade]["count"] += 1
        by_grade[grade]["bytes"] += size
        name = candidate["type"]
        slot = by_type.setdefault(name, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += size
        project = _project_label(candidate.get("project_root"), roots)
        slot = by_project.setdefault(project, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += size

    reclaimable = by_grade[model.GRADE_SAFE]["bytes"] + \
        by_grade[model.GRADE_CAUTION]["bytes"]

    ordered = sorted(candidates, key=lambda item: (-int(item["bytes"]), item["path"]))
    ranking = ordered[:top] if top else []

    skipped = payload.get("skipped") or []
    skipped_by_reason = {}
    for item in skipped:
        reason = item.get("reason") or "unknown"
        skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1

    totals = payload.get("totals") or {}
    return {
        "roots": list(roots),
        "candidate_count": len(candidates),
        "candidate_bytes": candidate_bytes,
        "reclaimable_bytes": reclaimable,
        "by_grade": by_grade,
        "by_type": sorted(((name, slot["count"], slot["bytes"])
                           for name, slot in by_type.items()),
                          key=lambda item: (-item[2], item[0])),
        "by_project": sorted(((name, slot["count"], slot["bytes"])
                              for name, slot in by_project.items()),
                             key=lambda item: (-item[2], item[0])),
        "ranking": ranking,
        "ranking_limit": top,
        "skipped_count": len(skipped),
        "skipped_by_reason": skipped_by_reason,
        "other_count": int(totals.get("other_count") or 0),
        "other_bytes": int(totals.get("other_bytes") or 0),
        "ignored_count": int(totals.get("ignored_count") or 0),
        "generated_by": payload.get("generated_by") or "",
    }


def _project_label(project_root, roots):
    if not project_root:
        return "（未归属项目）"
    return _relative(path=project_root, roots=roots)


def _relative(path, roots):
    """优先显示相对扫描根的路径；不在任何根内时退回绝对路径。"""
    absolute = os.path.abspath(path)
    best = None
    for root in roots:
        root_absolute = os.path.abspath(root)
        try:
            common = os.path.commonpath([absolute, root_absolute])
        except ValueError:
            continue
        if os.path.normcase(common) == os.path.normcase(root_absolute):
            if best is None or len(root_absolute) > len(best):
                best = root_absolute
    if best is None:
        return model.display_path(absolute)
    rel = os.path.relpath(absolute, best)
    if rel == os.curdir:
        return "."
    return rel.replace(os.sep, "/")


def _percent(part, whole):
    """整数运算得到一位小数百分比（确定性，不受浮点影响）。"""
    part = int(part)
    whole = int(whole)
    if whole <= 0:
        return "0.0%"
    permille = (part * 1000 + whole // 2) // whole
    return "%d.%d%%" % (permille // 10, permille % 10)


def _count_text(value):
    return "%d" % int(value)


# ------------------------------------------------------------------ 渲染小件


def _metric(tag, key, value, display=None, css=""):
    """带机器可读锚点的数值节点：`data-metric` + `data-value`（原始字节/计数）。"""
    class_attr = ' class="%s"' % _esc(css) if css else ""
    text = _count_text(value) if display is None else display
    return '<%s%s data-metric="%s" data-value="%d">%s</%s>' % (
        tag, class_attr, _esc(key), int(value), _esc(text), tag)


def _metric_cell(key, value, display=None, css=""):
    return _metric("td", key, value, display, css)


def _badge(grade):
    return '<span class="badge %s">%s</span>' % (_esc(grade),
                                                 _esc(model.GRADE_LABELS.get(grade, grade)))


def _truncate(value, limit=160):
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "……"


def _evidence_html(evidence):
    items = evidence or []
    if not items:
        return '<span class="muted">（无）</span>'
    parts = ['<ul class="ev">']
    for item in items:
        parts.append('<li><code>%s</code> <span class="evpath">%s</span> — %s</li>'
                     % (_esc(item.get("kind")), _esc(item.get("path")),
                        _esc(_truncate(item.get("detail")))))
    parts.append("</ul>")
    return "".join(parts)


def _section_start(section_id, title):
    return '<section id="%s">\n<h2>%s</h2>' % (_esc(section_id), _esc(title))


def _empty(text):
    return '<p class="muted">%s</p>' % _esc(text)


def _empty_html(markup):
    """与 `_empty` 同款样式，但内容由调用方给**已经转义过的**片段。"""
    return '<p class="muted">%s</p>' % markup


def _code(value):
    return "<code>%s</code>" % _esc(value)


def _table(headers, rows, attrs="", sort_keys=None):
    cells = []
    for index, item in enumerate(headers):
        key = (sort_keys or {}).get(item)
        if key:
            cells.append('<th data-sort="%s">%s</th>' % (_esc(key), _esc(item)))
        else:
            cells.append("<th>%s</th>" % _esc(item))
    body = "".join(rows)
    return ('<table class="grid"%s><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
            % (attrs, "".join(cells), body))


# ------------------------------------------------------------------ 渲染


def render_html(payload, audit=None, batches=None, top=None, as_of=None,
                audit_note=None, batch_note=None):
    """渲染单文件 HTML。返回完整文本（utf-8、LF、结尾换行由写盘层补齐）。"""
    stats = aggregate(payload, top=top)
    roots = stats["roots"]
    batches = list(batches or [])

    head = _render_head(stats, as_of)
    sections = [
        _render_source(stats, audit, audit_note, as_of, batches, batch_note),
        _render_overview(stats),
        _render_ranking(stats, roots),
        _render_aggregates(stats),
        _render_grades(stats),
        _render_candidates(stats, roots, payload["candidates"]),
        _render_docker(audit),
        _render_git(audit),
        _render_batches(batches),
        _render_disclaimer(),
    ]

    document = "".join([
        "<!DOCTYPE html>\n",
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
        "<title>开发者磁盘占用审计报告</title>\n",
        "<style>\n%s\n</style>\n</head>\n<body>\n" % STYLE,
        head,
        '<main>\n',
        "".join(sections),
        "</main>\n",
        "<script>\n%s\n</script>\n" % SCRIPT,
        "</body>\n</html>\n",
    ])
    # 零外链是渲染的出口条件：任何命中都直接抛错，绝不写出带外链的报告。
    assert_no_external_resources(document)
    # 文案守门同样常驻：报告不得承诺"删除后必然安全"。
    assert_no_promise_wording(document)
    return document


def assert_no_external_resources(document):
    """扫描零外链红线；命中即抛 `ReportError`（不是警告）。"""
    for pattern, label in EXTERNAL_PATTERNS:
        match = pattern.search(document)
        if match is not None:
            start = max(0, match.start() - 40)
            excerpt = document[start:match.end() + 40].replace("\n", " ")
            raise ReportError(
                "报告命中零外链红线（%s）：…%s…。下一步：检查渲染层是否引入了外部资源。"
                % (label, excerpt))
    return document


def assert_no_promise_wording(document):
    """文案守门：不得出现"删除后必然安全"类承诺。"""
    for pattern in PROMISE_PATTERNS:
        match = pattern.search(document)
        if match is not None:
            raise ReportError("报告出现安全承诺措辞：%r。下一步：改为“建议”口径。"
                              % match.group(0))


def _render_head(stats, as_of):
    return ('<header class="head"><h1>开发者磁盘占用审计报告</h1>'
            '<p class="lede">按可重建性给出分级可删清单；默认为只读建议，'
            '清理必须显式确认，删除走隔离区可回滚。</p></header>\n')


def _render_source(stats, audit, audit_note, as_of, batches, batch_note):
    rows = [
        ("扫描根", "、".join(_esc(root) for root in stats["roots"])),
        ("候选清单", _esc(stats.get("generated_by") or "devdisk scan")),
        ("审计输入", _esc(audit_note or ("已提供" if audit else "未提供"))),
        ("隔离批次", _esc(batch_note or ("%d 个" % len(batches) if batches
                                         else "未提供"))),
    ]
    if as_of:
        rows.append(("报告基准时间", _esc(as_of) + "（由 `--as-of` 固定）"))
    body = "".join('<tr><th scope="row">%s</th><td>%s</td></tr>' % (name, value)
                   for name, value in rows)
    return (_section_start("sec-source", "数据来源") +
            '<table class="meta"><tbody>%s</tbody></table>\n</section>\n' % body)


def _render_overview(stats):
    cards = [
        ("候选总数", "candidate-count", stats["candidate_count"],
         _count_text(stats["candidate_count"]), False),
        ("候选总体积", "candidate-bytes", stats["candidate_bytes"],
         model.format_bytes(stats["candidate_bytes"]), True),
        ("可删合计（安全可删 + 谨慎）", "reclaimable-bytes", stats["reclaimable_bytes"],
         model.format_bytes(stats["reclaimable_bytes"]), True),
        ("跳过条目", "skipped-count", stats["skipped_count"],
         _count_text(stats["skipped_count"]), False),
    ]
    cards_html = []
    for label, key, value, display, is_bytes in cards:
        raw = ('<span class="raw">%d 字节</span>' % int(value)) if is_bytes \
            else '<span class="raw">条</span>'
        cards_html.append(
            '<div class="card"><span class="label">%s</span>%s%s</div>'
            % (_esc(label), _metric("strong", key, value, display), raw))
    cards_html = "".join(cards_html)
    extra = []
    if stats["other_count"]:
        extra.append("低于阈值的目录 %d 个，合计 %s"
                     % (stats["other_count"], model.format_bytes(stats["other_bytes"])))
    if stats["ignored_count"]:
        extra.append("按忽略规则跳过 %d 个目录" % stats["ignored_count"])
    if stats["skipped_by_reason"]:
        detail = "、".join("%s=%d" % (reason, count)
                          for reason, count in sorted(stats["skipped_by_reason"].items()))
        extra.append("跳过原因：%s" % detail)
    note = "".join('<p class="muted">%s</p>' % _esc(item) for item in extra)
    return (_section_start("sec-overview", "总览") +
            '<div class="cards">%s</div>\n%s</section>\n' % (cards_html, note))


def _render_ranking(stats, roots):
    limit = stats["ranking_limit"]
    rows_html = []
    for index, candidate in enumerate(stats["ranking"], 1):
        rows_html.append(
            '<tr data-rank="%d" data-rel="%s">'
            '<td class="num">%d</td><td class="path">%s</td><td>%s</td>'
            '<td>%s</td><td>%s</td></tr>'
            % (index, _esc(_relative(candidate["path"], roots)), index,
               _esc(_relative(candidate["path"], roots)), _esc(candidate["type"]),
               _metric("span", "rank-bytes-%d" % index, int(candidate["bytes"]),
                       model.format_bytes(int(candidate["bytes"]))),
               _badge(candidate["grade"])))
    if not limit:
        body = _empty("未列出排行（--top 0）。")
    elif not rows_html:
        body = _empty("没有候选条目。")
    else:
        body = _table(("#", "路径", "类型", "体积", "分级"), rows_html)
    title = "体积排行（前 %d 条）" % limit if limit else "体积排行（未列出）"
    return (_section_start("sec-ranking", title) + body + "\n</section>\n")


def _render_aggregates(stats):
    type_rows = []
    for name, count, size in stats["by_type"]:
        type_rows.append(
            '<tr data-type="%s"><td class="path">%s</td>'
            '<td>%s</td><td>%s</td><td class="num">%s</td></tr>'
            % (_esc(name), _esc(name),
               _metric("span", "type-count:%s" % name, count),
               _metric("span", "type-bytes:%s" % name, size, model.format_bytes(size)),
               _esc(_percent(size, stats["candidate_bytes"]))))
    project_rows = []
    for index, (name, count, size) in enumerate(stats["by_project"], 1):
        project_rows.append(
            '<tr data-project="%s"><td class="path">%s</td>'
            '<td>%s</td><td>%s</td><td class="num">%s</td></tr>'
            % (_esc(name), _esc(name),
               _metric("span", "project-count-%d" % index, count),
               _metric("span", "project-bytes-%d" % index, size, model.format_bytes(size)),
               _esc(_percent(size, stats["candidate_bytes"]))))

    parts = [_section_start("sec-aggregates", "聚合")]
    parts.append("<h3>按类型</h3>")
    parts.append(_table(("类型", "数量", "体积", "占比"), type_rows)
                 if type_rows else _empty("没有候选条目。"))
    parts.append("<h3>按项目</h3>")
    parts.append(_table(("项目", "数量", "体积", "占比"), project_rows)
                 if project_rows else _empty("没有候选条目。"))
    parts.append("\n</section>\n")
    return "".join(parts)


def _render_grades(stats):
    rows = []
    for grade in model.GRADES:
        slot = stats["by_grade"][grade]
        rows.append(
            '<tr data-grade="%s"><td>%s</td><td>%s</td><td>%s</td>'
            '<td class="num">%s</td></tr>'
            % (_esc(grade), _badge(grade),
               _metric("span", "grade-count-%s" % grade, slot["count"]),
               _metric("span", "grade-bytes-%s" % grade, slot["bytes"],
                       model.format_bytes(slot["bytes"])),
               _esc(_percent(slot["bytes"], stats["candidate_bytes"]))))
    note = ('<p class="muted">可删合计只累加"安全可删"与"谨慎"，禁止项不计入可回收体积。'
            '分级是建议，不是删除后的安全保证。</p>')
    return (_section_start("sec-grades", "分级分布") +
            _table(("分级", "数量", "体积", "占候选总体积"), rows) + note +
            "\n</section>\n")


def _render_candidates(stats, roots, candidates):
    rows = []
    for candidate in sorted(candidates, key=lambda item: (-int(item["bytes"]), item["path"])):
        rows.append(
            '<tr data-grade="%s" data-type="%s" data-bytes="%d">'
            '<td class="path">%s</td><td>%s</td><td>%s</td><td>%s</td>'
            '<td>%s</td><td>%s</td><td>%s</td></tr>'
            % (_esc(candidate["grade"]), _esc(candidate["type"]), int(candidate["bytes"]),
               _esc(_relative(candidate["path"], roots)), _esc(candidate["type"]),
               _metric("span", "row-bytes:%s" % _esc(candidate["path"]),
                       int(candidate["bytes"]), model.format_bytes(int(candidate["bytes"]))),
               _esc(candidate.get("mtime_utc") or ""), _badge(candidate["grade"]),
               _esc(candidate.get("reason") or ""),
               _evidence_html(candidate.get("evidence"))))

    grades = "".join('<option value="%s">%s</option>' % (_esc(grade),
                                                         _esc(model.GRADE_LABELS[grade]))
                     for grade in model.GRADES)
    types = "".join('<option value="%s">%s</option>' % (_esc(name), _esc(name))
                    for name, _, _ in stats["by_type"])
    controls = (
        '<div class="controls">'
        '<label>筛选 <input id="filter-text" type="search" placeholder="路径 / 理由 / 证据"></label>'
        '<label>分级 <select id="filter-grade"><option value="">全部</option>%s</select></label>'
        '<label>类型 <select id="filter-type"><option value="">全部</option>%s</select></label>'
        '<span class="muted">当前显示 <b id="visible-count">%d</b> 条'
        '（JS 只做筛选/排序，不重算任何合计）</span>'
        '</div>' % (grades, types, len(rows)))
    table = _table(
        ("路径", "类型", "体积", "最后修改（UTC）", "分级", "理由", "证据"), rows,
        attrs=' id="candidate-table"',
        sort_keys={"路径": "path", "类型": "type", "体积": "bytes",
                   "分级": "grade"}) if rows else _empty("没有候选条目。")
    return (_section_start("sec-candidates", "候选明细") + controls + table +
            "\n</section>\n")


def _render_docker(audit):
    parts = [_section_start("sec-docker", "Docker 审计")]
    if audit is None:
        parts.append(_empty("未审计：本次生成未提供 audit.json。"))
        parts.append(_empty_html(
            "补充审计：" + _code(
                "devdisk audit --docker --docker-fixture "
                "data/fixtures/docker_system_df/v24_standard.txt "
                "--out devdisk-out/audit.json")))
        parts.append("\n</section>\n")
        return "".join(parts)

    section = audit.get("docker") or {}
    status = section.get("status")
    if status == STATUS_NOT_REQUESTED:
        parts.append(_empty_html(
            "未请求：生成 audit.json 时未加 " + _code("--docker") + "。"))
    elif status == STATUS_OK:
        rows = []
        for label, field in docker_audit.DOCKER_TYPES:
            slot = section.get(field) or {}
            total = int(slot.get("total_bytes") or 0)
            reclaimable = int(slot.get("reclaimable_bytes") or 0)
            rows.append(
                '<tr data-docker-type="%s"><td>%s</td><td>%s</td><td>%s</td></tr>'
                % (_esc(field), _esc(label),
                   _metric("span", "docker-total:%s" % field, total,
                           model.format_bytes(total)),
                   _metric("span", "docker-reclaimable:%s" % field, reclaimable,
                           model.format_bytes(reclaimable))))
        parts.append(_table(("类型", "总体积", "可回收"), rows))
        suggestions = section.get("suggestions") or []
        if suggestions:
            items = "".join('<li><code>%s</code> — %s</li>'
                            % (_esc(item.get("command")), _esc(item.get("reason")))
                            for item in suggestions)
            parts.append('<p>建议命令（只生成、不执行；固定 <code>applied: false</code>）：</p>'
                         '<ul class="cmds">%s</ul>' % items)
    else:
        parts.append('<p class="warn">未审计（降级）：<code>%s</code> — %s</p>'
                     % (_esc(status), _esc(section.get("reason") or "无原因说明")))
        excerpt = section.get("raw_excerpt") or []
        if excerpt:
            parts.append('<pre class="raw">%s</pre>'
                         % _esc("\n".join(str(line) for line in excerpt)))
    parts.append("\n</section>\n")
    return "".join(parts)


def _render_git(audit):
    parts = [_section_start("sec-git", ".git 审计")]
    if audit is None:
        parts.append(_empty("未审计：本次生成未提供 audit.json。"))
        parts.append("\n</section>\n")
        return "".join(parts)

    section = audit.get("git") or {}
    status = section.get("status")
    if status == STATUS_NOT_REQUESTED:
        parts.append(_empty_html(
            "未请求：生成 audit.json 时未加 " + _code("--git") + "。"))
        parts.append("\n</section>\n")
        return "".join(parts)

    repos = section.get("repos") or []
    if not repos:
        parts.append(_empty_html("未发现 " + _code(".git") + " 目录。"))
    else:
        rows = []
        for index, repo in enumerate(repos, 1):
            label = repo.get("relative_path") or repo.get("path")
            repo_status = repo.get("status") or "unknown"
            if repo_status == STATUS_OK:
                detail = _metric("span", "git-objects-%d" % index,
                                 int(repo.get("objects_bytes") or 0),
                                 model.format_bytes(int(repo.get("objects_bytes") or 0)))
                extra = "pack %s 个 / 松散对象 %s 个" % (
                    _esc(repo.get("pack_files", 0)), _esc(repo.get("loose_objects", 0)))
            else:
                detail = '<span class="warn">%s</span>' % _esc(repo_status)
                extra = _esc(repo.get("reason") or "")
            rows.append('<tr data-git="%s"><td class="path">%s</td>'
                        '<td>%s</td><td>%s</td></tr>'
                        % (_esc(label), _esc(label), detail, extra))
        parts.append(_table(("仓库", "对象体积", "说明"), rows))
        suggestions = []
        for repo in repos:
            for item in repo.get("suggestions") or []:
                suggestions.append('<li><code>%s</code> — %s</li>'
                                   % (_esc(item.get("command")),
                                      _esc(item.get("reason"))))
        if suggestions:
            parts.append('<p>建议命令（只生成、不执行；%s 只读，固定 %s）：</p>'
                         '<ul class="cmds">%s</ul>'
                         % (_code(".git"), _code("applied: false"),
                            "".join(suggestions)))
        if status != STATUS_OK:
            parts.append('<p class="warn">%s 段整体状态：%s</p>'
                         % (_code(".git"), _esc(status)))
    parts.append("\n</section>\n")
    return "".join(parts)


def _render_batches(batches):
    parts = [_section_start("sec-batches", "隔离批次与撤销")]
    if not batches:
        parts.append(_empty_html(
            "未提供隔离批次清单（未传 " + _code("--batch") + "，也未传 "
            + _code("--trash") + "）。"))
        parts.append(_empty_html(
            _code("clean") + " 的批次摘要默认不落盘；要展示批次，请显式传 "
            + _code("--batch .trash/<batch-id>/manifest.json") + "（可重复），或传 "
            + _code("--trash .trash") + " 读取其中的 manifest。"))
        parts.append('<pre class="cmd">devdisk undo &lt;batch-id&gt; --trash &lt;隔离区路径&gt; --yes</pre>')
        parts.append("\n</section>\n")
        return "".join(parts)

    rows = []
    for batch in batches:
        entries = batch.get("entries") or []
        counts = {}
        for entry in entries:
            status = entry.get("status") or "pending"
            counts[status] = counts.get(status, 0) + 1
        total = sum(int(entry.get("bytes") or 0) for entry in entries)
        detail = "、".join("%s=%d" % (status, count)
                          for status, count in sorted(counts.items()))
        trash_root = batch.get("trash_root") or ""
        command = "devdisk undo %s --trash %s --yes" % (batch["batch_id"], trash_root)
        rows.append(
            '<tr data-batch="%s"><td class="path">%s</td><td>%s</td>'
            '<td>%d</td><td>%s</td><td>%s</td><td><code>%s</code></td></tr>'
            % (_esc(batch["batch_id"]), _esc(batch["batch_id"]),
               _esc(batch.get("state") or ""), len(entries),
               _esc(model.format_bytes(total)), _esc(detail), _esc(command)))
    parts.append(_table(("批次", "状态", "条目", "体积", "条目状态", "撤销命令"), rows))
    parts.append('<p class="muted">撤销按 manifest 逆序执行，移动前后都校验 tree digest；'
                 '目标已存在即冲突且不覆盖。默认遇到冲突整体停止，'
                 '`--continue-on-conflict` 才继续处理其余条目。</p>')
    parts.append("\n</section>\n")
    return "".join(parts)


def _render_disclaimer():
    return (_section_start("sec-disclaimer", "免责声明") +
            '<p>分级是基于路径、体积、修改时间与白名单元数据文件得出的<strong>建议</strong>，'
            '不构成删除后的安全保证；重建成本与项目上下文需自行确认。</p>'
            '<p>清理只把选中项移入隔离区，不做覆盖式删除；撤销前请保持隔离区完整。'
            '`.git` 与 Docker 只做只读审计，任何建议命令都不会被执行。</p>'
            "\n</section>\n")


# ------------------------------------------------------------------ 摘要


def summary_lines(stats, audit, target, top=None, batches=None):
    """CLI 摘要（与报告章节同源、同数值）。"""
    lines = ["报告已生成：%s" % _display_out(target)]
    lines.append("候选：%d 个，合计 %d 字节（%s）"
                 % (stats["candidate_count"], stats["candidate_bytes"],
                    model.format_bytes(stats["candidate_bytes"])))
    lines.append("可删合计（安全可删 + 谨慎）：%d 字节（%s）"
                 % (stats["reclaimable_bytes"],
                    model.format_bytes(stats["reclaimable_bytes"])))
    parts = []
    for grade in model.GRADES:
        slot = stats["by_grade"][grade]
        parts.append("%s %d 个（%s）"
                     % (model.GRADE_LABELS[grade], slot["count"],
                        model.format_bytes(slot["bytes"])))
    lines.append("分级：" + "；".join(parts) + "；禁止项不计入可回收体积")
    lines.append("跳过条目：%d 个" % stats["skipped_count"])
    limit = stats["ranking_limit"] if top is None else int(top)
    lines.append("排行：体积前 %d 条" % limit if limit else "排行：未列出（--top 0）")
    lines.append("隔离批次：%d 个" % len(batches or []))
    lines.append("Docker：%s" % docker_status_text(audit))
    lines.append("Git：%s" % git_status_text(audit))
    return lines


def docker_status_text(audit):
    if audit is None:
        return "未审计（未提供 audit.json）"
    section = audit.get("docker") or {}
    status = section.get("status")
    if status == STATUS_NOT_REQUESTED:
        return "未请求（未加 --docker）"
    if status == STATUS_OK:
        parts = []
        for label, field in docker_audit.DOCKER_TYPES:
            slot = section.get(field) or {}
            parts.append("%s %s" % (label, model.format_bytes(
                int(slot.get("total_bytes") or 0))))
        return "；".join(parts)
    return "未审计（%s：%s）" % (status, section.get("reason") or "")


def git_status_text(audit):
    if audit is None:
        return "未审计（未提供 audit.json）"
    section = audit.get("git") or {}
    status = section.get("status")
    if status == STATUS_NOT_REQUESTED:
        return "未请求（未加 --git）"
    repos = section.get("repos") or []
    total = sum(int(repo.get("objects_bytes") or 0) for repo in repos)
    return "%d 个仓库，对象合计 %s（%s）" % (
        len(repos), model.format_bytes(total), status)


def _display_out(target):
    return os.path.abspath(target) if target else ""


# ------------------------------------------------------------------ 静态资源

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 20px 48px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
    "PingFang SC", "Hiragino Sans GB", sans-serif;
  color: #1f2328; background: #f6f7f9; line-height: 1.55;
}
main { max-width: 1180px; margin: 0 auto; }
header.head {
  max-width: 1180px; margin: 0 auto; padding: 28px 0 16px;
  border-bottom: 1px solid #d8dee4;
}
header.head h1 { margin: 0 0 6px; font-size: 26px; }
header.head .lede { margin: 0; color: #57606a; }
h2 { margin: 34px 0 12px; font-size: 19px; padding-bottom: 6px;
     border-bottom: 1px solid #d8dee4; }
h3 { margin: 22px 0 8px; font-size: 16px; }
p { margin: 8px 0; }
.muted { color: #6e7781; font-size: 13px; }
.warn { color: #9a6700; font-weight: 600; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px; margin: 8px 0 12px; }
.card { background: #fff; border: 1px solid #d8dee4; border-radius: 10px;
  padding: 14px 16px; display: flex; flex-direction: column; gap: 2px; }
.card .label { color: #57606a; font-size: 13px; }
.card strong { font-size: 22px; font-variant-numeric: tabular-nums; }
.card .raw { color: #8c959f; font-size: 12px; }
table.grid, table.meta { width: 100%; border-collapse: collapse; background: #fff;
  border: 1px solid #d8dee4; border-radius: 10px; overflow: hidden; }
table.grid th, table.grid td, table.meta th, table.meta td {
  padding: 8px 10px; border-bottom: 1px solid #eaeef2; text-align: left;
  vertical-align: top; font-size: 13px; }
table.grid thead th { position: sticky; top: 0; background: #f0f3f6;
  font-weight: 600; z-index: 1; }
table.grid tbody tr:hover { background: #f6f8fa; }
table.meta th { width: 150px; color: #57606a; font-weight: 600; background: #fafbfc; }
td.path, .path { font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono",
  monospace; overflow-wrap: anywhere; }
table.grid td.path { min-width: 240px; }
span[data-metric], .card strong { white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 12px; border: 1px solid transparent; white-space: nowrap; }
.badge.safe { color: #116329; background: #dafbe1; border-color: #aceebb; }
.badge.caution { color: #9a6700; background: #fff8c5; border-color: #eac54f; }
.badge.forbidden { color: #cf222e; background: #ffebe9; border-color: #ff8182; }
ul.ev { margin: 0; padding-left: 16px; }
ul.ev li { margin: 1px 0; }
ul.ev code, .cmds code { background: #eff1f3; padding: 0 4px; border-radius: 4px; }
.evpath { color: #57606a; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
ul.cmds { margin: 6px 0; padding-left: 20px; }
pre.raw, pre.cmd { background: #fff; border: 1px solid #d8dee4; border-radius: 8px;
  padding: 10px 12px; overflow: auto; font-size: 12px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  margin: 10px 0; padding: 10px 12px; background: #fff; border: 1px solid #d8dee4;
  border-radius: 10px; font-size: 13px; }
.controls input[type="search"] { width: 240px; padding: 5px 8px;
  border: 1px solid #d0d7de; border-radius: 6px; font-size: 13px; }
.controls select { padding: 5px 8px; border: 1px solid #d0d7de; border-radius: 6px;
  font-size: 13px; background: #fff; }
@media (max-width: 720px) {
  body { padding: 0 10px 32px; }
  .controls input[type="search"] { width: 100%; }
}
"""

SCRIPT = """
(function () {
  var table = document.getElementById('candidate-table');
  if (!table) { return; }
  var tbody = table.tBodies[0];
  var text = document.getElementById('filter-text');
  var grade = document.getElementById('filter-grade');
  var type = document.getElementById('filter-type');
  var visible = document.getElementById('visible-count');
  var rows = Array.prototype.slice.call(tbody.rows);

  function apply() {
    var query = (text.value || '').trim().toLowerCase();
    var wantedGrade = grade.value;
    var wantedType = type.value;
    var shown = 0;
    rows.forEach(function (row) {
      var ok = (!query || row.textContent.toLowerCase().indexOf(query) !== -1) &&
        (!wantedGrade || row.getAttribute('data-grade') === wantedGrade) &&
        (!wantedType || row.getAttribute('data-type') === wantedType);
      row.style.display = ok ? '' : 'none';
      if (ok) { shown += 1; }
    });
    if (visible) { visible.textContent = String(shown); }
  }

  [text, grade, type].forEach(function (node) {
    if (node) { node.addEventListener('input', apply); node.addEventListener('change', apply); }
  });

  table.tHead.addEventListener('click', function (event) {
    var th = event.target.closest('th[data-sort]');
    if (!th) { return; }
    var key = th.getAttribute('data-sort');
    var dir = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
    th.setAttribute('data-dir', dir);
    var sign = dir === 'asc' ? 1 : -1;
    rows.sort(function (a, b) {
      var av, bv;
      if (key === 'bytes') {
        av = parseInt(a.getAttribute('data-bytes'), 10);
        bv = parseInt(b.getAttribute('data-bytes'), 10);
      } else if (key === 'path') {
        av = a.cells[0].textContent;
        bv = b.cells[0].textContent;
      } else if (key === 'type') {
        av = a.getAttribute('data-type');
        bv = b.getAttribute('data-type');
      } else {
        av = a.getAttribute('data-grade');
        bv = b.getAttribute('data-grade');
      }
      if (av < bv) { return -1 * sign; }
      if (av > bv) { return 1 * sign; }
      return 0;
    });
    rows.forEach(function (row) { tbody.appendChild(row); });
  });
})();
"""
