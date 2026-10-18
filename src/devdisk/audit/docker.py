# -*- coding: utf-8 -*-
"""Docker 只读审计（模块③，M3）。

只调用 `docker system df`（`plan/03` 模块③）。任何 prune 命令都只作为建议输出，
固定 `applied: false`，绝不执行。

解析口径（`data/README.md` §四 第 12/13 条）：

- 固定列 `TYPE TOTAL ACTIVE SIZE RECLAIMABLE`；
- 体积按 **1024 进制**换算（`1kB` = 1024 字节）；
- `RECLAIMABLE` 可能是 `1.2GB (45%)`，只取前半段；
- 只有表头（无数据行）是合法输入 → 四类体积为 0；
- 缺列 / 无法解析 → `error:docker_parse`（附原文前 20 行），不填空值。
"""

import os
import subprocess

from .. import config
from .. import model
from . import (
    STATUS_DOCKER_PARSE,
    STATUS_DOCKER_TIMEOUT,
    STATUS_DOCKER_UNAVAILABLE,
    STATUS_OK,
)

DOCKER_COMMAND = ("docker", "system", "df")

# 显式禁用 Docker 探测（CI / 无 Docker 环境的确定性降级入口）。
# Windows 上 `docker.exe` 常登记在 `App Paths` 注册表里，查找优先级高于 PATH——
# 仅用 PATH 隔离模拟"机器上没有 Docker"在 Windows runner 上不成立（CI 首跑实录：
# ubuntu 两个矩阵过、windows 两个矩阵挂，同一步骤）。故提供显式开关。
DISABLE_ENV = "DEVDISK_DISABLE_DOCKER"
DISABLED_VALUES = ("1", "true", "yes", "on")

# Docker 的类型名 → 契约字段名。顺序固定，保证同一输入产出同一 JSON。
DOCKER_TYPES = (
    ("Images", "images"),
    ("Containers", "containers"),
    ("Local Volumes", "volumes"),
    ("Build Cache", "build_cache"),
)

# 每类资源对应的建议命令（只生成，不执行；`plan/03` 模块③）。
PRUNE_COMMANDS = {
    "images": "docker image prune",
    "containers": "docker container prune",
    "volumes": "docker volume prune",
    "build_cache": "docker builder prune",
}

# 体积单位一律 1024 进制。
UNIT_FACTORS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024 ** 2,
    "GB": 1024 ** 3,
    "TB": 1024 ** 4,
    "PB": 1024 ** 5,
}

RAW_EXCERPT_LIMIT = 20

EMPTY_SECTION = {"total_bytes": 0, "reclaimable_bytes": 0}


class DockerParseError(Exception):
    """`docker system df` 输出不符合固定列约定。"""


def parse_size(text):
    """把 `1.234GB` / `12.3kB` / `0B` 解析为字节（1024 进制）。

    返回 int；无法解析抛 `DockerParseError`。十进制小数用整数运算完成，
    避免浮点在 GB 量级丢字节。
    """
    raw = (text or "").strip()
    if not raw:
        raise DockerParseError("体积为空")
    split_at = 0
    while split_at < len(raw) and (raw[split_at].isdigit() or raw[split_at] == "."):
        split_at += 1
    number = raw[:split_at]
    unit = raw[split_at:].strip().upper()
    if not number or not unit:
        raise DockerParseError("体积格式无法识别：%r" % (text,))
    if unit not in UNIT_FACTORS:
        raise DockerParseError("未知体积单位：%r（%r）" % (unit, text))
    if number.count(".") > 1:
        raise DockerParseError("体积格式无法识别：%r" % (text,))
    whole, _, fraction = number.partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        raise DockerParseError("体积格式无法识别：%r" % (text,))
    factor = UNIT_FACTORS[unit]
    scaled = int(whole) * factor
    if fraction:
        # 小数部分按 1024 进制截断到整字节（与 Docker 展示精度一致）。
        scaled += (int(fraction) * factor) // (10 ** len(fraction))
    return scaled


def parse_reclaimable(text):
    """`1.2GB (45%)` → 1288490188；`0B` → 0。"""
    value = (text or "").strip()
    if not value:
        raise DockerParseError("RECLAIMABLE 为空")
    if value.endswith(")") and "(" in value:
        value = value[:value.rindex("(")].strip()
    return parse_size(value)


def _split_type(tokens):
    """从行首切出类型名，返回 (type_name, rest_tokens)。"""
    for name, _ in DOCKER_TYPES:
        parts = name.split()
        if [token.lower() for token in tokens[:len(parts)]] == [p.lower() for p in parts]:
            return name, tokens[len(parts):]
    return None, tokens


def parse_df(text):
    """解析 `docker system df` 的完整 stdout。

    返回 `{field: {"total_bytes": int, "reclaimable_bytes": int}}`；
    无法解析抛 `DockerParseError`。缺整类视为不合法（固定列意味着四类齐全）。
    """
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        raise DockerParseError("输出为空（未找到表头）")

    header = lines[0].split()
    expected = ["TYPE", "TOTAL", "ACTIVE", "SIZE", "RECLAIMABLE"]
    if [token.upper() for token in header] != expected:
        raise DockerParseError("表头不符合固定列：%r" % (lines[0],))

    parsed = {}
    for line in lines[1:]:
        tokens = line.split()
        type_name, rest = _split_type(tokens)
        if type_name is None:
            raise DockerParseError("未知资源类型：%r" % (line,))
        if len(rest) not in (4, 5):
            raise DockerParseError("列数不符（%s）：%r" % (type_name, line))
        try:
            int(rest[0])
            int(rest[1])
        except ValueError:
            raise DockerParseError("TOTAL/ACTIVE 不是整数：%r" % (line,))
        total = parse_size(rest[2])
        reclaimable = parse_reclaimable(" ".join(rest[3:]))
        field = dict(DOCKER_TYPES)[type_name]
        parsed[field] = {"total_bytes": total, "reclaimable_bytes": reclaimable}

    sections = {}
    data_lines = lines[1:]
    for _, field in DOCKER_TYPES:
        if field in parsed:
            sections[field] = parsed[field]
        elif data_lines:
            # 有数据行却整类缺失 → 输出形态变了，按解析失败处理（不填空值）。
            raise DockerParseError("缺少资源类型：%s" % field)
        else:
            # 只有表头（空 Docker 场景）是合法输入：四类体积为 0。
            sections[field] = dict(EMPTY_SECTION)
    return sections


def build_suggestions(sections):
    """只为有可回收体积的资源生成建议命令，全部 `applied: false`。"""
    suggestions = []
    for _, field in DOCKER_TYPES:
        reclaimable = sections[field]["reclaimable_bytes"]
        if reclaimable <= 0:
            continue
        suggestions.append({
            "command": PRUNE_COMMANDS[field],
            "applied": False,
            "reason": "%s 有 %s 可回收（本工具只生成命令，不执行）"
                      % (field, model.format_bytes(reclaimable)),
        })
    return suggestions


def _raw_excerpt(text):
    lines = (text or "").splitlines()
    return lines[:RAW_EXCERPT_LIMIT]


def parse_text(text):
    """解析文本 → `(status, section_dict)`，不抛异常（契约层友好）。"""
    try:
        sections = parse_df(text)
    except DockerParseError as exc:
        return STATUS_DOCKER_PARSE, error_section(
            text, "docker system df 输出无法解析：%s。"
                  "下一步：手动运行 `docker system df` 确认版本格式。" % exc)

    payload = {"status": STATUS_OK}
    payload.update(sections)
    payload["suggestions"] = build_suggestions(sections)
    return STATUS_OK, payload


def error_section(text, reason=None):
    """构造 `error:docker_parse` 段：不填四类体积（避免被读成"没有可清理内容"）。"""
    return {
        "status": STATUS_DOCKER_PARSE,
        "reason": reason or ("docker system df 输出无法解析。"
                             "下一步：手动运行 `docker system df` 确认版本格式。"),
        "raw_excerpt": _raw_excerpt(text),
        "suggestions": [],
    }


def parse_fixture(path):
    """读取一个 fixture 文件并解析（验收主路径，不依赖 Docker 安装）。"""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse_text(handle.read())


def docker_disabled_by_env():
    """环境变量显式禁用 Docker 探测（确定性降级入口，见 `DISABLE_ENV`）。"""
    raw = (os.environ.get(DISABLE_ENV) or "").strip().lower()
    return raw in DISABLED_VALUES


def _missing_section(reason, status=STATUS_DOCKER_UNAVAILABLE):
    payload = {
        "status": status,
        "reason": reason + " 下一步：安装并启动 Docker Desktop，或去掉 `--docker` 只审计 .git。",
        "suggestions": [],
    }
    for _, field in DOCKER_TYPES:
        payload[field] = dict(EMPTY_SECTION)
    return payload


def run_docker(timeout=None):
    """调用 `docker system df` 并解析。返回契约里的 `docker` 段。

    缺失/失败/超时 → `skipped:docker_unavailable` / `skipped:docker_timeout`；
    输出无法解析 → `error:docker_parse`。绝不静默返回 0 体积。
    """
    if timeout is None:
        timeout = config.DEFAULT_DOCKER_TIMEOUT
    if docker_disabled_by_env():
        return _missing_section(
            "已由环境变量 %s 显式禁用 Docker 探测。" % DISABLE_ENV)
    try:
        completed = subprocess.run(
            list(DOCKER_COMMAND),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return _missing_section("未找到 docker 命令（Docker 未安装或不在 PATH 中）。")
    except OSError as exc:
        return _missing_section("调用 docker 失败：%s。" % exc)
    except subprocess.TimeoutExpired:
        return _missing_section(
            "docker system df 超过 %d 秒未返回。" % timeout, status=STATUS_DOCKER_TIMEOUT)

    if completed.returncode != 0:
        message = (completed.stderr or "").strip().splitlines()
        detail = message[0] if message else "无 stderr 输出"
        return _missing_section(
            "docker system df 执行失败（退出码 %d）：%s。" % (completed.returncode, detail))

    status, payload = parse_text(completed.stdout)
    return payload


def fixture_names(data_dir=None):
    """列出入仓的 Docker fixture 文件名（排序、确定性）。"""
    from ..rules import resolve_data_dir

    root = os.path.join(data_dir or resolve_data_dir(), "fixtures", "docker_system_df")
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root) if name.endswith(".txt"))
