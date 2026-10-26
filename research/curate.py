#!/usr/bin/env python3
"""第三轮: 人工规则策展 — 强制收录经典, 剔除噪声, 平衡到 ~110 篇."""
import json, re, time, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
DATA = Path(__file__).parent / "data"
NS = {"a": "http://www.w3.org/2005/Atom"}

pool = {}
for line in (DATA / "pool.jsonl").read_text(encoding="utf-8").splitlines():
    p = json.loads(line); pool[p["id"]] = p

# 搜索 429 没拿到的基准论文, 直接按 id 拉
missing_ids = ["2404.06654","2410.10813","2402.17753","2502.05167"]
try:
    url = "https://export.arxiv.org/api/query?id_list=" + ",".join(missing_ids) + "&max_results=10"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"neverfull-research/0.1"}), timeout=25) as r:
        for e in ET.fromstring(r.read()).findall("a:entry", NS):
            aid = (e.findtext("a:id","",NS) or "").split("/abs/")[-1]
            aid = re.sub(r"v\d+$","",aid)
            if aid in pool: continue
            pool[aid] = {"id":aid,
                "title": re.sub(r"\s+"," ",e.findtext("a:title","",NS)).strip(),
                "abstract": re.sub(r"\s+"," ",e.findtext("a:summary","",NS)).strip()[:1500],
                "published": (e.findtext("a:published","",NS) or "")[:10],
                "updated":"", "authors":[], "cats":[], "via":"by-id", "foundational": False}
    print("by-id fetch ok:", [p for p in missing_ids if p in pool])
except Exception as ex:
    print("by-id fetch failed:", ex)

WHITELIST = [r"^MemGPT", r"Mem0:", r"^MemOS", r"A-MEM", r"MemoryBank", r"AriGraph", r"^MIRIX",
 r"^MemAgent", r"Memory-R", r"Agent Workflow Memory", r"HippoRAG", r"Graphiti", r"Zep: A Temporal",
 r"Infinite Retrieval", r"^Titans", r"Native Sparse Attention", r"MoBA", r"MInference", r"Query-Aware Sparsity",
 r"KIVI", r"SnapKV", r"Heavy Hitter Oracle", r"Efficient Streaming Language Models", r"LLMLingua",
 r"Compressing Context to Enhance Inference", r"^RECOMP", r"In-context Autoencoder for Context Compression",
 r"Gist Tokens", r"500xCompressor", r"Context ?Folding", r"AgentFold", r"LongMemEval", r"Very Long-Term Conversational",
 r"^RULER", r"^HELMET", r"NoLiMa", r"^MemBench", r"Survey of Context Engineering", r"Memory for Autonomous LLM Agents",
 r"From Storage to Experience", r"ReFold", r"REMORY", r"MemTrace", r"Compact-Bench", r"KEEP-Bench", r"MemoryTax",
 r"Uni-Mem", r"LazyMem", r"CMI-Mem", r"ContextWeaver", r"Organize then Retrieve", r"Rate.{1,3}Distortion View of Memory",
 r"Compress the Context, Keep the Commitments", r"Track Evolving State", r"Prune the Summary", r"Persistent Context Graphs",
 r"Cache Engineering for Agents", r"Observational Context Compression", r"Performance-oriented Context Compression",
 r"Coarse-to-fine Context Compression", r"Read As Human", r"IterCOMP", r"PC-SubMax", r"Referential Dangling",
 r"Lost in Compression", r"Compression Method Matters", r"Control Under Compression", r"Ground Truth First",
 r"Perplexity Paradox", r"Memory Consolidation Flattens", r"Context-Driven Incremental Compression",
 r"Beyond Position Bias", r"From Similarity to Structure", r"Evidence Frontloading", r"Temporal State Tracking",
 r"Narrative State Tracking", r"What Should a Skill Remember", r"Memo, Not True Memory", r"Next Frontier of Information Retrieval",
 r"A-TMA", r"Beyond Atomic Facts", r"Query-Conditioned Memory Compilation", r"Habit Induction", r"REM: What to Store",
 r"Governing Evolving Memory", r"Whose Credential", r"Compaction Laundering", r"Maximising Transparency in Context Compaction",
 r"Latent Compaction with Test-Time", r"Optimization Landscape of Learning Compacted", r"Telegraph English",
 r"Cross-Family Speculative Prefill", r"Attribution-Compression Frontier", r"Making Prospective Memory",
 r"Recall Is Not Enough", r"Semantic Hierarchy-Aware Hierarchical Memory", r"Memory as a Model",
 r"Memory-Augmented Model Context Optimization", r"Decoupling Logic from Persona", r"Safe, Persistent, and Evolving Agent Harness",
 r"Memory-Centric Continual Learning", r"Joint Optimization of Multi-agent Memory", r"Selection Integrity for LLM Graph Memory",
 r"PraMem", r"Bud ContextCompact", r"memory construction",]
BLACKLIST = [r"SECOMlint", r"FedMSPC", r"Quantile Online", r"semiconductor", r"CAE v2", r"Self-Supervised Representation",
 r"Filling time-series", r"Pixel Text", r"Arm-membench", r"5uGUE", r"5G Devices", r"JARVIS", r"TabNSA", r"VideoNSA",
 r"FSA: An Alternative", r"SpikingBrain", r"MOSAIC", r"Certified Sparse Attention", r"Hybrid Sequence Models",
 r"Hidden States Drift", r"Optimizing Mixture of Block", r"FlashNext", r"NeMo-DCR", r"WebCoder", r"NgramRL",
 r"CollabSciReM", r"METTA", r"LATTE", r"CineForge", r"ARGUS", r"Humanize MLE", r"Narrative World Model",
 r"Explore Like Humans", r"MemMark", r"ShortCoder", r"98.*Faster LLM Routing", r"Separating Constraint Compliance",
 r"Model Search Across Multiple", r"Optimizing Native Sparse Attention with Latent"]

def match(p, pats): 
    return any(re.search(pat, p["title"], re.I) for pat in pats)
kept, dropped = {}, []
for pid, p in pool.items():
    if match(p, BLACKLIST): dropped.append(p); continue
    kept[pid] = p
forced = [p for p in kept.values() if match(p, WHITELIST)]
forced_ids = {p["id"] for p in forced}
RULES = [
 ("survey", r"\bsurvey\b|\breview\b"),
 ("eval-bench", r"benchmark|LongMemEval|RULER|LoCoMo|HELMET|NoLiMa|evaluation of|InfiniteBench|MemBench|KEEP-Bench|Compact-Bench"),
 ("context-folding", r"context folding|AgentFold|context management|compaction|compress(ing)? (agent|conversation|interaction)|SECOM|context (usage|budget)|folding"),
 ("agent-memory", r"agent memory|memory (system|construction|management|agent|for agents|recall|consolidation)|MemGPT|Mem0|MemOS|A-MEM|MIRIX|AriGraph|MemoryBank|lifelong|procedural memory|episodic|experience"),
 ("kv-cache", r"KV cache|KV-cache|attention sink|sparse attention|KV eviction|cache eviction|StreamingLLM|snap|pyramid"),
 ("prompt-compression", r"prompt compression|LLMLingua|token (pruning|reduction)|soft prompt|In-context Autoencoder|gist|500x|compress prompts|selective context|context compression"),
 ("rl-policy", r"reinforcement learning|policy learning|bandit|reward"),
 ("rag", r"retrieval[- ]augmented|\bRAG\b|retriever|retrieval"),
 ("structured", r"state tracking|knowledge graph|graph memory|structured (memory|state)"),
 ("long-stream", r"long context|long-context|infinite|streaming|long-term|million-token"),
 ("summarization", r"summariz"),
]
def cluster(p):
    t = (p["title"] + " " + p["abstract"][:600]).lower()
    for name, pat in RULES:
        if re.search(pat, t): return name
    return "other"
for p in kept.values(): p["cluster"] = cluster(p)

recent = [p for p in kept.values() if not p["foundational"]]
recent.sort(key=lambda p: -(p.get("citations") or 0))
CAPS = {"agent-memory":24,"context-folding":18,"prompt-compression":12,"eval-bench":12,"kv-cache":9,
        "rl-policy":7,"rag":7,"structured":7,"long-stream":6,"summarization":4,"survey":4,"other":8}
sel, used = [], {}
for p in recent:
    c = p["cluster"]; cap = CAPS.get(c, 6)
    if used.get(c, 0) < cap: sel.append(p); used[c] = used.get(c,0)+1
sel.sort(key=lambda p: (p["cluster"], p["published"]))
# 奠基经典(2023-): 只保留白名单里的
found = [p for p in kept.values() if p["foundational"] and p["id"] in forced_ids]
sel += sorted(found, key=lambda p: p["published"])
DATA.joinpath("papers.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in sel), encoding="utf-8")
with open(DATA / "selected.tsv", "w", encoding="utf-8") as f:
    for p in sel:
        f.write(f"{p['cluster']}\t{p['published']}\t{p.get('citations') or '-'}\t{p['id']}\t{p['title'][:120]}\n")
print(f"final selected: {len(sel)}  dropped-noise: {len(dropped)}")
print("clusters:", dict(sorted(used.items())), "+ foundational:", len(found))
