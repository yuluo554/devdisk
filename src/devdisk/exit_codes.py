# -*- coding: utf-8 -*-
"""退出码语义（单点定义）。

任何模块都不得硬编码退出码数字；一律引用本模块常量。
语义变更必须同步 `plan/02-架构与技术选型.md` §5.2 与 README 的退出码表。
"""

OK = 0
REFUSED = 1
USAGE = 2
NOT_READY = 3
DEGRADED = 4
UNSAFE = 5

NAMES = {
    OK: "OK",
    REFUSED: "REFUSED",
    USAGE: "USAGE",
    NOT_READY: "NOT_READY",
    DEGRADED: "DEGRADED",
    UNSAFE: "UNSAFE",
}

DESCRIPTIONS = {
    OK: "正常完成",
    REFUSED: "可预期的业务拒绝或冲突",
    USAGE: "用法错误、未知命令或参数",
    NOT_READY: "骨架期该子命令尚未实现",
    DEGRADED: "可选能力不可用（如 Docker CLI 缺失或输出无法解析）",
    UNSAFE: "安全拒绝（跨设备、越界路径、禁止操作）",
}
