# STATUS — 2026-10-26 (V11)

## 开关一览

| 变量 | 默认 | 作用 |
|---|---|---|
| `COMPACTION_PROXY_FOLDING` | `0` | 可逆折叠：归档原文 + 类型化占位符 + 承诺契约 + 证据探针 |
| `COMPACTION_PROXY_CONTRACT_RETRY` | `1` | 证据分低于阈值时，带"MUST PRESERVE"清单重试一次 |
| `COMPACTION_PROXY_EVIDENCE_FLOOR` | `0.75` | 证据分下限（契约 45% / 标识符 25% / 分节 15% / 末次请求 15%） |
| `COMPACTION_PROXY_POLICY` | `0` | UCB bandit 选择 light/standard/deep |
| `COMPACTION_PROXY_AGENT_PROFILE` | 空 | `weak` / `strong` / `auto`：按模型能力调整预压缩阈值 |
| `COMPACTION_PROXY_STRICT_SESSION` | `0` | 拒绝内容指纹 session id（返回 ephemeral） |

## 已完成

### P0 工程基线
- `requirements.txt` / `requirements-optional.txt`
- CI：`.github/workflows/ci.yml`（Python 3.11/3.12/3.13，compile + 53 项测试）
- `tests/`：53 项 characterization 测试全绿
- **死模块归档** → `attic/`：`memskill_rl`、`incremental_compaction`、`async_precompressor`、`storage_backend`、`rust_ffi`、`rust/`（此前 README/ROADMAP 标 ✅ 但主程序从未 import）
- session id：header 优先；指纹兜底告警 + 指标；`STRICT_SESSION` 彻底拒绝；指纹加入 `user` 字段隔离

### P1a 可逆折叠（`folding.py`）
- `FoldArchive`：SQLite 原样归档，`fold:<hash>` 寻址
- `build_fold_digest()`：零 LLM 的结构化摘要（角色/工具/文件/命令/错误/承诺）
- `render_fold_block()`：`<folded_context ref=...>` 类型化占位符，不拍平消息结构
- `/fold/{ref}` 原样取回；`/health` 暴露归档状态

### P1b 状态一致性（`state_table.py`）
- 文件表：path → 最后操作/时间/编辑次数，标记"早期读取已过期"
- 决策表：goal/constraint/decision，重复去重、近似旧决策标 `superseded`（ghost-memory 防护）
- 每次压缩渲染 `<state_snapshot>` 注入上下文

### P2 证据探针 + 契约重试（`evidence.py`）
- 确定性探针：承诺覆盖 45%、精确标识符 25%、分节完整 15%、末次请求关键词 15%
- 低于 `EVIDENCE_FLOOR`：带 `MUST PRESERVE VERBATIM` 清单重试一次，取更优结果
- 未保留项用 `<unpreserved_commitments>` / `<evidence_report>` 显式披露

### P3 配对评测（`benchmark/paired_eval.py`）
- Compact-Bench 风格：full / truncate / summary / fold 四种视图，同一对话、同一探针
- 指标：压缩率 + 证据保留率（不依赖 LLM reader）
- 内置合成样例可直接跑；支持 `--data conversations.jsonl`、`--json`、`--markdown`

### P4 自适应策略（`policy.py`）
- `classify_task()`：code / data / ops / prose
- `ContextPolicy`：UCB1 bandit（light/standard/deep），持久化；证据分 EMA 低于下限时强制 `light`
- 动作映射到 `keep_turns` 调整；奖励 = 证据分

### P5 成本归因 + 能力档位
- `record_compaction_cost()`：压缩调用成本（估算）vs 节省 tokens、延迟累计
- `/health` 输出 `cost.net_saved_tokens`
- `effective_threshold()`：weak 0.75 / strong 0.90 / auto 按窗口（≥200k→0.90，≥100k→0.85，否则 0.78）

## 未完成 / 已知限制
- `memskill_rl.py` 仍在 `attic/`：其 RL 脚手架未接线；P4 用可审计的 bandit 替代
- 成本归因是估算值（上游 usage 未逐次解析进 `/metrics`）
- 配对评测的"任务延续成功率"需要真实 LLM reader；当前只测证据可用性
- README 的 "40+ techniques" 叙事仍是历史文本，待 P3 文档口径彻底重写
- 线上 8199/8200 实例仍运行旧部署副本；本仓库未 push
