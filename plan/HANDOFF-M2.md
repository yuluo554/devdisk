# HANDOFF-M2 · 下一棒：安全分级（模块②）

> 生成时间：2026-10-18（M1 收尾）。**本文件是跨对话的唯一上下文载体**——新对话只读它就能开工。
> 启动提示词（用户侧，绝对路径由用户自行拼接）：`/goal 读取 <项目根>\plan\HANDOFF-M2.md 继续完成任务`

## 一、当前进度 + 证据（M1 已完成）

| 交付物 | 落点 | 证据 |
|---|---|---|
| 类型 / 忽略规则表（单点数据） | `data/rules/types.json`、`data/rules/ignore.txt` | `devdisk selftest` 校验其可加载且能识别 `.git`；规则错 → 退出码 4 |
| 扫描层 | `src/devdisk/model.py`、`scanner.py`、`rules.py` | 软链不跟随（越界/环/根内三种原因分开）、junction≡symlink、`(dev,ino)` 去重、项目根、长路径前缀 |
| 编排层 | `src/devdisk/pipeline.py` | `run_scan()` 是 CLI 与测试的同一入口；JSON/CSV 都走 `serialize` 原子写 |
| CLI | `src/devdisk/cli.py` | `scan` 已从 `NOT_READY_COMMANDS` 退役；退出码 0/2/4 实测 |
| 合成树生成器 | `tools/gen_synth_tree.py` | 22 类样本 + splitmix64 + 固定 mtime；`--check` 位级一致（实测 exit 0） |
| 植入真值 | `devdisk-out/synth/truth/manifest.json`（按需生成，不入仓） | 无 `observed_*`；`expected_bytes`/`expected_mtime_utc` 由生成器按设计累加 |
| 扫描基准 | `tools/bench_scan.py` | **检出 18/18（100%）、误判 0、逐项差异 0**；`--min-bytes 0` |
| CI | `.github/workflows/ci.yml` | 新增 4 步：生成合成树 → `--check` → `scan` 离线冒烟 → `bench_scan` |

**本地等价校验（未接远端，CI 待首推验证）**：

| 通道 | 命令 | 结果 |
|---|---|---|
| 3.12 | `py -3.12 -m pytest -q` | 全绿（77 passed） |
| 3.8 | `py -3.8 -m pytest -q` | 全绿（77 passed） |
| 生成器 | `gen_synth_tree.py --check` | exit 0（位级一致） |
| 基准 | `bench_scan.py --tree devdisk-out/synth` | exit 0（误判 0，检出率达标） |
| 自检 | `devdisk selftest` | exit 0（含 M1 数据不变量） |
| 变异验证 ×8 | 类型规则错配 / 跟随软链 / 硬链接不去重 / 0 字节候选 / 目录 mtime 丢失 / 真值 bytes 偏 1 / 真值 mtime 偏 1 / 规模样本被阈值滤掉 | **全部变红**（无假门） |

**本机环境实测结论（新增，务必带走）**：

1. **本机 Windows 无 `SeCreateSymbolicLinkPrivilege`**：`os.symlink` 报 WinError 1314；生成器改用 `cmd /c mklink /J` 建 junction，扫描器把 reparse point 当链接（`symlink_kind: junction`）。测试按能力探测，不按平台名。
2. **`DirEntry.stat()` 在本机 Windows（3.8 与 3.12）返回 `st_ino=0 / st_dev=0 / st_nlink=0`**；`scanner._entry_stat()` 在 inode 缺失时退回 `os.lstat`。若不退回，硬链接去重会**静默失效**。
3. **Python 3.12 的 `datetime.utcfromtimestamp` 已弃用**：统一用 `model.utc_iso_from_ns()`（`timezone.utc` + 秒精度截断）。
4. **judgment 输出路径不要在 Git Bash 里直接看中文**：重定向到 `$TEMP` 再用 `py -3.12 -c "io.open(..., encoding='utf-8')"` 回读。

## 二、下一棒待办（M2：安全分级）

按 `plan/03-模块详设.md` 模块② 与 `data/README.md` §四 第 7/8 条执行，顺序建议：

1. `data/rules/grades.json`：分级规则表，**顺序即优先级**，规则是数据不是代码；本棒必须登记来源（`data/README.md` §二之一 新增一行）。
2. `src/devdisk/evidence.py`：证据提取。**只读白名单文件名与 `stat`，绝不打开文件正文**；`package.json` 只解析顶层键是否存在。
3. `src/devdisk/grader.py`：引擎只解释规则表，不写死规则；`reason` 由模板常量区生成；证据不足 → `caution`。
4. `src/devdisk/pipeline.py`：新增 `run_grade()`，或在 `run_scan()` 后串联；CLI 与测试共用同一入口。
5. **`scan` 输出改动**：`grade`/`reason`/`evidence` 从 M1 的占位 `null`/`null`/`[]` 变为真值；`totals.by_grade` 从 `{}` 变为三级体积合计。同时更新 `plan/02` §6.1（已预留说明）。
6. `tools/reference_grade.py`：分级的**独立参照实现**，生产模块禁止 import（AST 守门已列在 `plan/02` §三）。
7. `tests/`：每条候选 `reason` 非空、`evidence ≥ 1`；22 个样本分级与真值 `expected_grade` 逐项一致；证据不足样本全落 `caution`；两次分级逐字节一致。
8. **变异验证必须补一条**：删掉"证据不足降级"分支 → 测试必红（`plan/05` M2 DoD 明列）。

**M2 出口门**：见 `plan/05-里程碑与发布.md` §二 M2 全部复选框；收尾必须写 `plan/HANDOFF-M3.md`。

## 三、M1 已定的实现口径（动了会打挂测试）

| K | 口径 | 落点 |
|---|---|---|
| M1-1 | 候选产出即**不向下展开**：命中类型的目录整棵子树算作一个候选，嵌套同名目录不重复计数 | `scanner._walk()` |
| M1-2 | 链接判定顺序：**环 → 根外 → 目标缺失 → 根内不跟随**；原因分别 `symlink_cycle` / `outside_root` / `unreadable` / `symlink_not_followed` | `scanner.classify_link()` |
| M1-3 | 文件去重只对 `st_nlink > 1` 生效；目录去重按 `(st_dev, st_ino)`；Windows 缺 inode 时退回 `os.lstat` | `scanner._entry_stat()` |
| M1-4 | 读不到的候选目录**不产出 0 字节条目**，记 `skipped:permission_denied` | `scanner._build_candidate()` |
| M1-5 | `project_root` = 向上最近的标志文件目录；找不到为 `null`；不越过扫描根 | `scanner._find_project_root()` |
| M1-6 | mtime 取目录内文件最大值（秒精度 UTC）；无文件时退化为目录自身 mtime | `scanner._measure()` |
| M1-7 | 千目录规模样本的父目录命名**不能以 `cache-` 开头**（否则父目录先命中类型规则） | `gen_synth_tree.build_demo_mono()` |
| M1-8 | 真值 `expected_*` 由生成器按**自己写入的字节与设定时间戳**累加，禁止从扫描结果回填 | `gen_synth_tree.TreeBuilder` |
| M1-9 | junction 与 symlink 都算"链接"；`is_symlink` 为真，`symlink_kind` 区分 | `model.Candidate` |

原有 K1–K14（`plan/HANDOFF-M1.md` §三）继续有效。

## 四、M2 必须先看的三个"坑"

1. **`.git` 是候选**（type `git`），M2 必须把它判成 `forbidden`，但**不得**让它进入任何"可删合计"。`totals.by_grade` 落地后，报告层只把 `safe`/`caution` 计入可回收体积。
2. **`demo-mono/node_modules` 的共享证据来自 `skipped`**：`demo-mono/apps/app-a/node_modules` 会被跳过，`reason` 为 `symlink_not_followed`。M2 判"monorepo 共享依赖 → caution"时，数据源就是这些跳过条目的路径前缀关系（需要时可扩展 `skipped[].detail`，但**必须同步 plan/02 §6.1**）。
3. **样本 15 `demo-infra/out` 期望 caution（证据不足）**，但 `demo-infra` 里有 `Cargo.toml`。按 `plan/03` 规则 12 的字面表述它会变 safe——M2 落地时必须把"`out` 的构建证据"限定为 `package.json scripts` / `Makefile`（Rust 的产物目录是 `target`，不是 `out`），并同轮改 `plan/03` 规则 12。

## 五、DoD 逐项 checklist（M2 收尾时逐条打勾）

- [ ] `data/rules/grades.json` 为数据文件，顺序固定；引擎零硬编码规则
- [ ] 每条候选 `reason` 非空、`evidence >= 1`
- [ ] 22 个样本分级与真值 `expected_grade` 逐项一致（含 `forbidden` 的 `.git`）
- [ ] 证据不足样本（`insufficient_evidence`）全部落 `caution`
- [ ] 同输入两次分级输出逐字节一致
- [ ] 变异验证：删掉"证据不足降级"分支 → 测试必红
- [ ] `reason` 只引用路径/体积/mtime/白名单文件名，未打开任何文件正文（AST 或运行期断言）
- [ ] `plan/02` §6.1 的 `grade`/`reason`/`evidence`/`by_grade` 已从"占位"改为"已落地"
- [ ] `plan/00` 台账、`plan/05` §六 状态回写
- [ ] `plan/HANDOFF-M3.md` 已落盘并 commit

## 六、关键命令速查

```bash
# 全量测试（两个解释器各跑一遍）
py -3.12 -m pytest -q
py -3.8  -m pytest -q

# 合成树与真值（幂等重建 + 位级自检）
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check

# 扫描与基准
py -X utf8 -m devdisk scan devdisk-out/synth --out devdisk-out/scan.json --min-bytes 0
py -X utf8 tools/bench_scan.py --tree devdisk-out/synth

# 自检
devdisk selftest
```

## 七、当前已知缺口（如实）

- CI 从未实际运行：workflow 已含 M1 四步，**"CI 绿"目前不成立**，只能写"本地等价校验 + 待首推验证"。
- `M1-9` 的 `is_symlink` 在本机只可能为 `junction`；真 symlink 分支已写但**未被本机实测覆盖**（CI 的 ubuntu 矩阵会覆盖）。
- 权限拒绝样本在本机 Windows 无法构造，扫描器路径由 monkeypatch 测试覆盖；**真实 chmod 000 场景要在 ubuntu CI 上确认**。
- 尚无远端仓库：M6 才建仓；在此之前所有 push/CI 相关项一律按"未发生"处理。
