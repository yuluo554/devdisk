# -*- coding: utf-8 -*-
"""配置项单点定义与配置文件加载。

`candidate_min_bytes` 等会随机器调优的参数只在此处给默认值；
`data/README.md` §四 只声明"默认值在配置文件中单点定义"，不写死数字。

配置文件格式：TOML 子集（`plan/03` 模块① 声明的 `devdisk.toml`）。Python 3.8
没有标准库 `tomllib`，因此这里实现**只读的最小解析器**，支持：

- 顶层与 `[section]` 段；
- `key = 123`（整数）、`key = "text"`（字符串）、`key = true/false`（布尔）；
- `key = ["a", "b"]`（字符串数组）。

不支持嵌套表、多行字符串与日期——遇到即报错，不静默忽略。
"""

import os

# 候选阈值：低于该体积的目录仍计入"其他"合计，但不进入候选清单与排行。
DEFAULT_CANDIDATE_MIN_BYTES = 16 * 1024 * 1024

# 默认中间产物目录（相对当前工作目录）。
DEFAULT_OUT_DIR = "devdisk-out"

# 隔离区默认目录名（位于扫描根下）。
DEFAULT_TRASH_DIRNAME = ".trash"

# `.git` 审计建议阈值。
DEFAULT_GIT_GC_LOOSE_THRESHOLD = 1000
DEFAULT_GIT_GC_BYTES_THRESHOLD = 512 * 1024 * 1024

# Docker CLI 调用超时（秒）。
DEFAULT_DOCKER_TIMEOUT = 30

# 配置文件默认文件名（在扫描根或当前目录查找）。
DEFAULT_CONFIG_FILENAME = "devdisk.toml"


class ConfigError(Exception):
    """配置文件缺失或格式错误。"""


def default_out_dir():
    return os.path.abspath(DEFAULT_OUT_DIR)


def _parse_value(raw, where):
    text = raw.strip()
    if not text:
        raise ConfigError("%s：缺少值" % where)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        items = []
        for part in inner.split(","):
            part = part.strip()
            if len(part) < 2 or part[0] != '"' or part[-1] != '"':
                raise ConfigError("%s：数组元素必须是字符串（%r）" % (where, part))
            items.append(part[1:-1])
        return items
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        raise ConfigError("%s：无法识别的值 %r（支持整数/字符串/布尔/字符串数组）"
                          % (where, text))


def parse_config_text(text, source="<config>"):
    """解析 TOML 子集，返回扁平化 dict（段名作为前缀 `section.key`）。"""
    payload = {}
    section = ""
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        where = "%s:%d" % (source, lineno)
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            if not section or "." in section:
                raise ConfigError("%s：只支持单层段名（%r）" % (where, section))
            continue
        if "=" not in stripped:
            raise ConfigError("%s：缺少 `=`（%r）" % (where, stripped))
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigError("%s：缺少键名" % where)
        full_key = "%s.%s" % (section, key) if section else key
        payload[full_key] = _parse_value(raw_value, where)
    return payload


def load_config(path):
    """加载配置文件；`path` 为 None 时返回空配置（全部走默认值）。"""
    if path is None:
        return {}
    if not os.path.isfile(path):
        raise ConfigError("配置文件不存在：%s。下一步：省略 --config 使用默认值。" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ConfigError("配置文件无法读取：%s（%s）" % (path, exc))
    return parse_config_text(text, source=path)


def _lookup(settings, *names):
    for name in names:
        if name in settings:
            return settings[name]
    return None


def candidate_min_bytes(settings):
    value = _lookup(settings, "scan.candidate_min_bytes", "candidate_min_bytes")
    return DEFAULT_CANDIDATE_MIN_BYTES if value is None else int(value)


def configured_keep_paths(settings):
    value = _lookup(settings, "scan.keep_paths", "keep_paths")
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def data_dir(settings):
    return _lookup(settings, "scan.data_dir", "data_dir")


def git_gc_loose_threshold(settings):
    value = _lookup(settings, "audit.git_gc_loose_threshold", "git_gc_loose_threshold")
    return DEFAULT_GIT_GC_LOOSE_THRESHOLD if value is None else int(value)


def git_gc_bytes_threshold(settings):
    value = _lookup(settings, "audit.git_gc_bytes_threshold", "git_gc_bytes_threshold")
    return DEFAULT_GIT_GC_BYTES_THRESHOLD if value is None else int(value)


def docker_timeout(settings):
    value = _lookup(settings, "audit.docker_timeout", "docker_timeout")
    return DEFAULT_DOCKER_TIMEOUT if value is None else int(value)
