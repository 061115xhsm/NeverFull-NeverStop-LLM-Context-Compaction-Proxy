# 部署版升级记录:V6 → V10

> 日期:2026-08-22
> 部署版:`~/.local/bin/openclaw-compaction-proxy.py`
> 备份:`~/.local/bin/openclaw-compaction-proxy-v6-backup.py`
> 服务:`systemctl --user openclaw-compaction-proxy.service`(用户级)

## 背景

此前生产部署版停在 **V6**(仅 Provider 抽象层 + 溢出重试),而仓库已演进至 **V10**
(21/21 路线图:保真度门控、增量压缩、多层缓存、Rust 加速、预压缩等 15 模块)。
openclaw 实际链路(8198)一直在跑 V6,论文/评测中的 V10 能力在生产中未生效。

## 升级内容

### 1. 注入 V8-V10 增强导入段

在部署版导入区注入仓库版 V8 可选增强模块段(6 个 ENH_ 开关):

| 开关 | 模块 | 能力 |
|------|------|------|
| ENH_FIDELITY | fidelity.py | 保真度门控(Sim≥τ)+ 贪心选句 |
| ENH_MEMORY | memory_engine.py | 三层记忆 |
| ENH_KG | knowledge_graph.py | 知识图谱 |
| ENH_COMPRESS | compression_advanced.py | AST/差异/可逆压缩 |
| ENH_SECURITY | security_enhanced.py | PII 脱敏/加密 |
| ENH_PROTOCOL | protocol_extra.py | 多协议适配 |
| ENH_CACHE | cache_engine.py | 多级缓存 + 预压缩 |
| ENH_OBSERVABILITY | observability.py | 监控/降级 |

### 2. 复制 14 个 V10 增强模块到部署目录

`fidelity/memory_engine/knowledge_graph/compression_advanced/security_enhanced/`
`protocol_extra/cache_engine/observability/incremental_compaction/storage_backend/`
`async_precompressor/model_hub/memskill_rl/rust_ffi.py`

### 3. 保留 OpenClaw 专属配置(差异属有意设计)

- `COMPACTION_MODEL=xsparkx2agent`(讯飞模型)
- `maas-coding-api` Anthropic 格式端点
- 专属路径(semantic-memory / sessions.db / user-profile)
- Provider 抽象层(OpenAI/Anthropic/Gemini)

### 4. 版本标识更新

`/health` 端点 version 字段:`V6` → `V10`

## 验证结果

| 项 | 结果 |
|----|------|
| 14 个 V10 模块导入 | ✅ 全部成功(rust_ffi 优雅降级为纯 Python) |
| 部署版语法检查 | ✅ py_compile 通过 |
| 服务重启 | ✅ `systemctl --user restart` 成功,状态 active |
| 8198 端口 | ✅ 新进程监听 |
| `/health` | ✅ `{"status": "ok", "version": "V10", "compaction_model": "xsparkx2agent"}` |

## 回滚方式

```bash
cp ~/.local/bin/openclaw-compaction-proxy-v6-backup.py ~/.local/bin/openclaw-compaction-proxy.py
systemctl --user restart openclaw-compaction-proxy.service
```
