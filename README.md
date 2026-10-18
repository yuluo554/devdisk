# DevDiskAudit

> 开发者磁盘占用审计与安全清理：按**可重建性**给出分级可删清单，默认只出报告，清理走隔离区可回滚。

`devdisk` 扫描本机开发目录，把 `node_modules`、`.venv`、构建产物、Docker 镜像缓存与大 `.git` 对象统一成一份清单，
为每条候选给出 **安全可删 / 谨慎 / 禁止** 三级判定与可解释证据，并生成一份**单文件中文 HTML 报告**。
删除动作默认不执行；执行时移动到隔离区并写 manifest，可一键撤销。

- 纯本地：无服务器、无数据库、无网络请求、无遥测
- 零第三方运行时依赖：仅 Python 标准库（3.8+）
- 默认只读：`scan` / `audit` / `report` 不修改被扫描目录
- 永不 `rm -rf`：清理 = 移动到 `.trash/<batch-id>/`，`undo` 逐字节恢复
- `.git` 只读：只统计体积与对象数，最多建议 `git gc`，从不执行

**状态**：v0.1.0 已发布（M0–M6 全部完成）。发布留档见 `plan/RELEASE-M6.md`；
CI 在 windows/ubuntu × Python 3.8/3.12 四矩阵上全绿。
`scan` 输出**扫描事实**（类型 / 体积 / mtime / 项目根 / 跳过原因）与**分级结论**
（`grade` / `reason` / `evidence`，`totals.by_grade` 三级体积合计）；
分级判定来自数据文件 `data/rules/grades.json`，引擎零硬编码规则。
`audit` 输出 Docker 四类体积与 `.git` 对象统计（只读；建议命令固定 `applied: false`）。
`clean` 默认 dry-run，`--apply --yes` 才移动到 `.trash/<batch-id>/` 并写 manifest；
`undo` 按 manifest 逆序恢复，往返都校验 tree digest。
`report` 生成**单文件中文 HTML**（内联 CSS/JS、零外链、断网可读）：体积排行、
按类型/按项目聚合、分级分布与可删合计、候选明细（理由与证据）、Docker/.git 段
（缺失时显式写"未审计"）、隔离批次与撤销说明。报告只消费既有 JSON，不重新扫描、
不重新分级；同一输入两次生成逐字节一致。

## 定位与边界（如实说明）

本题是**功能增量**，不是新品类。已有工具已经解决了"找出来"：

| 能力 | 现有工具（基准日 2026-10-17） | 本项目位置 |
|---|---|---|
| `node_modules` 识别与清理 | npkill（9477★）已垄断用户心智；kondo（2451★）亦覆盖 | 作为统一清单中的一类，不做单点竞争 |
| 20+ 项目类型、`--dry-run`、`--older` | kondo 已内置 | 首期只覆盖常见类型；增量在证据化分级与回滚 |
| Docker 镜像 / 构建缓存 | 同类工具稀少（检索软论据） | 纳入统一只读审计；清理命令只生成、不执行 |
| `.git` 对象 | 未见产品级工具（检索软论据） | 只读统计 + `git gc` 建议，永不删除 |
| 可回滚隔离区 | 现有工具以直接删除或 dry-run 为主 | **核心增量** |
| 中文分级理由与中文报告 | 未见同类（检索软论据） | 全部界面与理由为中文 |

**非目标**：系统级清理、回收站、软件卸载、注册表、云同步目录、重复文件查找、一键清理全部。
详见 `plan/01-题目文档.md` §六。

## 安装

```bash
# 建议先建干净虚拟环境（激活后用 python，别用 py 启动器——它可能指向系统 Python）
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate

# Python 3.8 自带的 pip 20.2.3 无法可编辑安装 pyproject-only 项目，先升级
python -m pip install -U pip

python -m pip install -e ".[dev]"
```

要求 Python 3.8+。运行时不安装任何第三方库；`dev` 额外组只用于跑测试。

## 已知环境问题

- **wheel / 非 editable 安装找不到规则表**：`data/rules/*.json` 是仓库内数据资产，不随 wheel 打包。
  `pip install .`（非 editable）后从仓库外运行会报
  `找不到 data/rules/types.json`（退出码 4）。首期发布物是**源码包 + editable 安装**，不受影响；
  确实要用 wheel 时设置 `DEVDISK_DATA_DIR=<仓库>/data` 即可。本机实测两种形态都验过：
  editable 安装从任意工作目录可跑，wheel 安装仅在设了该变量后可跑。
- **Python 3.8 建 venv 后先升级 pip**：3.8 自带 pip 20.2.3 对 pyproject-only 项目报
  `editable mode currently requires a setup.py based build`；升级 pip 后正常。
- **Windows 上 `py` 启动器会绕过 venv**：`py -m pip install …` 装到系统 Python，
  激活 venv 后请用 `python -m pip …`。
- **想确定性地验证"Docker 缺失"降级**：Windows 的 `docker.exe` 常登记在 `App Paths`
  注册表里，查找优先级高于 `PATH`，因此把 `PATH` 清空仍可能找到 Docker。
  用显式开关 `DEVDISK_DISABLE_DOCKER=1` 代替（`audit --docker` 会返回
  `skipped:docker_unavailable` 与退出码 4）。CI 用的就是这个开关。

## 快速开始

```bash
# 版本与帮助
devdisk --version
devdisk --help

# 扫描并分级（M1 起可用；M2 起输出带 grade/reason/evidence）
devdisk scan ~/code --out devdisk-out/scan.json

# 只读审计 Docker 与 .git（M3 起可用）
devdisk audit --root ~/code --docker --git --out devdisk-out/audit.json

# 离线复跑解析：用固定文本 fixture 代替真实 Docker（验收主路径）
devdisk audit --docker-fixture data/fixtures/docker_system_df/v24_standard.txt --out devdisk-out/audit.json

# 生成单文件中文报告（M5）
devdisk report --input devdisk-out/scan.json --audit devdisk-out/audit.json   --html devdisk-out/report.html --top 50

# 报告里展示隔离批次（可选）：直接给 manifest，或让它读隔离区目录
devdisk report --input devdisk-out/scan.json --html devdisk-out/report.html   --trash ~/code/.trash

# 清理（默认 dry-run，只打印将移动什么）
devdisk clean --from devdisk-out/scan.json --select ~/code/demo-web/node_modules

# 真正移动（必须显式确认；无"全选"参数）
devdisk clean --from devdisk-out/scan.json \
  --select ~/code/demo-web/node_modules --apply --yes

# 撤销一个批次（按 manifest 逆序恢复，逐条校验 tree digest）
devdisk undo 20261018-120000-a1b2c3 --trash ~/code/.trash
```

`python -m devdisk <command>` 与裸 `devdisk <command>` 等价。

## 命令与退出码

| 命令 | 作用 | 关键参数 |
|---|---|---|
| `devdisk scan <root>...` | 扫描并分级（M1 起扫描，M2 起带分级） | `--out`、`--format json/csv`、`--min-bytes`、`--keep`、`--config` |
| `devdisk audit` | Docker / `.git` 只读审计 | `--root`（可重复）、`--docker`、`--docker-fixture`、`--git`、`--out`、`--config` |
| `devdisk clean` | 选中项移入隔离区 | `--from`、`--select`（可重复）、`--apply`、`--yes`、`--trash`、`--resume`、`--out` |
| `devdisk undo <batch-id>` | 按 manifest 逆序移回 | `--trash`、`--yes`、`--stop-on-conflict` / `--continue-on-conflict`、`--out` |
| `devdisk report` | 生成单文件 HTML 报告 | `--input`、`--audit`、`--html`、`--top`、`--as-of`、`--batch`（可重复）、`--trash` |

| 退出码 | 名称 | 含义 |
|---|---|---|
| 0 | OK | 正常完成 |
| 1 | REFUSED | 可预期的业务拒绝或冲突（如撤销目标已存在） |
| 2 | USAGE | 用法错误、未知命令/参数 |
| 3 | NOT_READY | 骨架期该子命令尚未实现（逐里程碑退役） |
| 4 | DEGRADED | 可选能力不可用（如 Docker CLI 缺失或输出无法解析） |
| 5 | UNSAFE | 安全拒绝（跨设备、越界路径、禁止操作） |

**不提供"全选/一键清理全部"参数**：清理必须逐条或逐批显式选择。

## 安全设计

- **不跟随符号链接**：`lstat` 判定，越界目标记 `skipped:outside_root`，环形链接不进入。
- **物理对象去重**：`(st_dev, st_ino)` 为键，硬链接与重复挂载只计一次。
- **证据门槛**：`safe` 必须有可核查证据；命中安全规则却取不到证据、或候选子树有读不全的目录 → 降级"谨慎"，绝不默认"安全可删"。
- **跨设备拒绝**：隔离区与源目录不同文件系统时直接报错（退出码 5），不用"复制后删除"绕过；
  源码里不存在 `shutil.copy*` 回退路径（静态守门）。
- **撤销校验**：移动前后计算 tree digest（类型前缀 + 相对路径 + 字节数 + 文件 sha256；
  含空目录与链接目标，不含 mtime），不一致即冲突，不覆盖。
- **隔离区可回滚**：`clean` 默认 dry-run（不建目录、不落盘），`--apply --yes` 才移动；
  manifest 状态机 `pending → moving → moved`，中断后 `--resume` 可续做。
- **隔离区不被重扫**：`.trash` 已在忽略规则里；自定义 `--trash` 落在扫描根内却未被忽略 → 退出码 5。
- **禁止项永不清理**：分级为 `forbidden` 的条目（典型是 `.git`）直接拒绝，退出码 5。
- **`.git` 只读**：不删除、不移动、不执行 `git gc`；审计前后对 `.git` 做元数据快照并断言未变化。
- **Docker 只读**：只调用 `docker system df`；清理命令只生成，固定 `applied: false`。
  Docker 缺失/超时 → `skipped:docker_unavailable` / `skipped:docker_timeout`（退出码 4）；
  输出无法解析 → `error:docker_parse`（退出码 4，附原文摘录），不静默输出 0 体积。
- **`audit` 全离线可验收**：`--docker-fixture` 直接喂固定文本，不要求本机安装 Docker。
- **报告零外链**：内联 CSS/JS，无 CDN、无字体、无图片外链；渲染出口断言扫描
  `<link href>`、`<script src>`、`url(//...)`、`@import` 与任何 `http(s)://` 字面量，
  命中即拒绝写出（退出码 2）。
- **报告只消费 JSON**：`report` 不重新扫描、不重新分级；路径/理由/证据全部 `html.escape`，
  同一输入两次生成逐字节一致（不写生成时刻，需要时间用 `--as-of` 固定）。

分级是**基于可观察证据的建议**，不是"删除后必然安全"的保证。重建成本需自行确认。

## 隐私

只读取路径、体积、mtime 与文件名；唯一允许打开正文的普通文件是白名单里的 `package.json`（且只解析顶层键）。
`audit` 另会读取 `.git` **文件**（worktree/submodule 形态）的前 4 KiB，只为取 `gitdir:` 一行；
不读取任何仓库内容或源码正文。不上传任何数据，无网络请求。
真实审计结果、隔离区、HTML 报告产物一律不入 git（见 `.gitignore`）。

## 数据与合成树（M1 起）

`scan` 的验收基准是一棵**确定性合成目录树** + 同一次生成的**植入真值台账**。
两者都不入仓（`devdisk-out/` 已在 `.gitignore`），按需生成：

```bash
# 生成（固定 seed，覆盖 plan/04 §2.2 的 22 类样本）
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018

# 位级一致自检：重新生成到临时目录逐项比对（一致 0 / 不一致 1）
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check

# 扫描（--min-bytes 0 让全部样本进入候选）
py -X utf8 -m devdisk scan devdisk-out/synth --out devdisk-out/scan.json --min-bytes 0

# 基准：扫描双边门（类型/体积/mtime/项目根逐项对账 + 误判 0 + 检出率 100%）
py -X utf8 tools/bench_scan.py --tree devdisk-out/synth

# 基准：分级门（分级与真值一致 + 理由/证据充分 + 证据不足落谨慎）
py -X utf8 tools/bench_grade.py --tree devdisk-out/synth

# 分级规则的独立参照实现对账（不 import 生产模块）
py -X utf8 tools/reference_grade.py --tree devdisk-out/synth --scan devdisk-out/scan.json

# M3：只读审计（fixture 主路径 + 合成 .git）
py -X utf8 -m devdisk audit --root devdisk-out/synth --git \
  --docker-fixture data/fixtures/docker_system_df/v24_standard.txt \
  --out devdisk-out/audit.json

# 基准：审计门（Docker fixture 逐字段 + .git 三值 + 前后无变化 + applied:false）
py -X utf8 tools/bench_audit.py --tree devdisk-out/synth

# M4：隔离区往返基准（在临时副本上跑，不动 devdisk-out/synth 本体）
# 清理 → 撤销 → 逐条 digest 比对；中断恢复；dry-run 只读；未选择退出码 1
py -X utf8 tools/bench_clean.py --tree devdisk-out/synth

# M5：报告（单文件 HTML，零外链）
py -X utf8 -m devdisk report --input devdisk-out/scan.json   --audit devdisk-out/audit.json --html devdisk-out/report.html --top 50

# 基准：报告数值对真值（从 HTML 读回）+ 零外链 + 双跑一致 + 转义/降级可见
py -X utf8 tools/bench_report.py --tree devdisk-out/synth
```

类型 / 忽略 / 分级三张规则表都是**单点数据文件**
（`data/rules/types.json`、`data/rules/ignore.txt`、`data/rules/grades.json`），
规则缺失或格式错时 `scan` 返回退出码 `4`，不会静默退回内置默认。

## 开发与回归

```bash
# 全量测试（两个解释器各跑一遍）
py -3.8 -m pytest -q
py -3.12 -m pytest -q
```

数据与口径纪律见 `data/README.md`；计划文档见 `plan/00-README总览.md`。
数值口径（体积单位、候选阈值、分级优先级、隔离区命名、Docker 降级语义）只在 `data/README.md` §四定义一次。

## 平台

| 平台 | 首期状态 |
|---|---|
| Windows 10/11 | 支持扫描与隔离；长路径走 `\\?\` 前缀；文件占用记 `skipped:in_use` |
| Linux | 支持扫描与隔离；权限拒绝记 `skipped:permission_denied` |
| macOS | 仅保证基本扫描与隔离；`~/Library/Caches` 适配留扩展项 |

WSL 发行版缓存、系统级缓存、定时审计与历史对比为首期扩展项（`plan/06` §七）。

## 许可

MIT，见 `LICENSE`。
