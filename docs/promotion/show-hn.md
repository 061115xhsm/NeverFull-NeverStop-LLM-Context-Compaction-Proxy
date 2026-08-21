# Show HN Draft — Fidelity-Gated Context Compaction Proxy

> 用途:发布到 Hacker News 的 Show HN 草稿(英文)
> 项目:NeverFull-NeverStop-LLM-Context-Compaction-Proxy(FF-Compactor)
> 发布时机建议:美国工作日早晨 8-11am EST

---

## 标题(≤80 字符,plain factual)

```
Show HN: FF-Compactor – fidelity-gated context compression for LLM agents (no GPU)
```

(备选标题:)
```
Show HN: Open-source proxy that auto-compacts LLM agent context, 0.996 fidelity, 42ms
```

---

## 正文(3-5 段)

**第一段:问题**
Your LLM agent hits the context window, truncation silently drops earlier messages, and your long-running session dies. Every agent developer knows this pain — and most "solutions" either truncate (losing information) or summarize (hallucinating).

**第二段:方案**
FF-Compactor is a transparent, zero-config proxy that sits between your agent and any LLM provider (OpenClaw, Claude Code, Hermes, AtomCode — all verified). When context approaches the limit, it compresses chat history with a **fidelity gate**: a semantic-similarity check (Sim ≥ τ) that guarantees the compressed result stays close to the original. If fidelity drops below the threshold, it rejects the result and tries a more conservative strategy. Compression amount is budget-driven; compression quality is fidelity-verified.

**第三段:实测数据(为什么不同)**
Measured on the official LongBench dataset (340 real long documents):
- 70.8% average compression, 99.6% semantic fidelity
- vs LLMLingua-7B (200 samples): 68.9% compression but only 82.8% fidelity — we beat it by **+17 points of fidelity at the same compression ratio**
- 42ms per compression on pure CPU vs LLMLingua's 1440ms on GPU — **34× faster, no GPU required, no training required**
- Full benchmark tables (ablation, sensitivity, Q&A accuracy, efficiency): BENCHMARK.md

**第四段:为什么与别人不同**
Most context-compression tools are threshold-triggered (they only act near the window limit), content-routed (they refuse to touch user messages or code), or training-dependent (LLMLingua-2, ICAE need fine-tuned models). We tested Headroom (66k stars) head-to-head: it skipped our Chinese long documents entirely (0% compression) because user messages are protected. FF-Compactor compresses any role, any content type, with a hard fidelity constraint — and it ships with a 7-page paper, 15 modules, and a full benchmark suite.

**第五段:链接**
- GitHub: https://github.com/061115xhsm/NeverFull-NeverStop-LLM-Context-Compaction-Proxy
- Benchmark data: https://github.com/061115xhsm/NeverFull-NeverStop-LLM-Context-Compaction-Proxy/blob/main/BENCHMARK.md
- Paper (7 pages): https://github.com/061115xhsm/NeverFull-NeverStop-LLM-Context-Compaction-Proxy/blob/main/docs/paper.pdf
- MIT licensed, open source, feedback welcome!

---

## 评论区常见问题 Q&A 预答(5 条)

**Q1: How is this different from just summarizing the conversation?**
A: Summarization is fidelity-blind — it can drop critical details or hallucinate. FF-Compactor scores every compression result with a real embedding similarity check and enforces a minimum fidelity (default τ=0.90). If the result fails the gate, it falls back to a more conservative compression. On LongBench, naive summary hit 79.6% fidelity vs our 99.6%.

**Q2: Does it work with my agent?**
A: It's a transparent HTTP proxy — point your agent's base URL at it (127.0.0.1:8198). Verified with OpenClaw, Claude Code, Hermes, and AtomCode. Zero code changes.

**Q3: Why no GPU?**
A: Compression is sentence-level (not token-level), using local embedding scoring and greedy selection — the whole pipeline runs in ~42ms on CPU. Token-level methods (LLMLingua) need a 7B model on GPU, which is 34× slower per compression.

**Q4: What's the actual compression ratio?**
A: Budget-driven — you control it. On LongBench we hit 70.8% average at 0.996 fidelity. The budget knob (B) makes compression ratio monotonic from ~0.71 to 0.85; the fidelity knob (τ) is stable across 0.85–0.95.

**Q5: Is this academic or production-ready?**
A: Both. It's a production proxy already integrated into local agent stacks, with 15 modules (fidelity gating, incremental L1/L2 compaction, multi-level cache, predictive precompression, reversible compression with search) plus a 7-page paper and complete reproducible benchmark suite.

---

*草稿字数:约 420 词(正文)/ 标题 82 字符(备选 78 字符)*
