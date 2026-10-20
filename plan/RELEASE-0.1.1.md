# RELEASE-0.1.1 · 发布留档（DevDiskAudit v0.1.1）

> **状态：已发布**。tag `v0.1.1`（annotated）与 Release `v0.1.1`，
> Release 附 `devdisk-0.1.1-src.tar.gz`（239609 B）。
> 发布终态门 `tools/check_release.py . --asset …`：**FAIL 0 / WARN 0 / PASS 14 / SKIP 0**，exit 0。
>
> **两条自指纪律（本仓上轮 `2be9e15` 定下，本轮继续遵守）**：
> 1. 本文件在附件包内，因此**不写**发布物自身的 sha256——写了会形成
>    "改摘要 → 包内容变 → 摘要失效"的循环。数值只在 **Release 说明正文**与 **tag 消息**里。
> 2. 同理**不写**本文件所在提交的 sha 或 tag 对象的 sha——那同样是"文档记录自身"的循环。
>    要核对就现场取：`git rev-parse v0.1.1 v0.1.1^{}`、
>    `sha256sum devdisk-0.1.1-src.tar.gz`、`gh release view v0.1.1 --json assets`。
>
> v0.1.0 的留档保持原样：`plan/RELEASE-M6.md`（其 tag 与附件不动不删）。
>
> **tag 重指过两次（如实记录）**：① 首次打 tag 后，tag 树 README 仍写"tag 尚未创建"
> （未发布态，会让 `README_STATUS` 判据红），故重指一次；② 重指后本文件里又写了
> tag 指向的提交 sha 与"Release 待上传"字样——那是旧的"文档记录自身"，一并改正后
> 再重指一次。两次都发生在 **Release 附件定稿之前**，且每次重指后都重新生成了源码包、
> 重新上传并重跑终态门；最终 `附件树 == tag 树` 逐文件一致（缺 0 / 多 0 / 异 0）。
> 教训：发布物内的文档**不要写自己的 commit/tag sha**，否则每次修订都要再重指一次。

## 一、本轮改了什么（全部实测）

| 编号 | 改动 | 依据（改前实测） |
|---|---|---|
| DEC1(a) | 新增 `MANIFEST.in`，把 `data/rules`、`data/fixtures`、`tools/`、`plan/`、`.gitignore`、`.gitattributes` 收进 sdist | `python -m build --sdist` 后 `tar -tzf` 里这四项 `grep -c` **全为 0**；仓库根无 `MANIFEST.in` |
| DEC1(b) | README「已知环境问题」写明"发布物是手工源码包" + 常驻测试 `test_readme_declares_release_artifact_is_handmade_source_package` / `test_sdist_manifest_collects_data_assets` | 改前**任何文档与测试都没写过**这句话 |
| DEC2 | 规则表与 Docker fixture 随 wheel 分发：`pyproject.toml` 用 `package-dir` 把仓库 `data/` 映射成包内 `devdisk.data`，`rules.resolve_data_dir()` 新增"包内数据目录"这一层 | wheel-only + 中立 CWD：`scan` → `找不到 data/rules/types.json`，**exit 4**，须 `DEVDISK_DATA_DIR` |
| DEC3 | `requires-python` `>=3.8` → `>=3.12`；classifier 收敛为 `3.12` 一条；CI 矩阵收敛为 `{ubuntu-latest, windows-latest} × "3.12"`；README 措辞同步 | 3.8 EOL 2024-10-07；3.10 已于 2026-10-01 EOL；`ubuntu-latest` 自 2026-10-19 起迁 Ubuntu 26，而本仓最后一次 run 是 2026-10-18 |
| D3 | `license = { text = "MIT" }` → SPDX `license = "MIT"` + `license-files`，删 `License ::` classifier，`setuptools>=61` → `>=77`（保留有意加的 `wheel`） | `python -m build` 实测 12 条 WARNING |
| D4 | `actions/checkout@v4` → `@v7`，`actions/setup-python@v5` → `@v7` | 每次 run 4 条 `Node.js 20 is deprecated` |
| DEC5 | 新增 `tools/check_release.py`（13 项终态判据 + `--probe` 10 组可证伪探针） | 本仓改前**没有**自动终态门（`tools/` 只有 5 个 bench + 生成器 + 参照实现） |
| D6 | `plan/RELEASE-M6.md` §二的"65 个跟踪文件 / 13 个提交"改为**带时点**（截至 `2be9e15` 为 67 / 24）+ 可复算命令 | 实测当前 67 个跟踪文件、24 个提交 |
| 复核锚 | `data/README.md` §二之一加 `next_review: 2027-01-17` 与基准日 2026-10-17 | 改前只有基准日，没有下次复核锚 |
| 交接 | `.gitignore` 加 `update/` | 交接目录会让"工作树干净"这条人工判据失真 |

**明确没动的**：`v0.1.0` 的 tag 与附件；清理/撤销语义（`.trash/` + manifest + `undo` 逆序 + 往返 digest）；
删除调用白名单；分级降级方向（无证据 → `caution`）；`data/rules/*` 与 `data/fixtures/*` 的内容；
退出码契约（Docker 缺失 4 / 规则缺失 4 / 扫描根不存在 2）。

## 二、发布前门禁（每条的期望值与实测）

| # | 命令 | 期望 |
|---|---|---|
| 1 | `python -m build --sdist` → `tar -tzf dist/*.tar.gz \| grep -c "data/rules"` | > 0（实测 **4**，改前 0） |
| 2 | 同上对 `data/fixtures` / `tools/` / `plan/` | > 0（实测 **7 / 10 / 16**，改前全 0） |
| 3 | sdist 解包树里 `pip install -e ".[dev]"` + `pytest` | 与仓库同一收集数全绿（实测 **256 passed / 2 skipped**，收集 258；2 项 skip 为按需生成 `devdisk-out/synth`） |
| 4 | wheel-only 干净 venv + 中立 CWD：`python -m devdisk scan <synth>` | **exit 0**（改前 exit 4） |
| 5 | `DEVDISK_DATA_DIR` 指向错误路径 | exit **4** + 明确"缺少 rules/types.json"（不静默） |
| 6 | `python -m build --wheel --sdist 2>&1 \| grep -ci warning` | **0**（改前 12） |
| 7 | wheel METADATA | `License-Expression: MIT` + `Requires-Python: >=3.12` + 无 `License ::` classifier |
| 8 | 仓库内 `python -m pytest` | **258 passed**，exit 0 |
| 9 | `devdisk --version` | `devdisk 0.1.1` |
| 10 | `tools/check_release.py --probe` | **10 / 10** 命中期望变异，exit 0 |
| 11 | 一次新 CI run（floor 3.12 两矩阵） | 全绿 + 注解 0 条 warning → **run `39417493019` / 2026-10-20T11:47:38Z**，两腿 success；注解仅 1 条 `ubuntu-latest` 迁移 notice（实测镜像仍为 `ubuntu-24.04`） |
| 12 | PR 前一轮（`e11d7f0`）的 CI | run `39416872347`（push）/ `39416913468`（pull_request）两轮均两腿 success，wall 结论一致 |
| 13 | tag 树（`git archive v0.1.1` 解包）里 `pytest` | **256 passed / 2 skipped**，exit 0（干净 venv `dd-iter-tag-env`） |
| 14 | **真下载 Release 附件** → sha256 | 239609 B，**== GitHub digest == 本地**（三方一致；数值见 Release 正文） |
| 15 | 附件解包树 vs `git archive v0.1.1` 解压树 | `diff -r` **无差异**（71 个文件） |
| 16 | 下载的包解包后装 + 跑 | **256 passed / 2 skipped**；`devdisk --version` → 0.1.1；`selftest` OK |
| 17 | `tools/check_release.py . --asset <本地包>`（含 gh） | **FAIL 0 / WARN 0 / PASS 14 / SKIP 0**，exit 0 |

## 三、发布动作（**已执行完毕**；以下为留档的命令序列）

顺序很重要：**先提交 → 再打 tag → 再打源码包 → 再上传附件**。附件必须由 tag 树生成，
否则"附件 == tag 树"这条判据不成立。

```bash
# 1) 已做：分支 release/0.1.1 → PR #1 → --merge 进 main（b995323）
#    （用 --merge 而非 squash：让被 CI 验过的那个提交留在 main 的祖先链上）

# 2) 已做：CI 全绿并记下 run id
RID=39417493019     # 2026-10-20T11:47:38Z，两腿 success，0 条 warning 注解
gh api "repos/yuluo554/devdisk/actions/runs/$RID/jobs" --jq '[.jobs[].conclusion]|unique'

# 3) 已做：annotated tag（经历一次重指，见文首说明）
git tag -a v0.1.1 -m "DevDiskAudit v0.1.1" && git push origin v0.1.1

# 4) 已做：确定性源码包（与 tag 树同源；-9n 去掉时间戳）
git archive --format=tar --prefix=devdisk-0.1.1/ v0.1.1 | gzip -9n > devdisk-0.1.1-src.tar.gz
sha256sum devdisk-0.1.1-src.tar.gz   # 双跑一致；数值见 Release 正文

# 5) 已做：Release（正文写 sha256；包内文件不写它自身）
gh release create v0.1.1 devdisk-0.1.1-src.tar.gz -R yuluo554/devdisk \
  --title "DevDiskAudit v0.1.1" --notes-file <release-body>

# 6) 已做：终态门
python tools/check_release.py . --asset devdisk-0.1.1-src.tar.gz   # FAIL 0
```

## 四、未验证（不许当成 PASS）

| 项 | 状态 | 原因 |
|---|---|---|
| actions v7 是否让注解归零 | **已验证** | run `39417493019`：4 条 `Node.js 20 is deprecated` → **0 条** |
| Ubuntu 26（新 `ubuntu-latest`）上两矩阵是否绿 | **未验证真实 Ubuntu 26** | 2026-10-20 的 run 里 ubuntu 腿 `Image: ubuntu-24.04`（runner `20261012.334.1`）；那条 notice 只是迁移预告。下一次 `ubuntu-latest` 滚到 26 之后需重跑确认 |
| `v0.1.1` 的 tag 与 Release | **已完成** | tag 为 annotated 且指向 HEAD（`git rev-parse v0.1.1^{}` == `git rev-parse HEAD`）；Release 非 draft 非 prerelease，附件 state=uploaded |
| 附件与 tag 树同源 | **已验证** | `check_release.py` 逐文件 sha256 比对：71 个文件，缺 0 / 多 0 / 异 0 |
| 报告 HTML 的实机目检（断网可读） | **未做** | 属人工目检项，机器通道测不到（`bench_report` 的零外链与确定性已是自动判据） |
| 本机真实 Docker 调用 | **未验证** | 本机无 Docker（README 已披露）；fixture 主路径 PASS |

> 更正一处诊断口径：诊断报告 D2 标 P2，本轮按用户 DEC1 决策当作结构性主线处理（因为它决定
> "走 sdist 渠道的人能否装完即用"）。严重性判断的改变来自用户决策，不是执行方的重新评估。
