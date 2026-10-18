# -*- coding: utf-8 -*-
"""模块⑤（单文件 HTML 报告）的验收测试。

门的定义见 `plan/05-里程碑与发布.md` M5 DoD 与 `plan/03` 模块⑤"验收基准"：

- 单文件 HTML、零外链（常驻断言 `assert_no_external_resources`）；
- 排行、聚合、分级合计与真值逐项一致；
- 同输入双跑逐字节一致；
- Docker 缺失时显式展示降级说明（"未审计" vs 0 要区分）；
- 路径转义样本（`<>&"`）不破坏页面；
- 编排三方对账：CLI stdout = 编排直调 = 报告章节数值；
- `report` 退役 `NOT_READY` + 离线冒烟 + `selftest.RETIRED_COMMANDS` 登记；
- 静态守门：报告模块无网络 import、无外链字符串常量。
"""

import io
import json
import os
import re
import shutil
from contextlib import redirect_stderr, redirect_stdout

import pytest

from devdisk import cli
from devdisk import exit_codes
from devdisk import model
from devdisk import pipeline
from devdisk import report
from devdisk.serialize import write_json_atomic

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRIC_RE = re.compile(r'data-metric="([^"]+)"\s+data-value="(-?\d+)"')


# ---------------------------------------------------------------- 夹具


def candidate(path, size, grade=model.GRADE_SAFE, **overrides):
    payload = {
        "path": path,
        "type": "node_modules",
        "bytes": size,
        "mtime_utc": "1970-01-01T00:00:01Z",
        "project_root": os.path.dirname(path),
        "is_symlink": False,
        "symlink_kind": "none",
        "file_count": 1,
        "directory_count": 1,
        "grade": grade,
        "reason": "锁文件存在，依赖可重建",
        "evidence": [{"kind": "lockfile", "path": "package-lock.json",
                      "detail": "锁文件存在"}],
    }
    payload.update(overrides)
    return payload


def scan_payload(root, candidates, skipped=None):
    by_grade = dict((grade, 0) for grade in model.GRADES)
    by_type = {}
    for item in candidates:
        if item["grade"] in by_grade:
            by_grade[item["grade"]] += item["bytes"]
        by_type[item["type"]] = by_type.get(item["type"], 0) + item["bytes"]
    return {
        "schema_version": 1,
        "generated_by": "devdisk test",
        "roots": [str(root)],
        "candidates": candidates,
        "skipped": skipped or [],
        "totals": {
            "candidate_bytes": sum(item["bytes"] for item in candidates),
            "candidate_count": len(candidates),
            "by_type": by_type,
            "by_grade": by_grade,
            "other_bytes": 0,
            "other_count": 0,
            "ignored_count": 0,
            "skipped_count": len(skipped or []),
        },
    }


def write_scan(tmp_path, payload, name="scan.json"):
    target = tmp_path / name
    write_json_atomic(str(target), payload)
    return str(target)


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture()
def project(tmp_path):
    """两项目三候选：safe / caution / forbidden 各一条，体积刻意不同。"""
    root = tmp_path / "root"
    payload = scan_payload(root, [
        candidate(str(root / "demo-web" / "node_modules"), 4096,
                  project_root=str(root / "demo-web")),
        candidate(str(root / "demo-api" / ".venv"), 3072, grade=model.GRADE_CAUTION,
                  type=".venv", project_root=str(root / "demo-api")),
        candidate(str(root / "demo-infra" / ".git"), 2048,
                  grade=model.GRADE_FORBIDDEN, type="git",
                  project_root=str(root / "demo-infra"),
                  reason="禁止清理：Git 版本库元数据"),
    ], skipped=[{"path": str(root / "demo-web" / "link-outside"),
                 "reason": "outside_root"}])
    return payload, write_scan(tmp_path, payload), root


def metrics(text):
    values = {}
    for key, value in METRIC_RE.findall(text):
        values.setdefault(key, int(value))
    return values


def section(text, section_id):
    return text.split('id="%s"' % section_id, 1)[1].split("</section>", 1)[0]


# ---------------------------------------------------------------- 入口与契约


def test_report_is_retired_from_not_ready():
    assert "report" not in cli.NOT_READY_COMMANDS
    assert "report" in repr(cli.NOT_READY_COMMANDS) or cli.NOT_READY_COMMANDS == {}


def test_selftest_registers_report_as_retired():
    from devdisk import selftest

    assert "report" in selftest.RETIRED_COMMANDS


def test_cli_requires_input_and_html(tmp_path):
    code, _, err = run_cli(["report", "--input", str(tmp_path / "nope.json")])
    assert code == exit_codes.USAGE
    assert "--html" in err or "html" in err


def test_missing_scan_returns_usage(tmp_path):
    code, _, err = run_cli(["report", "--input", str(tmp_path / "missing.json"),
                            "--html", str(tmp_path / "r.html")])
    assert code == exit_codes.USAGE
    assert "不存在" in err
    assert "下一步" in err
    assert not (tmp_path / "r.html").exists()


def test_broken_scan_returns_usage(tmp_path):
    bad = tmp_path / "scan.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _, err = run_cli(["report", "--input", str(bad),
                            "--html", str(tmp_path / "r.html")])
    assert code == exit_codes.USAGE
    assert "无法解析" in err


def test_scan_without_grades_is_refused(tmp_path):
    """M1 的占位 scan（grade=null）不能静默渲染成"未知等级"。"""
    payload = scan_payload(tmp_path, [candidate(str(tmp_path / "n"), 10,
                                                grade=None)])
    path = write_scan(tmp_path, payload)
    code, _, err = run_cli(["report", "--input", path, "--html", str(tmp_path / "r.html")])
    assert code == exit_codes.USAGE
    assert "分级" in err or "grade" in err


def test_totals_drift_is_refused(tmp_path):
    """scan.json 内部自相矛盾（汇总 ≠ 明细）时拒绝渲染。"""
    payload = scan_payload(tmp_path, [candidate(str(tmp_path / "a"), 100)])
    payload["totals"]["candidate_bytes"] = 999
    path = write_scan(tmp_path, payload)
    code, _, err = run_cli(["report", "--input", path, "--html", str(tmp_path / "r.html")])
    assert code == exit_codes.USAGE
    assert "不一致" in err


def test_unwritable_output_returns_usage(project, tmp_path, monkeypatch):
    _, scan_path, _ = project

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(report, "write_text_atomic", boom, raising=False)
    monkeypatch.setattr(pipeline, "write_text_atomic", boom)
    code, _, err = run_cli(["report", "--input", scan_path,
                            "--html", str(tmp_path / "r.html")])
    assert code == exit_codes.USAGE
    assert "写盘失败" in err


def test_report_writes_nothing_on_render_failure(project, tmp_path, monkeypatch):
    """渲染阶段抛错时不得留下半成品。"""
    _, scan_path, _ = project
    target = tmp_path / "r.html"

    def boom(document):
        raise report.ReportError("模拟渲染失败")

    monkeypatch.setattr(report, "assert_no_external_resources", boom)
    code, _, err = run_cli(["report", "--input", scan_path, "--html", str(target)])
    assert code == exit_codes.USAGE
    assert "模拟渲染失败" in err
    assert not target.exists()


# ---------------------------------------------------------------- 零外链


def test_zero_external_resources(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    code, _, err = run_cli(["report", "--input", scan_path, "--html", str(target)])
    assert code == exit_codes.OK, err
    text = target.read_text(encoding="utf-8")
    assert report.assert_no_external_resources(text) == text
    lowered = text.lower()
    for needle in ("<link", "<script src", "http://", "https://", "@import"):
        assert needle not in lowered, needle


@pytest.mark.parametrize("snippet", [
    '<link rel="stylesheet" href="https://cdn.example/x.css">',
    '<script src="https://cdn.example/x.js"></script>',
    "<style>body{background:url(//cdn.example/a.png)}</style>",
    "<style>@import url(x.css);</style>",
    '<img src="https://cdn.example/a.png">',
])
def test_external_guard_rejects(snippet):
    with pytest.raises(report.ReportError) as excinfo:
        report.assert_no_external_resources(snippet)
    assert "零外链" in str(excinfo.value)


def test_external_guard_accepts_plain_document(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    assert pipeline.run_report(scan_path, str(target))[0] == exit_codes.OK
    assert report.assert_no_external_resources(target.read_text(encoding="utf-8"))


def test_render_rejects_poisoned_style(project, tmp_path, monkeypatch):
    """零外链断言必须是渲染的出口条件，而不是"事后检查"。

    把内联样式换成带 `@import` 的内容：只要 `render_html` 不再调用
    `assert_no_external_resources`，本测试就会变红（变异验证用）。
    """
    payload, _, _ = project
    monkeypatch.setattr(report, "STYLE", "@import url(theme.css);\nbody{}")
    with pytest.raises(report.ReportError) as excinfo:
        report.render_html(payload, top=10)
    assert "零外链" in str(excinfo.value)


def test_render_rejects_poisoned_script(project, tmp_path, monkeypatch):
    payload, _, _ = project
    monkeypatch.setattr(report, "SCRIPT", 'var cdn = "https://cdn.example/x.js";')
    with pytest.raises(report.ReportError) as excinfo:
        report.render_html(payload, top=10)
    assert "零外链" in str(excinfo.value)


def test_pipeline_maps_guard_failure_to_usage(project, tmp_path, monkeypatch):
    """零外链失败经编排层映射为退出码 2，且不写出半成品。"""
    payload, scan_path, _ = project
    monkeypatch.setattr(report, "SCRIPT", 'var cdn = "https://cdn.example/x.js";')
    target = tmp_path / "r.html"
    code, _, err = run_cli(["report", "--input", scan_path, "--html", str(target)])
    assert code == exit_codes.USAGE
    assert "零外链" in err
    assert not target.exists()


def test_promise_wording_guard():
    with pytest.raises(report.ReportError) as excinfo:
        report.assert_no_promise_wording("<p>删除后必然安全</p>")
    assert "承诺" in str(excinfo.value)


def test_report_does_not_promise_safety(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    text = target.read_text(encoding="utf-8")
    report.assert_no_promise_wording(text)
    assert "建议" in text and "保证" in text


# ---------------------------------------------------------------- 转义


HOSTILE = 'evil <b>&"</b> <script>alert("x")</script>'


def test_escaping_covers_paths_reasons_evidence(tmp_path):
    root = tmp_path / "root"
    payload = scan_payload(root, [
        candidate(str(tmp_path / "root" / HOSTILE), 512,
                  project_root=str(root / HOSTILE),
                  reason=HOSTILE,
                  evidence=[{"kind": "lockfile", "path": HOSTILE, "detail": HOSTILE}]),
    ], skipped=[{"path": HOSTILE, "reason": "outside_root", "detail": HOSTILE}])
    scan_path = write_scan(tmp_path, payload)
    target = tmp_path / "r.html"
    code, _, err = run_cli(["report", "--input", scan_path, "--html", str(target)])
    assert code == exit_codes.OK, err

    text = target.read_text(encoding="utf-8")
    assert HOSTILE not in text
    assert "<script>alert(" not in text
    assert "&lt;script&gt;" in text
    assert text.count("<section") == len(report.SECTION_ORDER)
    assert report.assert_no_external_resources(text)


def test_escaping_keeps_page_readable(tmp_path):
    """转义后的样本必须仍能被检索到（不是抹掉内容）。"""
    root = tmp_path / "root"
    payload = scan_payload(root, [candidate(str(root / "a"), 10, type="a<b>")])
    scan_path = write_scan(tmp_path, payload)
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    text = target.read_text(encoding="utf-8")
    assert "a&lt;b&gt;" in text


# ---------------------------------------------------------------- 数值对账


def test_numbers_match_input(project, tmp_path):
    payload, scan_path, _ = project
    target = tmp_path / "r.html"
    code, stats, lines = pipeline.run_report(scan_path, str(target), top=10)
    assert code == exit_codes.OK
    text = target.read_text(encoding="utf-8")
    values = metrics(text)

    totals = payload["totals"]
    assert values["candidate-count"] == totals["candidate_count"] == 3
    assert values["candidate-bytes"] == totals["candidate_bytes"] == 4096 + 3072 + 2048
    assert values["reclaimable-bytes"] == 4096 + 3072
    for grade in model.GRADES:
        assert values["grade-bytes-%s" % grade] == totals["by_grade"][grade]
    for name, size in totals["by_type"].items():
        assert values["type-bytes:%s" % name] == size

    # 排行顺序：体积降序，同体积按路径字典序。
    rels = re.findall(r'data-rel="([^"]*)"', text)
    assert rels == ["demo-web/node_modules", "demo-api/.venv", "demo-infra/.git"]
    assert values["rank-bytes-1"] == 4096
    assert values["rank-bytes-3"] == 2048
    assert values["skipped-count"] == 1


def test_forbidden_is_not_reclaimable(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    text = target.read_text(encoding="utf-8")
    values = metrics(text)
    forbidden = values["grade-bytes-forbidden"]
    assert forbidden > 0
    assert values["reclaimable-bytes"] == values["candidate-bytes"] - forbidden


def test_project_aggregate(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    values = metrics(target.read_text(encoding="utf-8"))
    # 按体积降序：demo-web 4096 > demo-api 3072 > demo-infra 2048。
    assert values["project-bytes-1"] == 4096
    assert values["project-bytes-2"] == 3072
    assert values["project-bytes-3"] == 2048


def test_top_boundaries(project, tmp_path):
    _, scan_path, _ = project
    for top, expected in ((1, 1), (2, 2), (0, 0), (99, 3)):
        target = tmp_path / ("r-%d.html" % top)
        code, stats, _ = pipeline.run_report(scan_path, str(target), top=top)
        assert code == exit_codes.OK
        text = target.read_text(encoding="utf-8")
        assert len(re.findall(r'data-rel="', text)) == expected
        assert stats["ranking_limit"] == top
        # 总览与分级不受 --top 影响。
        assert metrics(text)["candidate-bytes"] == 4096 + 3072 + 2048


def test_negative_top_is_refused(project, tmp_path):
    _, scan_path, _ = project
    code, _, err = run_cli(["report", "--input", scan_path,
                            "--html", str(tmp_path / "r.html"), "--top", "-1"])
    assert code == exit_codes.USAGE
    assert "top" in err.lower()


def test_zero_rounding_does_not_change_sums(tmp_path):
    """体积展示四舍五入，但聚合必须基于原始字节。"""
    root = tmp_path / "root"
    payload = scan_payload(root, [
        candidate(str(root / "a"), 1),
        candidate(str(root / "b"), 1),
        candidate(str(root / "c"), 1),
    ])
    scan_path = write_scan(tmp_path, payload)
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    values = metrics(target.read_text(encoding="utf-8"))
    assert values["candidate-bytes"] == 3
    assert values["reclaimable-bytes"] == 3
    assert values["type-bytes:node_modules"] == 3


def test_ranking_uses_raw_bytes_not_display(tmp_path):
    """两条候选的展示值相同（1.00 MiB），排行仍按原始字节降序。"""
    root = tmp_path / "root"
    payload = scan_payload(root, [
        candidate(str(root / "small"), 1048575),
        candidate(str(root / "large"), 1048577),
    ])
    scan_path = write_scan(tmp_path, payload)
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target), top=10)
    text = target.read_text(encoding="utf-8")
    rels = re.findall(r'data-rel="([^"]*)"', text)
    assert rels == ["large", "small"]
    values = metrics(text)
    assert values["candidate-bytes"] == 1048575 + 1048577
    assert values["rank-bytes-1"] == 1048577


# ---------------------------------------------------------------- 确定性


def test_two_runs_are_byte_identical(project, tmp_path):
    _, scan_path, _ = project
    first, second = tmp_path / "a.html", tmp_path / "b.html"
    pipeline.run_report(scan_path, str(first), as_of="2026-01-01T00:00:00Z")
    pipeline.run_report(scan_path, str(second), as_of="2026-01-01T00:00:00Z")
    left, right = first.read_bytes(), second.read_bytes()
    assert left == right
    assert b"\r\n" not in left
    assert left.endswith(b"\n")


def test_report_has_no_generation_timestamp(project, tmp_path):
    """默认不写生成时刻：两次不同时刻生成仍要一致。"""
    _, scan_path, _ = project
    first, second = tmp_path / "a.html", tmp_path / "b.html"
    pipeline.run_report(scan_path, str(first))
    pipeline.run_report(scan_path, str(second))
    assert first.read_bytes() == second.read_bytes()
    text = first.read_text(encoding="utf-8")
    assert "生成时间" not in text
    assert "2026-" not in text.split('<section id="sec-source">', 1)[0]


def test_as_of_is_displayed_verbatim(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target), as_of="2026-01-01T00:00:00Z")
    text = target.read_text(encoding="utf-8")
    assert "2026-01-01T00:00:00Z" in section(text, "sec-source")


# ---------------------------------------------------------------- 区块与降级


def test_section_order_is_fixed(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    text = target.read_text(encoding="utf-8")
    actual = re.findall(r'<section id="(sec-[a-z]+)">', text)
    assert actual == list(report.SECTION_ORDER)
    assert len(actual) == 10


def test_missing_audit_shows_not_audited(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    text = target.read_text(encoding="utf-8")
    docker = section(text, "sec-docker")
    assert "未审计" in docker
    assert "docker-total:" not in docker
    git = section(text, "sec-git")
    assert "未审计" in git
    assert "git-objects-" not in git


def test_missing_audit_path_is_tolerated(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    code, _, _ = pipeline.run_report(scan_path, str(target),
                                     audit_path=str(tmp_path / "nope-audit.json"))
    assert code == exit_codes.OK
    assert "未审计" in section(target.read_text(encoding="utf-8"), "sec-docker")


def test_docker_degraded_reason_is_shown(project, tmp_path):
    _, scan_path, _ = project
    audit = {
        "schema_version": 1,
        "docker": {"status": "skipped:docker_unavailable",
                   "reason": "docker CLI 不可用", "suggestions": []},
        "git": {"status": "not_requested", "repos": []},
    }
    audit_path = tmp_path / "audit.json"
    write_json_atomic(str(audit_path), audit)
    target = tmp_path / "r.html"
    code, _, lines = pipeline.run_report(scan_path, str(target), audit_path=str(audit_path))
    assert code == exit_codes.OK
    docker = section(target.read_text(encoding="utf-8"), "sec-docker")
    assert "skipped:docker_unavailable" in docker
    assert "docker CLI 不可用" in docker
    assert "docker-total:" not in docker
    assert any("未审计" in line for line in lines)


def test_docker_ok_shows_four_sections(project, tmp_path):
    _, scan_path, _ = project
    audit = {
        "schema_version": 1,
        "docker": {
            "status": "ok",
            "images": {"total_bytes": 1024, "reclaimable_bytes": 512},
            "containers": {"total_bytes": 100, "reclaimable_bytes": 0},
            "volumes": {"total_bytes": 200, "reclaimable_bytes": 100},
            "build_cache": {"total_bytes": 0, "reclaimable_bytes": 0},
            "suggestions": [{"command": "docker image prune", "applied": False,
                             "reason": "images 有可回收"}],
        },
        "git": {"status": "ok", "repos": [
            {"path": "/x/.git", "relative_path": "demo-infra/.git", "status": "ok",
             "objects_bytes": 4096, "pack_files": 1, "loose_objects": 0,
             "suggestions": []},
        ]},
    }
    audit_path = tmp_path / "audit.json"
    write_json_atomic(str(audit_path), audit)
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target), audit_path=str(audit_path))
    text = target.read_text(encoding="utf-8")
    docker = section(text, "sec-docker")
    assert "docker-total:images" in docker
    assert "docker-reclaimable:images" in docker
    assert "docker image prune" in docker
    assert metrics(text)["docker-total:images"] == 1024
    assert metrics(text)["git-objects-1"] == 4096
    git = section(text, "sec-git")
    assert "skipped:gitdir_file" not in git


def test_batches_section_without_input(project, tmp_path):
    _, scan_path, _ = project
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    text = target.read_text(encoding="utf-8")
    batches = section(text, "sec-batches")
    assert "未提供" in batches
    assert "devdisk undo" in batches


def test_batches_from_manifest(project, tmp_path):
    _, scan_path, _ = project
    batch_dir = tmp_path / "trash" / "20260101-000000-a1b2c3"
    batch_dir.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "batch_id": "20260101-000000-a1b2c3",
        "trash_root": str(tmp_path / "trash"),
        "source_root": str(tmp_path / "root"),
        "created_from": {"mode": "select", "count": 1},
        "state": "complete",
        "entries": [{
            "original_path": str(tmp_path / "root" / "demo-web" / "node_modules"),
            "trash_path": str(batch_dir / "demo-web" / "node_modules"),
            "relative_path": "demo-web/node_modules",
            "bytes": 4096, "grade": "safe", "reason": "x", "mtime_utc": None,
            "tree_digest": "sha256:00", "status": "moved",
        }],
    }
    write_json_atomic(str(batch_dir / "manifest.json"), manifest)
    target = tmp_path / "r.html"
    code, _, _ = pipeline.run_report(scan_path, str(target),
                                     batches=[str(batch_dir / "manifest.json")])
    assert code == exit_codes.OK
    batches = section(target.read_text(encoding="utf-8"), "sec-batches")
    assert "20260101-000000-a1b2c3" in batches
    assert "moved=1" in batches
    assert "devdisk undo 20260101-000000-a1b2c3" in batches


def test_batches_from_trash_directory(project, tmp_path):
    _, scan_path, _ = project
    trash = tmp_path / "trash"
    for batch_id in ("20260102-000000-bbbbbb", "20260101-000000-aaaaaa"):
        batch_dir = trash / batch_id
        batch_dir.mkdir(parents=True)
        write_json_atomic(str(batch_dir / "manifest.json"), {
            "schema_version": 1, "batch_id": batch_id, "trash_root": str(trash),
            "source_root": str(tmp_path), "entries": [], "state": "undone",
        })
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target), trash=str(trash))
    text = target.read_text(encoding="utf-8")
    batches = section(text, "sec-batches")
    first = batches.index("20260101-000000-aaaaaa")
    second = batches.index("20260102-000000-bbbbbb")
    assert first < second  # 按批次 ID 排序，与目录枚举顺序无关


def test_broken_audit_is_refused(project, tmp_path):
    _, scan_path, _ = project
    bad = tmp_path / "audit.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _, err = run_cli(["report", "--input", scan_path,
                            "--html", str(tmp_path / "r.html"),
                            "--audit", str(bad)])
    assert code == exit_codes.USAGE
    assert "无法解析" in err


# ---------------------------------------------------------------- 三方对账


def test_three_way_reconciliation(project, tmp_path):
    """CLI stdout = 编排直调 = 报告章节数值。"""
    payload, scan_path, _ = project
    cli_target = tmp_path / "cli.html"
    code, stdout, err = run_cli(["report", "--input", scan_path,
                                 "--html", str(cli_target), "--top", "10"])
    assert code == exit_codes.OK, err

    direct_target = tmp_path / "direct.html"
    code, stats, lines = pipeline.run_report(scan_path, str(direct_target), top=10)
    assert code == exit_codes.OK
    assert direct_target.read_bytes() == cli_target.read_bytes()
    # 首行是输出路径，两者必然不同；其余摘要必须逐字一致。
    assert stdout.splitlines()[1:] == lines[1:]

    text = cli_target.read_text(encoding="utf-8")
    values = metrics(text)
    totals = payload["totals"]
    assert "候选：3 个，合计 9216 字节" in stdout
    assert values["candidate-count"] == 3
    assert values["candidate-bytes"] == 9216
    assert "可删合计（安全可删 + 谨慎）：7168 字节" in stdout
    assert values["reclaimable-bytes"] == 7168
    assert totals["candidate_bytes"] == 9216


def test_stdout_matches_synth_truth_gate(tmp_path):
    """在合成树上对账：CLI stdout 的候选数量/体积与报告锚点一致。"""
    tree = os.path.join(REPO_ROOT, "devdisk-out", "synth")
    if not os.path.isdir(tree):
        pytest.skip("合成树未生成（先运行 tools/gen_synth_tree.py）")
    scan_path = os.path.join(REPO_ROOT, "devdisk-out", "scan.json")
    if not os.path.isfile(scan_path):
        pytest.skip("候选清单未生成（先运行 devdisk scan）")
    target = tmp_path / "synth.html"
    code, stdout, err = run_cli(["report", "--input", scan_path,
                                 "--html", str(target), "--top", "5"])
    assert code == exit_codes.OK, err
    text = target.read_text(encoding="utf-8")
    with open(scan_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = "候选：%d 个，合计 %d 字节" % (payload["totals"]["candidate_count"],
                                             payload["totals"]["candidate_bytes"])
    assert expected in stdout
    assert metrics(text)["candidate-bytes"] == payload["totals"]["candidate_bytes"]
    assert len(re.findall(r'data-rel="', text)) == 5


# ---------------------------------------------------------------- 静态守门


def test_report_module_has_no_network_or_external_constants():
    with open(os.path.join(REPO_ROOT, "src", "devdisk", "report.py"),
              "r", encoding="utf-8") as handle:
        text = handle.read()
    for banned in ("import socket", "import urllib", "import http", "import requests",
                   "https://", "http://", "cdn.", "googleapis"):
        assert banned not in text, banned


def test_report_module_does_not_grade():
    """报告零判定：不得 import 分级引擎，也不得出现分级规则串。"""
    with open(os.path.join(REPO_ROOT, "src", "devdisk", "report.py"),
              "r", encoding="utf-8") as handle:
        text = handle.read()
    assert "from .grader" not in text
    assert "import grader" not in text
    assert "grade_result" not in text
    assert "load_grade_rules" not in text
    assert "evidence.py" not in text


def test_pipeline_report_does_not_rescan(project, tmp_path, monkeypatch):
    """report 只消费 JSON：不得触发扫描。"""
    _, scan_path, _ = project

    def boom(*args, **kwargs):
        raise AssertionError("report 不应重新扫描")

    monkeypatch.setattr(pipeline.Scanner, "scan", boom)
    code, _, err = run_cli(["report", "--input", scan_path,
                            "--html", str(tmp_path / "r.html")])
    assert code == exit_codes.OK, err


def test_report_does_not_write_outside_html(project, tmp_path):
    _, scan_path, _ = project
    before = sorted(os.listdir(str(tmp_path)))
    target = tmp_path / "r.html"
    pipeline.run_report(scan_path, str(target))
    after = sorted(os.listdir(str(tmp_path)))
    assert [name for name in after if name not in before] == ["r.html"]
