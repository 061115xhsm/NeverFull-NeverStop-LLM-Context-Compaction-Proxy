# STATUS — 2026-10-26 (V11 P0 + P1a)

## 已完成

### P0 工程基线
- `requirements.txt`（核心依赖 aiohttp）、`requirements-optional.txt`（sentence-transformers）
- `.github/workflows/ci.yml`：Python 3.11/3.12/3.13，compile + 28 项单元测试
- `tests/`：28 项 characterization 测试（token 估算、工具对清洗、承诺/标识符抽取、压缩器、切分、安全检查、session id、折叠归档/契约/分节）
- **死模块归档**：`memskill_rl.py`、`incremental_compaction.py`、`async_precompressor.py`、`storage_backend.py`、`rust_ffi.py`、`rust/` → `attic/`（此前 README/ROADMAP 标 ✅ 但主程序从未 import）
- **session id 加固**：显式 header 优先；内容指纹兜底改为"告警 + 指标"；新增 `COMPACTION_PROXY_STRICT_SESSION=1`（拒绝内容指纹，返回 ephemeral id）；指纹加入 OpenAI `user` 字段隔离

### P1a 可逆折叠（默认关闭）
- 新模块 `folding.py`：
  - `FoldArchive`：SQLite 原样归档被折叠的历史，`fold:<hash>` 寻址
  - `build_fold_digest()`：结构化摘要（角色/工具/文件/命令/错误/承诺），零 LLM 调用
  - `render_fold_block()`：`<folded_context ref=...>` 类型化占位符（不拍平消息结构）
  - `verify_commitments()`：承诺 + 精确标识符的硬校验，未保留项显式披露
  - `SUMMARY_SECTION_PROMPT`：`FACTS / DECISIONS / OPEN / COMPRESSOR_INFERENCE / COMMITMENTS` 分节，事实与压缩器推断分离
- 接线：`do_compaction` 中归档 + 占位符 + 契约校验（`COMPACTION_PROXY_FOLDING=1`），`/fold/{ref}` 原样取回端点，`/health` 暴露 folding 状态
- 版本号修正：health 的 `version` V6 → V11

## 未完成（路线图）
- P1b：状态表（文件/决策版本、superseded 标记、ghost-memory 防护）
- P2：契约失败重试（当前只披露不重试）、证据保留探针
- P3：Compact-Bench 式配对评测、文档与 README 口径重写
- P4：性能下限闭环、bandit 策略、memskill_rl 接线
- P5：成本归因指标、能力自适应档位
