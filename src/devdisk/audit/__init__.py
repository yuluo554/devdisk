# -*- coding: utf-8 -*-
"""只读审计层（模块③）：Docker 与 `.git`。

纪律（`plan/02` §一、`plan/03` 模块③）：

- 只读：Docker 只调用 `docker system df`；`.git` 只做文件系统统计。
- 绝不执行 git 的 gc / prune 子命令，也不执行任何 docker 清理子命令；它们只作为
  **建议**输出，且建议固定 `applied: false`。
- 任何降级都显式写进 `status`（`skipped:*` / `error:*`），不静默输出 0 体积。
"""

# 审计段状态（单点定义）。新增状态必须同步 `plan/03` 模块③ 的降级表。
STATUS_OK = "ok"
STATUS_NOT_REQUESTED = "not_requested"
STATUS_DOCKER_UNAVAILABLE = "skipped:docker_unavailable"
STATUS_DOCKER_TIMEOUT = "skipped:docker_timeout"
STATUS_DOCKER_PARSE = "error:docker_parse"
STATUS_GITDIR_FILE = "skipped:gitdir_file"
STATUS_PERMISSION_DENIED = "skipped:permission_denied"
STATUS_GIT_MUTATED = "error:git_mutated"

# 需要非零退出码的状态。
DEGRADED_STATUSES = (
    STATUS_DOCKER_UNAVAILABLE,
    STATUS_DOCKER_TIMEOUT,
    STATUS_DOCKER_PARSE,
)
