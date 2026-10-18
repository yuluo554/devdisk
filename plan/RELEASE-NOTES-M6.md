DevDiskAudit v0.1.0
==================

开发者磁盘占用审计与安全清理。扫描本机开发目录，把 node_modules / .venv / 构建产物 /
Docker 镜像缓存 / 大 .git 对象统一成一份清单，为每条候选给出 **安全可删 / 谨慎 / 禁止**
三级判定与可解释证据，并生成单文件中文 HTML 报告。清理默认只出报告，执行时移入隔离区，可一键撤销。

本版本是首期发布：五个模块（扫描 / 分级 / 只读审计 / 隔离与回滚 / 报告）全部落地，
Windows 与 Ubuntu × Python 3.8/3.12 四矩阵 CI 全绿。

亮点
----

- **永不 rm -rf**：清理 = 移动到 `<root>/.trash/<batch-id>/`，`undo` 按 manifest 逆序恢复，
  移动前后各校验一次 tree digest（类型前缀 + 相对路径 + 字节数 + 文件 sha256）。
- **证据化分级**：`safe` 必须有可核查证据；取不到证据或候选子树读不全 → 降级 `caution`。
- **Docker 与 .git 纳入统一审计**：Docker 只调用 `docker system df`（缺失/超时/畸形三态降级）；
  `.git` 只做文件系统统计，审计前后快照断言不变，只建议 `git gc`、永不执行。
- **单文件中文报告**：内联 CSS/JS、零外链、断网可读；同一输入两次生成逐字节一致。
- **纯本地零依赖**：仅 Python 标准库；无服务器、无网络请求、无遥测。

安装
----

    python -m venv .venv
    .venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
    python -m pip install -U pip
    python -m pip install -e ".[dev]"

要求 Python 3.8+。运行时不安装任何第三方库（`dev` 组只用于跑测试）。

快速开始
--------

    devdisk --version
    devdisk scan ~/code --out devdisk-out/scan.json
    devdisk audit --root ~/code --docker --git --out devdisk-out/audit.json
    devdisk report --input devdisk-out/scan.json --audit devdisk-out/audit.json \
        --html devdisk-out/report.html --top 50
    devdisk clean --from devdisk-out/scan.json --select ~/code/demo-web/node_modules
    devdisk clean --from devdisk-out/scan.json --select ~/code/demo-web/node_modules --apply --yes
    devdisk undo <batch-id> --trash ~/code/.trash

离线复跑（不要求本机有 Docker）：

    devdisk audit --docker-fixture data/fixtures/docker_system_df/v24_standard.txt \
        --out devdisk-out/audit.json

退出码：`0` 正常 / `1` 业务拒绝或冲突 / `2` 用法错误 / `3` 骨架期未实现（已全部退役）/
`4` 能力降级 / `5` 安全拒绝。不提供"全选"或"一键清理全部"。

发布物
------

`devdisk-0.1.0-src.tar.gz` —— 由 `git archive v0.1.0` + `gzip -9n` 生成的**确定性源码包**：
同一 commit 连续构建两次字节完全一致（sha256 见下方与 `plan/RELEASE-M6.md`）。

解包后按上面的「安装」段落执行即可；包内不含任何运行产物、隔离区或真实审计结果。

    sha256sum devdisk-0.1.0-src.tar.gz
    tar xzf devdisk-0.1.0-src.tar.gz
    cd devdisk-0.1.0
    python -m pip install -e ".[dev]"
    devdisk selftest

已知限制
--------

- 分级是**基于可观察证据的建议**，不是"删除后必然安全"的保证；重建成本需自行确认。
- Docker 真实调用为可选增强；验收主路径是固定文本 fixture。想确定性复现"缺失降级"，
  用 `DEVDISK_DISABLE_DOCKER=1`（Windows 上 `docker.exe` 在 App Paths 注册表里，PATH 隔离排不掉）。
- 首期发布物是源码包 + editable 安装；非 editable（wheel）安装不会带上 `data/rules/*.json`，
  从仓库外运行需 `DEVDISK_DATA_DIR=<仓库>/data`。
- macOS 仅保证基本扫描与隔离；WSL 缓存、系统级缓存、定时审计与历史对比为后续扩展项。

完整文档见仓库内 `README.md`、`plan/00-README总览.md` 与 `plan/RELEASE-M6.md`。
