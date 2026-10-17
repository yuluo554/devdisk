# -*- coding: utf-8 -*-
"""配置项单点定义。

`candidate_min_bytes` 等会随机器调优的参数只在此处给默认值；
`data/README.md` §四 只声明"默认值在配置文件中单点定义"，不写死数字。
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


def default_out_dir():
    return os.path.abspath(DEFAULT_OUT_DIR)
