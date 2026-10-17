# -*- coding: utf-8 -*-
"""控制台编码守卫。

本机习惯用 `py -X utf8` 跑命令，但 CI 与用户环境不一定带该参数。
入口按实际编码探针决定是否 reconfigure：已经是 UTF-8 的环境保持不动，
cp936 等中文控制台也不强改（避免把用户终端搞乱）。
"""

import sys


def ensure_utf8_stdio():
    """尽力把标准输出/错误切到 UTF-8；失败时静默保留原状。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding in ("utf-8", "utf8"):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
