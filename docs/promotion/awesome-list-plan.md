# Awesome List 收录目标调研

> 项目:NeverFull-NeverStop-LLM-Context-Compaction-Proxy(FF-Compactor)
> 目标:提交 PR 进入相关 awesome list,获得永久收录流量
> 调研日期:2026-08-22

## 一、最对口:jihoo-kim/awesome-context-engineering ⭐⭐⭐

- **URL**: https://github.com/jihoo-kim/awesome-context-engineering
- **收录主题**: 上下文工程(Long-term memory、MCP、**Prompt/RAG Compression**、Multi-Agent)
- **关键发现**: 已有 `## ✂️ Compress Context` 分类,收录了 **LLMLingua**(microsoft)、sam(微软)等压缩工具——**与我们的项目 100% 对口**
- **提交建议**: PR 提交到 "Compress Context" 分类,紧挨 LLMLingua 之后
- **PR 标题**: `Add NeverFull-NeverStop-LLM-Context-Compaction-Proxy to Compress Context`

## 二、次选:Shubhamsaboo/awesome-llm-apps ⭐⭐

- **URL**: https://github.com/Shubhamsaboo/awesome-llm-apps
- **收录主题**: 100+ AI Agents / Agent Skills / RAG Apps(开源,Awesome LLM Apps)
- **分类匹配**: `🎯 LLM Optimization Tools`(LLM 优化工具)最合适
- **提交建议**: PR 到 LLM Optimization Tools 分类,说明"fidelity-gated context compaction proxy for agents"

## 三、次选:Hannibal046/Awesome-LLM ⭐⭐

- **URL**: https://github.com/Hannibal046/Awesome-LLM
- **收录主题**: LLM 论文 + 框架 + 应用(超大知名 list)
- **分类匹配**: `LLM Applications` 分类
- **提交建议**: 可附论文链接(7 页 paper),以"LLM 应用"身份提交;此 list 维护严格,优先考虑前两个

## 四、自托管向:av/awesome-llm-services ⭐

- **URL**: https://github.com/av/awesome-llm-services
- **收录主题**: 142+ 自托管 LLM 服务(Open Source、Self-hostable、Docker 友好)
- **分类匹配**: `API & Proxies`(LLM gateways and aggregators)——我们的透明代理形态匹配
- **注意**: 要求 Docker 友好(我们有 systemd/本地运行,需确认是否满足其收录标准)

## 五、备选:msb-msb/awesome-private-ai ⭐

- **URL**: https://github.com/msb-msb/awesome-private-ai
- **收录主题**: 私有 AI(本地/离线/数据不出设备)
- **匹配点**: 我们的代理可完全本地运行、无 GPU 需求,符合"私有"定位

## 六、提交 PR 通用步骤

```bash
# 1. fork 目标 awesome list 仓库
# 2. 按分类格式加一行(通常格式: - [项目名](URL) - 一句话描述 + 徽章)
# 3. 保持字母序/格式一致(先看该分类现有条目格式)
# 4. 提交 PR,标题与提交说明用英文,说明为什么匹配收录标准
```

## 七、优先级结论

| 优先级 | 目标 | 理由 |
|--------|------|------|
| 1 | **awesome-context-engineering** | 有现成 "Compress Context" 分类,与 LLMLingua 同列表,最对口 |
| 2 | **awesome-llm-apps** | 大流量,有 LLM Optimization Tools 分类 |
| 3 | awesome-llm-services / awesome-private-ai | 自托管定位,需确认收录标准 |
