# data/README.md — 数据依据与纪律

## 一、依据查证记录（三态：✅已核对 / ⬜待核对；基准日 2026-10-17）

| 条目 | 状态 | 渠道/URL | 备注 |
|---|---|---|---|
| npkill 星数与活跃度：**9477★**，TypeScript，2026-10-13 活跃 | ✅ 已核对 | GitHub 仓库页（基准日 2026-10-17） | 星数为检索快照，随日期漂移 |
| npkill npm 下载量：**58,924 次/月**（统计窗口 2026-09-13~10-12） | ✅ 已核对 | `api.npmjs.org`（基准日 2026-10-17） | 下载量为快照，随月份漂移 |
| kondo 星数与状态：**2451★**，Rust，快照日期 2026-04-24 | ✅ 已核对 | GitHub 仓库页（基准日 2026-10-17） | README 自称"20+ 项目类型" |
| kondo 覆盖类型可查：Node、React Native、Python（`__pycache__`/`.mypy_cache`/`.tox` 等）、Gradle、Maven、SBT、CMake、Unreal、Unity、.NET、Terraform、CocoaPods 等 | ✅ 已核对 | `kondo-lib/src/lib.rs` 常量 / README | 源码常量可查 |
| kondo 已内置 `--dry-run` 与 `--older` | ✅ 已核对 | kondo README / CLI 帮助 | 本题不重复实现该能力，只做增量 |
| B 站中文需求侧条目：「如何快速检索 node_modules」「快速删除 Node_modules 目录，减少磁盘空间占用」「全平台可用-批量删除 node_modules」「npkill 清理老旧的 node_modules 文件夹」 | ✅ 已核对（条目原文） | B 站检索 | 证明需求侧独立存在；非量化证据，不作市场规模论据 |
| 检索式 `disk cleaner CLI` → 135 | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 无入库截图；检索式与基准日可回溯 |
| 检索式 `developer cache cleaner` → 114 | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 同上 |
| 检索式 `node_modules cleaner` → 145（单类型高度拥挤） | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 同上 |
| 检索式 `docker prune tui` → **3** | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 同上 |
| 检索式 `docker prune gui` → **0** | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 同上 |
| 检索式 `git gc large repo cleanup tool` → **0** | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 同上 |
| 检索式 `developer disk space cleanup windows gui` → **0** | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 同上 |
| 检索式 `磁盘清理` → 仅 DawnyFY/FluffyDiskCleaner，**0★**（Windows 系统清理，非开发产物） | ⬜ 软论据 | GitHub 检索，基准日 2026-10-17 | 同上；该仓库定位为系统清理 |
| 误删风险事实：`<venv>/bin` 常为软链；monorepo 可能共享 node_modules；手删 `.git/objects` 毁历史 | ✅ 已核对（目录结构与 Git 对象模型的已知事实） | Git 对象模型 / Python venv 结构 | 产品设计必须处理的硬约束 |
| Docker 只读审计入口 `docker system df`；清理入口 `docker image prune`（需显式确认） | ✅ 已核对 | Docker CLI 官方命令 | 本题只读调用前者；后者只生成命令不执行 |
| `.git/objects/pack` 体积与 loose object 数量可直接从文件系统统计；`git gc` 可回收松散对象 | ✅ 已核对 | Git 文档与仓库布局 | 只读统计；不执行 gc |

**判定：缝隙很窄。** npkill 与 kondo 已经证明需求真实且工具成熟；本题的增量仅限"Docker 镜像缓存 + `.git` 对象 + 统一安全分级 + 中文界面 + 可回滚隔离区"。检索快照类数字（⬜）只作辅助判断，不当作"无人做过"的硬证据。

## 二、数据源可获取性记录

| 数据源 | 状态 | 说明 / 阻断原因 | 处置 |
|---|---|---|---|
| 真实开发者磁盘目录 | 不获取 | 路径含用户名/项目名，内容敏感；采集即隐私风险 | 全程使用合成目录树 |
| 合成目录树 | ✅ 可获取（自建） | 程序化生成，固定随机种子，无外部依赖 | 生成器随仓入库；产物按需生成，默认不入仓 |
| 植入真值（分级与总体积） | ✅ 可获取（自建） | 生成时同步写出 truth 台账 | 与样本同源版本化，回归可复算 |
| Docker 审计数据 | ✅ 可获取（可选） | `docker system df` 只读输出；不同版本格式有差异 | 用固定文本 fixture 覆盖常见格式；运行时缺失则 `skipped:docker_unavailable` |
| Docker 真实环境 | ⬜ 待获取 | 开发/CI 环境不保证安装 Docker Desktop | 不把 Docker 真实调用作为验收前置；fixture 为验收主路径 |
| `.git` 对象统计 | ✅ 可获取 | 直接遍历 `objects/pack` 与 loose 目录 | 合成 Git 仓库用于基准 |
| 外部网络数据 | 不获取 | 本题无外部数据依赖，且运行时禁止联网 | 无 |

**可获取性结论**：本题**无外部数据依赖**；验收主路径是合成目录树 + 固定 fixture，Docker 真实环境只作可选增强。

### 二之一、仓库内数据资产登记（M1 起）

> **复核锚**：`next_review: 2027-01-17`（本节资产来源与外部对标结论的**下次复核日**；
> 与组合内其它仓同一口径。只作提醒，不改任何既有结论与数字）。
> **基准日**：外部工具对标（npkill / kondo 等）为 2026-10-17，属"软论据"，见 `README.md` 定位表。

| 资产 | 位置 | 来源 | 许可/状态 |
|---|---|---|---|
| 类型规则表 | `data/rules/types.json` | 本项目设计（目录名 + 项目标志文件），依据 `plan/01` 模块① 与 `plan/03` 模块① | 本项目原创，随源码包与 wheel 分发（v0.1.1 起）；JSON 自带 `source` 字段 |
| 忽略规则表 | `data/rules/ignore.txt` | 本项目设计（自定最小语法：字面名 / `*` 前缀后缀 / `/` 锚定），**非** `.gitignore` 拷贝 | 同上 |
| 分级规则表 | `data/rules/grades.json` | 本项目设计（等级顺序、类型范围、模式组、证据源、理由模板），依据 `plan/03` 模块② 与本节 §四 第 7/8 条 | 本项目原创，随源码包与 wheel 分发（v0.1.1 起）；JSON 自带 `source` 字段与显式 `order` |
| Docker fixture | `data/fixtures/docker_system_df/*.txt` | 手写脱敏文本，按 Docker 官方 `docker system df` 输出形态（新版/旧版/空表/畸形）构造，不含真实镜像名或容器 ID | 本项目原创，随源码包与 wheel 分发（v0.1.1 起）；来源与换算口径见 `plan/04` §三 |
| Docker fixture 真值 | `data/fixtures/docker_system_df/expect.json` | 逐列按 1024 进制手算，**不由解析器回填** | 本项目原创；`source` 字段说明换算口径 |

> 三个规则表是类型 / 忽略 / 分级的**唯一来源**；缺失或格式错时运行期返回退出码 4，禁止静默回退到内置默认。
> 分级规则表里**只有数据**：等级、顺序、类型范围、文件模式、证据源名与理由模板；
> 引擎（`grader.py` + `evidence.py`）不含任何具体规则（守门测试断言规则串不出现在引擎源码里）。
> 合成目录树与植入真值仍是"按需生成、不入仓"（生成器：`tools/gen_synth_tree.py`）。

## 三、合成数据纪律

- **假名白名单**：合成目录树一律使用假项目名（如 `demo-web`、`demo-api`、`demo-mobile`）、假包名（如 `demo-pkg-a`）、假用户名目录（如 `devuser`），禁用任何真实项目名、真实公司名、真实用户名。
- **假体积**：文件内容用固定字节填充生成，体积按样本清单精确控制；真值台账记录每个候选目录的**应有字节数**，不手写"约 X MB"。
- **植入真值**：生成器同时输出 `truth/manifest.json`，明确记录——每个候选目录的期望类型、期望分级（安全可删/谨慎/禁止）、期望体积、证据样本（如是否含 `.env`、是否含 `.gitignore` 未覆盖文件、是否软链）。
- **样本覆盖**：必须包含——纯 `node_modules`、`node_modules` 含手工文件、monorepo 共享依赖（软链）、`.venv`（含 `bin` 软链与不含）、`__pycache__`/`.mypy_cache`/`.tox`、`dist` 纯产物、`dist` 含手工配置、`build`/`target`/`out`、`.next`/`.cache`、指向扫描根外的符号链接、符号链接环、权限拒绝目录、大 `.git/objects`、空 Docker 场景。
- **真值与引擎解耦**：真值只描述"事实"（目录类型、体积、是否存在某文件），分级与理由由引擎计算；不得在真值里直接写引擎的评分。
- **样本可再生成**：固定随机种子；同种子产物逐字节一致（文件 mtime 由生成器显式设定，避免依赖生成时刻）。

## 四、数值口径纪律（一处定义）

以下规则**只在本节定义一次**，plan/01、代码注记、HTML 报告文案一律引用本节，不得手写第二口径：

1. **体积单位**：内部一律用**字节（int）**累计与比较；展示层按 1024 进制换算为 KiB/MiB/GiB，保留两位小数；聚合与排行必须基于原始字节，不得四舍五入后再求和。
2. **候选阈值**：只有体积 ≥ 配置项 `candidate_min_bytes` 的目录才进入候选清单（默认值在配置文件中单点定义，不在文档写死）。低于阈值的目录仍计入"其他"合计，但不参与排行。
3. **目录类型规则**：类型标签与匹配规则（目录名、所在项目根标志文件）只在类型规则表中定义；同一路径只取一个主类型，命中多个时按规则表顺序取第一个。
4. **物理对象去重**：目录以 `(st_dev, st_ino)` 为键；文件仅对 `st_nlink > 1` 的硬链接去重。硬链接与重复挂载只计一次。Windows 上 `DirEntry.stat()` 的 `st_ino` 恒为 0，扫描器在 inode 缺失时必须退回 `os.lstat`——否则去重会静默失效。
5. **符号链接**：`lstat` 判定；**一律不跟随**。跳过原因细分为：指向扫描根之外的目标 → `skipped:outside_root`；环形链接 → `skipped:symlink_cycle`；指向根内 → `skipped:symlink_not_followed`。Windows 上 junction（reparse point）与 symlink 同等对待，用 `symlink_kind` 区分，不按平台名分支。
6. **最后修改时间**：取目录内文件 `mtime` 的**最大值**（目录自身 mtime 不作为依据，避免被新建/删除子项扰动）；统一以 UTC ISO-8601 输出。
7. **分级枚举与优先级**：`forbidden`（禁止）> `caution`（谨慎）> `safe`（安全可删）。命中规则表靠前的等级即返回；无规则命中或证据不足 → `caution`（**不得默认 safe**）。规则表位置与顺序**只定义一次**：`data/rules/grades.json` 的 `rules` 数组顺序即优先级（`order` 字段是同一事实的显式声明，加载时校验两者一致且等级单调）。
8. **证据门槛**：`evidence` 至少 1 条才允许输出 `safe`；命中安全规则却取不到证据、或候选子树有读不全的目录（无法证明"纯缓存/纯依赖"）→ 降级 `caution`。`forbidden` 与 `caution` 也必须给理由字符串。证据只来自路径、体积、mtime 与白名单元数据文件（`.gitignore`、`package.json`、`pyproject.toml`、`requirements.txt`、`poetry.lock`、`package-lock.json` 等）；其中**只允许打开** `package.json`（且仅解析顶层键），其余文件只读文件名与 `stat`。证据 `path` 为相对路径（候选内→相对候选；项目根内→相对项目根；候选之外→相对扫描根）。
9. **隔离区命名**：`.trash/<batch-id>/`，其中 `batch-id` = `YYYYMMDD-HHMMSS-<6位随机十六进制>`；隔离区必须位于被扫描根所在的同一文件系统；跨设备直接拒绝（`error:cross_device`）。
10. **manifest 状态机**：`pending → moving → moved`，失败为 `failed:<reason>`；撤销时逆序执行 `moved → restoring → restored`，目标已存在则 `conflict` 并停止该条，不覆盖。
11. **完整性校验**：批次移动完成后按"相对路径 + 文件字节数 + 文件内容 sha256"生成 tree digest；撤销后重新计算并与 manifest 中的值比较，必须完全一致。
12. **Docker 降级语义**：Docker CLI 不存在、命令失败或输出无法解析 → 结果标记 `skipped:docker_unavailable` / `error:docker_parse`，CLI 退出码为**专用降级码**（与"成功但无可清理项"区分）；不得静默输出 0 体积。
13. **`.git` 审计口径**：统计 `objects/pack/*.pack` 与 `objects/??/*` 松散对象的字节数、pack 文件数、loose 对象数；`git gc` 建议仅在 loose 数或 objects 体积超过配置阈值时输出，且固定标注 `applied: false`。
14. **HTML 报告确定性**：报告内不含生成时刻、主机名、用户名等易变字段；所有聚合值由同一份 JSON 输入计算，页面不再二次计算。
15. **真值基准口径**：扫描/分级/报告的验收，以"合成真值 manifest 的逐项字段"为唯一判据；不由测试代码另行手写期望值。

## 五、隐私与提交纪律

- **真实磁盘审计结果永不入库**：`.trash/`、`*.db`、`*.jsonl` 审计原始输出、HTML 报告产物一律入 `.gitignore`。
- 合成样本与真值可入库（假名白名单内）；真实路径、真实用户名不得出现在任何提交中。
- 运行时零网络：不调用任何远程接口；HTML 报告零外链、零 CDN、零遥测。
- 提交元数据统一为 `yuluo554 <282769740+yuluo554@users.noreply.github.com>`；仓库内不得出现本机绝对路径。
- **品牌隔离**：文档不提及本系列任何其他项目；命名空间围绕"磁盘 / 体积 / 分级 / 隔离"词系。

## 附录：假数据白名单（随用例扩充登记）

| 类型 | 白名单 | 状态 |
|---|---|---|
| 项目名 | demo-web、demo-api、demo-mobile、demo-infra、demo-mono | ✅ 登记 |
| 包名 | demo-pkg-a、demo-pkg-b、demo-lib-core | ✅ 登记 |
| 用户名目录 | devuser、tester | ✅ 登记 |
| 公司/机构名 | 一律禁用 | 红线 |
| 真实项目名 / 真实用户名 / 真实路径 | 一律禁用 | 红线 |
