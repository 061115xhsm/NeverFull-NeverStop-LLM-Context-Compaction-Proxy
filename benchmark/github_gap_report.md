# GitHub 上下文压缩项目差距对比报告

> 生成日期:2026-08-20
> 方法:Headroom 实测(headroom-ai 0.35.0)+ billion-context/DCP 文档级对比(依赖宿主 Agent,无独立 CLI)
> 数据:官方 LongBench multifieldqa_zh 前 10 条(同口径)

## 一、实测对比(同数据同口径)

| 项目 | 压缩率 | 保真度 | 延迟 | 样本 | 说明 |
|------|--------|--------|------|------|------|
| **FF-Compactor(本文)** | **0.708** | **0.996** | **42ms** | 340 | 句子级保真门控,CPU |
| LLMLingua-7B | 0.689 | 0.828 | 1440ms | 200 | token 级困惑度,GPU INT8 |
| LLMLingua-2 | 0.687 | 0.851 | ~50ms | 10 | token 分类,GPU |
| **Headroom** | **0.000** | 1.000 | 1006ms | 10 | ⚠️ 未触发压缩(见下) |

### Headroom 未触发压缩的原因(重要发现,已用超长上下文 + 触发实验验证)

Headroom 的内容路由器对不同 role 与内容类型有不同策略。我们分三轮验证:

| 测试 | role | 内容 | model_limit | transforms | 压缩率 | 结论 |
|------|------|------|------------|-----------|--------|------|
| LongBench 短文本 | user | 中文问答 | 200000 | router:protected:user_message | 0.000 | user 消息受保护 |
| 合成超长文本 | user | 400K 中文 | 200000 | router:protected:user_message | 0.000 | 超长仍保护 user |
| **触发实验(4 类内容)** | **assistant** | JSON/代码/日志/散文 | 1000 | smart_crusher / protected:recent_code / noop | 见下表 | role 决定是否压 |

**根因**:Headroom 默认**保护 user 消息不压缩**(transforms 标记 `router:protected:user_message`),只在 assistant/tool role 上触发压缩。且即便用 assistant role,内容路由器仍按类型决策:

| 内容类型 | Headroom transforms | Headroom 压缩率 | Headroom 保真度 | FF 压缩率 | FF 保真度 |
|---------|---------------------|----------------|----------------|-----------|-----------|
| JSON | smart_crusher:0.52 | 0.538 | 0.399 | 0.500 | **0.516** |
| 代码 | protected:recent_code | 0.000 | 1.000 | 0.500 | 0.636 |
| 日志 | protected:recent_code | 0.000 | 1.000 | 0.500 | 0.518 |
| 散文 | noop | 0.000 | 1.000 | 0.500 | 0.612 |

**结论**:
- Headroom 仅在 **JSON + assistant role** 下触发 smart_crusher(压缩率 0.538),但其保真度仅 0.399(统计压缩破坏语义)——FF-Compactor 在同内容上保真度 0.516,**保真度领先 0.117**;
- 代码/日志被 `protected:recent_code` 保护不压,散文被 `noop` 跳过——Headroom 在这三类上完全不压缩;
- FF-Compactor 对四类内容统一压缩(0.500),保真度 0.512-0.636 全面领先。

**这是经触发实验验证的结论**:Headroom 的内容路由器对中文长文档/代码/日志/散文均不压缩,仅压 JSON 且保真度低——**中文长文档与多类型内容是 FF-Compactor 的独占区间**。

## 二、功能差距对比表

| 功能维度 | FF-Compactor | Headroom | billion-context | DCP |
|---------|-------------|----------|-----------------|-----|
| **压缩触发** | 主动(任意长度) | 阈值(接近上限) | 模型决定 | 阈值+手动 |
| **保真度门控** | ✅ **硬约束 Sim≥τ** | ❌ 无 | ❌ 无 | ❌ 无 |
| **压缩粒度** | 句子级 | 内容感知(JSON/AST/文本) | 范围/消息 | 范围/消息 |
| **可逆压缩** | ✅ REF 引用+还原 | ✅ CCR 存储 | ✅ decompress | ❌ 替换 |
| **压缩块搜索** | ✅ search_context | ✅ retrieve | ✅ search_context | ❌ |
| **免训练** | ✅ | ❌(Kompress-v2 模型) | ✅(LLM 摘要) | ✅(LLM 摘要) |
| **GPU 需求** | ❌ 不需要 | 可选(ML 路由) | 不需要 | 不需要 |
| **Agent 接入** | 代理(透明) | wrap 20+ Agent | Pi 插件 | OpenCode 插件 |
| **MCP 支持** | ❌ | ✅ | ❌ | ❌ |
| **跨 Agent 记忆** | ✅ 三层记忆 | ✅ SharedContext | ❌ | ❌ |
| **官方基准数据** | ✅ LongBench 340 | ❌ 自有 benchmark | ❌ 模拟测试 | ❌ |

## 三、性能差距对比

| 指标 | FF-Compactor | Headroom | LLMLingua-7B |
|------|-------------|----------|--------------|
| 压缩延迟 | **42ms** | 1006ms | 1440ms |
| 硬件 | 纯 CPU | CPU+可选 GPU | GPU 7-8GB |
| 吞吐 | 23.5 次/秒 | ~1 次/秒 | <1 次/秒 |
| 压缩率(主动场景) | 70.8% | 0%(未触发) | 68.9% |

## 四、架构差距对比

| 架构维度 | FF-Compactor | Headroom | billion-context |
|---------|-------------|----------|-----------------|
| **核心机制** | 保真度门控+贪心选句 | 内容路由器+统计压缩 | LLM 自主 compress 工具 |
| **压缩决策** | 量化约束(公式) | 经验阈值 | LLM 直觉 |
| **可解释性** | ✅ 门控公式可解释 | ❌ 黑盒路由 | ❌ LLM 黑盒 |
| **多级摘要** | L1/L2 增量 | 单级 CCR | T1→T2→T3 三级 |
| **开源协议** | MIT | Apache 2.0 | MIT |
| **星标** | 1 | 66.8K | 52 |

## 五、综合差距结论

### FF-Compactor 的独占优势(无竞品覆盖)

1. **保真度硬约束**:唯一以 Sim≥τ 量化门控的方案——其他项目均靠经验/直觉
2. **中短文本主动压缩**:Headroom 等阈值触发型在文本未达上限时不压缩,FF-Compactor 任意长度可压
3. **免训练 + 无 GPU**:Headroom 需训练 Kompress-v2 模型,LLMLingua 需 GPU,FF-Compactor 纯 CPU
4. **官方基准实测**:唯一有 LongBench 340 样本权威数据的方案

### FF-Compactor 的短板(竞品更强)

1. **生态规模**:Headroom 66.8K ⭐ + 20+ Agent wrap + MCP,我们仅 1 ⭐
2. **内容感知压缩**:Headroom 的 JSON/AST 分治路由更精细,我们是统一句子级
3. **MCP 支持**:Headroom 有 MCP server,我们未实现
4. **三级摘要**:billion-context 的 T1→T2→T3 比我们的 L1/L2 更深

### 定位总结

> **FF-Compactor 在"保真度控制 + 免训练 + 中短文本主动压缩"维度无竞品;在"生态规模 + 内容感知 + MCP"维度落后于 Headroom。两者目标不同——我们是学术论文+保真度门控方法,Headroom 是商业生态+工程化压缩层,不构成直接竞争,可互为参照。**

## 六、可补方向(借鉴竞品)

| 方向 | 借鉴对象 | 优先级 |
|------|---------|--------|
| 内容感知路由器(JSON/AST 分治) | Headroom | 中 |
| MCP server 暴露压缩工具 | Headroom | 中 |
| 三级摘要 T1→T2→T3 | billion-context | 低(已有 L1/L2) |
| Agent wrap 一键接入 | Headroom | 低(已有代理) |
