DevDiskAudit v0.1.1
===================

> 相对 v0.1.0 的**打包与口径**修复；产品语义（分级、隔离区、撤销、报告）一行未改。

高光
----

- **装完即用**：规则表与 Docker fixture 随 wheel 分发（包内 `devdisk.data`）。
  `pip install`（非 editable）后从**任意工作目录**都能直接 `devdisk scan`，
  不再需要 `DEVDISK_DATA_DIR`。该环境变量保留为**覆盖**入口。
- **sdist 自足**：新增 `MANIFEST.in`，`python -m build --sdist` 的产物含
  `data/rules`、`data/fixtures`、`tools/`、`plan/`，解包后可直接安装并复跑全部基准。
- **发布物口径写清**：Release 附件是**手工源码包**（`git archive <tag>`），
  不是 `python -m build` 的产物——README、常驻测试、发布门三处同一口径。
- **floor 抬到 3.12**：3.8 已于 2024-10-07 EOL、3.10 已于 2026-10-01 EOL；
  CI 矩阵收敛为 windows/ubuntu × 3.12。
- **打包元数据零告警**：SPDX `License-Expression: MIT` + `license-files`，
  删掉 `License ::` classifier，`setuptools>=77`；`python -m build` 输出 0 条 WARNING。
- **CI actions 升 v7**：消除每次 run 的 `Node.js 20 is deprecated` 注解。
- **新增发布终态门** `tools/check_release.py`：13 项判据（tag 形态/版本一致/README 已发布态/
  附件与 tag 树逐文件同源/包形态/元数据/脱敏）+ `--probe` 10 组可证伪探针。

升级提示
--------

- Python **3.12+**（v0.1.0 起声明的 3.8 已 EOL）。3.8/3.9/3.10 不再受支持。
- `DEVDISK_DATA_DIR` 从"必需"变为"可选覆盖"；已有的设置可以保留，行为不变。
- 退出码契约不变：Docker 缺失 = 4、规则缺失 = 4、扫描根不存在 = 2。

校验
----

发布物：`devdisk-0.1.1-src.tar.gz`（`git archive v0.1.1` + `gzip -9n`，确定性）。
sha256 登记在 **Release 说明正文**（不写进包内文件，避免自指）。

```bash
# 与 tag 树同源（在自己的 clone 里复算）
git archive --format=tar --prefix=devdisk-0.1.1/ v0.1.1 | gzip -9n > rebuilt.tar.gz
cmp rebuilt.tar.gz devdisk-0.1.1-src.tar.gz && echo "与 tag 树同源"
sha256sum devdisk-0.1.1-src.tar.gz

# 解包后自证
tar xzf devdisk-0.1.1-src.tar.gz && cd devdisk-0.1.1
python -m venv .venv && python -m pip install -U pip && python -m pip install -e ".[dev]"
python -m pytest -q          # 期望：256 passed / 2 skipped（2 项 skip 为按需生成的合成树）
python -m devdisk selftest   # 期望：OK 骨架不变量全部通过
```

**台账不自指**：本文件与包内任何文件都**不写**发布物自身的 sha256；
数值只在 Release 说明正文、tag 消息与 `.sha256` 附件里出现。
