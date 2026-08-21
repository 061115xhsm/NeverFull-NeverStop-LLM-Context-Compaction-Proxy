# Reddit 推广发帖清单与规范

> 项目:NeverFull-NeverStop-LLM-Context-Compaction-Proxy(FF-Compactor)
> 目标:在 Reddit 相关板块引入种子流量(0→100 star 的关键渠道)
> 核心原则:**"我做了 X 解决 Y,求反馈"框架 > 纯广告**,用问题导向而非项目名

---

## 一、对口 Subreddit 清单

| Subreddit | 主题 | 适合帖型 | 订阅量级* | 自推广规则 |
|-----------|------|---------|----------|-----------|
| **r/LocalLLaMA** | 本地 LLM 部署/推理 | 技术评测帖(实测数据+对比表) | 大型(~百万) | 允许,需展示真实技术与数据,标题可带 Show HN 风格 |
| **r/selfhosted** | 自托管工具 | "我做了个自托管的 LLM 上下文压缩代理" | 大型 | 允许,强调"本地运行/免费/无需 GPU" |
| **r/opensource** | 开源项目 | 介绍帖 + 求 star/贡献 | 大型 | 允许,需遵守"我做的 X,反馈欢迎"格式 |
| **r/coolgithubprojects** | GitHub 项目分享 | 简短项目介绍 | 中型 | 最宽松,直接发项目链接+一句话 |
| **r/SideProject** | 独立开发 | 项目展示 + 成长记录 | 中型 | 允许,带故事性(踩坑/数据) |
| **r/MachineLearning** | ML 学术 | 论文+评测帖 | 大型 | 学术向,附论文链接,避免营销腔 |
| **r/LanguageTechnology** | NLP | 上下文压缩技术讨论 | 中型 | 允许,技术深度优先 |
| **r/ClaudeAI** | Claude 生态 | "Claude Code 上下文超限?试试这个" | 大型 | 允许,强调与 Claude Code 集成 |

*订阅量级为粗略估计,发帖前请自行确认。

## 二、发帖通用规范

1. **标题格式**:`[Project] Name — what it does`(r/coolgithubprojects)或描述性问题标题(r/LocalLLaMA:`I built a fidelity-gated context compressor that beats LLMLingua by +17pp fidelity — 42ms on CPU, no GPU`)
2. **正文结构**:问题(1 段)→ 方案(1 段)→ 实测数据(表格/要点)→ 链接(仓库+BENCHMARK+论文)
3. **数据是最好的广告**:贴出 LongBench 340 样本 0.996 保真度、vs LLMLingua 对比表、42ms CPU——这些硬数据是 Reddit 技术圈最吃的一套
4. **回应评论**:发帖后 24 小时内蹲在帖子里回答每一个问题(Show HN/Reddit 算法权重看互动)
5. **不要**:标题党、求 star 求关注、重复刷屏、纯链接帖(会被删)

## 三、两个帖子草稿

### 草稿 A:r/LocalLLaMA(技术评测向)

**标题**:
```
I benchmarked 5 context-compression methods on official LongBench — my fidelity-gated approach wins by +17pp at equal compression, runs in 42ms on CPU

**正文**:
> Context window overflow is the #1 killer of long-running LLM agent sessions. I built and benchmarked a fidelity-gated compaction proxy against LLMLingua-7B, LLMLingua-2, Headroom, and naive summarization on the official LongBench dataset (340 real long docs, same data, same fidelity metric — sentence-transformers cosine).
>
> Results (avg):
> | Method | Compression | Fidelity |
> |---|---|---|
> | LLMLingua-7B (200 samples) | 68.9% | 82.8% |
> | LLMLingua-2 (10 samples) | 68.7% | 85.1% |
> | Headroom (JSON only, 10) | 34.6% | 39.7% |
> | **FF-Compactor (340 samples)** | **70.8%** | **99.6%** |
>
> The key idea: compression amount is budget-driven, but **quality is enforced by a semantic gate** (Sim ≥ τ). If the compressed result falls below the fidelity threshold, it's rejected and a more conservative strategy is tried. No training, no GPU (42ms CPU vs LLMLingua's 1440ms GPU).
>
> Full reproducible benchmark suite + 7-page paper in the repo. Feedback very welcome — what edge cases would you test?
>
> GitHub: [link] · BENCHMARK.md: [link] · Paper: [link]

### 草稿 B:r/selfhosted(实用向)

**标题**:
```
Show HN: Self-hosted LLM context compaction proxy — auto-compresses agent history at 99.6% fidelity, no GPU, MIT

**正文**:
> I got tired of my Claude Code / OpenClaw sessions dying at the context window limit, so I built a transparent proxy that sits between the agent and the LLM provider.
>
> What it does:
> - Auto-compacts chat history when context approaches the limit (preemptive at 80%)
> - Fidelity-gated: every compression is checked against the original (99.6% semantic fidelity on official LongBench, 340 samples)
> - Pure CPU, ~42ms per compression — no GPU needed, runs on any $5 VPS or old laptop
> - Zero config: point your agent's base URL at it; verified with OpenClaw, Claude Code, Hermes, AtomCode
> - Incremental L1/L2 compaction + multi-level cache + reversible compression with search
>
> MIT licensed, fully local (no data leaves your machine), and there's a 7-page paper + full benchmark suite if you care about the numbers.
>
> Repo: [link]
> Happy to answer questions — especially: what's your current context management pain?

---

## 四、发布时机与节奏建议

| 阶段 | 动作 | 时机 |
|------|------|------|
| 发布日 | r/LocalLLaMA + r/coolgithubprojects 同时发 | 美国工作日早晨(EST 8-11am) |
| 24h 内 | 蹲帖回答所有评论 | 持续 |
| 次日 | r/selfhosted + r/SideProject | 错峰 12-24h |
| 48h 后 | dev.to 长文(SEO 长尾) | 任意 |
| 里程碑 | v1.0 / 大版本 re-launch | 每次发版 |

> ⚠️ **集中冲刺战术**:Show HN + Reddit + 文章压进同一 24-48h,趋势榜看"星速"不看总量,50-300 星/天可上榜,上榜后有机流量自增长。

## 五、避坑警告

- ❌ 不要买星/互刷(可检测、GitHub 会清、伤信誉)
- ❌ 不要群发私信求 star(违反政策)
- ❌ 不要纯广告帖(Reddit 会删,用"我做了 X 解决 Y"框架)
- ✅ 发帖前读各版 rules/wiki(每个 subreddit 规范不同)
