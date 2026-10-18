# HANDOFF-M3 · 下一棒：只读审计（模块③）

> 生成时间：2026-10-18（M2 收尾）。**本文件是跨对话的唯一上下文载体**——新对话只读它就能开工。
> 启动提示词（用户侧，绝对路径由用户自行拼接）：`/goal 读取 <项目根>\plan\HANDOFF-M3.md 继续完成任务`

## 一、当前进度 + 证据（M2 已完成）

| 交付物 | 落点 | 证据 |
|---|---|---|
| 分级规则表（数据，顺序即优先级） | `data/rules/grades.json` | 载入期校验：`order` 与 `rules` 数组一致、等级单调、证据源/模式组名合法、`reason` 必须含 `{evidence}`；坏表 → 退出码 4 |
| 证据提取 | `src/devdisk/evidence.py` | 只读文件名 + `stat`；**唯一 `open()`** 是白名单文件 `package.json`（仅顶层键）；每候选只遍历一次（流式聚合） |
| 分级引擎 | `src/devdisk/grader.py` | 只解释规则表（零硬编码规则）；证据门槛降级；兜底 `caution` |
| 编排接线 | `src/devdisk/pipeline.py` | `run_scan()` 内串联分级；新增 `run_grade()`（CLI/测试共用同一入口） |
| `scan` 输出改动 | `scan.json` | `grade`/`reason`/`evidence` 由占位变真值；`totals.by_grade` 三级键恒在（禁止项不计入可回收体积） |
| 自检 | `devdisk selftest` | 新增 M2 不变量：分级表可加载、`git` 型必 `forbidden`、无证据必 `caution` |
| 分级基准 | `tools/bench_grade.py` | **分级 18/18 一致、理由/证据缺失 0、逐项差异 0** |
| 独立参照实现 | `tools/reference_grade.py` | 候选清单取自植入真值、规则逻辑另行抄写、**不 import 生产模块**；18/18 PASS |

**本地等价校验（未接远端，CI 待首推验证）**：

| 通道 | 命令 | 结果 |
|---|---|---|
| 3.12 | `py -3.12 -m pytest -q` | 全绿（108 collected） |
| 3.8 | `py -3.8 -m pytest -q` | 全绿（108 collected） |
| 生成器 | `gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check` | exit 0（位级一致） |
| 扫描基准（M1 门复跑） | `bench_scan.py --tree devdisk-out/synth` | exit 0（检出 18/18、误判 0、逐项差异 0） |
| 分级基准 | `bench_grade.py --tree devdisk-out/synth` | exit 0（分级 18/18、证据缺失 0、逐项差异 0） |
| 独立参照 | `reference_grade.py --tree devdisk-out/synth --scan devdisk-out/scan.json` | exit 0（对账 18 样本、不一致 0） |
| 自检 | `devdisk selftest` | exit 0（含 M2 分级不变量） |
| 变异验证（M2 新增） | 删掉"证据不足降级"分支（`grader._finalize` 改成原样返回） | **必红**：常驻门报告"evidence 少于 1 条"（`tests/test_grader.py::test_mutation_removing_evidence_gate_turns_red`）；源码级实测同轮红 4 项 |
| 变异验证（M2 新增） | 摘掉"读不全就不给 safe"分支（`or unreadable` 去掉） | **必红**：`test_unreadable_subtree_refuses_safe` + 变异锚点用例红 2 项 |
| 变异验证（源码级） | 从 `safe:dist_build_config` 删掉 `package.json scripts` 证据源 | **必红**：`bench_grade` 17/18（`demo-web/dist` 期望 safe / 实际 caution）+ pytest 3 项红 |
| 变异验证（源码级） | 摘掉 `evidence._read_json_keys` 的 open 白名单并顺手读项目根文件正文 | **必红**：`test_evidence_never_opens_non_whitelisted_files` |
| 变异验证（源码级） | `grades.json` 的 `order` 与 `rules` 数组不一致 | **必红**：载入期 `RulesError` → `bench_grade` 退出码 1（退出码 4 语义） |
| 变异验证（源码级） | `totals.by_grade` 退回稀疏（三键缺失） | **必红**：`test_totals_by_grade_always_has_three_keys` + `bench_grade`（合计不闭合） |
| 变异验证（M1 门复跑） | 硬链接不去重（`st_nlink > 1` 分支失效） | **必红**：`test_hardlink_counted_once`（200 ≠ 100） |
| 变异验证（M1 门复跑） | 生成器真值 `expected_bytes` 偏 1 | **必红**：`test_synth_tree_matches_truth` |
| 变异验证（**非假门发现**） | 只从 `local_state` 模式组删掉 `.env` | **不会变红**（不是漏网）：同一份 `.env` 仍被 `manual_config` 组 + `node_modules` 规则的 `veto` 拦住 → 结论依旧是 `caution`，属于刻意的双重覆盖；改表时若想真正改判定，必须同时动两组（`test_patterns_come_from_the_rule_table` 就是这么做的） |

**M2 实测得到的本机结论（务必带走）**：

1. **`demo-infra` 同时有 `Cargo.toml` 与 `Makefile`**：如果 `out` 的构建证据允许 `Makefile`，样本 15 会误判 `safe`。M2 把 `out` 的证据**只限定为 `package.json` 顶层 `scripts`**（`safe:out_build_config`），`target` 才认 `Cargo.toml`；`plan/03` 规则 12 已同轮改成按类型分列。
2. **共享依赖的证据不在候选清单里，而在 `skipped` 里**：`demo-mono/apps/app-a/node_modules` 是根内链接（`skipped:symlink_not_followed`，`detail` 为目标路径）。判"多项目共享"的做法是：链接目标落在候选目录内 **且** 引用方项目根（由 `scanner.find_project_root()` 现算）与本候选的 `project_root` 不同。
3. **`node_modules` 里混进 `.env` 会同时命中"本地状态"与"手工文件晚于产物"两条模式组**：两组的绑定关系全在规则表里，引擎不知道任何模式串（有守门测试断言引擎源码里不出现规则表里的类型名/模式串字面量）。
4. **`demo-web/dist` 的手工文件判定基准是"目录内非手工文件的最大 mtime"**，不是项目根配置文件的时间戳；否则正常产物也会被判 `caution`。
5. **`totals.by_grade` 三个键恒在**（含 0）；报告层（M5）的可删合计只加 `safe` + `caution`。
6. **判断 `package.json` 是否存在要区分"不存在"与"解析失败"**：不存在只是没有这条证据（不记 `evidence_error`），解析失败才记 `evidence_error` 并按无证据处理 → `caution`。

## 二、下一棒待办（M3：Docker 与 `.git` 只读审计）

按 `plan/03-模块详设.md` 模块③ 与 `plan/04` §三/§四执行，顺序建议：

1. `data/fixtures/docker_system_df/*.txt`：至少四份手写脱敏文本（新版 / 旧版 / 只有表头 / 缺列乱码），登记来源到 `data/README.md` §二之一 与 `plan/04` §一。
2. `src/devdisk/audit/docker.py`：解析 `docker system df` 固定列（`TYPE TOTAL ACTIVE SIZE RECLAIMABLE` 四类），体积字符串按 1024 进制；`RECLAIMABLE` 形如 `1.2GB (45%)` 取前半段；缺失 → `skipped:docker_unavailable`（退出码 4）；解析失败 → `error:docker_parse` + 原文前 20 行。
3. `src/devdisk/audit/git.py`：`.git` 目录（普通库）或文件（worktree/submodule，记 `skipped:gitdir_file`）；统计 `objects/pack/*.pack` 字节和与文件数、`objects/??/*` 松散对象数与字节和（不统计 `objects/info/`）；**审计前后对 `.git` 做 `(相对路径, size, mtime_ns)` 快照并断言不变**（不一致 → `error:git_mutated`，退出码 5）。
4. `src/devdisk/pipeline.py`：新增 `run_audit()`；`plan/02` §6.2 的契约字段一次到位（`applied: false` 贯穿）。
5. `cli.py`：`audit` 从 `NOT_READY_COMMANDS` 退役 + `selftest.RETIRED_COMMANDS` 加 `audit`；补离线 CI 冒烟。
6. `tools/bench_audit.py`：fixture 解析与合成仓库统计的双边门（真值手写在 fixture 旁，不由解析器回填）。
7. `tests/`：三值精确匹配、四类体积解析、降级路径（用 `PATH` 隔离模拟 Docker 缺失）、`applied == false`、静态守门（`src/` 无 `git gc`/`git prune`/`docker * prune` 执行）、审计前后 `.git` 元数据不变。
8. **变异验证必须补**：把 `.git` 快照断言摘掉（或让 `git gc` 命令被执行）→ 测试必红。

**M3 出口门**：见 `plan/05-里程碑与发布.md` M3 全部复选框；收尾必须写 `plan/HANDOFF-M4.md`。

## 三、M2 已定的实现口径（动了会打挂测试）

| K | 口径 | 落点 |
|---|---|---|
| M2-1 | 规则表数组顺序即优先级；`order` 是同一事实的显式声明，加载期校验两者一致 + 等级单调（forbidden→caution→safe） | `rules.GradeRules.load` |
| M2-2 | 规则 id 形如 `<grade>:<name>`，前缀必须等于 `grade`；`reason` 模板必须含 `{evidence}` | `rules.GradeRule` |
| M2-3 | 证据源是**词表**（12 个），三处必须一致：规则表 `sources` 段、`rules.GRADE_SOURCES`、`evidence._SOURCES` | `tests/test_grader.py::test_grade_rules_contract` 锁住 |
| M2-4 | 证据提取每候选只遍历一次（流式聚合）；不跟随链接；读不全的目录记入 `facts.unreadable` | `evidence.CandidateEvidence` |
| M2-5 | **唯一允许打开**的文件是 `package.json`（仅顶层键）；文件不存在不算错误、解析失败记 `evidence_error` | `evidence._read_json_keys` |
| M2-6 | 证据 `path` 一律相对路径（候选内→相对候选、项目根内→相对项目根、候选之外→相对扫描根）；`reason` 里不得出现绝对路径 | `evidence._rel` / `grader.GradeContext.rel_to_root` |
| M2-7 | 证据摘要最多列 3 条，多余写"等 N 项" | `grader.SUMMARY_LIMIT` |
| M2-8 | 证据门槛：`safe` 必须有证据且候选子树全部可读；否则降级 `caution`（分支在 `Grader._finalize`，变异验证锚点）；无规则命中 → 兜底 `caution` | `grader.Grader._finalize` / `_fallback` |
| M2-9 | `totals.by_grade` 三级键恒在（含 0）；`model.GRADES` / `GRADE_LABELS` 是等级与中文名的单点 | `model.totals()` |
| M2-10 | 分级规则表加载在 `rules.py`（`load_grade_rules`），`grader.py` 零规则知识；`evidence.py` 零判定 | `plan/02` §十二 偏差登记 |
| M2-11 | 参照实现不得被生产模块 import，且自身不 import `devdisk` | `tests/test_repo_discipline.py` |

原有 K1–K14（`plan/HANDOFF-M1.md` §三）与 M1-1…M1-9（`plan/HANDOFF-M2.md` §三）继续有效。

## 四、M3 必须先看的三个"坑"

1. **Docker 真实环境不保证存在**：本机与 CI 都未必有 Docker Desktop。**fixture 是验收主路径**；真实调用只作可选增强，缺失必须走 `skipped:docker_unavailable` + 退出码 4，不得静默输出 0 体积。
2. **`.git` 是候选也是禁止项**：`scan` 已把 `demo-infra/.git` 判 `forbidden`；`audit` 只能**只读统计**它，绝不能因为"禁止删除"就跳过审计，也不能让它进入任何"可回收体积"。
3. **审计前后快照必须包含 mtime_ns 与相对路径**：只比字节数会漏掉"被改写但大小不变"的破坏；相对路径保证日志可复现且不含本机绝对路径。

## 五、DoD 逐项 checklist（M3 收尾时逐条打勾）

- [ ] Docker 四类体积解析对 ≥3 种 fixture 正确；`malformed.txt` → `error:docker_parse`
- [ ] Docker 缺失 → `skipped:docker_unavailable` + 退出码 4（用 `PATH` 隔离模拟）
- [ ] `.git` 三值（`objects_bytes`/`pack_files`/`loose_objects`）对合成仓库精确一致
- [ ] 审计前后 `.git` 元数据清单（相对路径 + size + mtime_ns）不变（断言）
- [ ] 静态守门：`src/` 无 `git gc` / `git prune` / `docker * prune` 执行调用
- [ ] 所有建议 `applied == false`
- [ ] `audit` 退役 `NOT_READY` + 离线 CI 冒烟 + `selftest.RETIRED_COMMANDS` 登记
- [ ] 变异验证：摘掉 `.git` 快照断言 → 测试必红
- [ ] `plan/02` §6.2 契约与实现逐字段一致；`plan/00` 台账、`plan/05` §六 状态回写
- [ ] `plan/HANDOFF-M4.md` 已落盘并 commit

## 六、关键命令速查

```bash
# 全量测试（两个解释器各跑一遍）
py -3.12 -m pytest -q
py -3.8  -m pytest -q

# 合成树与真值（幂等重建 + 位级自检）
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check

# 扫描（含分级）、双基准、独立参照
py -X utf8 -m devdisk scan devdisk-out/synth --out devdisk-out/scan.json --min-bytes 0
py -X utf8 tools/bench_scan.py --tree devdisk-out/synth
py -X utf8 tools/bench_grade.py --tree devdisk-out/synth
py -X utf8 tools/reference_grade.py --tree devdisk-out/synth --scan devdisk-out/scan.json

# 自检
devdisk selftest
```

## 七、当前已知缺口（如实）

- CI 从未实际运行：workflow 已含 M1 四步 + M2 两步（`bench_grade` / `reference_grade`），**"CI 绿"目前不成立**，只能写"本地等价校验 + 待首推验证"。
- `M2-5` 的"只打开 `package.json`"是**运行期断言**（monkeypatch `open` 记录），不是静态证明；新增证据源时该测试会立刻发现越界。
- 权限拒绝样本在本机 Windows 无法构造；"读不全就不给 safe"的用例用 monkeypatch `os.scandir` 模拟，**真实 chmod 000 场景要在 ubuntu CI 上确认**。
- M2 的变异验证里，M1 变异表只复跑了 2 条（硬链接去重、真值 bytes 偏 1）作为回归抽查——本次改动了 `scanner.py`（抽出 `find_project_root`）与 `rules.py`（新增分级表加载）。M1 的另外 6 条仍按 `plan/05` M1 行的留档口径有效，但**未在 M2 重跑**。
- 尚无远端仓库：M6 才建仓；在此之前所有 push/CI 相关项一律按"未发生"处理。
