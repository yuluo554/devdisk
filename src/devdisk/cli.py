# -*- coding: utf-8 -*-
"""命令行入口。

纪律：
- 只做参数解析、编排调用与退出码映射，不含任何分级/体积/建议判定。
- 人类可读摘要走 stdout，错误与降级说明走 stderr。
- 中文文案；错误信息要说明"下一步可做什么"。
"""

import argparse
import sys

from . import __version__
from . import exit_codes
from .encoding import ensure_utf8_stdio

PROG = "devdisk"

# 骨架期未实现的子命令；能力落地那一棒必须同轮从本表移除并补离线冒烟。
# M5 收口后本表为空：五个子命令全部落地，`NOT_READY` 仅保留退出码语义。
NOT_READY_COMMANDS = {}


def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="开发者磁盘占用审计与安全清理（默认只读，清理走隔离区可回滚）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 正常 / 1 拒绝或冲突 / 2 用法错误 / 3 未就绪 / 4 能力降级 / 5 安全拒绝\n"
            "本工具不提供\"全选\"或\"一键清理全部\"；清理必须逐条或逐批显式选择。"
        ),
    )
    parser.add_argument("--version", action="version", version="%s %s" % (PROG, __version__))

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    scan = subparsers.add_parser("scan", help="扫描目录并分级，输出候选清单")
    scan.add_argument("roots", nargs="+", metavar="<root>", help="一个或多个扫描根目录")
    scan.add_argument("--out", default=None, help="候选清单输出路径（默认 devdisk-out/scan.json）")
    scan.add_argument("--format", choices=["json", "csv"], default="json", help="输出格式")
    scan.add_argument("--min-bytes", type=int, default=None, help="候选体积阈值（字节）")
    scan.add_argument("--keep", action="append", default=[], metavar="<path>",
                      help="显式保留路径，可重复（命中的条目分级为禁止）")
    scan.add_argument("--config", default=None, help="配置文件路径")

    audit = subparsers.add_parser("audit", help="Docker 与 .git 只读审计")
    audit.add_argument("--root", action="append", default=[], metavar="<root>",
                       help="扫描根，可重复（`--git` 时必填）")
    audit.add_argument("--docker", action="store_true", help="审计 Docker（只调用 docker system df）")
    audit.add_argument("--docker-fixture", default=None, metavar="<file>",
                       help="用固定的 docker system df 文本代替真实调用（离线验收主路径）")
    audit.add_argument("--git", action="store_true", help="审计 .git 对象（只读统计）")
    audit.add_argument("--out", default=None, help="审计结果输出路径（默认 devdisk-out/audit.json）")
    audit.add_argument("--config", default=None, help="配置文件路径（审计阈值）")

    clean = subparsers.add_parser("clean", help="把选中项移入隔离区（默认 dry-run）")
    clean.add_argument("--from", dest="from_scan", default=None, metavar="<scan.json>",
                       help="候选清单路径")
    clean.add_argument("--select", action="append", default=[], metavar="<path>",
                       help="显式选中的路径，可重复；不提供全选")
    clean.add_argument("--apply", action="store_true", help="真正执行移动（缺省为 dry-run）")
    clean.add_argument("--yes", action="store_true", help="跳过交互确认")
    clean.add_argument("--trash", default=None, help="隔离区根目录（默认 <root>/.trash）")
    clean.add_argument("--resume", default=None, metavar="<batch-id>",
                       help="按 manifest 续做中断的批次（与 --select 互斥）")
    clean.add_argument("--out", default=None, help="批次摘要输出路径（默认 devdisk-out/clean.json）")
    clean.add_argument("--config", default=None, help="配置文件路径")

    undo = subparsers.add_parser("undo", help="按 manifest 逆序撤销一个批次")
    undo.add_argument("batch_id", metavar="<batch-id>", help="批次 ID")
    undo.add_argument("--trash", default=None,
                      help="隔离区根目录（默认当前目录下的 .trash）")
    undo.add_argument("--yes", action="store_true", help="确认执行撤销")
    undo.add_argument("--stop-on-conflict", dest="stop_on_conflict",
                      action="store_true", default=True,
                      help="遇到冲突立即停止（默认）")
    undo.add_argument("--continue-on-conflict", dest="stop_on_conflict",
                      action="store_false",
                      help="遇到冲突继续处理其余条目")
    undo.add_argument("--out", default=None, help="撤销结果输出路径（默认 devdisk-out/undo.json）")

    report = subparsers.add_parser("report", help="生成单文件中文 HTML 报告")
    report.add_argument("--input", required=True, metavar="<scan.json>", help="候选清单路径")
    report.add_argument("--audit", default=None, metavar="<audit.json>", help="审计结果路径")
    report.add_argument("--html", required=True, metavar="<out.html>", help="报告输出路径")
    report.add_argument("--top", type=int, default=50, help="体积排行条数（默认 50）")
    report.add_argument("--as-of", dest="as_of", default=None, metavar="<text>",
                        help="固定展示的基准时间（默认不显示时间）")
    report.add_argument("--batch", action="append", default=[], metavar="<manifest.json>",
                        help="隔离批次 manifest，可重复（默认不展示批次）")
    report.add_argument("--trash", default=None, metavar="<dir>",
                        help="从隔离区目录读取 <dir>/*/manifest.json")

    subparsers.add_parser("selftest", help="自检：校验交付态不变量")

    return parser


def _emit(lines, stream=None):
    stream = stream or sys.stdout
    for line in lines:
        stream.write(line + "\n")


def _not_ready(command):
    milestone = NOT_READY_COMMANDS[command]
    sys.stderr.write(
        "%s：子命令 `%s` 尚未实现（计划里程碑 %s）。\n"
        "下一步：查看 `plan/05-里程碑与发布.md` 的退役表，或先运行 `%s selftest`。\n"
        % (PROG, command, milestone, PROG)
    )
    return exit_codes.NOT_READY


def _run_scan(args):
    """scan 子命令：只做参数传递与退出码映射。"""
    from . import pipeline

    try:
        code, payload, lines = pipeline.run_scan(
            roots=args.roots,
            out=args.out,
            output_format=args.format,
            min_bytes=args.min_bytes,
            keep_paths=args.keep,
            config_path=args.config,
        )
    except pipeline.ScanError as exc:
        sys.stderr.write("%s：%s\n" % (PROG, exc))
        return exc.exit_code
    except OSError as exc:
        sys.stderr.write(
            "%s：写盘失败：%s\n下一步：确认输出目录可写，或用 `--out` 指定其他路径。\n"
            % (PROG, exc)
        )
        return exit_codes.USAGE

    _emit(lines)
    return code


def _run_audit(args):
    """audit 子命令：只做参数传递与退出码映射。"""
    from . import pipeline

    try:
        code, payload, lines = pipeline.run_audit(
            roots=args.root,
            out=args.out,
            use_docker=args.docker or bool(args.docker_fixture),
            use_git=args.git,
            docker_fixture=args.docker_fixture,
            config_path=args.config,
        )
    except pipeline.ScanError as exc:
        sys.stderr.write("%s：%s\n" % (PROG, exc))
        return exc.exit_code
    except OSError as exc:
        sys.stderr.write(
            "%s：写盘失败：%s\n下一步：确认输出目录可写，或用 `--out` 指定其他路径。\n"
            % (PROG, exc)
        )
        return exit_codes.USAGE

    _emit(lines)
    return code


def _run_clean(args):
    """clean 子命令：只做参数传递与退出码映射。"""
    from . import pipeline

    try:
        code, payload, lines = pipeline.run_clean(
            from_scan=args.from_scan,
            select=args.select,
            apply=args.apply,
            yes=args.yes,
            trash=args.trash,
            resume_batch=args.resume,
            out=args.out,
            config_path=args.config,
        )
    except pipeline.ScanError as exc:
        sys.stderr.write("%s：%s\n" % (PROG, exc))
        return exc.exit_code
    except OSError as exc:
        sys.stderr.write(
            "%s：写盘失败：%s\n下一步：确认输出目录可写，或用 `--out` 指定其他路径。\n"
            % (PROG, exc)
        )
        return exit_codes.USAGE

    _emit(lines)
    return code


def _run_undo(args):
    """undo 子命令：只做参数传递与退出码映射。"""
    from . import pipeline

    try:
        code, payload, lines = pipeline.run_undo(
            batch_id=args.batch_id,
            trash=args.trash,
            yes=args.yes,
            out=args.out,
            stop_on_conflict=args.stop_on_conflict,
        )
    except pipeline.ScanError as exc:
        sys.stderr.write("%s：%s\n" % (PROG, exc))
        return exc.exit_code
    except OSError as exc:
        sys.stderr.write(
            "%s：写盘失败：%s\n下一步：确认输出目录可写，或用 `--out` 指定其他路径。\n"
            % (PROG, exc)
        )
        return exit_codes.USAGE

    _emit(lines)
    return code


def _run_report(args):
    """report 子命令：只做参数传递与退出码映射（渲染在 report.py）。"""
    from . import pipeline

    try:
        code, _stats, lines = pipeline.run_report(
            input_path=args.input,
            html_path=args.html,
            audit_path=args.audit,
            top=args.top,
            as_of=args.as_of,
            batches=args.batch,
            trash=args.trash,
        )
    except pipeline.ScanError as exc:
        sys.stderr.write("%s：%s\n" % (PROG, exc))
        return exc.exit_code
    except OSError as exc:
        sys.stderr.write(
            "%s：写盘失败：%s\n下一步：确认输出目录可写，或用 `--html` 指定其他路径。\n"
            % (PROG, exc)
        )
        return exit_codes.USAGE

    _emit(lines)
    return code


def main(argv=None):
    ensure_utf8_stdio()
    parser = build_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 的 --help / --version / 用法错误
        code = exc.code if isinstance(exc.code, int) else exit_codes.USAGE
        return code

    if args.command is None:
        parser.print_help(sys.stderr)
        sys.stderr.write("\n%s：缺少子命令。请使用 `%s --help` 查看可用命令。\n" % (PROG, PROG))
        return exit_codes.USAGE

    if args.command == "selftest":
        from .selftest import run_selftest

        code, lines = run_selftest(parser)
        _emit(lines)
        return code

    if args.command == "scan":
        return _run_scan(args)

    if args.command == "audit":
        return _run_audit(args)

    if args.command == "clean":
        return _run_clean(args)

    if args.command == "undo":
        return _run_undo(args)

    if args.command == "report":
        return _run_report(args)

    if args.command in NOT_READY_COMMANDS:
        return _not_ready(args.command)

    sys.stderr.write("%s：未知子命令 `%s`。\n" % (PROG, args.command))
    return exit_codes.USAGE


if __name__ == "__main__":
    sys.exit(main())
