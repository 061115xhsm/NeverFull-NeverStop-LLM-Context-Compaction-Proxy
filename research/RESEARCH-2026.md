# 上下文压缩领域调研报告（截至 2026-10）

> 服务于 NeverFull-NeverStop 代理的魔改（V10 → V11）。
> 方法：arXiv API 12 组主题检索 + 45 组定向标题检索（按提交时间倒序），去重得 550 篇池，
> 经白名单强制收录 + 噪声剔除后固化 **109 篇**（2023-10 ~ 2026-10）。
> 数据：`research/data/papers.jsonl`（含摘要/引用数）、`selected.tsv`、`papers-list.md`（完整分组清单）。
> 复现：`research/harvest.py`、`harvest2.py`、`curate.py`、`curate2.py`。

---

## 0. 一页结论（TL;DR）

1. **"摘要式压缩"已经是上一代方案**。2026 年的共识是**上下文折叠（context folding）**：把旧内容折叠成可寻址、可逆、带类型的占位结构，而不是用一段自由文本摘要替换历史。摘要在信息论上是不可逆的，而折叠保留"按需展开"的能力。
2. **压缩的正确性标准变了**：从"语义相似度"转向**承诺保留（commitment preservation）**+**证据保留（evidence preservation）**+**任务延续成功率**。余弦相似度头条已被两篇 2026 基准论文实质否定。
3. **改写历史是有安全后果的**：拍平消息结构会摧毁 provenance，已被命名为一种可利用的信任边界破坏（compaction laundering）。
4. **对强模型，最好的记忆可能是不记忆**：保留可搜索的原始历史，胜过精心设计的 memory 管线（bitter lesson）。压缩强度应该自适应模型能力。
5. **压缩网关的成本收益需要实证**：有实测论文表明"压缩能保质量"和"压缩能省钱"在真实多轮 agent 里并不互相蕴含。

---

## 1. 领域地图：四个层次的压缩

| 层次 | 代表工作 | 我们能否触及 |
|---|---|---|
| Token 级（prompt compression） | LLMLingua/2、LongLLMLingua、RECOMP、ICAE、Gist、COMI、RAM、ATACompressor | ✅ 作为压缩策略之一 |
| KV Cache 级 | H2O、SnapKV、KIVI、StreamingLLM、Quest、MInference、NSA、MoBA、Titans | ❌ 代理在 API 之外，碰不到 |
| **会话级（context folding / compaction）** | **MemGPT、AgentFold、ContextFolding、ReFold、REMEMORY、MemTrace、Persistent Context Graphs** | ✅ **我们的主战场** |
| 系统级（agent memory） | Mem0、MemOS、A-MEM、MemoryBank、AriGraph、MIRIX、HippoRAG、AWM | ⚠️ 部分（语义记忆/用户画像） |

我们代理处于第 3 层，同时被第 1 层的压缩器和第 4 层的记忆系统夹击。2026 年的关键认识（2607.08032, "Rate–Distortion View of Memory Compaction"）：**四个原本独立的研究社区（KV cache / prompt / recurrent state / conversation memory）正在收敛到同一个率失真问题**——"给多少比特的记忆预算，失真最小"。这给了我们一个统一的设计语言。

## 2. 十二个关键发现

### K1 折叠取代摘要：可逆、免训练、无额外调用
- **ReFold** (2610.07863)：训练无关的**可逆**轮间折叠。现有预测式方法（预测还剩多少上下文→决定何时压缩）引入运行时开销且预测错了就不可逆；ReFold 把折叠做成确定性的、可逆的操作。
- **REMEMORY** (2610.11287)：纯文本摘要不足以支撑后续所有决策，用有界的 soft memory tokens 补充摘要。
- **MemTrace** (2610.04838)：编码 agent 的长程任务里，**仓库变更会使早期执行证据失效**——压缩必须带状态一致性（什么证据还有效）。
- **Persistent Context Graphs** (2609.40118)：一次性摘要是错的，因为"新请求会改变哪些历史重要"；应该维护持久上下文图，按需物化。
- **对本项目**：我们现在的 `do_compaction` 是一次性 LLM 摘要 + 丢弃。魔改方向：**折叠优先**——旧 turn 折叠成 `{type, participants, tools, elided_ptr, digest}` 的结构化占位（我们已有 `_elide/elide_middle` 的雏形），摘要只作为 digest 的附注而不是唯一载体；被折叠内容进入 server 端可寻址存档（ARC log 已有雏形）。

### K2 压缩的正确性 = 承诺保留，不是相似度
- **Compress the Context, Keep the Commitments** (2605.17304)：上下文不是一堆 token，而是一组**承诺**（目标、约束、决定、偏好、工具结果、证据、产物、安全边界）。压缩方法"很少明确说明哪些承诺必须存活"。提出可验证压缩的形式框架。
- **KEEP-Bench** (2610.26827)：4102 个案例直接测压缩的**证据保留**。核心论点：**QA 指标会低估不忠实压缩的危害**——读者可能碰巧答对，但证据链已经断了。
- **对本项目**：我们已经在做 commitments/identifiers 提取（`extract_commitments`/`extract_identifiers`），但只用它给摘要提示词加权。魔改：升级为**硬契约**——压缩前提取承诺清单，压缩后逐项核对是否出现在结果里，缺失即拒绝+重试（我们已有 fidelity 重试骨架，换成 commitment 检查器），并输出 per-commitment 保留报告。

### K3 评测重构：配对延续 + 状态追踪，抛弃余弦头条
- **Compact-Bench** (2610.31710)：**配对评测协议**——同一仓库状态下，full-history 延续 vs summary-only 延续，测反复劣化。这是目前最严谨的 compaction 评测设计。
- **Can Agent Memory Systems Track Evolving State?** (2608.19652)：记忆基准大多测召回，但真正的要求是**追踪演进状态**——事实被修订后，答案必须反映当前状态而非被取代的旧状态。
- **A-TMA** (2607.01935)：命名了 **ghost memory** 失败模式——旧事实、当前事实、过渡事实共存于记忆库，检索时混合，误导答案。
- **Ground Truth First** (2607.21962)、**MemBench** (2506.21605)、**LongMemEval** (2410.10813)、**LoCoMo** (2402.17753)、**RULER** (2404.06654)、**NoLiMa** (2502.05167)、**HELMET** (2410.02694)。
- **对本项目**：我们 README 的 0.996 余弦头条在 2026 标准下是自欺（baseline 截断都能拿 1.000）。魔改：内置一个 Compact-Bench 式配对评测 harness（full vs compacted 在同一任务上继续跑），加上 commitment/证据探针；fidelity gate 保留但降级为守门断言，不再是卖点指标。

### K4 溯源是信任边界：永远不要拍平结构
- **Compaction Laundering** (2610.29869)：把 typed 对话拍平成 `[User]:` 式纯文本、再当记忆往前带，**摧毁 provenance，是可利用的攻击面**（注入的内容经过压缩后看起来像正常历史）。
- **From Agent Traces to Trust** (2606.04990，综述)：执行溯源/证据追踪是 agent 信任的基础设施。
- **对本项目**：我们代理改写请求体时要**绝不**把消息数组拍平成单字符串；保留 role/类型结构、tool_use/tool_result 配对（已有 `sanitize_tool_pairs`，继续强化）；折叠占位符必须带类型标记（如 `<folded type="tool_output" ref="arc:123" />`），并且 our compaction system prompt 要区分"环境确立的事实"与"压缩器自己写的推断"。

### K5 摘要里的"压缩器私货"要被剪掉
- **Prune the Summary, Not the History** (2610.20927)：摘要混合了两类内容——**环境确立的事实**和**压缩器自己写的推理**；agent 无法区分。应该对摘要做 source-judged 修剪，而不是简单剪历史。
- **Maximising Transparency** (2610.20116)：对小模型，**最大化压缩透明度**（明确标注哪里被压了、丢了什么）能改善事实与承诺的保留。
- **对本项目**：摘要 prompt 强制分节 `[FACTS | DECISIONS | OPEN_QUESTIONS | COMPRESSOR_INFERENCE]`，后一类在续传时可被降权或剔除；折叠占位符带"损失清单"。

### K6 状态一致性：追踪"现在还是真的吗"
- **Can Agent Memory Systems Track Evolving State?**（见 K3）、**MemTrace**（见 K1）、**A-TMA**（ghost memory）。
- **对本项目**：加一个**状态表**——文件路径→最后版本/行号、决策→版本链、环境快照。压缩时把"被新状态取代的旧证据"标记为 superseded 而不是删除（保留演进痕迹），检索时默认只物化 current。

### K7 学习式压缩策略（RL/bandit）
- **Memory-R1** (2508.19828)：RL 训练 ADD/UPDATE/DELETE/NOOP 记忆操作，奖励来自下游 QA。
- **Memory-R2** (2605.21768)：多 session RL 的**信用分配**问题——记忆让"过去的动作"变成"未来的环境"，rollout 间不可比。
- **Uni-Mem** (2610.29671)：端到端在线 RL 的统一记忆管理器（构建+检索联合优化）。
- **MemAgent** (2507.02259)：分段读取 + 固定大小 memory 的 RL agent，线性复杂度外推到极长文本。
- **CMI-Mem** (2607.05577)：CMI 增强的 RL 记忆管理泛化。
- **对本项目**：`memskill_rl.py` 脚手架存在但没接线。魔改：第一步做**规则+反馈回路**（PoC 式 performance floor：用户给可接受的性能下限，系统自适应压缩强度）；第二步 bandit 在 {fold / summarize / keep} 上按块选择，reward = 任务延续成功率 + token 净节省；RL 全量训练是长期项（K7 论文都指出信用分配是真难点）。

### K8 性能导向而非比例导向
- **PoC** (2603.19733)：范式转变——开发者指定**可接受的性能下限**，系统最大化压缩率，而不是指定压缩率然后祈祷性能。用性能预测器闭环。
- **对本项目**：`COMPACTION_PROXY_PREEMPTIVE_THRESHOLD=0.80` 是比例思维。改成 `COMPACTION_PROXY_PERF_FLOOR` + 内置探针任务（每次压缩后跑 1-2 个轻量校验问句），闭环调强度。

### K9 成本幻觉：压缩网关要自证省钱
- **An Empirical Cost Attribution of Context-Compression Gateways** (2609.22114)：对生产压缩网关（Paritok，Claude Code/Codex 前置）做实测——**"压缩保质量"与"压缩省钱"在真实多轮 agent 里不互相蕴含**（压缩调用本身有成本、且改变缓存行为）。
- **Prompt Compression in the Wild** (2604.02985)：测延迟、rate adherence、质量的实况口径。
- **对本项目**：`/metrics` 增加 **cost attribution**：每个请求记录 `压缩调用成本（tokens+延迟）` vs `节省的 input tokens`，滚动窗口给出净收益。这是我们相对"关掉压缩直接用大窗口"的正当性证明，没有它整个项目站不住。

### K10 强模型的 bitter lesson：少设计，多搜索
- **MemoryTax** (2610.29823)：在 LoCoMo 419 轮对话上对比 full context / BM25 / Mem0 / Zep 与三个 coding agent——**当模型足够强、能自己搜原始历史时，预制记忆管线的收益趋零甚至为负**。
- **Skip the Compressor** (2610.21073)：对编码任务，压缩器/检索器选片段不如**直接选整个文件**——补丁需要"必须改的文件"+"逐字精确的现有代码"。
- **Memo, Not True Memory** (2604.27707)：现有 agentic memory 是 lookup 不是 memory，是范畴错误。
- **对本项目**：默认档应该是**轻折叠 + 原始历史全文可检索**（我们已有 `/arc/retrieve` 端点和 ARC log——这是被低估的正确设计），激进摘要只留给"模型弱/窗口小"的场景。加一个 `agent_capability` 档位自适应。

### K11 结构瓶颈：LLM 压缩器输给全文的原因
- **Fix the Structural Bottleneck** (2602.03784)：LLM-as-compressor 明显不如全文，部分源于**无法有效保存上下文结构信息**——用显式信息传输结构修复。
- **Data Distribution Matters** (2602.01778)：压缩方法的效果强烈依赖数据分布。
- **Lost in Compression** (2608.26175)：跨语言审计抽取式压缩器的语义缺口。
- **Perplexity Paradox** (2602.15843)：代码比数学好压的机理。
- **Compression Method Matters** (2603.23527)：压缩方法与下游 benchmark 强耦合——**在 A 基准上调好的压缩参数到 B 基准会翻车**。
- **对本项目**：我们的自适应强度调度要用**任务类型条件化**（代码/JSON/散文/日志分档——已有 `classify_tool_fidelity` 雏形），并把"压缩方法×任务分布"的交互效应写进文档，别承诺全局最优。

### K12 噪声观测是第一瓶颈
- **Self-Evolving Terminal Agents** (2604.19572)：长程终端 agent 的瓶颈不是窗口长度，而是**噪声观测的累积**——对观测做压缩+自进化策略。
- **COMI** (2602.01719)：粗到细，按边际信息增益选内容。
- **Read As Human** (2602.01840)：精读/略读混合策略。
- **对本项目**：工具输出的**观测压缩**（去重、归一化、尾部截断+指针）应该先于对话摘要发生——大块的 exec 输出占了历史体积的大头。我们已有 `compress_code_output/compress_log_output/compress_json_output`，把它们提到摘要之前作为独立一层，并带"原始仍在存档"指针。

## 3. 对本项目（V10）的判决

**保留**（已经是 2026 正确方向的部分）：
- overflow 被动兜底 + 重试（几乎所有折叠/压缩论文的标配路径）
- tool_use/tool_result 配对完整性（`sanitize_tool_pairs`）
- commitments/identifiers 提取（升级为硬契约，见 K2）
- ARC log + `/arc/retrieve`（K10 的"可搜索原始历史"）
- 分工具类型的保真分级（K11 任务条件化）
- thrashing 检测、熔断、pre/post hook

**砍掉**：6 个未接线模块（memskill_rl 保留脚手架待接线）、"40+ 技术"营销叙事、余弦保真度头条、内容指纹 session 兜底（保留但降级为最后手段并打日志告警）。

**新增**：折叠占位结构（K1）、承诺契约验证器（K2）、配对评测 harness（K3）、溯源保护（K4）、状态表与 ghost-memory 防护（K6）、性能下限闭环（K8）、成本归因指标（K9）、能力自适应档位（K10）。

## 4. 魔改路线图 V11

| 阶段 | 内容 | 对应论文 |
|---|---|---|
| **P0 工程还债** | requirements.txt + CI + 拆分 7710 行单文件 + session_id 强制 header + 死模块处置 | — |
| **P1 结构正确性** | 折叠占位结构替代"摘要替换"；保持 typed 消息结构；ARC 存档寻址；摘要分节（事实/推断分离） | ReFold, Persistent Context Graphs, Prune the Summary, Compaction Laundering |
| **P2 可验证压缩** | 承诺契约：压缩前提取→压缩后逐项核对→缺失重试；证据保留探针；per-commitment 报告 | Keep the Commitments, KEEP-Bench |
| **P3 评测重构** | Compact-Bench 式配对延续评测内置化；放弃余弦头条；fidelity 降级为守门 | Compact-Bench, Can Memory Track Evolving State |
| **P4 自适应与学习** | 性能下限闭环；bandit 选 {fold/summarize/keep}；memskill_rl 接线（离线 replay 起步） | PoC, Memory-R1/R2, Uni-Mem |
| **P5 成本自证** | /metrics 成本归因（压缩成本 vs 节省）；能力自适应档位 | 2609.22114, MemoryTax |

## 5. 完整论文清单
见 `research/data/papers-list.md`（109 篇，按聚类分组，全部带 arXiv 链接）。
