# attic/ — 未接线模块归档（2026-10-26）

这些文件在 V10 里被 README/ROADMAP 标为"已实现"，但 `compaction-proxy.py` **从未 import 它们**。
按 V11 的"声明必须与代码一致"原则，移到这里存档；需要时再接线回来。

| 文件 | 原声称 | 实际情况 |
|---|---|---|
| `memskill_rl.py` | V7 MemSkill 自进化 RL | 纯标准库脚手架（bandit 近似），未接线；P4 阶段再接 |
| `incremental_compaction.py` | L1/L2 分层增量压缩 | 未接线；主程序里有另一份增量摘要实现 |
| `async_precompressor.py` | 预测式异步预压缩 | 未接线；`cache_engine.PredictivePrecompressor` 有同类实现 |
| `storage_backend.py` | PostgreSQL/Redis 分布式存储 | 未接线；运行时只用 SQLite |
| `rust_ffi.py` + `rust/` | Rust 加速（降延迟 40%+） | 未接线；纯 Python 路径才是实际运行路径 |

`model_hub.py` 保留在根目录，因为 `benchmark/benchmark.py` 仍在用。

恢复方式：`git mv attic/<file> .` 并在 `compaction-proxy.py` 里显式 import + 加开关 + 测试。
