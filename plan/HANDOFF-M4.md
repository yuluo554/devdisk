# HANDOFF-M4 · 下一棒：隔离区清理与撤销（模块④）

> 生成时间：2026-10-18（M3 收尾）。**本文件是跨对话的唯一上下文载体**——新对话只读它就能开工。
> 启动提示词（用户侧，绝对路径由用户自行拼接）：`/goal 读取 <项目根>\plan\HANDOFF-M4.md 继续完成任务`

## 一、当前进度 + 证据（M3 已完成）

| 交付物 | 落点 | 证据 |
|---|---|---|
| Docker 固定文本 fixture（4 种形态） | `data/fixtures/docker_system_df/*.txt` | `v24_standard`（带百分比）/ `v20_standard`（旧列宽、`kB`）/ `empty`（只有表头）/ `malformed`（缺列 + 乱码） |
| Docker 手写真值 | `data/fixtures/docker_system_df/expect.json` | 四类 `total_bytes`/`reclaimable_bytes`、`status`、建议命令逐项写死；**不由解析器回填** |
| Docker 解析 + 降级 | `src/devdisk/audit/docker.py` | 1024 进制整数解析；`RECLAIMABLE` 取 `(` 前半段；缺失/超时/解析失败三种状态；只有表头合法、有数据行缺整类报错 |
| `.git` 只读统计 | `src/devdisk/audit/git.py` | `objects/pack/*.pack`（不含 `.idx`）+ `objects/??/*`（不进 `info/`）；文件形态记 `skipped:gitdir_file` |
| `.git` 前后快照 | `src/devdisk/audit/git.py::snapshot` | `(相对路径, size, mtime_ns)` 排序清单；不一致抛 `GitAuditError` → `error:git_mutated` + 退出码 5 |
| 编排接线 | `src/devdisk/pipeline.py::run_audit` | 契约字段一次到位；`_docker_section` / `_git_section` / `_audit_summary_lines` |
| CLI 退役 | `src/devdisk/cli.py` | `audit` 已移出 `NOT_READY_COMMANDS`；新增 `--docker-fixture`、`--config` |
| 自检 | `devdisk selftest` | 新增 M3 不变量：fixture 可解析且非 0、畸形→`error:docker_parse`、合成 `.git` 三值精确、前后快照不变、建议 `applied:false` |
| 生成器真值 | `tools/gen_synth_tree.py` | `truth/manifest.json` 新增 `git_repos[]` / `gitdir_files[]`；`.git` 样本扩为 2 pack + 2 loose + `info/` 与 `.idx` 干扰项 + worktree `.git` 文件 |
| 审计基准 | `tools/bench_audit.py` | Docker 4 用例差异 0；`.git` 三值精确；误判 0；前后无变化；`applied:false` |
| 测试 | `tests/test_audit.py` | 37 项：四类体积、单位、百分比、畸形、缺失/超时、三值、快照、变异、worktree、建议阈值、契约、`applied:false`、双跑一致、离线冒烟、隐私（目录形态只 stat / 文件形态只读 `.git`） |

**本地等价校验（未接远端，CI 待首推验证）**：

| 通道 | 命令 | 结果 |
|---|---|---|
| 3.12 | `py -3.12 -m pytest -q` | 全绿（149 passed） |
| 3.8 | `py -3.8 -m pytest -q` | 全绿（149 passed） |
| 生成器 | `gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check` | exit 0（位级一致） |
| 扫描基准（M1 门复跑） | `bench_scan.py --tree devdisk-out/synth` | exit 0（检出 18/18、误判 0、逐项差异 0） |
| 分级基准（M2 门复跑） | `bench_grade.py --tree devdisk-out/synth` | exit 0（分级 18/18、证据缺失 0、逐项差异 0） |
| 独立参照（M2 门复跑） | `reference_grade.py --tree devdisk-out/synth --scan devdisk-out/scan.json` | exit 0（18 样本、不一致 0） |
| 审计基准 | `bench_audit.py --tree devdisk-out/synth` | exit 0（Docker 4 用例差异 0；`.git` objects=4198912 / pack=2 / loose=2、差异 0） |
| 审计 CLI（fixture + git） | `devdisk audit --root devdisk-out/synth --git --docker-fixture .../v24_standard.txt` | exit 0；Docker 四类与建议齐全；2 个仓库（1 ok + 1 `gitdir_file`） |
| Docker 缺失（PATH 隔离） | `PATH=<空目录> python -m devdisk audit --docker` | exit 4；`status=skipped:docker_unavailable`；四类体积 0（不是"成功"） |
| 自检 | `devdisk selftest` | exit 0（含 M3 审计不变量） |
| 变异验证（M3 新增 ×9） | 见下表 | **9/9 必红** |

**M3 变异验证留档（全部实测，改源码后跑门禁，复原后再全绿）**：

| # | 变异 | 必红的门 |
|---|---|---|
| 1 | 摘掉 `git.audit_one` 的前后快照比较（`if False:`） | `test_audit_detects_real_concurrent_mutation` |
| 2 | `.git` 建议的 `applied: False` 改成 `True` | `test_git_suggestions_threshold_and_applied` |
| 3 | loose 统计把 `objects/info` 也算进去 | `bench_audit` 退出 1（loose/bytes 不符） |
| 4 | `.git` 文件形态不再记 `skipped:gitdir_file` | `test_git_file_worktree_is_skipped` |
| 5 | Docker 缺失时静默返回 `status=ok` + 0 体积 | `test_missing_docker_degrades_via_path_isolation` |
| 6 | `RECLAIMABLE` 不切掉 `(45%)` 百分比 | `bench_audit` 退出 1（reclaimable 解析失败/不符） |
| 7 | 只有表头也当解析失败 | `bench_audit` 退出 1（`empty.txt` status 不符） |
| 8 | pack 统计把 `*.idx` 也算进字节与计数 | `bench_audit` 退出 1（objects/pack 不符） |
| 9 | `selftest.RETIRED_COMMANDS` 去掉 `audit` | `test_audit_is_retired_from_not_ready` |

**M3 实测得到的本机结论（务必带走）**：

1. **Docker 只有表头是合法输入**：`empty.txt` 必须解析成四类 0，而不是 `error:docker_parse`。区分点是"有没有数据行"：无数据行 → 空场景；有数据行却缺整类 → 格式变了。
2. **`error:docker_parse` 不能填四类体积**（连 0 都不给）：否则消费者会把"没读懂"读成"没有可清理内容"。只有 `skipped:*` 与 `ok` 才给四类字段。
3. **`objects/info/` 与 `*.idx` 是两个独立的干扰项**：前者是目录名不是两位十六进制，后者是后缀不是 `.pack`。合成树里同时放了两个，缺一个就会漏测。
4. **`.git` 文件形态的 `gitdir` 可能指到不存在的目录**（合成树里就是这样）：审计不得因此失败，只记目标字符串。目标在根内时 `gitdir` 给绝对路径、`reason` 里给相对路径。
5. **快照必须含 `mtime_ns`**：`test_snapshot_detects_mutation` 用"大小不变、只改 mtime"的方式反证；只比 size 会漏。
6. **`discover()` 命中 `.git` 后不再下探**：否则会进 `.git` 内部找仓库，把 `objects` 当普通目录遍历一遍（慢，且可能碰到权限问题）。
7. **`audit --git` 必须显式给 `--root`**：没有根就没有相对路径基准，也容易误扫整个磁盘。现在缺根 → 退出码 2。

## 二、下一棒待办（M4：隔离与回滚）

按 `plan/03-模块详设.md` 模块④ 与 `plan/05` M4 DoD 执行，顺序建议：

1. `src/devdisk/quarantine.py`：批次 ID（`YYYYMMDD-HHMMSS-<6 位十六进制>`，UTC，冲突重试 ≤5 次）、同文件系统校验、`shutil.move` 移动、相对路径保持。
2. `src/devdisk/manifest.py`：`pending → moving → moved` 状态机 + `failed:<reason>`；先写 pending 再逐条推进；manifest 用 `serialize.write_json_atomic`。
3. `src/devdisk/digest.py`：`sha256` over 排序后的 `relative_path\0size\0sha256(content)`；移动前算一次，撤销后重算比对。
4. `pipeline.run_clean()` / `run_undo()`：`--from scan.json` 或 `--select`（可重复）；**未给选择 → 退出码 1，不得默认全选**。
5. `cli.py`：`clean` / `undo` 从 `NOT_READY_COMMANDS` 退役；`selftest.RETIRED_COMMANDS` 追加两者；补离线 CI 冒烟。
6. `tools/bench_clean.py`：往返基准（移动 → 撤销 → digest 一致）+ 中断恢复用例。
7. `tests/test_clean.py`：默认 dry-run 不写盘、`--apply` 无 `--yes` → 1、`forbidden` → 5、跨设备 → 5、目标存在 → `conflict`、中断恢复、`.trash/` 不入 git。
8. **变异验证必须补**：摘掉同文件系统校验 / 摘掉 digest 校验 / 允许"未选择即全选" → 测试必红。

**M4 出口门**：见 `plan/05-里程碑与发布.md` M4 全部复选框；收尾必须写 `plan/HANDOFF-M5.md`。

## 三、M3 已定的实现口径（动了会打挂测试）

| K | 口径 | 落点 |
|---|---|---|
| M3-1 | Docker 体积一律 1024 进制；小数用整数运算截断，不用 float | `audit/docker.py::parse_size` |
| M3-2 | `RECLAIMABLE` 取 `(` 之前的部分；无括号直接用整串 | `audit/docker.py::parse_reclaimable` |
| M3-3 | Docker 段只有 `ok` / `skipped:*` 给四类体积；`error:docker_parse` 只给 `raw_excerpt`（≤20 行） | `audit/docker.py::parse_text` |
| M3-4 | 状态字符串单点定义在 `audit/__init__.py`；新增状态必须同步 `plan/03` 降级表 | `STATUS_*` |
| M3-5 | `.git` 统计：`pack/*.pack` + `objects/??/*`；排除 `info/`、`*.idx`、链接、非普通文件 | `audit/git.py::measure` |
| M3-6 | 前后快照是**断言**不是记录：不一致必须抛 `GitAuditError`，由编排层映射为退出码 5 | `audit/git.py::audit_one` |
| M3-7 | 快照元素为 `(相对路径, size, mtime_ns)`；不可读记 `-1` 哨兵 | `audit/git.py::snapshot` |
| M3-8 | 建议命令只为"超阈值/有可回收"的情况生成；**任何建议都固定 `applied: false`** | `docker.build_suggestions` / `git.build_suggestions` |
| M3-9 | Docker 只调用 `docker system df`；`.git` 模块不含 `subprocess` | `tests/test_repo_discipline.py` 锁住 |
| M3-10 | `audit --docker-fixture` 是验收主路径；真实调用只作可选增强 | `pipeline._docker_section` |

原有 K1–K14（`plan/HANDOFF-M1.md` §三）、M1-1…M1-9（`plan/HANDOFF-M2.md` §三）、
M2-1…M2-11（`plan/HANDOFF-M3.md` §三）继续有效。

## 四、M4 必须先看的三个"坑"

1. **`.git` 是候选也是禁止项**：M3 已把 `demo-infra/.git` 判 `forbidden`，`clean` 必须直接拒绝它（退出码 5），不能因为"审计过"就放行。
2. **`.trash/` 位于扫描根内**：`scan` 已通过 `data/rules/ignore.txt` 的 `.trash` 忽略它；`clean` 写隔离区后必须保证**再次扫描不会把隔离区当候选**（否则会形成"清理 → 又发现 → 再清理"的假循环）。
3. **`undo` 的目标可能已被占用**：撤销前先 `lexists` 检查原路径；存在即 `conflict`，**不覆盖**，退出码 1，并保留已完成条目。

## 五、DoD 逐项 checklist（M4 收尾时逐条打勾）

- [ ] `clean` 默认 dry-run，不写盘（断言隔离区目录不存在）
- [ ] `--apply` 无 `--yes` → 退出码 1；有 `--yes` → 移动
- [ ] manifest 状态机 `pending → moving → moved`，中断可恢复
- [ ] `undo` 后 tree digest 与移动前一致
- [ ] 目标已存在 → `conflict`，不覆盖
- [ ] 跨设备 → 退出码 5，不复制
- [ ] `forbidden` 候选 → 退出码 5
- [ ] 无 `--from` / `--select` → 退出码 1（无"全选"）
- [ ] 静态守门：无 `shutil.rmtree`、无删除候选目录的 `os.remove`、CLI 无全选参数
- [ ] `.trash/` 不入 git（`.gitignore` 解析测试）
- [ ] `clean` / `undo` 退役 `NOT_READY` + 离线 CI 冒烟 + `selftest.RETIRED_COMMANDS` 登记
- [ ] 变异验证：摘掉同文件系统校验 / digest 校验 / 选择必填 → 测试必红
- [ ] `plan/02` §6.3 契约与实现逐字段一致；`plan/00` 台账、`plan/05` §六 状态回写
- [ ] `plan/HANDOFF-M5.md` 已落盘并 commit

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

# 自检
devdisk selftest
```

## 七、当前已知缺口（如实）

- CI 从未实际运行：workflow 已含 M1 四步 + M2 两步 + M3 三步（审计冒烟 / Docker 缺失降级冒烟 / `bench_audit`），**"CI 绿"目前不成立**，只能写"本地等价校验 + 待首推验证"。
- Docker 真实调用路径（`run_docker` 的 `subprocess.run`）只有单测用 monkeypatch 覆盖；本机未装 Docker，**真实 `docker system df` 输出未实测**。fixture 已覆盖新版/旧版/空/畸形四种形态，新增版本形态时补 fixture 即可。
- Windows 上 `PATH` 隔离不一定能挡住 `docker.exe` 被系统找到；CI 的 bash 步骤用显式解析后的 `python` 绝对路径执行，测试里则直接 monkeypatch `subprocess.run`。真实"无 Docker 机器"的端到端验证留待 ubuntu runner。
- `.git` 审计只覆盖合成结构，**未对真实 `git init` 仓库实测**（`plan/04` §四 已说明这是刻意取舍：不依赖 git 可用性与版本差异）。
- M3 变异验证里，M1/M2 的门只做了回归复跑（生成器 `--check`、`bench_scan`、`bench_grade`、`reference_grade`），**未重跑 M1/M2 的变异表**；那些留档口径继续按 `plan/HANDOFF-M2.md` / `plan/HANDOFF-M3.md` §一 有效。
- 尚无远端仓库：M6 才建仓；在此之前所有 push/CI 相关项一律按"未发生"处理。
