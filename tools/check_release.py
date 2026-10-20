# -*- coding: utf-8 -*-
"""发布终态机械门禁（可复跑；**刻意不进 CI**）。

用法（任意解释器，在仓库根或给绝对路径）：

    py -3.12 tools/check_release.py .
    py -3.12 tools/check_release.py . --tag v0.1.1 --no-gh
    py -3.12 tools/check_release.py . --asset devdisk-0.1.1-src.tar.gz
    py -3.12 tools/check_release.py --probe

退出码：0 = 无 FAIL（WARN 允许）；1 = 有 FAIL；2 = 用法错误。

为什么刻意不进 CI：这道门在**发布之前本来就该红**——tag 还没打、附件还没上传、
发布留档还没进 tag 树。挂进 CI 只会让 tag 之前的每次正常提交都红。

判据（本仓形态：发布物是**手工源码包**，不是 `python -m build` 的产物）：

1. `TAG_EXISTS`         tag 存在、且能解析到提交（annotated tag 剥壳后再判）
2. `TAG_ANNOTATED`      annotated tag 才是留档形态；轻量 tag 记 WARN
3. `TAG_AT_HEAD`        tag 之后无提交（发布留档的回写提交只允许"标为追加类"）
4. `VERSION_MATCH`      `pyproject.toml` 的 version 与 tag 名一致
5. `README_STATUS`      tag 树的 README 是已发布态，且版本号与 tag 一致
6. `RELEASE_DOC`        tag 树内含 `plan/RELEASE*.md`
7. `GH_ASSET_SET`       Release 附件集合与 digest（需 gh；`--no-gh` 记 SKIP）
8. `ASSET_TREE`         本地附件解包树与 `git archive <tag>` 解包树**逐文件同源**
9. `PACKAGE_SDIST`      `python -m build` 的 sdist 含 data/rules、data/fixtures、tools/、plan/
10. `PACKAGE_WHEEL`     wheel 内含规则表与 Docker fixture（DEC2=A：装完即用）
11. `PACKAGE_METADATA`  wheel 元数据是 SPDX（`License-Expression: MIT`）+ `Requires-Python: >=3.12`
12. `PACKAGE_WARNINGS`  构建输出无 `SetuptoolsDeprecationWarning`
13. `DESENSITIZE`       跟踪文本面：本机绝对路径 / 非 noreply 邮箱 / 私钥头，全 0

**可证伪**：`--probe` 会构造 10 个"坏世界"（临时 git 仓库 + 变异），逐一断言对应项变红；
任何一项没被抓住即退出码 1。门本身没被验证过 = 没有门。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

RESULTS = []  # (level, code, message)

FAIL, WARN, PASS, SKIP = "FAIL", "WARN", "PASS", "SKIP"

VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)
#: README 里出现这些措辞 = 还不是"已发布态"。
STALE_STATUS_RE = re.compile(r"收尾中|开发中|待首推|待发布|下一棒[：:]\s*M|未发布")
PUBLISHED_RE = re.compile(r"已发布")
#: 发布留档（tag 树内必须有）。
RELEASE_DOC_RE = re.compile(r"RELEASE", re.I)

TEXT_SUFFIXES = (".py", ".md", ".json", ".toml", ".yml", ".yaml", ".cfg", ".txt",
                 ".in", ".gitignore", ".gitattributes")
SKIP_DIRS = (".git", "__pycache__", ".pytest_cache", ".venv", "venv", "build",
             "dist", ".tmp", "devdisk-out", "update")


def add(level, code, msg):
    RESULTS.append((level, code, msg))


def reset():
    del RESULTS[:]


def run(args, cwd=None):
    proc = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def git(repo, *args):
    return run(["git", "-C", repo] + list(args))


def is_git_repo(repo):
    if not os.path.isdir(repo):
        return False
    rc, _ = git(repo, "rev-parse", "--is-inside-work-tree")
    return rc == 0


def unquote(name):
    """git ls-tree 会把非 ASCII 文件名加引号并转义；还原成真实路径。"""
    if name.startswith('"') and name.endswith('"'):
        body = name[1:-1]
        try:
            return body.encode("latin-1", "backslashreplace").decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return body
    return name


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# 1–3：tag
# --------------------------------------------------------------------------


def check_tag_exists(repo, tag):
    rc, kind = git(repo, "cat-file", "-t", "refs/tags/%s" % tag)
    if rc != 0:
        add(FAIL, "TAG_EXISTS", "tag %s 不存在（%s）" % (tag, kind))
        return False
    add(PASS, "TAG_EXISTS", "tag %s 存在（%s）" % (tag, kind.strip()))
    return True


def check_tag_annotated(repo, tag):
    rc, kind = git(repo, "cat-file", "-t", "refs/tags/%s" % tag)
    if rc != 0:
        return
    kind = kind.strip()
    if kind == "tag":
        rc, obj = git(repo, "rev-parse", "%s^{}" % tag)
        add(PASS, "TAG_ANNOTATED", "annotated tag，指向提交 %s" % obj.strip()[:12])
    else:
        add(WARN, "TAG_ANNOTATED", "轻量 tag（无 tagger/消息）；留档请用 `git tag -a`")


def check_tag_at_head(repo, tag):
    rc, out = git(repo, "log", "--format=%h %s", "%s..HEAD" % tag)
    if rc != 0:
        add(FAIL, "TAG_AT_HEAD", "无法计算 %s..HEAD" % tag)
        return
    commits = [line for line in out.splitlines() if line.strip()]
    if not commits:
        add(PASS, "TAG_AT_HEAD", "HEAD == tag（tag 之后无提交）")
        return
    allowed = re.compile(r"回写|补记|对账|留档|追加|收口|复核|校准|快照")
    offenders = [line for line in commits if not allowed.search(line)]
    detail = "%d 个提交在 tag 之后：%s" % (len(commits), "; ".join(commits[:3]))
    if offenders:
        add(FAIL, "TAG_AT_HEAD", detail + " —— 非追加类提交：%s" % offenders[0])
    else:
        add(WARN, "TAG_AT_HEAD", detail + " —— 均为追加类（发布后回写），留档须显式声明")


# --------------------------------------------------------------------------
# 4–6：版本、README 状态、发布留档
# --------------------------------------------------------------------------


def pyproject_version(repo):
    path = os.path.join(repo, "pyproject.toml")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        match = VERSION_RE.search(handle.read())
    return match.group(1) if match else None


def check_version_matches_tag(repo, tag):
    version = pyproject_version(repo)
    if not version:
        add(FAIL, "VERSION_MATCH", "pyproject.toml 没有字面量 version")
        return
    expected = tag[1:] if tag.startswith("v") else tag
    if version == expected:
        add(PASS, "VERSION_MATCH", "pyproject version %s == tag %s" % (version, tag))
    else:
        add(FAIL, "VERSION_MATCH", "pyproject version %s != tag %s（发布物会带错版本号）"
            % (version, tag))


def check_readme_status(repo, tag):
    path = os.path.join(repo, "README.md")
    if not os.path.isfile(path):
        add(FAIL, "README_STATUS", "仓库内没有 README.md")
        return
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    version = tag[1:] if tag.startswith("v") else tag
    stale = STALE_STATUS_RE.search(text)
    published = PUBLISHED_RE.search(text)
    if not published:
        add(FAIL, "README_STATUS", "README 没有已发布态措辞（找不到‘已发布’）")
    elif stale:
        add(FAIL, "README_STATUS", "README 仍含未发布措辞：%s" % stale.group(0))
    elif version not in text:
        add(FAIL, "README_STATUS", "README 未提及当前版本 %s" % version)
    else:
        add(PASS, "README_STATUS", "README 为已发布态且提到 %s" % version)


def check_release_doc(repo, tag):
    rc, out = git(repo, "-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", tag)
    if rc != 0:
        add(FAIL, "RELEASE_DOC", "无法读取 tag 树")
        return
    files = [unquote(line) for line in out.splitlines() if line.strip()]
    hits = [name for name in files
            if name.startswith("plan/") and RELEASE_DOC_RE.search(os.path.basename(name))]
    if hits:
        add(PASS, "RELEASE_DOC", "tag 树内含发布留档：%s" % ", ".join(sorted(hits)[:4]))
    else:
        add(FAIL, "RELEASE_DOC", "tag 树内没有 plan/RELEASE*.md（留档必须先于 tag 进树）")


# --------------------------------------------------------------------------
# 7–8：Release 附件
# --------------------------------------------------------------------------


def _gh_json(args):
    rc, out = run(["gh"] + list(args))
    if rc != 0:
        return None, out
    try:
        return json.loads(out), out
    except ValueError:
        return None, out


def check_gh_asset_set(repo, tag, use_gh):
    if not use_gh:
        add(SKIP, "GH_ASSET_SET", "--no-gh：跳过 Release 附件集合与 digest 检查")
        return None
    repo_slug = os.environ.get("DEVDISK_GH_REPO")
    if not repo_slug:
        rc, out = git(repo, "remote", "get-url", "origin")
        if rc != 0:
            add(SKIP, "GH_ASSET_SET", "无法确定远端仓库（无 origin）")
            return None
        out = out.strip()
        match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", out)
        if not match:
            add(SKIP, "GH_ASSET_SET", "远端 URL 形态无法解析：%s" % out)
            return None
        repo_slug = "%s/%s" % (match.group(1), match.group(2))

    payload, raw = _gh_json(["release", "view", tag, "-R", repo_slug, "--json",
                             "assets,tagName,isDraft,isPrerelease"])
    if payload is None:
        add(FAIL, "GH_ASSET_SET", "读不到 Release %s：%s" % (tag, raw[:200]))
        return None
    assets = payload.get("assets") or []
    if payload.get("isDraft"):
        add(FAIL, "GH_ASSET_SET", "Release %s 仍是 draft" % tag)
    names = [asset.get("name") for asset in assets]
    expected = "devdisk-%s-src.tar.gz" % (tag[1:] if tag.startswith("v") else tag)
    if expected not in names:
        add(FAIL, "GH_ASSET_SET", "Release 附件缺少 %s（实际：%s）" % (expected, names))
    else:
        found = [asset for asset in assets if asset.get("name") == expected][0]
        add(PASS, "GH_ASSET_SET", "附件 %s，digest %s，%s B"
            % (expected, found.get("digest"), found.get("size")))
    return {asset.get("name"): asset.get("digest") for asset in assets}


def _extract_tree(tar_path, dest):
    with tarfile.open(tar_path) as archive:
        try:
            archive.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 没有 filter 参数
            archive.extractall(dest)


def _tree_digest(root):
    """相对路径 → sha256；用于逐文件同源比对。"""
    entries = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            entries[rel] = sha256_file(full)
    return entries


def _single_root(dest):
    children = [name for name in os.listdir(dest) if os.path.isdir(os.path.join(dest, name))]
    if len(children) == 1:
        return os.path.join(dest, children[0])
    return dest


def check_asset_tree(repo, tag, asset_path, gh_digests):
    if not asset_path:
        add(SKIP, "ASSET_TREE", "未给 --asset：跳过附件与 tag 树的逐文件同源检查")
        return
    if not os.path.isfile(asset_path):
        add(FAIL, "ASSET_TREE", "附件文件不存在：%s" % asset_path)
        return
    local = sha256_file(asset_path)
    expected = None
    if gh_digests:
        name = os.path.basename(asset_path)
        expected = (gh_digests.get(name) or "").split(":", 1)[-1] or None
    if expected and expected != local:
        add(FAIL, "ASSET_TREE", "附件 sha256 %s != Release digest %s" % (local, expected))
        return
    add(PASS, "ASSET_TREE", "附件 sha256 %s%s" % (local, "" if expected else "（无 gh digest 可比）"))

    temp = tempfile.mkdtemp(prefix="dd-rel-")
    try:
        asset_dir = os.path.join(temp, "asset")
        tag_dir = os.path.join(temp, "tag")
        os.makedirs(asset_dir)
        os.makedirs(tag_dir)
        _extract_tree(asset_path, asset_dir)
        archive = os.path.join(temp, "tag.tar")
        rc, out = run(["git", "-C", repo, "archive", "--format=tar", "-o", archive, tag])
        if rc != 0:
            add(FAIL, "ASSET_TREE", "git archive %s 失败：%s" % (tag, out[:200]))
            return
        _extract_tree(archive, tag_dir)
        left = _tree_digest(_single_root(asset_dir))
        right = _tree_digest(_single_root(tag_dir))
        missing = sorted(set(right) - set(left))
        extra = sorted(set(left) - set(right))
        differ = sorted(name for name in set(left) & set(right) if left[name] != right[name])
        if missing or extra or differ:
            add(FAIL, "ASSET_TREE", "附件与 tag 树不同源：缺 %d / 多 %d / 异 %d（例：%s）"
                % (len(missing), len(extra), len(differ),
                   (missing + extra + differ)[:3]))
        else:
            add(PASS, "ASSET_TREE", "附件与 tag %s 树逐文件一致（%d 个文件）" % (tag, len(right)))
    finally:
        shutil.rmtree(temp, ignore_errors=True)


# --------------------------------------------------------------------------
# 9–12：包形态（真构建，不读文本）
# --------------------------------------------------------------------------


def _copy_project(repo, dest):
    def ignore(directory, names):
        return [name for name in names if name in SKIP_DIRS or name.endswith(".egg-info")]

    shutil.copytree(repo, dest, ignore=ignore)


BUILD_SCRIPT = (
    "import sys, setuptools.build_meta as m\n"
    "out = sys.argv[1]\n"
    "print('SDIST ' + m.build_sdist(out))\n"
    "print('WHEEL ' + m.build_wheel(out))\n"
)


def build_artifacts(repo, workdir):
    """在仓库副本里真构建 sdist + wheel；返回 (sdist, wheel, 输出)。"""
    copy = os.path.join(workdir, "src-tree")
    out = os.path.join(workdir, "dist")
    os.makedirs(out)
    _copy_project(repo, copy)
    script = os.path.join(workdir, "build_once.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(BUILD_SCRIPT)
    rc, output = run([sys.executable, script, out], cwd=copy)
    if rc != 0:
        return None, None, output
    sdists = sorted(name for name in os.listdir(out) if name.endswith(".tar.gz"))
    wheels = sorted(name for name in os.listdir(out) if name.endswith(".whl"))
    if not sdists or not wheels:
        return None, None, output
    return os.path.join(out, sdists[0]), os.path.join(out, wheels[0]), output


def check_package_form(repo):
    workdir = tempfile.mkdtemp(prefix="dd-pkg-")
    try:
        sdist, wheel, output = build_artifacts(repo, workdir)
        if sdist is None:
            add(FAIL, "PACKAGE_SDIST", "构建失败：%s" % output[-400:])
            add(FAIL, "PACKAGE_WHEEL", "构建失败，无法检查 wheel")
            add(FAIL, "PACKAGE_METADATA", "构建失败，无法检查元数据")
            add(FAIL, "PACKAGE_WARNINGS", "构建失败，无法检查告警")
            return

        with tarfile.open(sdist) as archive:
            names = archive.getnames()
        groups = {
            "data/rules": sum(1 for name in names if "/data/rules/" in name),
            "data/fixtures": sum(1 for name in names if "/data/fixtures/" in name),
            "tools/": sum(1 for name in names if "/tools/" in name),
            "plan/": sum(1 for name in names if "/plan/" in name),
        }
        empty = [key for key, count in groups.items() if not count]
        detail = "、".join("%s=%d" % item for item in sorted(groups.items()))
        if empty:
            add(FAIL, "PACKAGE_SDIST", "sdist 缺资产：%s（%s）" % (empty, detail))
        else:
            add(PASS, "PACKAGE_SDIST", "sdist 自足（%s）" % detail)

        with zipfile.ZipFile(wheel) as archive:
            wheel_names = archive.namelist()
            metadata_name = [name for name in wheel_names if name.endswith(".dist-info/METADATA")][0]
            metadata = archive.read(metadata_name).decode("utf-8", "replace")
        rules = [name for name in wheel_names if "/data/rules/" in name]
        fixtures = [name for name in wheel_names if "/data/fixtures/" in name]
        if len(rules) >= 3 and fixtures:
            add(PASS, "PACKAGE_WHEEL", "wheel 内含规则表 %d 个、fixture %d 个" % (len(rules), len(fixtures)))
        else:
            add(FAIL, "PACKAGE_WHEEL", "wheel 缺规则表或 fixture（rules=%d, fixtures=%d）"
                % (len(rules), len(fixtures)))

        problems = []
        if "License-Expression: MIT" not in metadata:
            problems.append("缺 License-Expression: MIT")
        if "Requires-Python: >=3.12" not in metadata:
            problems.append("Requires-Python 不是 >=3.12")
        if "License ::" in metadata:
            problems.append("仍有 License :: classifier")
        if problems:
            add(FAIL, "PACKAGE_METADATA", "；".join(problems))
        else:
            add(PASS, "PACKAGE_METADATA", "SPDX MIT + Requires-Python >=3.12 + 无 License classifier")

        if "SetuptoolsDeprecationWarning" in output:
            add(FAIL, "PACKAGE_WARNINGS", "构建输出含 SetuptoolsDeprecationWarning")
        else:
            add(PASS, "PACKAGE_WARNINGS", "构建输出无弃用告警")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# 13：脱敏（跟踪文本面）
# --------------------------------------------------------------------------
# 纪律：扫描器自己的"要找什么"不写成整串字面量，否则扫描器会命中自己。
_DRIVE = "[A-Za-z]:" + r"[\\/]"
_LOCAL_DIRS = "|".join(["Us" + "ers", "Pro" + "gramData", "Pro" + "gram Files"])
LOCAL_PATH_RE = re.compile(_DRIVE + "(?:" + _LOCAL_DIRS + r")[\\/]")
POSIX_HOME_RE = re.compile(r"/(?:ho" + r"me|Us" + r"ers)/[A-Za-z0-9._-]+/")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NOREPLY_SUFFIX = "users.noreply.github.com"
KEY_HEADER_RE = re.compile("BEGIN " + "(?:RSA |OPENSSH |EC |DSA )?" + "PRIVATE KEY")


def tracked_files(repo, tag=None):
    if is_git_repo(repo):
        args = ["-c", "core.quotepath=false", "ls-tree", "-r", "--name-only"]
        args.append(tag if tag else "HEAD")
        rc, out = git(repo, *args)
        if rc == 0:
            return [unquote(line) for line in out.splitlines() if line.strip()]
    files = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            files.append(os.path.relpath(os.path.join(dirpath, name), repo).replace(os.sep, "/"))
    return sorted(files)


def check_desensitize(repo, tag):
    offenders = []
    scanned = 0
    for rel in tracked_files(repo, tag):
        if not rel.endswith(TEXT_SUFFIXES):
            continue
        path = os.path.join(repo, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            if LOCAL_PATH_RE.search(line) or POSIX_HOME_RE.search(line):
                offenders.append("%s:%d 本机绝对路径" % (rel, lineno))
            if KEY_HEADER_RE.search(line):
                offenders.append("%s:%d 私钥头" % (rel, lineno))
            for match in EMAIL_RE.finditer(line):
                if not match.group(0).endswith(NOREPLY_SUFFIX):
                    offenders.append("%s:%d 非 noreply 邮箱" % (rel, lineno))
    if offenders:
        add(FAIL, "DESENSITIZE", "%d 处命中：%s" % (len(offenders), "; ".join(offenders[:4])))
    else:
        add(PASS, "DESENSITIZE", "%d 个跟踪文本文件 0 命中（路径/邮箱/私钥头）" % scanned)


# --------------------------------------------------------------------------
# 编排
# --------------------------------------------------------------------------


def resolve_tag(repo, explicit):
    if explicit:
        return explicit
    rc, out = git(repo, "tag", "-l", "--sort=-v:refname")
    if rc != 0 or not out:
        return None
    return out.splitlines()[0].strip()


def check_all(repo, tag, use_gh=True, asset=None, do_build=True):
    if not check_tag_exists(repo, tag):
        add(SKIP, "TAG_ANNOTATED", "tag 不存在，跳过")
        add(SKIP, "TAG_AT_HEAD", "tag 不存在，跳过")
        add(SKIP, "RELEASE_DOC", "tag 不存在，跳过")
        check_version_matches_tag(repo, tag)
        check_readme_status(repo, tag)
        check_gh_asset_set(repo, tag, use_gh)
        check_asset_tree(repo, tag, asset, None)
        if do_build:
            check_package_form(repo)
        check_desensitize(repo, None)
        return
    check_tag_annotated(repo, tag)
    check_tag_at_head(repo, tag)
    check_version_matches_tag(repo, tag)
    check_readme_status(repo, tag)
    check_release_doc(repo, tag)
    digests = check_gh_asset_set(repo, tag, use_gh)
    check_asset_tree(repo, tag, asset, digests)
    if do_build:
        check_package_form(repo)
    else:
        for code in ("PACKAGE_SDIST", "PACKAGE_WHEEL", "PACKAGE_METADATA", "PACKAGE_WARNINGS"):
            add(SKIP, code, "--no-build：跳过真构建")
    check_desensitize(repo, tag)


def print_results():
    order = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3}
    for level, code, msg in sorted(RESULTS, key=lambda item: (order[item[0]], item[1])):
        print("[%-4s] %-17s %s" % (level, code, msg))
    counts = {level: sum(1 for item in RESULTS if item[0] == level)
              for level in (FAIL, WARN, PASS, SKIP)}
    print("\n汇总：FAIL %d / WARN %d / PASS %d / SKIP %d"
          % (counts[FAIL], counts[WARN], counts[PASS], counts[SKIP]))
    return 1 if counts[FAIL] else 0


# --------------------------------------------------------------------------
# 可证伪探针：构造坏世界，逐条断言对应检查项变红
# --------------------------------------------------------------------------

PROBE_FILES = {
    "pyproject.toml": (
        "[build-system]\n"
        "requires = [\"setuptools>=77\", \"wheel\"]\n"
        "build-backend = \"setuptools.build_meta\"\n\n"
        "[project]\n"
        "name = \"devdisk\"\n"
        "version = \"0.1.1\"\n"
        "requires-python = \">=3.12\"\n"
        "license = \"MIT\"\n"
        "license-files = [\"LICENSE\"]\n"
        "classifiers = [\"Programming Language :: Python :: 3.12\"]\n"
        "dependencies = []\n\n"
        "[project.scripts]\n"
        "devdisk = \"devdisk.cli:main\"\n\n"
        "[tool.setuptools]\n"
        "package-dir = { \"\" = \"src\", \"devdisk.data\" = \"data\" }\n"
        "packages = [\"devdisk\", \"devdisk.data\", \"devdisk.data.rules\"]\n\n"
        "[tool.setuptools.package-data]\n"
        "\"devdisk.data\" = [\"rules/*\"]\n"
    ),
    "README.md": "# devdisk\n\n**状态**：v0.1.1 已发布。\n",
    "LICENSE": "MIT License\n",
    "src/devdisk/__init__.py": "__version__ = \"0.1.1\"\n",
    "src/devdisk/cli.py": (
        "from __future__ import print_function\n\n\n"
        "def main(argv=None):\n    return 0\n"
    ),
    "data/rules/types.json": "{\"source\": \"probe\", \"order\": [], \"types\": []}\n",
    "data/rules/ignore.txt": ".trash\n",
    "data/rules/grades.json": "{\"source\": \"probe\", \"order\": [], \"rules\": []}\n",
    "data/fixtures/docker_system_df/v24_standard.txt": "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\n",
    "tools/bench_scan.py": "def main():\n    return 0\n\n\nif __name__ == \"__main__\":\n    main()\n",
    "plan/RELEASE-M6.md": "# RELEASE\n\nprobe\n",
}


def _write_world(root, overrides=None, drop=()):
    files = dict(PROBE_FILES)
    for name in drop:
        files.pop(name, None)
    files.update(overrides or {})
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)


def _git_world(root, tag="v0.1.1", annotated=True, extra_commits=0, commit_msg="初始"):
    env = ["-c", "user.name=probe", "-c", "user.email=probe@users.noreply.github.com",
           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
    git(root, "init", "-q")
    git(root, "add", "-A")
    rc, out = git(root, *(env + ["commit", "-q", "-m", commit_msg]))
    if rc != 0:
        raise RuntimeError("probe commit 失败：%s" % out)
    if tag:
        if annotated:
            git(root, *(env + ["tag", "-a", tag, "-m", "probe release"]))
        else:
            git(root, "tag", tag)
    for index in range(extra_commits):
        with open(os.path.join(root, "extra-%d.txt" % index), "w", encoding="utf-8") as handle:
            handle.write("x\n")
        git(root, "add", "-A")
        # 注意：消息里不能出现 回写/追加/对账/留档 等"允许类"词，否则 TAG_AT_HEAD 会记 WARN。
        git(root, *(env + ["commit", "-q", "-m", "feat: 新增功能 %d" % index]))


def _make_asset(world, dest, mutate=None, tag="v0.1.1"):
    temp = tempfile.mkdtemp(prefix="dd-probe-asset-")
    try:
        pack_dir = os.path.join(temp, "pack")
        tree_dir = os.path.join(temp, "tree")
        os.makedirs(pack_dir)
        os.makedirs(tree_dir)
        rc, out = run(["git", "-C", world, "archive", "--format=tar", "-o",
                       os.path.join(pack_dir, "t.tar"), tag])
        if rc != 0:
            raise RuntimeError("probe archive 失败：%s" % out)
        _extract_tree(os.path.join(pack_dir, "t.tar"), tree_dir)
        root = _single_root(tree_dir)
        if mutate:
            mutate(root)
        with tarfile.open(dest, "w:gz") as archive:
            archive.add(root, arcname=os.path.basename(root))
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def run_probes(verbose=False):
    """每个探针 = (名字, 变异函数, 期望变红的检查项)。"""
    probes = []

    def probe(name, expect_code, builder, expected_level=FAIL):
        probes.append((name, expect_code, builder, expected_level))

    def no_tag(root):
        _write_world(root)
        _git_world(root, tag=None)

    def lightweight(root):
        _write_world(root)
        _git_world(root, annotated=False)

    def behind(root):
        _write_world(root)
        _git_world(root, extra_commits=2)

    def version_skew(root):
        _write_world(root, {"pyproject.toml": PROBE_FILES["pyproject.toml"].replace(
            'version = "0.1.1"', 'version = "0.1.0"')})
        _git_world(root)

    def stale_readme(root):
        _write_world(root, {"README.md": "# devdisk\n\n**状态**：收尾中，待发布。\n"})
        _git_world(root)

    def no_release_doc(root):
        _write_world(root, drop=("plan/RELEASE-M6.md",))
        _git_world(root)

    def no_package_data(root):
        text = PROBE_FILES["pyproject.toml"].replace(
            "[tool.setuptools.package-data]\n\"devdisk.data\" = [\"rules/*\"]\n", "")
        _write_world(root, {"pyproject.toml": text})
        _git_world(root)

    def deprecated_metadata(root):
        text = PROBE_FILES["pyproject.toml"].replace(
            'license = "MIT"\nlicense-files = ["LICENSE"]\n',
            'license = { text = "MIT" }\n').replace(
            'classifiers = ["Programming Language :: Python :: 3.12"]',
            'classifiers = ["License :: OSI Approved :: MIT License"]')
        _write_world(root, {"pyproject.toml": text})
        _git_world(root)

    def leak(root):
        # 纪律：探针里也不写整串字面值，否则清扫器会命中自己（拼装方式同 LOCAL_PATH_RE）。
        planted = "path: " + "C" + ":" + "/" + "Us" + "ers/someone/code\n"
        _write_world(root, {"notes.txt": planted})
        _git_world(root)

    def asset_drift(root):
        _write_world(root)
        _git_world(root)
        asset = os.path.join(root, "devdisk-0.1.1-src.tar.gz")

        def mutate(tree_root):
            with open(os.path.join(tree_root, "README.md"), "a", encoding="utf-8") as handle:
                handle.write("\n篡改\n")

        _make_asset(root, asset, mutate=mutate)
        # 附件不能进 tag 树（会污染后续检查），移到仓库外。
        outside = os.path.join(tempfile.mkdtemp(prefix="dd-probe-out-"), "asset.tar.gz")
        shutil.move(asset, outside)
        globals()["_PROBE_ASSET"] = outside

    probe("no_tag", "TAG_EXISTS", no_tag)
    probe("lightweight_tag", "TAG_ANNOTATED", lightweight, WARN)
    probe("tag_behind_head", "TAG_AT_HEAD", behind)
    probe("version_skew", "VERSION_MATCH", version_skew)
    probe("stale_readme", "README_STATUS", stale_readme)
    probe("no_release_doc", "RELEASE_DOC", no_release_doc)
    probe("wheel_without_rules", "PACKAGE_WHEEL", no_package_data)
    probe("deprecated_metadata", "PACKAGE_METADATA", deprecated_metadata)
    probe("tracked_leak", "DESENSITIZE", leak)
    probe("asset_drift", "ASSET_TREE", asset_drift)

    failures = 0
    for name, expect_code, builder, expected_level in probes:
        workdir = tempfile.mkdtemp(prefix="dd-probe-world-")
        try:
            reset()
            globals().pop("_PROBE_ASSET", None)
            builder(workdir)
            asset = globals().pop("_PROBE_ASSET", None)
            tag = resolve_tag(workdir, None) or "v0.1.1"
            check_all(workdir, tag, use_gh=False, asset=asset,
                      do_build=expect_code.startswith("PACKAGE_"))
            hit = [item for item in RESULTS if item[1] == expect_code and item[0] == expected_level]
            any_hit = [item for item in RESULTS if item[1] == expect_code]
            ok = bool(hit)
            line = "[%s] %-22s → %-17s 期望 %s，实际 %s" % (
                "ok" if ok else "MISS", name, expect_code, expected_level,
                hit[0][0] if hit else (any_hit[0][0] if any_hit else "（该项未产生结果）"))
            print(line)
            if verbose and hit:
                print("       %s" % hit[0][2][:160])
            if not ok:
                failures += 1
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    total = len(probes)
    print("\n探针：%d / %d 命中期望变异" % (total - failures, total))
    return 0 if failures == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="发布终态机械门禁（可复跑）")
    parser.add_argument("repo", nargs="?", default=".", help="仓库路径（默认当前目录）")
    parser.add_argument("--tag", help="tag 名（默认取版本号最大的 tag）")
    parser.add_argument("--asset", help="本地发布物（如 devdisk-0.1.1-src.tar.gz）")
    parser.add_argument("--no-gh", action="store_true", help="跳过 GitHub Release 检查")
    parser.add_argument("--no-build", action="store_true", help="跳过 sdist/wheel 真构建")
    parser.add_argument("--probe", action="store_true", help="跑可证伪探针（构造坏世界）")
    parser.add_argument("--verbose", action="store_true", help="探针模式下打印命中详情")
    args = parser.parse_args(argv)

    if args.probe:
        return run_probes(verbose=args.verbose)

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print("用法错误：仓库路径不存在：%s" % repo, file=sys.stderr)
        return 2
    if not is_git_repo(repo):
        print("用法错误：%s 不是 git 仓库（附件与 tag 同源检查需要 .git）" % repo, file=sys.stderr)
        return 2

    tag = resolve_tag(repo, args.tag)
    if not tag:
        print("用法错误：仓库内没有任何 tag，也没有给 --tag", file=sys.stderr)
        return 2

    reset()
    print("发布终态门禁：%s @ %s\n" % (repo, tag))
    check_all(repo, tag, use_gh=not args.no_gh, asset=args.asset,
              do_build=not args.no_build)
    return print_results()


if __name__ == "__main__":
    sys.exit(main())
