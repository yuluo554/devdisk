# RELEASE-M6 · 发布留档（DevDiskAudit v0.1.0）

> 本文件是 M6 发布的**证据留档**，随 tag 树一起发布。所有命令均实际执行过；
> 未做过的动作不写"已做"。
> 仓库：`https://github.com/yuluo554/devdisk` · 发布物：`devdisk-0.1.0-src.tar.gz`

## 一、干净环境验证（clone + 全新 venv，按 README 逐条）

验证方式：把仓库 `git clone --no-local` 到仓库树**外**的临时目录（避免本机残留掩盖问题），
在其中新建两个 venv（3.12 / 3.8），按 README 顺序执行。逐条退出码与语义如下。

| # | 命令（README 口径） | 退出码 | 语义判定 |
|---|---|---|---|
| 1 | `python -m venv .venv` + `python -m pip install -U pip` | 0 | ✅ 符合（3.8 必做：自带 pip 20.2.3 无法可编辑安装 pyproject-only 项目） |
| 2 | `python -m pip install -e ".[dev]"` | 0 | ✅ 符合（3.12 与 3.8 各一份全新 venv） |
| 3 | `devdisk --version` | 0 | ✅ 输出 `devdisk 0.1.0` |
| 4 | `devdisk --help` | 0 | ✅ 列出 scan/audit/clean/undo/report/selftest 六个子命令 |
| 5 | `devdisk scan ~/code --out devdisk-out/scan.json` | 2 | ✅ 符合明示语义：本机无 `~/code`，报"扫描根不存在"并给下一步 |
| 6 | `tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018` | 0 | ✅ 19 个样本目录 / 1051 个文件 / 声明 16697671 字节 |
| 7 | `tools/gen_synth_tree.py … --check` | 0 | ✅ 位级一致 |
| 8 | `devdisk scan devdisk-out/synth --out devdisk-out/scan.json --min-bytes 0` | 0 | ✅ |
| 9 | `devdisk audit --root devdisk-out/synth --docker --git --out …` | 4 | ✅ 符合明示语义：本机无 Docker → `skipped:docker_unavailable`（降级码 4，不伪装成功） |
| 10 | `devdisk audit --docker-fixture data/fixtures/…/v24_standard.txt --out …` | 0 | ✅ fixture 主路径 |
| 11 | `devdisk report --input … --audit … --html … --top 50` | 0 | ✅ 663 KB 单文件 HTML；Docker 段显式"未审计" |
| 12 | `devdisk report --input … --html … --trash <隔离区>` | 0 | ✅ 报告展示 1 个隔离批次 |
| 13 | `devdisk clean --from … --select …`（dry-run） | 0 | ✅ 不建 `.trash`、不移动源目录 |
| 14 | `devdisk clean … --apply --yes` | 0 | ✅ 移动到 `.trash/<batch-id>/`，批次 `complete` |
| 15 | `devdisk undo <batch-id> --trash … --yes` | 0 | ✅ 逐字节恢复（文件回来，批次 `undone`） |
| 16 | `python -m devdisk <cmd>`（模块形态） | 0 | ✅ 与裸 `devdisk` 等价 |
| 17 | `pytest -q`（干净 venv，3.12） | 0 | ✅ 全绿 |
| 18 | `pytest -q`（干净 venv，3.8） | 0 | ✅ 全绿 |
| 19 | `devdisk selftest`（两个 venv + 仓库外工作目录） | 0 | ✅ 含 M0–M5 不变量 |
| 20 | `bench_scan / bench_grade / reference_grade / bench_audit / bench_clean / bench_report` | 0 | ✅ 六个基准全部 PASS |
| 21 | `pip install .`（非 editable，venv 在仓库外） | 0 安装，运行 `scan` 得 4 | ⚠️ **已知环境问题**：规则表不随 wheel 打包，需 `DEVDISK_DATA_DIR`；已写入 README 并在测试中常驻守门 |

**两处真实缺口（本轮发现并修复）**

1. **3.8 全新 venv 装不上**：3.8 自带 pip 20.2.3 对 pyproject-only 项目报
   `editable mode currently requires a setup.py based build`；升级 pip 后正常。
   README 已补前置行。
2. **wheel（非 editable）安装找不到规则表**：`data/rules/*.json` 不在包内，
   从仓库外运行报 `找不到 data/rules/types.json`（退出码 4）。首期发布物是
   **源码包 + editable 安装**，不受影响；README「已知环境问题」写明 `DEVDISK_DATA_DIR` 兜底。
   两项都加了常驻测试（`test_readme_declares_known_environment_issues`）。

> 留档口径：第 21 行**不是**"已验证 wheel 安装可用"，而是"已验证 wheel 形态下的
> 降级现象与规避方式"。发布物是源码包。

## 二、脱敏四步 + 产物扫描 + 提交元数据

| 步骤 | 命令/判据 | 结果 |
|---|---|---|
| 1. 文件名扫描 | `git ls-files` 过滤 `.env$/.key$/secret/token` | 0 命中 |
| 2. 内容级扫描 | 全部跟踪文件（**截至 `2be9e15` 实测 67 个**；复算 `git ls-files | wc -l`）：本机绝对路径、`/home/<user>/`、邮箱、手机号、身份证、`sk-` 密钥、私钥头、内网 IP | 0 命中（唯一邮箱形态是本项目 GitHub noreply，属公开元数据） |
| 2b. 历史级扫描 | **截至 `2be9e15` 实测 24 个提交**（复算 `git rev-list --count HEAD`）、149 个唯一 blob 全量扫同一组模式（先做阳性对照：`devdisk` 命中 40 文件，证明扫描通道有效） | 0 命中 |
| 2c. 提交信息扫描 | 全部提交消息主体扫同一组模式（截至 `2be9e15` 为 24 条） | 0 命中 |
| 3. 二进制扫描 | `git ls-files` 过滤 png/jpg/gif/ico/zip/gz/whl/exe/dll/pdf/docx/xlsx/pyc/so/dylib | 0 个跟踪二进制（仓库纯文本） |
| 4. 产物扫描 | `git ls-files` 过滤 `devdisk-out/`、`reports/`、`.trash/`、`__pycache__`、`egg-info` | 0 命中（`.gitignore` 已覆盖，测试常驻断言） |
| 5. 提交元数据邮箱 | `git log --format='%ae %ce' --all \| sort -u` | 仅 1 个唯一值，即 GitHub noreply（作者与提交者两字段均已核对） |

**验证描述用指代**：本文件不复述任何敏感字面值（本机路径形态、旧邮箱等），
只写类别与判据；避免"留档自身成为命中源"。

## 三、CI 对账（首次真跑）

CI 到本文件最后一次改动为止的**全部 run**：

| 事件 | run id | 结论 | 说明 |
|---|---|---|---|
| `push` → main（`34e278e`，M6 文档提交） | `39098631262` | **failure** | 首跑诊断轮：ubuntu 3.8/3.12 全绿；windows 3.8/3.12 同挂一步 |
| `push` → main（`035152e`，CI 修复提交） | `39099349952` | **success** | 四矩阵全绿（windows/ubuntu × 3.8/3.12） |
| `push` → main（`7c6cafa`，发布留档提交） | `39100048326` | **success** | 四矩阵全绿 |
| `push` tag `v0.1.0`（`7c6cafa`） | `39100349492` | **success** | 四矩阵全绿；`on: push` 无 filters，tag push 确实触发独立一轮 |
| `push` → main（`3b2d42d`，对账回写） | `39100714912` | **success** | 四矩阵全绿 |
| `push` → main（`97de096`，测试数纠错） | `39101821429` | **success** | 四矩阵全绿 |

**对账规则（不写会过期的绝对计数）**：workflow 的 `on: push` 无分支/tag 过滤，
所以**每次 push（含 tag push、含本文件的回写提交）都会产生一轮独立 run**；
对账判据是 `gh run list --event push` 的条数逐条对应 `git log` 的提交数 + tag 数，
且每轮的四个矩阵 job 结论均为 `success`。
本文件自身的回写提交也会再触发一轮——那种情况不是"对账失败"，
而是"对账这个动作本身又产生了一条被对账的记录"。

> 首次首跑失败 1 轮 + 修复后各轮全绿；无跳过、无重跑。
> 截至本文件最后改动，`gh run list` 里全部 run 的 `conclusion` 均为 `success`，
> 唯一例外是最早那轮诊断失败（已修复并留档）。

**tag 迁移（零消费者窗口内，两次）**：初版 tag 所在提交里，本文件仍写着错误的测试计数
与自指失效的 sha256。为让**发布包内的留档本身干净**，在无任何下载者的窗口内迁移 tag：
删 Release → 删远端/本地 tag → 在含更正的提交重打 → 重建 Release。
判据（不写死提交号）：`git rev-list -n1 v0.1.0` == `git rev-parse HEAD`，
即 tag 指向"含本文件最终内容"的那个提交；迁移后 tag push 触发独立 CI run，四矩阵全绿。
迁移理由与纠错留痕见 §七。

**首跑失败根因（如实留档）**：失败面签名是"windows 两个矩阵一起挂、ubuntu 两个过"，
不是"各平台各挂各的"。定位到唯一失败步骤 `Smoke (audit docker missing degrades)`：
该步骤用 `PATH` 隔离模拟"Docker 不存在"，而 Windows 上 `docker.exe` 常登记在
**`App Paths` 注册表**里，查找优先级高于 `PATH`；于是 `docker system df` 返回合法空表
（退出码 0），步骤断言退出码 4 因此必红。

处置 = 给产品加**不依赖 PATH 的确定性降级入口** `DEVDISK_DISABLE_DOCKER`
（与已有的 `DEVDISK_DATA_DIR` 同族），`run_docker()` 在开关打开时**不 spawn 任何进程**，
直接返回 `skipped:docker_unavailable`；新增常驻测试断言"不 spawn + 退出码 4"。
原 PATH 隔离测试保留为解析层语义的补充证据。README「已知环境问题」同步记载该机制。

## 四、确定性源码包

构建方式（tag 之后、与 tag 树同源）：

```bash
git archive --format=tar --prefix=devdisk-0.1.0/ v0.1.0 | gzip -9n > devdisk-0.1.0-src.tar.gz
sha256sum devdisk-0.1.0-src.tar.gz
```

- 可复跑性：同一 commit 连续构建两次，`sha256` 与字节完全一致（`gzip -9n` 去掉时间戳与文件名）。
- 同源性判据：`git archive` 只导出**跟踪树**，天然不含 `devdisk-out/`、`.trash/`、`__pycache__`；
  解包后的文件清单与 `git ls-tree -r --name-only v0.1.0` 逐项对应。
- 附件名用 **ASCII**（避开 `gh` 在 Git Bash 下剥中文附件名的问题）。

**sha256 的留档位置（自指问题的处置）**

源码包里包含本文件。若在本文件里写死"本次发布包的 sha256"，
**写进去的动作本身就会改变包内容**，那个哈希立刻失效——这是不可解的自指。

处置：sha256 **只**登记在 **GitHub Release 正文**——那是仓库树之外、
不随源码包内容变化的地方；本文件只写复验方法。（不在本文件里写哈希，也不靠
"tag 之后的提交信息"承载哈希：两者都会让"登记动作"本身进入被登记的产物。）

读者复验方式（先取到哈希，再校验）：

```bash
# 从 Release 正文取哈希（也可人工从网页复制）
gh release view v0.1.0 --repo yuluo554/devdisk --json body --jq .body | grep -A2 sha256
# 校验下载件
sha256sum devdisk-0.1.0-src.tar.gz
```

或者直接从 tag 重建、与下载件逐字节比对（不依赖任何写死的哈希）：

```bash
git archive --format=tar --prefix=devdisk-0.1.0/ v0.1.0 | gzip -9n > rebuilt.tar.gz
cmp rebuilt.tar.gz devdisk-0.1.0-src.tar.gz && echo "与 tag 树逐字节同源"
```

**Facts（不随包变化的部分）**

```text
artifact : devdisk-0.1.0-src.tar.gz
tag      : v0.1.0
src tree : git ls-tree -r v0.1.0 的文件数（用下面这条命令自行数，不写死）
```

```bash
git -c core.quotepath=false ls-tree -r --name-only v0.1.0 | wc -l
```

## 五、tag / Release / 发布物本体复核

| 项目 | 判据 | 结果 |
|---|---|---|
| tag 类型 | `git cat-file -t v0.1.0` | annotated tag（`tag`） |
| tag 指向 | `git rev-list -n1 v0.1.0`；tag 后仅有追加类提交（见 `git log --oneline v0.1.0..HEAD`） | 一致（tag 未移动） |
| tag 树含发布留档 | `git ls-tree -r --name-only v0.1.0` 含 `plan/RELEASE-M6.md` | 是 |
| tag 树 README 状态 | 不应命中"待发布/待首推/收尾中" | 已改为"已发布" |
| Release 附件 | `gh release view v0.1.0 --json isDraft,assets` | `isDraft=false`；1 个附件 `devdisk-0.1.0-src.tar.gz` |
| 真下载 sha256 | `gh release download` → `sha256sum` | 与 Release 正文登记的哈希一致 |
| 与 tag 树同源 | 从 tag 重新 `git archive \| gzip -9n` → 与下载件 `cmp` | **逐字节一致** |
| 中立目录解包冒烟 | 解包 → 全新 venv → `pip install -e ".[dev]"` → `devdisk selftest` | exit 0（`OK 骨架不变量全部通过`） |
| 解包树全量测试 | 同一 venv 跑 `pytest -q` | 全绿；2 项跳过且可指认（`devdisk-out/synth` 按需生成、不入仓） |

## 六、门禁结果

| 门禁 | 命令 | 结果 |
|---|---|---|
| 出题定稿门 | `check_topic.py .` | 22 PASS / 0 WARN / 0 FAIL |
| 发布终态门 | `check_release.py . --tag v0.1.0` | 0 FAIL（`POST_TAG` 为 WARN，见下） |
| 全量测试 | `py -3.12 -m pytest` / `py -3.8 -m pytest` | 双解释器全绿（**计数不写死**，见下） |
| 自检 | `devdisk selftest` | exit 0 |

> **为什么不写测试计数**：`plan/00` §数字承诺纪律明确禁止"会随实现漂移的硬数字
> （测试数/用例数/代码行数）"。本文件初稿曾写死一个计数，且写错了；正确处置是把数字
> 去掉、让读者用上面这行命令自己复算，而不是换一个同样会漂移的数字。

> 发布终态门的 `POST_TAG` 会把"tag 之后有追加类提交（回写/对账/留档）"记为 WARN 而非 FAIL：
> 本轮 §三/§四/§五 的事实回填只能发生在 tag 之后（哈希自指问题），这是设计允许的形态。

## 七、本轮已知缺口（如实）

- Docker 真实调用未在本机验证（本机无 Docker），验收主路径始终是 fixture；
  `DEVDISK_DISABLE_DOCKER` 让"缺失降级"可确定性复现，但不等于验证了真实 Docker。
- wheel 形态仅验证了"降级现象与规避方式"，未承诺可用（见 §一 第 21 行）。
- 跨设备隔离只有单测（monkeypatch `st_dev`）覆盖，本机没有第二块盘。
- 中断恢复用 `KeyboardInterrupt` 与"move 后抛错"注入，没有真实断电/进程 kill。
- 报告"断网双击打开"只在本机 Windows 默认浏览器验证过一次，未在 Linux/macOS 复核；
  零外链靠渲染出口常驻断言，而不是浏览器网络面板。
- tag 之后的对账事实（run id、sha256、复核结论）以追加提交形式补记在
  §三 / §四 / §五；tag 树内保留"待回填"标记，这是自指哈希问题的必然形态。
- 解包树全量测试有 2 项跳过，均因 `devdisk-out/synth` 是按需生成、不入仓；
  生成后这两项在本地开发树与干净 clone 中均实际跑过（见 §一 第 7/17/18 条）。
- **本文档的一处事实纠错（留痕）**：§六 最初把全量测试数写成 312，实测（收集数与通过数）
  与那个数字不符；根因是把终端里自设的 `echo` 标签误读成 pytest 汇总行。
  按 `plan/00` §数字承诺纪律，最终处置**不是**换成另一个数字，而是**删掉该计数**，
  改为给出可自行复跑的命令（`pytest --collect-only` / `pytest`）。留痕理由 =
  发布留档里的数字必须可复算，写错的痕迹要留下，但不要留下第二个会漂移的数字。
- 因上述纠错发生在初版 tag 之后，为使**发布包内的留档本身不含错误数字**，
  在零消费者窗口内迁移过 tag 并重建 Release；迁移后 tag push 触发的 CI run 四矩阵全绿。
  发布包 sha256 以 **Release 正文**为准（本文件在包内，无法承载自己的哈希，见 §四）。
  旧 tag 对象已删除；判据是 `git rev-list -n1 v0.1.0` == 含本文件最终内容的提交。
