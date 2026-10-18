# 00 · README 总览 — 10-18-dev-disk-audit（开发者磁盘占用审计与安全清理）

- 性质：不参赛项目题 · 发布 GitHub **公开仓库** · 署名 yuluo554
- 定位宣言："开发者磁盘被 node_modules/.venv/构建产物/Docker 镜像缓存/大 .git 对象吃掉，但没人敢删——本工具按'可重建性 + 最后修改时间 + 依赖关系'给出**分级可删清单**，默认只出报告、清理必须显式确认、删除走隔离区可回滚。**安全分级与中文界面是差异化**：已有工具覆盖了多数目录类型，但没人把 Docker 与 .git 纳入统一的安全分级，也没人把'误删的代价'做成可回滚。"
- 交付形态：**纯本地 Python CLI + 本地单文件 HTML 报告**（唯一形态）；无服务器、无 GPU、无网络。
- 发布：GitHub 公开仓库 `https://github.com/yuluo554/devdisk`，v0.1.0（tag + Release 附确定性源码包）；发布留档 `plan/RELEASE-M6.md`。
- 下一棒：**无**（M0–M6 全部完成）。后续扩展项见 `plan/06` §七。

## 文档索引表

| 文档 | 内容 | 状态 |
|---|---|---|
| plan/00-README总览.md | 本文档：索引 / 决策记录 / 承诺-证据台账 / 里程碑 | ✅ v1 |
| plan/01-题目文档.md | 十段式题目定稿（含招牌动词表、风险登记、能力重叠清单） | ✅ v1 |
| plan/02-架构与技术选型.md | 分层架构、CLI 契约、退出码、数据契约、确定性写盘、安全策略 | ✅ v1（M0） |
| plan/03-模块详设.md | 五个模块的输入/输出/实现/降级/验收，编排与数据流 | ✅ v1（M0） |
| plan/04-数据计划.md | 合成树样本清单、真值台账结构、Docker/Git fixture、报告基准 | ✅ v1（M0） |
| plan/05-里程碑与发布.md | 逐里程碑 DoD、退出码退役表、发布流程、状态列 | ✅ v1（M0） |
| plan/06-交付对标与决策.md | 提交材料对标、评分对标、招牌动词表、待定决策、开放项 | ✅ v1（M0） |
| plan/HANDOFF-M1.md | 下一棒（M1 数据先行 + 扫描）的交接快照 | ✅ M0 |
| plan/HANDOFF-M2.md | 下一棒（M2 安全分级）的交接快照 | ✅ M1 |
| plan/HANDOFF-M3.md | 下一棒（M3 Docker 与 .git 只读审计）的交接快照 | ✅ M2 |
| plan/HANDOFF-M4.md | 下一棒（M4 隔离与回滚）的交接快照 | ✅ M3 |
| plan/HANDOFF-M5.md | M5（报告与收口）的交接快照 | ✅ M4 |
| plan/HANDOFF-M6.md | 下一棒（M6 发布）的交接快照 | ✅ M5 |
| data/README.md | 依据查证记录 + 数据源可获取性 + 合成与口径纪律 | ✅ v1 |
| .gitignore / .gitattributes | 运行时产物不入仓；源码 LF、二进制不归一 | ✅ v1 |
| pyproject.toml / src/ / tests/ / tools/ | 可安装骨架、CLI 契约、纪律守门 | ✅ M0 |
| data/rules/types.json + ignore.txt | 类型规则与忽略规则（单点数据文件，M1） | ✅ M1 |
| data/rules/grades.json | 分级规则表（等级顺序 / 类型范围 / 模式组 / 证据源 / 理由模板，单点数据文件，M2） | ✅ M2 |
| src/devdisk/evidence.py + grader.py | 证据提取与分级引擎（引擎零硬编码规则，M2） | ✅ M2 |
| tools/gen_synth_tree.py + tools/bench_scan.py | 合成树生成器与扫描真值基准（M1） | ✅ M1 |
| tools/bench_grade.py + tools/reference_grade.py | 分级基准与分级规则的独立参照实现（M2） | ✅ M2 |
| src/devdisk/audit/ + tools/bench_audit.py | Docker 与 `.git` 只读审计 + fixture/合成仓库双边基准（M3） | ✅ M3 |
| src/devdisk/quarantine.py + manifest.py + digest.py | 批次 ID、同文件系统校验、manifest 状态机、tree digest 完整性校验（M4） | ✅ M4 |
| src/devdisk/report.py | 单文件 HTML 报告（单一渲染源、零外链出口断言、全量转义、10 段固定顺序，M5） | ✅ M5 |
| tools/bench_report.py | 报告基准（真值聚合 + 从 HTML 读回数值 + 零外链 + 双跑一致 + 转义/降级），M5 | ✅ M5 |
| tools/bench_clean.py | 隔离区往返基准（清理→撤销 digest 一致）+ 中断恢复 + dry-run 只读（M4） | ✅ M4 |
| data/fixtures/docker_system_df/ | Docker `system df` 固定文本 fixture + 手写真值 `expect.json`（M3） | ✅ M3 |
| .github/workflows/ci.yml | CI 矩阵（windows/ubuntu × 3.8/3.12） | ✅ M0（M6 首推后四矩阵全绿） |
| plan/RELEASE-M6.md | 发布留档（干净环境验证 / 脱敏四步 / CI 对账 / 确定性源码包 sha256 / Release 复核） | ✅ M6 |

## 决策记录表

> 本表同时登记已定案决策与**弃选项**（含弃选理由），后续里程碑不再重复论证。

| 日期 | 决策 | 结论与理由 |
|---|---|---|
| 2026-10-17 | **定位如实降级为"功能增量"** | kondo 2451★ 已覆盖 20+ 项目类型并内置 `--dry-run`/`--older`，覆盖约 80% 的常见目录类型；npkill 9477★ 已垄断 node_modules 心智。本题**不是新品类**，只争取"统一多类型 + Docker 镜像缓存 + .git 对象 + 安全分级 + 中文界面 + 可回滚隔离区"这一窄缝。题面、data/README、README 首段均按此措辞，禁止写"空白/首创"。 |
| 2026-10-17 | **失败判据写死**：Docker 审计、`.git` 审计、隔离区回滚三项任一被砍，视为失败 | 这三项是本题与 kondo 的唯一实质差异。若开发中因工期砍掉其一，产品退化为 kondo 的复制品，验收时不得以"功能可用"通过。 |
| 2026-10-17 | 形态定案：CLI + 单文件 HTML 报告（唯一形态） | 报告需要中文排版与体积排行，终端表格难以阅读；单文件 HTML 内联 CSS/JS、零外链、断网可读，仍是纯本地。不设备选形态，避免"任选其一"留空壳后门。 |
| 2026-10-17 | 弃"直接删除（rm -rf）" | 误删不可逆是本题最大的风险；`<venv>/bin` 软链、monorepo 共享依赖、`.git/objects` 都会因直接删除造成不可恢复损失。改为**移动到 `.trash/<batch-id>/` + manifest + 一键撤销**。 |
| 2026-10-17 | 弃"自动执行 `git gc`" | `git gc` 会改写仓库对象与 reflog，可能影响用户正在进行的工作，且耗时不可控。改为只读统计 + 输出建议命令，`applied: false`。 |
| 2026-10-17 | 弃"系统级清理" | 系统缓存、回收站、软件卸载与专业工具正面相撞，且 Linux 需 root、Windows 需管理员权限，与"纯本地轻量、无需提权"冲突。 |
| 2026-10-17 | 弃"重复文件查找" | 已有成熟工具（按内容哈希找重复），本题不做第二件事；本题的分级依据是"可重建性"，不是"内容相同"。 |
| 2026-10-17 | 弃"一键清理全部" | 这是误操作最大入口，与"默认只出报告、逐条确认"的产品纪律冲突。 |
| 2026-10-17 | 弃"读取文件正文做判断" | 隐私红线：只读路径、体积、mtime 与白名单元数据文件（`.gitignore`、包清单）。读取源码既无必要，也会把工具变成隐私风险点。 |
| 2026-10-17 | 弃"GUI 桌面 exe / 本地 Web 服务" | 单文件 HTML 已满足"看报告"的需求，省去 PyInstaller 打包与本地端口管理；CLI 负责执行，职责边界清晰。 |
| 2026-10-18 | **M0 骨架落地形态**：`src/devdisk` 单包 + 5 子命令 + `selftest` | 包名/CLI 名统一 `devdisk`；Python 底线 3.8；零运行时依赖；`dev` extra 只有 pytest。**用户未逐项答复 D1–D8，按 `plan/06` §五 推荐默认执行，可事后调整**（均为零消费者窗口内的可逆项）。 |
| 2026-10-18 | 退出码 6 值语义定案（0/1/2/3/4/5） | `NOT_READY` 只用于骨架期，逐里程碑退役（`plan/05` §三）。骨架期先把"未知命令=2"与"未实现=3"分开，避免后续把"没做"误读成"用法错误"。 |
| 2026-10-18 | 源码删除调用采用**白名单制守门** | `shutil.rmtree`/`os.rmdir` 全禁；`os.remove`/`os.unlink` 仅允许出现在 `serialize.py` 的临时文件清理里。理由：绝对禁止会让确定性写盘无法清理失败残留，而按文件白名单可审计、可变异验证。 |
| 2026-10-18 | **M1 落地形态**：规则表为数据文件、扫描层与编排层分离 | `data/rules/types.json` + `ignore.txt` 是类型与忽略的**唯一来源**；规则缺失/格式错 → 退出码 4，不静默用内置默认。新增 `model.py`（值对象）/`rules.py`（规则加载）/`pipeline.py`（编排）三层，`cli.py` 保持零判定。 |
| 2026-10-18 | Windows 上 junction 与 symlink 统一按"链接"处理 | 本机与 CI 的 Windows runner 都没有 `SeCreateSymbolicLinkPrivilege`；junction 具备同等"不跟随 + 可能越界"的安全语义。测试按**文件系统能力**探测并如实记录，不按平台名硬编码。 |
| 2026-10-18 | 扫描器在 inode 缺失时退回 `os.lstat` | 实测（3.8 与 3.12）`DirEntry.stat()` 在 Windows 上 `st_ino/st_dev/st_nlink` 恒为 0，会静默禁用硬链接去重。这是正确性修复，不是可选项。 |
| 2026-10-18 | 读不到的候选目录**不产出 0 字节条目** | 把"读不到"报成"0 字节"会让分级与报告给出错误结论；改为记 `skipped:permission_denied` 并从候选清单排除。 |
| 2026-10-18 | **M2 落地形态**：分级规则表是数据、引擎零规则、`reason` 模板随规则表 | `data/rules/grades.json` 声明等级顺序（顺序即优先级）、类型范围、文件模式组、证据源名与理由模板；`rules.py` 只负责加载与校验，`grader.py` 只解释，`evidence.py` 只取证据。守门测试断言规则表里的类型名/模式串不出现在两个引擎模块的字符串字面量里。 |
| 2026-10-18 | M2：安全结论必须过**证据门槛**（无证据或子树读不全 → 谨慎） | 与"读不到不给结论"同源：证明不了"纯缓存/纯依赖"就不能宣称可安全删除。变异验证：摘掉该分支 → 常驻门变红。 |
| 2026-10-18 | M2：`totals.by_grade` 三级键恒在，`forbidden` 不计入可回收体积 | 报告层要直接相加三级体积；`.git` 这类禁止项只做只读审计对象，永不进入"可删合计"。 |
| 2026-10-18 | M2：分级另做一份**独立参照实现**（`tools/reference_grade.py`） | 引擎与自己的测试同源会掩盖错误；参照实现候选清单取自植入真值、规则逻辑另行抄写、禁止 import 生产模块，逐样本对账。 |
| 2026-10-18 | **M3 落地形态**：`audit/` 子包 + fixture 为验收主路径 + `.git` 只做文件系统统计 | Docker 真实环境不保证存在，固定文本 fixture 才是可复现的验收主路径（`--docker-fixture` 直接喂文本，真实调用只作可选增强）；`.git` 统计不调用 git，避免版本差异与任何写操作风险。 |
| 2026-10-18 | M3：`.git` 审计前后做 `(相对路径, size, mtime_ns)` 快照并断言不变 | 只比字节数会漏掉"被改写但大小不变"的破坏；相对路径保证日志可复现且不含本机绝对路径。不一致 → `error:git_mutated` + 退出码 5。 |
| 2026-10-18 | M3：`.git` 是文件（worktree/submodule）时记 `skipped:gitdir_file` 且不跟随 | 跟随 gitdir 会把审计范围伸出扫描根，也容易把共享仓库统计两遍；记目标路径即可让用户自行判断。 |
| 2026-10-18 | M3：Docker 只有表头视为合法空场景，有数据行却缺整类视为解析失败 | "没有资源"与"格式变了"必须区分：前者四类为 0，后者 `error:docker_parse`，不能静默填空。 |
| 2026-10-18 | **M4 落地形态**：`quarantine.py`（移动）+ `manifest.py`（状态机）+ `digest.py`（完整性）三层分离 | 编排层只做"校验 → 调用 → 组装 → 写盘"。移动前后各算一次 digest，由编排层比对；隔离层不含任何退出码判定（`QuarantineError` 由编排层映射），CLI 仍然零判定。 |
| 2026-10-18 | M4：`--apply` 必须有 `--yes`（非 TTY 不读终端） | 非交互环境里 `input()` 可能抛 `OSError` 或读到 EOF；显式拒绝（退出码 1）比"猜用户想要"安全。TTY 下的二次确认要求输入完整 `MOVE <batch-id>`，避免回车误触。 |
| 2026-10-18 | M4：**未给 `--select` 一律退出码 1**，`--from` 只提供候选上下文，绝不充当"全选" | 这是产品纪律里"无一键清理全部"的实现单点：`--from` 单独出现时没有任何条目可移动，必须显式列出路径。守门测试同时反射 CLI 参数面与源码语义。 |
| 2026-10-18 | M4：隔离区在扫描根内时必须被忽略规则命中（默认 `.trash`） | 否则"清理 → 重扫又发现 → 再清理"形成假循环。自定义 `--trash` 若落在根内且未被忽略，直接退出码 5，而不是静默允许。 |
| 2026-10-18 | M4：撤销默认 `--stop-on-conflict`（整体停止），可显式改 `--continue-on-conflict` | 默认停止保护"用户没看到的后续条目"；继续模式留给"已知冲突、只想救回其余"的场景。两种模式都必须保持"不覆盖"与"已完成条目保留"。 |
| 2026-10-18 | M4：`tree_digest` 不含 mtime，含空目录与链接目标 | 移动会改时间戳，但内容与结构不变；空目录是结构事实（撤销后必须还原）；链接目标字符串不跟随，避免把隔离区外的内容卷入校验。 |
| 2026-10-18 | M5：报告是**单一渲染源**，聚合只从输入 JSON 累加，页面 JS 只做筛选/排序 | `data/README.md` §四 第 14 条要求报告不再二次计算；`report.py` 不 import `grader`/`rules`，测试断言报告模块无分级引擎引用。 |
| 2026-10-18 | M5：零外链断言放在 `render_html()` 的**出口**，不是测试里的"事后检查" | 出口断言保证"写出的字节一定通过检查"；变异验证证明摘掉它会有 4 个测试变红。扫描包含 `http(s)://` 字面量，防止 JS 常量里混入 CDN。 |
| 2026-10-18 | M5：报告真值 `truth/report_expect.json` 随合成树生成（不入仓），由 `build_report_expect()` 独立推导 | 与 `manifest.json` 同一次生成，且**不复用** `report.py` 实现；否则"报告自己验自己"。基准从渲染后的 HTML 的 `data-value` 读回数值。 |
| 2026-10-18 | M5：`--as-of` 是唯一时间来源；默认报告不含任何时间 | 生成时刻会让双跑逐字节一致的断言失效；`--as-of` 让"需要展示时间"的场景可复现。 |

## 承诺-证据台账

> 全项目唯一一份。里程碑完成时，同一次提交内同步翻转本表状态列。

| 承诺项 | 素材来源 | 交付物内可达性 | 核验方式 | 状态 |
|---|---|---|---|---|
| 多类型扫描与分类（node_modules/dist/build/.venv/__pycache__/.mypy_cache/.tox/.next/.cache/target/out 等） | Python 标准库 `os.scandir`；类型规则表随包 | 纯本地，无外部服务 | 合成树基准：类型识别与体积合计对真值 | ✅ M1（`bench_scan.py` 18/18 检出、0 误判、逐项 0 差异） |
| 符号链接不跟随、不越界、不重复计数 | `entry.is_symlink()` + reparse 判定 + `(st_dev, st_ino)` 去重 | 运行时逻辑 | 符号链接与环形链接负样本；扫描根外目标断言被跳过 | ✅ M1（越界记 `outside_root`、环记 `symlink_cycle`、硬链接去重有专门用例） |
| 三级安全分级（安全可删/谨慎/禁止） | 本题设计；判定规则在 `data/rules/grades.json`；证据来自目录结构、mtime、白名单文件名 | 运行时逻辑 | 分级基准：与植入真值逐样本一致（18/18）；证据不足落"谨慎"；独立参照实现对账一致 | ✅ M2（`bench_grade.py` 18/18、逐项差异 0；`reference_grade.py` PASS） |
| 分级理由与证据可解释（非裸标签） | 本题设计 | CLI 摘要、JSON 输出的 `reason`/`evidence` 字段（M5 报告沿用） | 每条候选断言 `reason` 非空且 `evidence` ≥ 1 条；证据 `path` 为相对路径，不泄漏绝对路径 | ✅ M2（常驻不变量门 + `bench_grade.py` 证据门） |
| Docker 只读审计（`docker system df` 解析四类体积） | Docker CLI 可选增强 | 缺失时 `skipped:docker_unavailable` 降级 | fixture 基准：解析正确；无 prune 调用；`applied: false` | ✅ M3（`bench_audit.py` 4 个 fixture 用例差异 0；缺失/超时/畸形三条降级路径有专门用例；`applied:false` 断言常驻） |
| `.git` 只读审计（objects 体积、pack 数、loose 数） | 文件系统统计；不调用 git 写操作 | 纯本地 | 合成仓库基准：数值正确；审计前后 `.git` 元数据不变 | ✅ M3（objects 4198912 / pack 2 / loose 2 精确；`objects/info` 与 `.idx` 排除；worktree `.git` 文件记 `skipped:gitdir_file`；前后快照断言） |
| `.git` 永不自动处理、只建议 `git gc` | 产品纪律 | 代码层无删除/gc 调用；输出标注 `applied: false` | 静态扫描：无 `git gc`/`git prune` 执行；动态：仓库无变化 | ✅ M3（`audit/git.py` 无 `subprocess`；`applied:false` 断言；审计前后元数据清单不变） |
| 隔离区 `.trash/<batch-id>/` + manifest + 一键撤销 | 本题设计；`shutil.move` | CLI 子命令 | 撤销后 tree digest 一致；中断恢复用例 | ✅ M4（`bench_clean.py` 往返 digest 一致；中断恢复用例通过；manifest 状态机 `pending→moving→moved` / `moved→restoring→restored`） |
| 永不 `rm -rf`、无"一键清理全部" | 产品纪律 | 代码与 CLI 均无此路径 | 静态扫描：无 `rm -rf`/`shutil.rmtree`/`shutil.copy*`；CLI 无全选参数 | ✅ M4（`test_quarantine_never_deletes_and_never_copies`、`test_cli_has_no_select_all_for_clean`、`test_clean_requires_explicit_selection_in_source` 常驻） |
| 单文件中文 HTML 报告（体积排行/项目与类型聚合/可删合计） | 标准库模板 | 报告文件随仓生成 | 报告无外部 URL；合计与排行对真值；断网可读 | ✅ M5（`bench_report.py` + `tests/test_report.py`） |
| 合成目录树 + 植入真值（已知分级与总体积） | 本题自制（假项目名/假体积） | 生成器随仓 | 程序化再生成；同种子一致；真值与引擎解耦 | ✅ M1（`--check` 位级一致；真值无 `observed_*`） |
| 隐私红线：只扫路径/体积/mtime，零上传、零遥测 | 本题设计 | 仓库基建 + 运行时 | 无网络调用扫描；真实审计结果与隔离区不入 git | ✅ M1（`test_source_has_no_network_imports` 常驻；`devdisk-out/` 不入仓） |
| 与 kondo/npkill 能力重叠如实标注 | 基准日检索快照 + README 原文 | 题面 §六 与 README | 文档核对：含重叠清单，无"首创/空白"措辞 | ✅ M6（`plan/01` §六、README「定位与边界」逐行标注；`check_topic.py` 22 PASS / 0 FAIL） |
| 发布三件套 | 本题设计 | 仓库发布流程 | git tag + GitHub Release（附产物）+ `plan/RELEASE-M6.md` 留档 | ✅ M6（tag `v0.1.0` + Release 附 `devdisk-0.1.0-src.tar.gz` + 发布留档；`check_release.py` 无 FAIL） |

## 里程碑初稿

| 里程碑 | 内容 | DoD |
|---|---|---|
| **M0 计划与骨架** | `plan/00–06` 定稿 + 可安装骨架（`pyproject` / `src/devdisk` / `tests` / CI） | 六份文档齐备；`pip install -e .` 可用；`--version`/`--help`/`selftest` 可跑；退出码契约锁进测试；双解释器 pytest 绿；`check_topic.py` 0 FAIL |
| M1 数据先行 | 合成目录树生成器（假项目名/假体积，含软链、环形链、共享 node_modules、构建后手工文件、大 `.git` objects 等样本）+ 植入真值台账；扫描与分类层（类型规则、`(dev, ino)` 去重、跳过符号链接、跳过原因落库） | 真值可程序化再生成且同种子一致；1000 个候选目录扫描不崩；软链不越界；两次扫描输出一致 |
| M2 安全分级 | 三级规则表（数据文件）、证据提取（白名单文件名与 `stat`）、理由字符串、证据门槛降级、独立参照实现 | 每条候选有非空 `reason` 与 ≥1 条 `evidence`；分级与真值逐样本一致；证据不足全部落"谨慎"；摘掉降级分支测试变红 |
| M3 只读审计 | Docker `system df` 解析与降级语义；`.git` objects/pack/loose 统计与 `git gc` 建议；`applied: false` 贯穿输出 | Docker fixture 解析正确；缺失时退出码进入降级语义；`.git` 审计前后元数据不变；无 prune/gc 执行 |
| M4 隔离与回滚 | `.trash/<batch-id>/`、manifest 状态机、dry-run 默认、`undo` 逆序移回、中断恢复 | 撤销后 tree digest 一致；中断可恢复；无 `rm -rf`；隔离区不入 git |
| M5 报告 | 单文件中文 HTML：排行、项目/类型聚合、可删合计、分级理由、Docker/.git 段、降级说明 | 无外部 URL；断网可读；合计与排行对真值；降级说明可见 |
| M6 发布 | **git tag + GitHub Release（附产物，本地留 release 产物副本并 sha256 留档可复验）+ plan/RELEASE-M6.md 发布留档三件套**；发布 GitHub 公开仓库 | 三件套缺一即未达成；发布留档在 tag 之前进 tag 树；收尾跑 `check_release.py` 无 FAIL |

## 数字承诺纪律

不写会随实现漂移的硬数字（测试数/用例数/代码行数）；分级规则、体积单位、候选体积阈值、隔离区命名、Docker 降级语义等**数值口径只在 `data/README.md` §四定义一次**，plan/01、代码注记、报告文案一律引用该节，不得手写第二口径。里程碑完成时同一次提交内同步翻转本台账状态列。
