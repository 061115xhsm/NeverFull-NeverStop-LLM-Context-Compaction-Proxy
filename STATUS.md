# STATUS — 2026-10-27 (V12)

## 开关

| 变量 | 默认 | 作用 |
|---|---|---|
| `COMPACTION_PROXY_FOLDING` | `0` | 可逆折叠：归档原文 + 类型化占位符 + 承诺契约 + 证据探针 |
| `COMPACTION_PROXY_CONTRACT_RETRY` | `1` | 证据分低于阈值时带 MUST-PRESERVE 清单重试一次 |
| `COMPACTION_PROXY_EVIDENCE_FLOOR` | `0.75` | 证据分下限 |
| `COMPACTION_PROXY_POLICY` | `0` | UCB bandit 选择 light/standard/deep |
| `COMPACTION_PROXY_AGENT_PROFILE` | 空 | `weak`/`strong`/`auto` 调整预压缩阈值 |
| `COMPACTION_PROXY_STRICT_SESSION` | `0` | 拒绝内容指纹 session id |
| `COMPACTION_PROXY_MAX_BODY_MB` | `2` | 请求体上限（超出 413） |
| `COMPACTION_PROXY_RATE_LIMIT` | `0` | 每 IP 每分钟请求数（0 关闭） |
| `COMPACTION_PROXY_REQUIRE_AUTH` | `0` | 无 API secret 时拒绝启动 |
| `COMPACTION_PROXY_RETENTION_DAYS` | `30` | folds/state/sessions 保留天数 |
| `COMPACTION_PROXY_MAX_FOLDS` / `MAX_SESSIONS` | `5000` / `2000` | 归档/会话条数上限 |
| `COMPACTION_PROXY_MAINTENANCE_EVERY` | `100` | 每 N 次成功压缩跑一次保留清理 |
| `COMPACTION_PROXY_FOLD_ENCRYPT` | `0` | 归档加密（Fernet，key 自动生成 0600） |
| `COMPACTION_PROXY_CALIBRATION_DB` | 数据目录 | 每模型 token 校准持久化 |

## 已完成

### P0 稳定性 / 运维
- **SessionStore 并发加固**：RLock + `busy_timeout=10000` + 连接超时；13 个公共方法统一加锁
- **保留策略**：`fold_archive.prune`、`state_table.prune`、`session_store.prune`；每 100 次压缩自动执行；`POST /maintenance/prune` 手动触发
- **安全**：`REQUIRE_AUTH` 无 secret 拒绝启动（fail-closed）；按 IP 限流；请求体上限；归档可选加密（ciphertext at rest 已实测）
- **成本归因**：优先读取上游 `usage`（OpenAI/Anthropic/Gemini 三种字段），记录 prompt/completion tokens；无 usage 时回退估算

### P1 质量
- **任务承诺**：`TODO / pending / next step / 待办 / 下一步` 纳入 commitment 与状态表
- **token 校准**：按模型学习 `actual / estimated` 比率（EMA，clamp 0.5–2.0），用于预压缩阈值
- **评测**：`benchmark/paired_eval.py` 三套 fixture（coding/data/ops），CI 自动跑；`compare_systems.py` 三方对比（OpenClaw/Hermes/NeverFull）

### P2 可观测 / 性能
- `/metrics` 与 `/health` 增加延迟直方图（p50/p95/max）
- `/fold/{ref}` 支持 `offset/limit` 分页（默认 200，上限 500）
- 折叠/状态写入全部加锁；2000 条消息估算 60ms、submodular 选择 177ms（可接受）

## 验证

- **64 项测试全绿**（含并发、保留、加密、校准、任务承诺）
- 冒烟：`/health` 显示 encryption/retention；`/fold` 分页 `total=5 / offset=1 / limit=2`；`/maintenance/prune` 正常；`REQUIRE_AUTH=1` 无 secret 时进程退出码 1
- 并发 8×25 次 fold/state/session 写入：**0 错误**
- HTTP：100 次串行 health p50 1ms；200 并发 0.09s；畸形 JSON 400；1.5MB 413；上游宕机 502


### P6 多租户 / 多实例（2026-10）
- `X-Tenant-Id` 全链路：session id 命名空间、fold 读取校验、限流桶、成本事件
- `COMPACTION_PROXY_REDIS_URL` 共享压缩缓存（Redis SETEX；无 Redis 时本地）
- `COMPACTION_PROXY_INSTANCE_ID` + `X-Instance-Id` 响应头；`/health.storage` 暴露模式
- `COMPACTION_PROXY_MAINTENANCE_OWNER=0` 让从实例跳过定期清理
- `storage_backend.py` 移到根目录；`session_store_pg.py` 实现完整 PostgreSQL SessionStore（sessions/prior/transcripts/ARC/搜索），`COMPACTION_PROXY_STORAGE=postgres` + `PG_DSN` 启用，CI 带 Postgres 服务
- 租户配额：`TENANT_MAX_FOLDS` / `_MAX_SESSIONS`（写入时裁剪）、`TENANT_DAILY_TOKENS` + `ENFORCE_QUOTA`（超限跳过压缩）
- 密钥轮换端点：`POST /maintenance/rotate-key`（实测轮换后数据可读）
- 流式集成测试 + 归档密钥轮换 + 按请求成本追踪


### P7 影子模式（2026-10-27）
- `COMPACTION_PROXY_SHADOW=1`：只观测不压缩；记录 tokens/阈值/预计节省，请求原样转发
- `/health.shadow` 输出 requests / would_compact / potential_saved_tokens / overflow_detected
- 用于生产前试跑：拿到真实会话的压缩压力与潜在收益，再决定是否接管


## V12（2026-10-27，按导师方案落地）

| 阶段 | 已实现 | 验证 |
|---|---|---|
| P0 计量 | usage 缓存字段解析（OpenAI/Anthropic/Gemini）、有效成本（读 0.1x / 写 1.25x）、TTFT、命中率计数 | 单测 + `/health.upstream_cache` |
| P1 布局 | 尾部瞬态状态帧；append-only checkpoint（SQLite+PG）；工具确定性排序；`prompt_cache_key`；Anthropic 4 断点 | 单测 + 缓存前缀稳定性 |
| P2 异步/展开 | 软 65%/硬 92% 双水位线；后台候选压缩 + 证据/计划 judge；热替换；`neverfull_restore_context` 虚拟工具注入 + 非流式/流式拦截回填 + 内部重发（流式带心跳） | 单测 + 流式解析测试 |
| P3 状态/存储 | Python AST 符号级哈希（函数/类，stdlib ast）+ 50 行分块回退；PG FoldArchive；信封加密（租户 DEK + KEK，rotate 只重包 DEK）；Redis 共享策略状态；`neverfull/` 包门面 | 117 项测试（含 8 项真 PG） |

**未做/后续**：Tree-sitter 多语言 AST（当前 Python 用 stdlib ast，其他语言用行块哈希）、Hybrid LinUCB、S3/MinIO 分层存储、Anthropic 流式虚拟工具拦截。

## 仍未做（下一轮）

- 多实例共享存储（Redis/Postgres）仍在 `attic/`；当前单进程
- 流式路径（Anthropic SSE × 压缩）缺集成测试
- 成本归因含按事件明细（cost_recent）与按租户聚合；cache 命中已计入收益
- 归档 key 轮换 / 多租户隔离未实现
- README 的“40+ techniques”叙事仍待彻底重写（STATUS 已给出真实清单）
