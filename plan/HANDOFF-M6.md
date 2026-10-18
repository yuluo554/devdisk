# HANDOFF-M6 · 下一棒：发布与终态收口（M6）

> 生成时间：2026-10-18（M5 收尾）。**本文件是跨对话的唯一上下文载体**——新对话只读它就能开工。
> 启动提示词（用户侧，绝对路径由用户自行拼接）：`/goal 读取 <项目根>\plan\HANDOFF-M6.md 继续完成任务`

## 一、当前进度 + 证据（M1–M5 全部完成）

| 里程碑 | 交付物 | 落点 | 证据 |
|---|---|---|---|
| M1 | 扫描 + 合成树 + 真值 | `scanner.py` / `gen_synth_tree.py` | `bench_scan.py`：检出 18/18、误判 0、逐项差异 0 |
| M2 | 安全分级 | `grader.py` / `evidence.py` / `rules.py` | `bench_grade.py` 逐项差异 0；`reference_grade.py` 18/18 |
| M3 | Docker 与 `.git` 只读审计 | `audit/` | `bench_audit.py`：4 个 fixture 差异 0、`.git` 三值精确 |
| M4 | 隔离区清理与撤销 | `quarantine.py` / `manifest.py` / `digest.py` | `bench_clean.py`：往返 digest 一致、中断可恢复 |
| M5 | 单文件 HTML 报告 | `report.py` + `pipeline.run_report()` | `bench_report.py`：数值对真值、零外链、双跑一致 |

**M5 新增落点**：

| 文件 | 内容 |
|---|---|
| `src/devdisk/report.py` | 单一渲染源：读入 `scan.json`（必需）/ `audit.json`（可选）/ 批次 manifest（可选），聚合 → 渲染。10 段固定顺序 `SECTION_ORDER`；`assert_no_external_resources` 与 `assert_no_promise_wording` 在 `render_html()` 出口常驻；全量 `html.escape`；数值节点带 `data-metric`/`data-value` |
| `src/devdisk/pipeline.py::run_report()` | `--input` / `--audit` / `--html` / `--top` / `--as-of` / `--batch` / `--trash`；输入缺失或坏 → 2；零外链命中 → 2；原子写 |
| `src/devdisk/cli.py` | `report` 移出 `NOT_READY_COMMANDS`（**该表已清空**）；新增 `--as-of` / `--batch` / `--trash` |
| `src/devdisk/selftest.py` | `RETIRED_COMMANDS` 追加 `report`；新增 M5 不变量（零外链 / 转义 / 双跑一致 / 10 段顺序） |
| `tools/gen_synth_tree.py` | 新增 `build_report_expect()`，与真值同一次生成写出 `truth/report_expect.json`（**不复用报告实现**） |
| `tools/bench_report.py` | 报告基准：真值聚合对账 + **从 HTML 读回数值** + 零外链 + 双跑一致 + 转义样本 + Docker 降级可见 |
| `tests/test_report.py` | 48 项：入口契约、零外链、转义、数值对账、`--top` 边界、确定性、10 段顺序、批次、三方对账、静态守门 |
| `.github/workflows/ci.yml` | 追加 M5 两步（report 离线冒烟 / `bench_report.py`）— **仍未实际运行** |

**本地等价校验（未接远端，CI 待首推验证）**：

| 通道 | 命令 | 结果 |
|---|---|---|
| 3.12 | `py -3.12 -m pytest` | 248 passed |
| 3.8 | `py -3.8 -m pytest` | 248 passed |
| 生成器 | `gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check` | exit 0（位级一致，含 `report_expect.json`） |
| 扫描基准 | `bench_scan.py --tree devdisk-out/synth` | exit 0 |
| 分级基准 | `bench_grade.py --tree devdisk-out/synth` | exit 0 |
| 独立参照 | `reference_grade.py ...` | exit 0（18 样本、不一致 0） |
| 审计基准 | `bench_audit.py --tree devdisk-out/synth` | exit 0 |
| 隔离区基准 | `bench_clean.py --tree devdisk-out/synth` | exit 0 |
| 报告基准 | `bench_report.py --tree devdisk-out/synth` | exit 0（问题 0，报告 671181 字节） |
| 自检 | `devdisk selftest` | exit 0（含 M5 不变量） |
| 变异验证（M5 新增 ×3） | 见下表 | **3/3 必红** |

**M5 变异验证留档（全部实测，改源码后跑门禁，复原后再全绿）**：

| # | 变异 | 必红的门 |
|---|---|---|
| 1 | `render_html()` 里删掉 `assert_no_external_resources(document)` 调用 | `test_report_writes_nothing_on_render_failure` / `test_render_rejects_poisoned_style` / `test_render_rejects_poisoned_script` / `test_pipeline_maps_guard_failure_to_usage` |
| 2 | `_esc()` 改成 `return str(value)`（摘掉 `html.escape`） | `test_escaping_covers_paths_reasons_evidence` / `test_escaping_keeps_page_readable` |
| 3 | `aggregate()` 里先 `round(bytes/1024)*1024` 再求和 | `test_zero_rounding_does_not_change_sums` / `test_stdout_matches_synth_truth_gate` |

## 二、下一棒待办（M6：发布）

按 `plan/05` §四 执行，顺序固定：

1. **全量回归**（双解释器 pytest + 生成器 `--check` + 五个基准 + `selftest`）。
2. **干净 clone + 全新 venv**：按 README 逐条跑通，逐条记录退出码与语义。
3. **脱敏四步**：真实路径/用户名/主机名扫描、产物扫描（`devdisk-out/`、`.trash/`、`reports/` 不入仓）、提交元数据邮箱检查（`yuluo554 <282769740+yuluo554@users.noreply.github.com>`）。
4. **远端仓库**：创建 GitHub 公开仓库 → push → 四矩阵 CI 全绿（首次真跑）。
5. **确定性源码包**：`git archive` 语义 + `gzip -9n` → sha256 留档 → 与 tag 树逐字节同源。
6. **tag + Release**：用户确认后打 annotated tag → 推送 → `gh release create <tag> <artifact> --notes-file plan/RELEASE-NOTES-M6.md`。
7. **发布后复核**：真下载 → sha256 → 中立目录解包 → `devdisk selftest` 通过；`check_release.py` / `check_topic.py` 无 FAIL。
8. **回填**：`plan/05` §六 M6 行、`plan/00` 台账、`plan/RELEASE-M6.md`、README 状态行。

## 三、M5 已定的实现口径（动了会打挂测试）

| K | 口径 | 落点 |
|---|---|---|
| M5-1 | `render_html()` 的出口必须调用 `assert_no_external_resources` 与 `assert_no_promise_wording`；不是"测试里事后检查" | `report.render_html` |
| M5-2 | 零外链扫描包含 `<link href>`、`<script src>`、`url(//...)`、`@import`、内嵌外部资源标签、**任何 `http(s)://` 字面量** | `report.EXTERNAL_PATTERNS` |
| M5-3 | 所有插值只经 `report._esc()`（`html.escape(..., quote=True)`），禁止裸 f-string 拼接 | `report._esc` |
| M5-4 | 聚合只用原始字节；展示层 `model.format_bytes`；占比用整数运算（`_percent`） | `report.aggregate` / `_percent` |
| M5-5 | 报告默认不含任何时间；`--as-of` 是唯一展示时间来源 | `report.render_html` / `_render_source` |
| M5-6 | 页面 10 段顺序固定，测试按 `id` 断言 | `report.SECTION_ORDER` |
| M5-7 | 缺 `audit.json` 或 `status != ok` → 显式"未审计" + 原始状态/原因，**不输出 0 体积指标** | `_render_docker` / `_render_git` |
| M5-8 | `report` 只消费 JSON：不 import `grader`/`rules`、不调用 `Scanner` | `report.py` / 静态守门 |
| M5-9 | `scan.json` 汇总与明细不一致 → 退出码 2（不静默渲染错误总数） | `report.validate_scan` |
| M5-10 | 报告真值由生成器独立推导，不复用报告实现 | `gen_synth_tree.build_report_expect` |
| M5-11 | 批次只从显式 `--batch` 或 `--trash` 读取；**不假设 `devdisk-out/clean.json` 存在** | `report.load_batches` |
| M5-12 | `--top 0` 表示不列排行，但总览/聚合/分级不受影响 | `report.aggregate` |

原有 K1–K14、M1-1…M1-9、M2-1…M2-11、M3-1…M3-10、M4-1…M4-13 继续有效。

## 四、M6 必须先看的三个"坑"

1. **CI 从未真跑过**：workflow 已含 M0–M5 全部步骤，但"CI 绿"目前不成立。首次 push 后必须逐 step 对账，别把"workflow 写了"当成"CI 通过"。
2. **合成树与报告产物都不入仓**：`devdisk-out/` 已在 `.gitignore`；干净 clone 后必须先跑生成器，否则 `bench_report.py` / `bench_clean.py` 会以退出码 2 拒绝运行（这是刻意的）。
3. **报告含本机绝对路径**：`scan.json` 的 `roots`/`candidates[].path` 是绝对路径，报告会把它们原样渲染（用户需要可直接复制）。发布前**不要把 `devdisk-out/report.html` 提交**；README 示例统一用 `~/code` 这类可移植写法，仓库内不得出现本机绝对路径（`tests/test_repo_discipline.py` 已有守门）。

## 五、DoD 逐项 checklist（M6 收尾时逐条打勾）

- [ ] 干净 clone + 全新 venv 按 README 逐条跑通（逐条记录退出码与语义）
- [ ] 脱敏四步 + 产物扫描 + 提交元数据邮箱检查留档
- [ ] CI 四矩阵全绿；push/tag 事件与 run 逐条对账
- [ ] 手工确定性源码包（`git archive` 语义）→ sha256 留档 → 与 tag 树逐字节同源
- [ ] `git tag` + GitHub Release（附产物）+ `plan/RELEASE-M6.md` 三件套齐备
- [ ] `check_release.py` 无 FAIL；`check_topic.py` 无 FAIL
- [ ] 发布物本体复核：真下载 → sha256 → 中立目录解包 → `devdisk selftest` 通过
- [ ] README 状态行、`plan/00` 台账、`plan/05` §六 同轮回写

## 六、关键命令速查

```bash
# 全量测试（两个解释器各跑一遍）
py -3.12 -m pytest -q
py -3.8  -m pytest -q

# 合成树、候选清单、审计（M5 报告的前置输入）
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check
py -X utf8 -m devdisk scan devdisk-out/synth --out devdisk-out/scan.json --min-bytes 0
py -X utf8 -m devdisk audit --root devdisk-out/synth --git \
  --docker-fixture data/fixtures/docker_system_df/v24_standard.txt \
  --out devdisk-out/audit.json

# 报告（M5）
py -X utf8 -m devdisk report --input devdisk-out/scan.json \
  --audit devdisk-out/audit.json --html devdisk-out/report.html --top 50
py -X utf8 tools/bench_report.py --tree devdisk-out/synth

# 全部基准
py -X utf8 tools/bench_scan.py --tree devdisk-out/synth
py -X utf8 tools/bench_grade.py --tree devdisk-out/synth
py -X utf8 tools/reference_grade.py --tree devdisk-out/synth --scan devdisk-out/scan.json
py -X utf8 tools/bench_audit.py --tree devdisk-out/synth
py -X utf8 tools/bench_clean.py --tree devdisk-out/synth

# 自检
devdisk selftest
```

## 七、当前已知缺口（如实）

- **CI 从未实际运行**：workflow 含 M0–M5 全部步骤，但"CI 绿"目前不成立，只能写"本地等价校验 + 待首推验证"。
- 跨设备路径只有单测 monkeypatch 覆盖 `st_dev`；本机没有第二块盘。
- 中断恢复用 `KeyboardInterrupt` 与"move 后抛错"注入；没有真实断电/进程 kill 验证。
- 报告的手工"断网双击打开"只做过一次本机检查（Windows 默认浏览器），未在 Linux/macOS 浏览器复核；零外链靠常驻断言而不是浏览器网络面板。
- 报告在 1000+ 候选时约 670 KB（可接受，未做分页/虚拟滚动）。
- 尚无远端仓库；M6 之前所有 push/CI 相关项一律按"未发生"处理。
- `CHECK` 脚本 `check_release.py` / `check_topic.py` 尚需在 M6 按其契约运行（前者属发布终态自检）。
