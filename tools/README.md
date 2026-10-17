# tools/ — 生成器与基准（M1 起填充）

本目录放**可复跑的构建期脚本**，与 `src/devdisk/` 的运行时逻辑分离：

| 脚本 | 里程碑 | 作用 |
|---|---|---|
| `gen_synth_tree.py` | M1 | 生成合成目录树 + 植入真值 `truth/manifest.json`（固定 seed，`--check` 位级一致） |
| `reference_grade.py` | M2 | 分级规则的**独立参照实现**（生产模块禁止 import，AST 守门） |
| `bench_scan.py` | M1–M2 | 扫描/分级基准：对真值逐项对账，检出率与误判率双边断言 |
| `bench_audit.py` | M3 | Docker fixture 解析与 `.git` 统计基准 |
| `bench_clean.py` | M4 | 隔离区往返基准（tree digest、中断恢复） |
| `bench_report.py` | M5 | 报告数值与零外链基准 |
| `check_release.py` | M6 | 发布终态自检（离线 / `--online` 两模，刻意不进 CI） |

M0 阶段本目录只有本说明，**不落任何空壳脚本**——空 `pass` 与 `NotImplementedError` 桩会静默混过里程碑。
