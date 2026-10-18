# HANDOFF-M5 · 下一棒：单文件 HTML 报告与收口（模块⑤）

> 生成时间：2026-10-18（M4 收尾）。**本文件是跨对话的唯一上下文载体**——新对话只读它就能开工。
> 启动提示词（用户侧，绝对路径由用户自行拼接）：`/goal 读取 <项目根>\plan\HANDOFF-M5.md 继续完成任务`

## 一、当前进度 + 证据（M4 已完成）

| 交付物 | 落点 | 证据 |
|---|---|---|
| 完整性校验 | `src/devdisk/digest.py` | `tree_digest` = `sha256` over 排序后的 `f/d/l` 类型前缀 + 相对路径 + 字节数 + 内容 sha256；含空目录与链接目标（不跟随）；**不含 mtime** |
| manifest 状态机 | `src/devdisk/manifest.py` | 条目 `pending→moving→moved` / `moved→restoring→restored` / `failed:<reason>` / `conflict`；批次 `moving→complete/partial`、撤销 `undone/undo_partial`；`serialize.write_json_atomic` 原子写 |
| 隔离区 | `src/devdisk/quarantine.py` | 批次 ID `YYYYMMDD-HHMMSS-<6 hex>`（UTC、冲突重试 ≤5）；同 `st_dev` 校验（跨设备退出码 5、**不复制**）；`relative_path` 保持结构；`QuarantineError` 带退出码，由编排层映射 |
| 编排接线 | `src/devdisk/pipeline.py::run_clean/run_undo` | `--from` + `--select` 或纯 `--select`；`--apply` 需 `--yes`；`--resume` 续做；`--out` 摘要；撤销逆序 + 前后 digest 双校验 |
| CLI 退役 | `src/devdisk/cli.py` | `clean`/`undo` 移出 `NOT_READY_COMMANDS`（只剩 `report`）；新增 `--resume`、`--out`、`--stop-on-conflict`/`--continue-on-conflict` |
| 自检 | `devdisk selftest` | M4 不变量：批次 ID 形态、移动后 digest 一致、撤销回原样、**未选择退出码 1**、dry-run 不建隔离区 |
| 基准 | `tools/bench_clean.py` | 往返 digest 一致；中断恢复（第 2 条注入 `KeyboardInterrupt`）；dry-run 只读；未选择退出码 1；清理后重扫不含 `.trash` |
| 测试 | `tests/test_clean.py` | 46 项（批次 ID、digest 语义、dry-run、apply、forbidden、跨设备、嵌套、保留列表、撤销冲突/篡改/逆序、中断恢复、契约字段、静态守门） |

**本地等价校验（未接远端，CI 待首推验证）**：

| 通道 | 命令 | 结果 |
|---|---|---|
| 3.12 | `py -3.12 -m pytest -q` | 全绿 |
| 3.8 | `py -3.8 -m pytest -q` | 全绿 |
| 生成器 | `gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check` | exit 0（位级一致） |
| 扫描基准（M1 门复跑） | `bench_scan.py --tree devdisk-out/synth` | exit 0 |
| 分级基准（M2 门复跑） | `bench_grade.py --tree devdisk-out/synth` | exit 0 |
| 独立参照（M2 门复跑） | `reference_grade.py --tree ... --scan ...` | exit 0（18 样本、不一致 0） |
| 审计基准（M3 门复跑） | `bench_audit.py --tree devdisk-out/synth` | exit 0 |
| 隔离区基准（M4 门） | `bench_clean.py --tree devdisk-out/synth` | exit 0（往返 digest 一致、中断可恢复） |
| 自检 | `devdisk selftest` | exit 0（含 M4 不变量） |
| 变异验证（M4 新增 ×4） | 见下表 | **4/4 必红** |

**M4 变异验证留档（全部实测，改源码后跑门禁，复原后再全绿）**：

| # | 变异 | 必红的门 |
|---|---|---|
| 1 | `quarantine.ensure_same_device` 的 `if source_device != trash_device:` 改成 `if False:` | `test_cross_device_is_unsafe` / `test_cross_device_on_apply_does_not_copy` |
| 2 | `pipeline._restore_entries` 摘掉撤销前的 digest 比对 | `test_undo_detects_tampered_quarantine` |
| 3 | `run_clean` 在未给 `--select` 时自动取候选清单全部（伪装全选） | `test_clean_without_selection_is_refused` / `..._via_cli_returns_one` |
| 4 | dry-run 分支前插入 `ensure_trash_root` | `test_dry_run_writes_nothing` |

**M4 实测得到的本机结论（务必带走）**：

1. **`--from` 不是"全选"**：它只提供 `grade`/`reason`/`bytes`/`mtime` 的上下文。没有 `--select` 时退出码 1，这条既是产品纪律也是测试断言。变异 3 证明只要允许回退到全选，两道门立刻变红。
2. **同设备校验必须在 dry-run 之前做**：否则用户会看到"预演通过、真跑失败"（跨设备）。`device_of_existing` 用于隔离区尚未创建时向上找最近的已存在祖先。
3. **"已移动但没写成 moved"只能靠 `trash_path` 推断**：条目在 move 前落 `moving` + digest，move 后再落 `moved`；中断后若目标已在、源已消失，就补记 `moved`（`test_move_between_save_and_move_is_recovered`）。
4. **撤销默认整体停止**：`--stop-on-conflict` 是默认，`--continue-on-conflict` 才继续；两种模式都必须"不覆盖"且"已完成条目保留"。
5. **隔离区必须被忽略规则命中**：`.trash` 已在 `data/rules/ignore.txt`；自定义 `--trash` 落在扫描根内却未被忽略 → 退出码 5（否则"清理→重扫又发现"的假循环）。`test_rescan_after_clean_does_not_see_trash` 常驻。
6. **`--select` 不在候选清单里但确实是目录**：现场以父目录为扫描根重新分级；仍无证据 → `caution` 兜底（`test_explicit_directory_outside_scan_is_caution`）。**绝不默认 safe**。
7. **`keep_paths` 在 clean 里也是硬约束**：命中的选择直接退出码 5（`test_keep_paths_are_refused`），与 `scan` 的分级口径一致。
8. **dry-run 连 `--out` 摘要都不默认写**：只有显式 `--out` 才落盘；默认产物目录 `devdisk-out/` 可能已存在，不能靠"目录不存在"当断言。
9. **`bench_clean.py` 不能用 `copytree` 复制合成树**：Windows 上 junction 与环形链接会 `WinError 1921`；改为用同一 seed **重新生成**到临时目录。

## 二、下一棒待办（M5：报告与收口）

按 `plan/03` 模块⑤ 与 `plan/05` M5 DoD 执行，顺序建议：

1. `src/devdisk/report.py`：单一渲染源。输入 `scan.json`（必需）+ `audit.json`（可选）+ 隔离批次清单（可选）；页面区块顺序固定（10 段）。
2. `pipeline.run_report()`：`--input` / `--audit` / `--html` / `--top` / `--as-of`；输入缺失 → 2；输出不可写 → 2（原子写）。
3. 零外链断言常驻：生成后扫描 `<link href>`、`<script src>`、`url(http...)`、`@import`，命中即抛错（`assert_no_external_resources`）。
4. `html.escape` 覆盖全部插值（路径、理由、证据）；准备 `<>&"` 样本。
5. `cli.py`：`report` 从 `NOT_READY_COMMANDS` 退役（此时该表应为空，需同步 `test_skeleton_commands_report_not_ready` 与 `selftest`）；`selftest.RETIRED_COMMANDS` 追加 `report`；补离线 CI 冒烟。
6. `tools/bench_report.py`：报告数值（总体积 / 各级合计 / 排行顺序 / 按类型聚合）与真值逐项一致 + 零外链 + 双跑逐字节一致。
7. `tests/test_report.py`：零外链、转义、Docker 缺失展示"未审计"或降级原因、双跑一致、`--top` 边界、缺 `scan.json` → 2、编排三方对账（CLI stdout = 编排直调 = 报告章节数值）。
8. **变异验证必须补**：摘掉零外链断言 / 摘掉 `html.escape` / 允许总量四舍五入后求和 → 测试必红。
9. `plan/02` §6.5（如新增契约字段）、`plan/00` 台账、`plan/05` §六 状态回写。

**M5 出口门**：见 `plan/05-里程碑与发布.md` M5 全部复选框；收尾必须写 `plan/HANDOFF-M6.md`。

## 三、M4 已定的实现口径（动了会打挂测试）

| K | 口径 | 落点 |
|---|---|---|
| M4-1 | 批次 ID `YYYYMMDD-HHMMSS-<6 hex>`（UTC），冲突重试 ≤5 次；dry-run 展示的 ID 会被 `--apply` 优先复用 | `quarantine.make_batch_id` / `create_batch_dir` |
| M4-2 | 隔离区默认 `<source_root>/.trash`；**不创建**（dry-run）由 `resolve_trash_root` 保证，创建只在 apply 路径 | `quarantine.resolve_trash_root` / `ensure_trash_root` |
| M4-3 | 跨设备用 `st_dev` 比较（隔离区未创建时向上找最近已存在祖先）；不一致 → 退出码 5，无 `shutil.copy*` 回退 | `quarantine.ensure_same_device` / `device_of_existing` |
| M4-4 | 隔离区在扫描根内时首层目录名必须被忽略规则命中；否则退出码 5 | `pipeline._ensure_trash_ignored` |
| M4-5 | 选择项不得嵌套；隔离区不得位于选中项内部或反之 | `quarantine.ensure_not_nested` / `ensure_isolated` |
| M4-6 | 未给 `--select` → 退出码 1；`--from` 只提供上下文，绝不全选 | `pipeline.run_clean` / `_resolve_selection` |
| M4-7 | `--apply` 必须 `--yes`；非 TTY 不读终端（直接拒绝） | `pipeline._confirm_move` |
| M4-8 | 条目级状态单点定义在 `manifest.py`；失败原因取 errno 名或固定枚举，不写自由文本 | `manifest.py` |
| M4-9 | 撤销逆序；先 digest 比对（→`conflict`），再 `lexists` 目标（→`conflict`），移动后复算 digest（→`failed:digest_after_restore`） | `pipeline._restore_entries` |
| M4-10 | 撤销默认 `--stop-on-conflict`；`--continue-on-conflict` 才继续 | `pipeline.run_undo` |
| M4-11 | `tree_digest` 含类型前缀/空目录/链接目标，**不含 mtime**；`sha256:` 前缀 | `digest.py` / `plan/02` §6.3.1 |
| M4-12 | 批次摘要只在显式 `--out` 时写；dry-run 一个字节都不落 | `pipeline._write_clean_out` |
| M4-13 | `keep_paths` 命中的选择 → 退出码 5（与 scan 分级同源） | `pipeline._resolve_selection` |

原有 K1–K14（`plan/HANDOFF-M1.md` §三）、M1-1…M1-9（`plan/HANDOFF-M2.md` §三）、
M2-1…M2-11（`plan/HANDOFF-M3.md` §三）、M3-1…M3-10（`plan/HANDOFF-M4.md` §三）继续有效。

## 四、M5 必须先看的三个"坑"

1. **报告零判定**：`report.py` 只读入 JSON 并渲染，不得重算分级；聚合值从输入算（`data/README.md` §四 第 14 条）。任何"报告里再判断一次安全不安全"的写法都会让三方对账失败。
2. **确定性**：不写生成时刻/主机名/用户名。需要展示时间时用 `--as-of` 固定；两次生成必须逐字节一致（`serialize.write_text_atomic` 已保证 LF 与结尾换行）。
3. **隔离批次清单不是 manifest 数组**：M4 的批次摘要（`--out` 的 `clean.json`）默认不落盘。M5 若要展示批次，要么让用户显式传 `--batch <manifest.json>`（可重复），要么直接读 `.trash/*/manifest.json`；**不要假设 `devdisk-out/clean.json` 一定存在**。

## 五、DoD 逐项 checklist（M5 收尾时逐条打勾）

- [ ] 单文件 HTML，零外链（常驻断言 `assert_no_external_resources`）
- [ ] 排行、聚合、分级合计与真值逐项一致
- [ ] 同输入双跑逐字节一致
- [ ] Docker 缺失时报告显式展示降级说明（"未审计" vs 0 要区分）
- [ ] 路径转义样本（`<>&"`）不破坏页面
- [ ] 编排三方对账：CLI stdout = 编排直调 = 报告章节数值
- [ ] `report` 退役 `NOT_READY` + 离线 CI 冒烟 + `selftest.RETIRED_COMMANDS` 登记
- [ ] 变异验证：摘掉零外链断言 / 摘掉转义 / 允许四舍五入后求和 → 测试必红
- [ ] 静态守门：报告模块无网络 import、无外链字符串常量
- [ ] `plan/HANDOFF-M6.md` 已落盘并 commit

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

# 只读审计（M3）
py -X utf8 -m devdisk audit --root devdisk-out/synth --git \
  --docker-fixture data/fixtures/docker_system_df/v24_standard.txt \
  --out devdisk-out/audit.json
py -X utf8 tools/bench_audit.py --tree devdisk-out/synth

# 隔离区（M4）
py -X utf8 -m devdisk clean --from devdisk-out/scan.json \
  --select devdisk-out/synth/demo-web/node_modules            # dry-run
py -X utf8 -m devdisk clean --from devdisk-out/scan.json \
  --select devdisk-out/synth/demo-web/node_modules --apply --yes
py -X utf8 -m devdisk undo <batch-id> --trash devdisk-out/synth/.trash --yes
py -X utf8 tools/bench_clean.py --tree devdisk-out/synth

# 自检
devdisk selftest
```

## 七、当前已知缺口（如实）

- CI 从未实际运行：workflow 已含 M1 四步 + M2 两步 + M3 三步 + M4 两步（clean/undo 冒烟 / `bench_clean`），**"CI 绿"目前不成立**，只能写"本地等价校验 + 待首推验证"。
- 跨设备路径只有单测用 monkeypatch 覆盖 `st_dev`；本机没有第二块盘可做真实跨设备验证，留待有条件的机器/CI runner。
- 中断恢复用 `KeyboardInterrupt` 与"move 后抛错"两种注入；**没有做真实断电/进程 kill 的验证**（需要操作系统级故障注入）。
- `.trash/` 在 Windows 上删除受文件占用影响；`bench_clean.py` 的临时副本用完由 `TemporaryDirectory` 清理，若遇到占用会残留（不在门禁断言内）。
- 报告（M5）、发布（M6）未开始；M5 之前 README 首段的"HTML 报告"仍是未兑现承诺。
- 尚无远端仓库：M6 才建仓；在此之前所有 push/CI 相关项一律按"未发生"处理。
