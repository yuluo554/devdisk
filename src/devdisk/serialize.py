# -*- coding: utf-8 -*-
"""确定性 JSON 写盘（单点定义）。

纪律（见 `data/README.md` §四 与 `plan/02` §6.4）：
- `sort_keys=True`、`ensure_ascii=False`、`indent=2`；
- UTF-8、`newline="\\n"`、结尾一个换行；
- 原子写：同目录临时文件 + `os.replace`，中断不留下半个产物。
"""

import json
import os
import tempfile

JSON_KWARGS = {"sort_keys": True, "ensure_ascii": False, "indent": 2}


def dumps_json(payload):
    """返回确定性 JSON 文本（含结尾换行）。"""
    return json.dumps(payload, **JSON_KWARGS) + "\n"


def write_json_atomic(path, payload):
    """原子写 JSON；失败时不留半成品。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    text = dumps_json(payload)
    fd, tmp_path = tempfile.mkstemp(prefix=".devdisk-", suffix=".tmp", dir=directory or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
