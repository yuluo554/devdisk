#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成合成目录树 + 植入真值台账（M1）。

用法：
    py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018
    py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check

纪律（`plan/04-数据计划.md` §二、`data/README.md` §三）：

- 零第三方依赖；**自研 splitmix64**，不用 `random`（跨版本序列不保证一致）。
- 所有文件与目录的 mtime 由本脚本显式设定，不取系统时钟。
- 同 seed 两次生成 → 逐字节一致（内容、大小、mtime）。
- `--check`：重新生成到临时目录并与目标目录逐项比对；一致 exit 0，不一致 exit 1。
- 真值只写**事实与期望**，不写 `observed_*`；`expected_bytes` 由本脚本按设计累加，
  不是从扫描结果抄回。
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from devdisk import model  # noqa: E402  只复用 ISO 时间与路径口径

GENERATOR_VERSION = 1
DEFAULT_SEED = 20261018
SCHEMA_VERSION = 1

# 固定时间基准：2026-01-01T00:00:00Z。所有样本按偏移量设定，绝不取系统时钟。
BASE_EPOCH = 1767225600

MASK64 = (1 << 64) - 1


class SplitMix64(object):
    """splitmix64：确定性、跨版本稳定、实现可审计。"""

    def __init__(self, seed):
        self.state = int(seed) & MASK64

    def next_u64(self):
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK64
        value = self.state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
        return (value ^ (value >> 31)) & MASK64

    def below(self, limit):
        return self.next_u64() % limit

    def fill(self, size):
        chunks = []
        remaining = size
        while remaining > 0:
            block = self.next_u64().to_bytes(8, "little")
            take = min(8, remaining)
            chunks.append(block[:take])
            remaining -= take
        return b"".join(chunks)


def write_file(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "wb") as handle:
        handle.write(data)


def ensure_dir(path):
    """逐级创建目录，返回新建的目录列表（自顶向下）。"""
    created = []
    missing = []
    current = os.path.abspath(path)
    while not os.path.isdir(current):
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for item in reversed(missing):
        os.mkdir(item)
        created.append(item)
    return created


def make_junction(link_path, target_path):
    """Windows 上创建 junction（无需 SeCreateSymbolicLinkPrivilege）。"""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", link_path, target_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError("mklink /J 失败：%s -> %s" % (link_path, target_path))


class TreeBuilder(object):
    def __init__(self, root, seed):
        self.root = os.path.abspath(root)
        self.rng = SplitMix64(seed)
        self.seed = seed
        self.files = {}          # rel_path -> declared size
        self.file_mtimes = {}    # rel_path -> epoch seconds
        self.dir_mtimes = {}     # rel_path -> epoch seconds
        self.deferred_chmod = []  # [(rel_path, mode)]，必须在 apply_mtimes 之后执行
        self.samples = []
        self.skipped_expect = []
        self.scale_samples = []
        self.link_kinds = {}
        self.links = {}

    # ---------------------------------------------------------------- 写入

    def path(self, rel_path):
        return os.path.join(self.root, *rel_path.split("/")) if rel_path else self.root

    def add_dir(self, rel_path, mtime):
        target = self.path(rel_path)
        if not os.path.isdir(target):
            os.makedirs(target)
        self._register_dirs(rel_path, mtime)
        return target

    def _register_dirs(self, rel_path, mtime):
        """登记 `rel_path` 及其所有祖先（相对 root）的 mtime。

        中间目录常由 `makedirs` 隐式创建；不登记就会保留创建时刻，
        破坏 `--check` 的位级一致。
        """
        parts = rel_path.split("/") if rel_path else []
        for index in range(1, len(parts) + 1):
            self.dir_mtimes.setdefault("/".join(parts[:index]), mtime)

    def add_file(self, rel_path, size, mtime):
        data = self.rng.fill(size)
        write_file(self.path(rel_path), data)
        self.files[rel_path] = size
        self.file_mtimes[rel_path] = mtime
        self._register_dirs(rel_path.rsplit("/", 1)[0] if "/" in rel_path else "", mtime)
        return data

    def add_text(self, rel_path, text, mtime):
        data = text.encode("utf-8")
        write_file(self.path(rel_path), data)
        self.files[rel_path] = len(data)
        self.file_mtimes[rel_path] = mtime
        self._register_dirs(rel_path.rsplit("/", 1)[0] if "/" in rel_path else "", mtime)
        return data

    def add_symlink(self, rel_path, target_rel_or_abs, mtime=0):
        """优先真符号链接；Windows 无权限时退化为 junction。

        目标写成**相对链接所在目录**的形式，保证 `--check` 在不同绝对路径下
        仍能逐项比对（junction 只能存绝对路径，比对时按相对位置归一）。
        """
        link_path = self.path(rel_path)
        parent = os.path.dirname(link_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        self._register_dirs(rel_path.rsplit("/", 1)[0] if "/" in rel_path else "", mtime)
        target = target_rel_or_abs
        if not os.path.isabs(target):
            target = os.path.abspath(os.path.join(parent, target))
        kind = None
        try:
            os.symlink(target, link_path, target_is_directory=True)
            kind = "symlink"
        except (OSError, NotImplementedError, AttributeError):
            if os.name != "nt":
                raise
            make_junction(link_path, target)
            kind = "junction"
        self.link_kinds[rel_path] = kind
        self.links[rel_path] = target_rel_or_abs
        return kind

    # ---------------------------------------------------------------- 真值

    def _prefix(self, rel_dir):
        return rel_dir.rstrip("/") + "/" if rel_dir else ""

    def expected_bytes(self, rel_dir):
        prefix = self._prefix(rel_dir)
        return sum(size for rel_path, size in self.files.items()
                   if rel_path.startswith(prefix))

    def expected_mtime(self, rel_dir):
        prefix = self._prefix(rel_dir)
        stamps = [mtime for rel_path, mtime in self.file_mtimes.items()
                  if rel_path.startswith(prefix)]
        return max(stamps) if stamps else 0

    def sample(self, rel_path, type_name, expected_grade, evidence_facts,
               note="", project_root_rel=None):
        self.samples.append({
            "rel_path": rel_path,
            "type": type_name,
            "expected_grade": expected_grade,
            "expected_bytes": self.expected_bytes(rel_path),
            "expected_mtime_utc": model.iso_from_epoch_seconds(self.expected_mtime(rel_path)),
            "project_root_rel": project_root_rel,
            "evidence_facts": list(evidence_facts),
            "note": note,
        })

    def skipped(self, rel_path, reason, platform=None, note=""):
        entry = {"rel_path": rel_path, "reason": reason}
        if platform:
            entry["platform"] = platform
        if note:
            entry["note"] = note
        self.skipped_expect.append(entry)

    # ------------------------------------------------------------ 收尾设定

    def apply_mtimes(self):
        """先设文件，再自底向上设目录——否则创建子项会把父目录 mtime 冲掉。"""
        for rel_path in sorted(self.files):
            target = self.path(rel_path)
            stamp = float(self.file_mtimes[rel_path])
            os.utime(target, (stamp, stamp))

        directories = [rel for rel in self.dir_mtimes if os.path.isdir(self.path(rel))]
        directories.sort(key=lambda item: item.count("/"), reverse=True)
        for rel_path in directories:
            target = self.path(rel_path)
            stamp = float(self.dir_mtimes[rel_path])
            os.utime(target, (stamp, stamp))

        for rel_path, mode in self.deferred_chmod:
            os.chmod(self.path(rel_path), mode)


# --------------------------------------------------------------------- 样本


def build_demo_web(builder):
    """样本 1 / 2 / 9 / 10 / 12 / 13 / 17。"""
    base = 0
    builder.add_text("demo-web/package.json",
                     '{\n  "name": "demo-web",\n  "scripts": {"build": "vite build"},\n'
                     '  "dependencies": {"demo-pkg-a": "1.0.0"}\n}\n', base + 10)
    builder.add_text("demo-web/package-lock.json", '{"lockfileVersion": 3}\n', base + 11)
    builder.add_text("demo-web/.gitignore", "node_modules/\ndist/\n", base + 12)

    # 样本 1：纯依赖
    builder.add_dir("demo-web/node_modules", base + 20)
    builder.add_text("demo-web/node_modules/.package-lock.json", '{"name": "demo-web"}\n', base + 21)
    builder.add_text("demo-web/node_modules/demo-pkg-a/package.json",
                     '{"name": "demo-pkg-a", "version": "1.0.0"}\n', base + 22)
    builder.add_file("demo-web/node_modules/demo-pkg-a/index.js", 256 * 1024, base + 23)
    builder.add_file("demo-web/node_modules/demo-pkg-a/dist/bundle.js", 768 * 1024, base + 24)
    builder.sample("demo-web/node_modules", "node_modules", "safe",
                   ["has_lockfile", "no_local_state_file"],
                   "纯依赖；锁文件在项目根", project_root_rel="demo-web")

    # 样本 2：node_modules 含 .env（独立子工作区，保持每个项目只有一份 node_modules）
    builder.add_dir("demo-web/legacy/node_modules", base + 25)
    builder.add_text("demo-web/legacy/node_modules/demo-pkg-b/package.json",
                     '{"name": "demo-pkg-b"}\n', base + 26)
    builder.add_file("demo-web/legacy/node_modules/demo-pkg-b/index.js", 512 * 1024, base + 27)
    builder.add_text("demo-web/legacy/node_modules/.env",
                     "API_BASE=http://localhost:8080\n", base + 28)
    builder.sample("demo-web/legacy/node_modules", "node_modules", "caution",
                   ["has_env_file"], "依赖目录里混入 .env（本地状态）",
                   project_root_rel="demo-web")

    # 样本 9：纯 dist
    builder.add_dir("demo-web/dist", base + 30)
    builder.add_text("demo-web/dist/index.html", "<!doctype html><title>demo</title>\n", base + 31)
    builder.add_file("demo-web/dist/assets/app.js", 640 * 1024, base + 32)
    builder.sample("demo-web/dist", "dist", "safe",
                   ["has_build_script", "no_file_newer_than_build_marker"],
                   "package.json 含 build script", project_root_rel="demo-web")

    # 样本 10：dist 里混入手工配置（mtime 晚于产物）
    builder.add_dir("demo-web/legacy/dist", base + 40)
    builder.add_file("demo-web/legacy/dist/app.js", 320 * 1024, base + 41)
    builder.add_text("demo-web/legacy/dist/config.local.json", '{"env": "local"}\n', base + 50)
    builder.sample("demo-web/legacy/dist", "dist", "caution",
                   ["has_manual_config_after_build"],
                   "config.local.json 的 mtime 晚于产物", project_root_rel="demo-web")

    # 样本 12：.next
    builder.add_dir("demo-web/.next", base + 60)
    builder.add_file("demo-web/.next/build-manifest.json", 2 * 1024, base + 61)
    builder.add_file("demo-web/.next/server/pages/index.js", 766 * 1024, base + 62)
    builder.sample("demo-web/.next", ".next", "safe",
                   ["has_package_json"], "Next.js 构建产物", project_root_rel="demo-web")

    # 样本 13：.cache
    builder.add_dir("demo-web/.cache", base + 70)
    builder.add_file("demo-web/.cache/turbo/abc123", 256 * 1024, base + 71)
    builder.sample("demo-web/.cache", ".cache", "safe",
                   ["pure_cache_structure"], "纯缓存结构", project_root_rel="demo-web")

    # 样本 17：指向扫描根之外的链接（目标在根外，不跟随）
    builder.add_symlink("demo-web/link-outside", "../../synth-outside-target")
    builder.skipped("demo-web/link-outside", "outside_root",
                    note="目标在扫描根之外，不跟随")


def build_demo_mobile(builder):
    """样本 8。"""
    base = 1000
    builder.add_text("demo-mobile/package.json",
                     '{"name": "demo-mobile", "dependencies": {"demo-pkg-b": "2.0.0"}}\n', base + 10)
    builder.add_text("demo-mobile/package-lock.json", '{"lockfileVersion": 3}\n', base + 11)

    # 样本 8：.tox
    builder.add_dir("demo-mobile/.tox", base + 30)
    builder.add_file("demo-mobile/.tox/py38/lib/site-packages/demo_lib_core.py",
                     128 * 1024, base + 31)
    builder.sample("demo-mobile/.tox", ".tox", "safe",
                   ["pure_cache_structure"], "tox 环境可重建", project_root_rel="demo-mobile")


def build_demo_mono(builder):
    """样本 16 / 22。"""
    base = 1500
    builder.add_text("demo-mono/package.json", '{"name": "demo-mono", "private": true}\n', base + 10)
    builder.add_text("demo-mono/package-lock.json", '{"lockfileVersion": 3}\n', base + 11)

    # 样本 16：monorepo 共享 node_modules（第二个工作区用链接指向共享目录）
    builder.add_dir("demo-mono/node_modules", base + 20)
    builder.add_file("demo-mono/node_modules/demo-lib-core/index.js", 384 * 1024, base + 21)
    builder.add_dir("demo-mono/apps/app-a", base + 22)
    builder.add_text("demo-mono/apps/app-a/package.json", '{"name": "app-a"}\n', base + 23)
    kind = builder.add_symlink("demo-mono/apps/app-a/node_modules", "../../node_modules")
    builder.sample("demo-mono/node_modules", "node_modules", "caution",
                   ["referenced_by_multiple_projects"],
                   "被 apps/app-a 以 %s 共享引用" % kind, project_root_rel="demo-mono")
    builder.skipped("demo-mono/apps/app-a/node_modules", "symlink_not_followed",
                    note="根内链接不跟随；共享依赖存在误删风险")

    # 样本 22：1000 个 cache-xxxx 小目录
    # 注意：父目录不能以 `cache-` 开头，否则它自己先命中类型规则、整棵子树不再展开。
    builder.add_dir("demo-mono/pool", base + 50)
    for index in range(1000):
        rel = "demo-mono/pool/cache-%04d" % index
        builder.add_dir(rel, base + 51 + index)
        builder.add_file(rel + "/entry.bin", 4096, base + 51 + index)
    builder.sample("demo-mono/pool/cache-0000", ".cache", "safe",
                   ["pure_cache_structure"], "1000 目录规模样本之一（首个）",
                   project_root_rel="demo-mono")
    builder.scale_samples.append({
        "prefix": "demo-mono/pool/cache-",
        "count": 1000,
        "type": ".cache",
        "expected_bytes_each": 4096,
        "expected_bytes_total": 1000 * 4096,
        "note": "规模样本：1000 个 cache-xxxx 目录，每个 1 个 4096 字节文件",
    })


def build_demo_api(builder):
    """样本 3 / 4 / 5 / 6 / 7 / 11 / 18 / 19。"""
    base = 2000
    builder.add_text("demo-api/pyproject.toml",
                     '[project]\nname = "demo-api"\nversion = "0.1.0"\n', base + 10)
    builder.add_text("demo-api/requirements.txt", "demo-pkg-a==1.0.0\n", base + 11)

    # 样本 3：常规 .venv
    builder.add_dir("demo-api/.venv", base + 20)
    builder.add_text("demo-api/.venv/pyvenv.cfg", "home = /usr/bin\n", base + 21)
    builder.add_file("demo-api/.venv/lib/site-packages/demo_pkg_a/__init__.py",
                     2 * 1024 * 1024, base + 22)
    builder.sample("demo-api/.venv", ".venv", "safe",
                   ["has_pyproject", "no_custom_pth"], "常规虚拟环境",
                   project_root_rel="demo-api")

    # 样本 4：.venv 含自定义 .pth（子工作区）
    builder.add_dir("demo-api/services/worker/.venv", base + 30)
    builder.add_text("demo-api/services/worker/.venv/pyvenv.cfg", "home = /usr/bin\n", base + 31)
    builder.add_file("demo-api/services/worker/.venv/lib/site-packages/demo_pkg_a/__init__.py",
                     256 * 1024, base + 32)
    builder.add_text("demo-api/services/worker/.venv/lib/site-packages/zz_local.pth",
                     "/opt/devuser/local-lib\n", base + 33)
    builder.sample("demo-api/services/worker/.venv", ".venv", "caution",
                   ["has_custom_pth"], "含非包管理器生成的自定义 .pth",
                   project_root_rel="demo-api")

    # 样本 5 / 6 / 7
    builder.add_dir("demo-api/__pycache__", base + 40)
    builder.add_file("demo-api/__pycache__/main.cpython-312.pyc", 64 * 1024, base + 41)
    builder.sample("demo-api/__pycache__", "__pycache__", "safe",
                   ["pure_pyc"], "纯字节码缓存", project_root_rel="demo-api")

    builder.add_dir("demo-api/.mypy_cache", base + 50)
    builder.add_file("demo-api/.mypy_cache/3.12/main.data.json", 96 * 1024, base + 51)
    builder.sample("demo-api/.mypy_cache", ".mypy_cache", "safe",
                   ["pure_cache_structure"], "mypy 增量缓存", project_root_rel="demo-api")

    builder.add_dir("demo-api/.pytest_cache", base + 60)
    builder.add_file("demo-api/.pytest_cache/v/cache/nodeids", 32 * 1024, base + 61)
    builder.sample("demo-api/.pytest_cache", ".pytest_cache", "safe",
                   ["pure_cache_structure"], "pytest 缓存", project_root_rel="demo-api")

    # 样本 11：build
    builder.add_dir("demo-api/build", base + 70)
    builder.add_file("demo-api/build/lib/demo_api/__init__.py", 512 * 1024, base + 71)
    builder.sample("demo-api/build", "build", "safe",
                   ["has_pyproject"], "构建产物可重建", project_root_rel="demo-api")

    # 样本 18：软链环（悬空互指，不越界、不落到真实目录）
    builder.add_symlink("demo-api/loop-a", "loop-b")
    builder.add_symlink("demo-api/loop-b", "loop-a")
    builder.skipped("demo-api/loop-a", "symlink_cycle", note="环形链接，不跟随")
    builder.skipped("demo-api/loop-b", "symlink_cycle", note="环形链接，不跟随")

    # 样本 19：权限拒绝（仅 POSIX 可稳定构造）
    builder.add_dir("demo-api/locked", base + 80)
    builder.add_file("demo-api/locked/secret.bin", 1024, base + 81)
    if os.name != "nt":
        builder.deferred_chmod.append(("demo-api/locked", 0))
        builder.skipped("demo-api/locked", "permission_denied", platform="posix",
                        note="chmod 000，扫描应记录并继续")
    else:
        builder.skipped("demo-api/locked", "permission_denied", platform="posix",
                        note="Windows 无法可靠构造；测试按平台能力跳过")


def build_demo_infra(builder):
    """样本 14 / 15 / 20。"""
    base = 3000
    builder.add_text("demo-infra/Cargo.toml",
                     '[package]\nname = "demo-infra"\nversion = "0.1.0"\n', base + 10)
    builder.add_text("demo-infra/Makefile", "all:\n\tcargo build\n", base + 11)

    # 样本 14：target
    builder.add_dir("demo-infra/target", base + 20)
    builder.add_file("demo-infra/target/debug/demo_infra", 1024 * 1024, base + 21)
    builder.sample("demo-infra/target", "target", "safe",
                   ["has_cargo_toml"], "Cargo 构建产物", project_root_rel="demo-infra")

    # 样本 15：out，无构建配置 → 证据不足
    builder.add_dir("demo-infra/out", base + 30)
    builder.add_file("demo-infra/out/report.bin", 128 * 1024, base + 31)
    builder.sample("demo-infra/out", "out", "caution",
                   ["insufficient_evidence"], "无构建配置可证明可重建",
                   project_root_rel="demo-infra")

    # 样本 20：大 .git objects（只读审计样本）
    builder.add_dir("demo-infra/.git/objects/pack", base + 40)
    builder.add_file("demo-infra/.git/objects/pack/pack-0001.pack", 3 * 1024 * 1024, base + 41)
    builder.add_file("demo-infra/.git/objects/pack/pack-0001.idx", 8 * 1024, base + 42)
    builder.add_dir("demo-infra/.git/objects/ab", base + 43)
    builder.add_file("demo-infra/.git/objects/ab/cdef0123456789", 4096, base + 44)
    builder.add_text("demo-infra/.git/HEAD", "ref: refs/heads/main\n", base + 45)
    builder.add_text("demo-infra/.git/config",
                     '[core]\n\trepositoryformatversion = 0\n', base + 46)
    builder.sample("demo-infra/.git", "git", "forbidden",
                   ["git_metadata"], "版本历史元数据；只读，永不删除",
                   project_root_rel="demo-infra")


def build_root_sample(builder):
    """样本 21：扫描根本身。"""
    builder.add_text("README.md", "# synth tree\n", 0)
    builder.sample("", "root", "forbidden", ["scan_root"],
                   "扫描根本身不可作为清理目标")


def build_tree(out_dir, seed):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    builder = TreeBuilder(out_dir, seed)
    build_demo_web(builder)
    build_demo_mobile(builder)
    build_demo_mono(builder)
    build_demo_api(builder)
    build_demo_infra(builder)
    build_root_sample(builder)
    builder.apply_mtimes()

    # truth 目录必须在 apply_mtimes 之后创建，并显式设定 mtime，
    # 否则 `--check` 会在目录时间戳上出现假差异。
    truth_dir = os.path.join(out_dir, "truth")
    if not os.path.isdir(truth_dir):
        os.makedirs(truth_dir)
    truth_path = os.path.join(truth_dir, "manifest.json")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "generator_version": GENERATOR_VERSION,
        "base_epoch_utc": model.iso_from_epoch_seconds(BASE_EPOCH),
        "roots": ["."],
        "samples": builder.samples,
        "scale_samples": builder.scale_samples,
        "skipped_expect": builder.skipped_expect,
        "link_kinds": builder.link_kinds,
        "counts": {
            "sample_dirs": len(builder.samples),
            "files": len(builder.files),
            "total_bytes": sum(builder.files.values()),
        },
    }
    with open(truth_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    stamp = float(BASE_EPOCH)
    os.utime(truth_path, (stamp, stamp))
    os.utime(truth_dir, (stamp, stamp))
    return manifest


# ------------------------------------------------------------------- 比对


def _normalize_link_target(root, link_path, target):
    """把链接目标归一为"相对 root 的形式"，使不同绝对路径下的生成可比对。

    junction 只能存绝对路径；真 symlink 存相对或绝对。统一投影成
    `@in:<rel>`（根内）或 `@out:<原样>`（根外/悬空），避免临时目录名进指纹。
    """
    if target.startswith("\\\\?\\"):
        target = target[4:]
    if not os.path.isabs(target):
        target = os.path.normpath(os.path.join(os.path.dirname(link_path), target))
    try:
        rel = os.path.relpath(os.path.normpath(target), root)
    except ValueError:
        return "@out:" + target
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return "@out:" + os.path.basename(os.path.normpath(target))
    return "@in:" + rel.replace(os.sep, "/")


def snapshot(root):
    """返回 {rel_path: 指纹}。指纹含类型、大小、内容哈希、mtime_ns。

    链接只记类型与目标（junction 的目标通过 readlink 读），不跟随。
    """
    root = os.path.abspath(root)
    items = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for name in dirnames + filenames:
            absolute = os.path.join(dirpath, name)
            rel = os.path.relpath(absolute, root).replace(os.sep, "/")
            try:
                st = os.lstat(absolute)
            except OSError:
                items[rel] = ("unreadable", "", 0, 0)
                continue
            attributes = getattr(st, "st_file_attributes", 0)
            is_link = stat.S_ISLNK(st.st_mode) or bool(
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            if is_link:
                try:
                    target = os.readlink(absolute)
                except OSError:
                    target = "<unreadable>"
                target = _normalize_link_target(root, absolute, target)
                items[rel] = ("link", target, 0, 0)
            elif stat.S_ISDIR(st.st_mode):
                items[rel] = ("dir", "", 0, st.st_mtime_ns)
            else:
                digest = hashlib.sha256()
                try:
                    with open(absolute, "rb") as handle:
                        for chunk in iter(lambda: handle.read(65536), b""):
                            digest.update(chunk)
                    content = digest.hexdigest()
                except OSError:
                    content = "<unreadable>"
                items[rel] = ("file", content, st.st_size, st.st_mtime_ns)
    return items


def compare_trees(left, right, limit=20):
    left_items = snapshot(left)
    right_items = snapshot(right)
    problems = []

    for rel in sorted(set(left_items) | set(right_items)):
        if rel not in left_items:
            problems.append("仅目标存在：%s" % rel)
        elif rel not in right_items:
            problems.append("仅重新生成存在：%s" % rel)
        elif left_items[rel] != right_items[rel]:
            problems.append("不一致：%s（目标 %r / 重新生成 %r）"
                            % (rel, left_items[rel], right_items[rel]))
        if len(problems) >= limit:
            problems.append("……（已截断，仅显示前 %d 条）" % limit)
            break
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="生成 DevDiskAudit 合成目录树与植入真值（确定性）")
    parser.add_argument("--out", required=True, help="输出目录（会被整体重建）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子")
    parser.add_argument("--check", action="store_true",
                        help="重新生成到临时目录并与 --out 逐项比对")
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out)

    if args.check:
        if not os.path.isdir(out_dir):
            sys.stderr.write("检查失败：目标目录不存在：%s\n"
                             "下一步：先不带 --check 运行一次生成。\n" % out_dir)
            return 1
        temp_dir = tempfile.mkdtemp(prefix="devdisk-synth-check-")
        try:
            regenerated = os.path.join(temp_dir, "synth")
            build_tree(regenerated, args.seed)
            problems = compare_trees(out_dir, regenerated)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if problems:
            sys.stderr.write("不一致：%d 处（seed=%d）\n" % (len(problems), args.seed))
            for problem in problems:
                sys.stderr.write("  %s\n" % problem)
            return 1
        print("一致：%s 与 seed=%d 的重新生成逐项相同" % (out_dir, args.seed))
        return 0

    manifest = build_tree(out_dir, args.seed)
    counts = manifest["counts"]
    print("已生成：%s" % out_dir)
    print("样本目录：%d 个；文件：%d 个；声明总字节：%d"
          % (counts["sample_dirs"], counts["files"], counts["total_bytes"]))
    print("真值：%s" % os.path.join(out_dir, "truth", "manifest.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
