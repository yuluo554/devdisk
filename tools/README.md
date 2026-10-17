# tools/ — 生成器与基准

本目录放**可复跑的构建期脚本**，与 `src/devdisk/` 的运行时逻辑分离：

| 脚本 | 里程碑 | 状态 | 作用 |
|---|---|---|---|
| `gen_synth_tree.py` | M1 | ✅ 已落地 | 生成合成目录树 + 植入真值 `truth/manifest.json`（splitmix64、固定 mtime、`--check` 逐项一致） |
| `bench_scan.py` | M1–M2 | ✅ M1 部分 | 扫描基准：真值逐项对账（类型/体积/mtime/项目根）+ 误判 0 + 检出率 100%，逐样本点名 |
| `reference_grade.py` | M2 | ⬜ 待落地 | 分级规则的**独立参照实现**（生产模块禁止 import，AST 守门） |
| `bench_grade.py` | M2 | ⬜ 待落地 | 分级基准：误判率与证据覆盖率 |
| `bench_audit.py` | M3 | ⬜ 待落地 | Docker fixture 解析与 `.git` 统计基准 |
| `bench_clean.py` | M4 | ⬜ 待落地 | 隔离区往返基准（tree digest、中断恢复） |
| `bench_report.py` | M5 | ⬜ 待落地 | 报告数值与零外链基准 |
| `check_release.py` | M6 | ⬜ 待落地 | 发布终态自检（离线 / `--online` 两模，刻意不进 CI） |

纪律不变：**不落空壳脚本**——没有真实断言的 `pass` / `NotImplementedError` 桩会静默混过里程碑。

M1 实跑：

```bash
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018
py -X utf8 tools/gen_synth_tree.py --out devdisk-out/synth --seed 20261018 --check
py -X utf8 tools/bench_scan.py --tree devdisk-out/synth     # 退出 0 = 双边门全绿
```

> 两个脚本都会把 `src/` 插入 `sys.path`，因此未安装也能在源码树里直接跑。
