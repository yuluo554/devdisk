# -*- coding: utf-8 -*-
"""隔离区完整性校验：逐文件 sha256 与 tree digest（模块④）。

口径（`data/README.md` §四 第 10/11 条）：

- `tree_digest` = `sha256` over **按相对路径排序**的条目规范化编码；
- 每个普通文件贡献 `f\\0<相对路径>\\0<字节数>\\0<内容 sha256>`；
- 每个目录贡献 `d\\0<相对路径>`（空目录结构也纳入校验）；
- 每个链接贡献 `l\\0<相对路径>\\0<链接目标>`（**不跟随**，目标字符串即事实）；
- 每条记录以 `\\n` 结尾，输出前缀固定 `sha256:`。

前导类型字节让"同名文件"与"同名目录"无法互相冒充；排序保证同一棵树
在两次遍历中产生逐字节相同的摘要。

本模块只读文件内容，绝不修改、移动或删除任何条目。
"""

import hashlib
import os
import stat

from . import model

DIGEST_PREFIX = "sha256:"
CHUNK_SIZE = 1024 * 1024

FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

KIND_FILE = "f"
KIND_DIRECTORY = "d"
KIND_LINK = "l"


class DigestError(Exception):
    """树不可读或不存在（完整性校验无法完成）。"""


def _fs_path(path):
    """复用扫描层的 Windows 长路径处理（单点定义在 `scanner.fs_path`）。"""
    from .scanner import fs_path

    return fs_path(path)


def _is_link(st):
    if stat.S_ISLNK(st.st_mode):
        return True
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def file_sha256(path):
    """普通文件内容的 sha256 十六进制摘要。"""
    digest = hashlib.sha256()
    with open(_fs_path(path), "rb") as handle:
        while True:
            block = handle.read(CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def walk_entries(path):
    """收集 `path` 下的条目，按 `(相对路径, 类型)` 排序。

    返回 `(kind, relative_posix_path, payload, mtime_ns)` 列表：
    - 文件：`payload = (size, sha256)`，`mtime_ns` 取该文件；
    - 目录：`payload = None`，`mtime_ns` 取该目录（仅用于展示，不进 digest）；
    - 链接：`payload = target`（读不到时为空串），不跟随。

    不可读条目抛 `DigestError`：不允许把读不到的东西静默算成空树。
    """
    root = os.path.abspath(path)
    if not os.path.isdir(root):
        raise DigestError("不是目录：%s" % model.display_path(root))

    entries = []

    def walk(directory, rel_dir):
        try:
            with os.scandir(_fs_path(directory)) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise DigestError("目录不可读：%s（%s）"
                              % (model.display_path(directory), exc))

        for entry in children:
            rel_path = entry.name if not rel_dir else rel_dir + "/" + entry.name
            full_path = os.path.join(directory, entry.name)
            try:
                st = entry.stat(follow_symlinks=False)
                if st.st_ino == 0 or st.st_dev == 0:
                    st = os.lstat(_fs_path(full_path))
            except OSError as exc:
                raise DigestError("条目不可读：%s（%s）"
                                  % (model.display_path(full_path), exc))

            if _is_link(st):
                try:
                    target = os.readlink(_fs_path(full_path))
                except (OSError, ValueError):
                    target = ""
                entries.append((KIND_LINK, rel_path, target, int(st.st_mtime_ns)))
                continue
            if stat.S_ISDIR(st.st_mode):
                entries.append((KIND_DIRECTORY, rel_path, None, int(st.st_mtime_ns)))
                walk(full_path, rel_path)
                continue
            if not stat.S_ISREG(st.st_mode):
                # 设备 / 管道 / 套接字：不计体积也不读内容，但必须留痕。
                entries.append((KIND_LINK, rel_path, "<special:%o>" % st.st_mode,
                                int(st.st_mtime_ns)))
                continue
            try:
                content_digest = file_sha256(full_path)
            except OSError as exc:
                raise DigestError("文件不可读：%s（%s）"
                                  % (model.display_path(full_path), exc))
            entries.append((KIND_FILE, rel_path, (int(st.st_size), content_digest),
                            int(st.st_mtime_ns)))

    walk(root, "")
    entries.sort(key=lambda item: (item[1], item[0]))
    return entries


def collect_entries(path):
    """`walk_entries` 的 3 元组投影（digest 只需要类型/路径/载荷）。"""
    return [(kind, rel_path, payload)
            for kind, rel_path, payload, _ in walk_entries(path)]


def encode_entries(entries):
    """把条目列表编码为规范化字节串（tree digest 的输入）。"""
    chunks = []
    for kind, rel_path, payload in entries:
        rel_bytes = rel_path.encode("utf-8")
        if kind == KIND_FILE:
            size, content_digest = payload
            chunks.append(b"f\0" + rel_bytes + b"\0" + str(size).encode("ascii")
                          + b"\0" + content_digest.encode("ascii") + b"\n")
        elif kind == KIND_DIRECTORY:
            chunks.append(b"d\0" + rel_bytes + b"\n")
        else:
            target = payload or ""
            if not isinstance(target, str):
                target = str(target)
            chunks.append(b"l\0" + rel_bytes + b"\0" + target.encode("utf-8") + b"\n")
    return b"".join(chunks)


def tree_digest(path):
    """返回 `sha256:<hex>`；路径不可读时抛 `DigestError`。"""
    entries = collect_entries(path)
    digest = hashlib.sha256(encode_entries(entries))
    return DIGEST_PREFIX + digest.hexdigest()


def measure(path):
    """统计一棵树的普通文件数、字节数、链接数与最新文件 mtime（供摘要展示）。"""
    files = 0
    directories = 0
    links = 0
    total_bytes = 0
    newest_ns = None
    for kind, _, payload, mtime_ns in walk_entries(path):
        if kind == KIND_FILE:
            files += 1
            total_bytes += payload[0]
            if newest_ns is None or mtime_ns > newest_ns:
                newest_ns = mtime_ns
        elif kind == KIND_DIRECTORY:
            directories += 1
        else:
            links += 1
    return {
        "files": files,
        "directories": directories,
        "links": links,
        "bytes": total_bytes,
        "mtime_utc": model.utc_iso_from_ns(newest_ns) if newest_ns is not None else None,
    }


def combine_tree_digests(items):
    """批次级 digest：`sha256` over 排序后的 `relative_path\\0tree_digest`。

    只用于展示"这一批移动了什么"；撤销时仍逐条比对，不依赖批次摘要。
    """
    digest = hashlib.sha256()
    for relative_path, value in sorted(items):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((value or "").encode("ascii"))
        digest.update(b"\n")
    return DIGEST_PREFIX + digest.hexdigest()


def digests_match(first, second):
    """`sha256:` 前缀大小写不敏感比较（manifest 里统一小写）。"""
    return (first or "").strip().lower() == (second or "").strip().lower()
