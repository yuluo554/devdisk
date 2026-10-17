# HANDOFF-M1 · 下一棒：数据先行 + 扫描（模块①）

> 生成时间：2026-10-18（M0 收尾）。**本文件是跨对话的唯一上下文载体**——新对话只读它就能开工。
> 启动提示词（用户侧，绝对路径由用户自行拼接）：`/goal 读取 <项目根>\plan\HANDOFF-M1.md 继续完成任务`

## 一、当前进度 + 证据

**M0（计划与骨架）已完成。**

| 交付物 | 落点 | 证据 |
|---|---|---|
| 计划六件套 | `plan/00`–`plan/06` | `check_topic.py` 22 PASS / 0 FAIL / 0 WARN |
| 可安装骨架 | `pyproject.toml`、`src/devdisk/` | `pip install -e ".[dev]"` 成功；`devdisk --version` → `devdisk 0.1.0` |
| CLI 契约 | `src/devdisk/cli.py`、`exit_codes.py` | 6 值退出码；5 子命令 + `selftest` 已注册 |
| 自检 | `src/devdisk/selftest.py` | `devdisk selftest` → 退出 0；摘子命令/撞退出码两种变异均变红 |
| 确定性写盘 | `src/devdisk/serialize.py` | `sort_keys` + `ensure_ascii=False` + LF + 原子替换；失败清理临时文件 |
| 纪律守门 | `tests/test_repo_discipline.py` | 无 `rmtree`、无网络 import、无 prune/gc、README 命令存在、无本机绝对路径 |
| CI | `.github/workflows/ci.yml` | 矩阵 `{ubuntu, windows} × {3.8, 3.12}`；4 条冒烟（module / console script / selftest / help） |

**本地等价校验（未接远端，CI 待首推验证）**：

| 通道 | 命令 | 结果 |
|---|---|---|
| 3.12 | `py -3.12 -m pytest` | 32 passed |
| 3.8 | `py -3.8 -m pytest` | 32 passed |
| console script | `devdisk selftest` | exit 0（仓库内与仓库外各跑一次） |
| 变异验证 ×2 | 复制仓库到临时目录后注入 `shutil.rmtree` / 伪造 README 命令 | 对应守门均变红（非假门） |

**不可本机实跑的通路（待首推验证）**：ubuntu 矩阵、GitHub Actions 环境下的 console script 与 help 冒烟。

## 二、下一棒待办（M1）

按 `plan/03-模块详设.md` 模块① 与 `plan/04-数据计划.md` §二 执行，顺序建议：

1. `data/rules/types.json` + `data/rules/ignore.txt`（规则表单点定义，生成物或手写均需登记来源）。
2. `tools/gen_synth_tree.py`：覆盖 `plan/04` §2.2 的 22 类样本；自研 splitmix64 RNG；固定 mtime；`--check` 位级一致。
3. 真值 `truth/manifest.json`：只存事实与期望，禁止 `observed_*`；`expected_bytes` 精确到字节。
4. `src/devdisk/model.py` + `scanner.py`：`os.scandir`、不跟随 symlink、`(st_dev, st_ino)` 去重、`skipped:<reason>`、项目根识别、长路径前缀。
5. `src/devdisk/pipeline.py`：`run_scan()` 公开函数（CLI 与测试共用同一入口）。
6. `cli.py`：`scan` 从 `NOT_READY_COMMANDS` 移除，接真实现；同轮补离线 CI 冒烟步骤。
7. `tests/`：真值对账、符号链接越界、环形链接、1000 目录规模、两次扫描逐字节一致、失败路径（根不存在 → 2）。
8. 基准脚本 `tools/bench_scan.py`：**双边门**（类型/体积误判 0 + 检出率达标），逐样本点名差异。

**M1 出口门**：见 `plan/05-里程碑与发布.md` §二 M1 全部复选框；收尾必须写 `plan/HANDOFF-M2.md`。

## 三、既定口径清单（K 编号，动了会打挂测试）

| K | 口径 | 落点 |
|---|---|---|
| K1 | 体积内部一律**字节 int**；展示层 1024 进制两位小数；聚合用原始字节 | `data/README.md` §四-1 |
| K2 | JSON 写盘：`sort_keys=True` + `ensure_ascii=False` + `indent=2` + UTF-8 + LF + 结尾换行 | `src/devdisk/serialize.py` |
| K3 | 退出码 0/1/2/3/4/5 语义固定；`NOT_READY` 只存在于骨架期，逐里程碑退役 | `src/devdisk/exit_codes.py`、`plan/05` §三 |
| K4 | 分级优先级 `forbidden > caution > safe`；无命中或证据不足 → `caution` | `data/README.md` §四-7 |
| K5 | `evidence` ≥ 1 条才允许 `safe`；`forbidden`/`caution` 也必须有理由字符串 | `data/README.md` §四-8 |
| K6 | 隔离区 `.trash/<batch-id>/`；`batch-id = YYYYMMDD-HHMMSS-<6 hex>`；跨设备拒绝 | `data/README.md` §四-9 |
| K7 | manifest 状态机 `pending → moving → moved`，撤销 `moved → restoring → restored`；目标存在即 `conflict` | `data/README.md` §四-10 |
| K8 | tree digest = 逐文件 `相对路径\0size\0sha256(content)` 排序拼接后取 sha256 | `data/README.md` §四-11 |
| K9 | Docker 缺失/失败 → `skipped:*` / `error:*` + 退出码 4，**不得输出 0 体积** | `data/README.md` §四-12 |
| K10 | `.git` 只读；建议固定 `applied: false`；禁止执行 `git gc`/`prune` | `plan/01` §六、`tests/test_repo_discipline.py` |
| K11 | 报告不含生成时刻/主机名/用户名；零外链；渲染层零判定 | `data/README.md` §四-14、`plan/03` 模块⑤ |
| K12 | 真值只存输入与期望，禁止 `observed_*`；测试不得手写期望值 | `data/README.md` §四-15 |
| K13 | 源码删除调用白名单：`rmtree`/`rmdir` 全禁；`remove`/`unlink` 仅限 `serialize.py` | `tests/test_repo_discipline.py` |
| K14 | 不提供"全选/一键清理全部"参数（反射 `build_parser()` 断言） | `tests/test_cli_contract.py` |

**改口径的正确姿势**：先改 `data/README.md` §四，再同步 `plan/02`/`plan/03` 引用处与代码，同轮更新本表。

## 四、本机环境坑（只写实测过的）

1. **本机有两条解释器通道**：`py -3.8`（3.8.8）与 `py -3.12`（3.12.10）。两条都已 `pip install -e .`，可直接 `py -3.x -m pytest`。跨版本行为差异仍以 CI 为准。
2. **`py -3.8 -m pip install -e` 后 `devdisk.exe` 落在 `...\Python38\Scripts`，该目录不在 PATH**——3.8 通道的 console script 冒烟要用绝对路径，或走 `py -3.8 -m devdisk`。
3. **本机是 Windows + Git Bash**：跑脚本一律 `py -X utf8`；CI 里由 `PYTHONUTF8=1` 兜底（workflow 已写）。
4. **`shutil.which('devdisk')` 当前指向 3.12 通道**——换解释器装包会覆盖同一个入口，验证"哪个解释器在跑"要看 `python -c "import devdisk; print(devdisk.__file__)"`。
5. **写盘产物的 CRLF 来自子进程**：`subprocess.run(..., stdout=PIPE)` 在 Windows 上会翻行尾；落盘前 `.replace("\r\n","\n")`（M3 起会用到）。
6. **临时验证产物一律落仓库外**（本机 `$TEMP`）——落进仓库会被下一轮遍历收进产物，也会污染 `git status`。
7. **不要用 `grep` 直接看中文文件的判定结论**：Git Bash 控制台可能显示乱码，但文件本身是对的；用 `py -3.12 -c` 以 `encoding="utf-8"` 回读或走测试断言。

## 五、DoD 逐项 checklist（M1 收尾时逐条打勾）

- [ ] `tools/gen_synth_tree.py` 覆盖 `plan/04` §2.2 全部样本
- [ ] 同 seed 两次生成逐字节一致；`--check` 退出码语义正确（一致 0 / 不一致 1）
- [ ] 真值无 `observed_*`；`expected_bytes` 由生成器精确写出
- [ ] `devdisk scan` 输出契约与 `plan/02` §6.1 一致
- [ ] 符号链接不跟随、不越界（`skipped:outside_root`）、环形链接不死循环
- [ ] `(st_dev, st_ino)` 去重生效（硬链接样本不重复计数）
- [ ] 1000 候选目录规模样本扫描通过
- [ ] 同树两次扫描 `scan.json` 逐字节一致
- [ ] 失败路径：根不存在 → 2；权限拒绝 → `skipped:permission_denied` 且继续
- [ ] `scan` 已从 `NOT_READY_COMMANDS` 移除，CI 补 `devdisk scan` 离线冒烟
- [ ] `tools/bench_scan.py` 双边门：误判 0 + 检出率达标 + 逐样本点名
- [ ] 双解释器全量 pytest 绿
- [ ] `plan/00` 台账、`plan/05` §六 状态回写
- [ ] `plan/HANDOFF-M2.md` 已落盘并 commit

## 六、关键命令速查

```bash
# 安装（本机两通道各装一次）
py -3.12 -m pip install -e ".[dev]"
py -3.8  -m pip install -e ".[dev]"

# 全量测试
py -3.12 -m pytest
py -3.8  -m pytest

# 冒烟（三条通路：模块 / console script / 自检）
py -3.12 -m devdisk --version
py -3.12 -m devdisk selftest
devdisk selftest

# 出题定稿门禁（skill 脚本，路径按本机 skill 安装位置）
py -3.12 <skill>/scripts/check_topic.py .

# M1 起会用到的数据命令（脚本尚未实现，M1 落地后有效）
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check
py -X utf8 -m devdisk scan devdisk-out/synth --out devdisk-out/scan.json
```

## 七、当前已知缺口（如实）

- CI 从未实际运行：workflow 已落盘，**"CI 绿"目前不成立**，只能写"本地等价校验 + 待首推验证"。
- 尚无 `data/rules/`、`tools/*.py`、`devdisk-out/`：M0 刻意不落空壳脚本，避免桩静默混过里程碑。
- 尚无远端仓库：M6 才建仓；在此之前所有 push/CI 相关项一律按"未发生"处理。
- 报告与审计尚未实现，README 中对应命令当前返回退出码 3（退役表见 `plan/05` §三）。
