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

**状态**：v0.1.0 · M2 安全分级已落地（合成树生成器 + 植入真值 + `scan` 带分级输出）。
`scan` 输出**扫描事实**（类型 / 体积 / mtime / 项目根 / 跳过原因）与**分级结论**
（`grade` / `reason` / `evidence`，`totals.by_grade` 三级体积合计）；
分级判定来自数据文件 `data/rules/grades.json`，引擎零硬编码规则。
`audit` / `clean` / `undo` / `report` 仍返回退出码 `3`（未就绪），退役表见 `plan/05-里程碑与发布.md` §三。

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
pip install -e ".[dev]"
```

要求 Python 3.8+。运行时不安装任何第三方库；`dev` 额外组只用于跑测试。

## 快速开始

```bash
# 版本与帮助
devdisk --version
devdisk --help

# 扫描并分级（M1 起可用；M2 起输出带 grade/reason/evidence）
devdisk scan ~/code --out devdisk-out/scan.json

# 只读审计 Docker 与 .git（M3 落地后可用）
devdisk audit --root ~/code --docker --git --out devdisk-out/audit.json

# 生成单文件中文报告（M5 落地后可用）
devdisk report --input devdisk-out/scan.json --audit devdisk-out/audit.json --html devdisk-out/report.html

# 清理（默认 dry-run，M4 落地后可用）
devdisk clean --from devdisk-out/scan.json --select ~/code/demo-web/node_modules

# 撤销一个批次
devdisk undo 20261018-120000-a1b2c3
```

`python -m devdisk <command>` 与裸 `devdisk <command>` 等价。

## 命令与退出码

| 命令 | 作用 | 关键参数 |
|---|---|---|
| `devdisk scan <root>...` | 扫描并分级（M1 起扫描，M2 起带分级） | `--out`、`--format json/csv`、`--min-bytes`、`--keep`、`--config` |
| `devdisk audit` | Docker / `.git` 只读审计 | `--root`（可重复）、`--docker`、`--git`、`--out` |
| `devdisk clean` | 选中项移入隔离区 | `--from`、`--select`（可重复）、`--apply`、`--yes`、`--trash` |
| `devdisk undo <batch-id>` | 按 manifest 逆序移回 | `--trash`、`--yes` |
| `devdisk report` | 生成单文件 HTML 报告 | `--input`、`--audit`、`--html`、`--top` |

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
- **跨设备拒绝**：隔离区与源目录不同文件系统时直接报错，不用"复制后删除"绕过。
- **撤销校验**：移动前后计算 tree digest（相对路径 + 字节数 + 文件 sha256），不一致即报冲突。
- **`.git` 只读**：不删除、不移动、不执行 `git gc`；审计前后对 `.git` 做元数据快照并断言未变化。
- **Docker 只读**：只调用 `docker system df`；清理命令只生成，固定 `applied: false`。

分级是**基于可观察证据的建议**，不是"删除后必然安全"的保证。重建成本需自行确认。

## 隐私

只读取路径、体积、mtime 与文件名；唯一允许打开正文的文件是白名单里的 `package.json`（且只解析顶层键）。
不读取源码或文档正文，不上传任何数据，无网络请求。
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
