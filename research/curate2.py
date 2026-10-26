#!/usr/bin/env python3
"""第四轮: 补齐搜索429漏掉的经典 + 剔除误入噪声, 固化最终 121 篇."""
import json, re, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
DATA = Path(__file__).parent / "data"
NS = {"a": "http://www.w3.org/2005/Atom"}
pool = {json.loads(l)["id"]: json.loads(l) for l in (DATA/"pool.jsonl").read_text(encoding="utf-8").splitlines()}
cur = {json.loads(l)["id"]: json.loads(l) for l in (DATA/"papers.jsonl").read_text(encoding="utf-8").splitlines()}

ADD = ["2403.12968","2310.06839","2304.12102","2306.14048","2504.19413","2507.03724","2502.12110"]
need = [i for i in ADD if i in pool and i not in cur]
for i in need: cur[i] = pool[i]
still = [i for i in ADD if i not in cur]
if still:
    url = "https://export.arxiv.org/api/query?id_list=" + ",".join(still)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"neverfull-research/0.1"}), timeout=25) as r:
            for e in ET.fromstring(r.read()).findall("a:entry", NS):
                aid = re.sub(r"v\d+$","",((e.findtext("a:id","",NS) or "").split("/abs/")[-1]))
                if aid in cur: continue
                cur[aid] = {"id":aid,"title":re.sub(r"\s+"," ",e.findtext("a:title","",NS)).strip(),
                    "abstract":re.sub(r"\s+"," ",e.findtext("a:summary","",NS)).strip()[:1500],
                    "published":(e.findtext("a:published","",NS) or "")[:10],"updated":"","authors":[],"cats":[],"via":"by-id2","foundational":(e.findtext("a:published","")[:10]<"2024-01-01")}
    except Exception as ex: print("id fetch fail:", ex)
print("added:", need or "-", "| fetched:", still or "-")
for i in still:
    if i in cur: print("  verify:", i, "->", cur[i]["title"][:80])

JUNK = ["2311.02800","2603.20782","2604.07784","2602.04750","2608.05446","2609.36720","2610.28121",
        "2610.28776","2610.25957","2610.24236","2610.21548","2609.38900","2610.31098","2604.08426",
        "2608.17950","2609.38832","2610.06686","2610.19026","2610.22985"]
for j in JUNK: cur.pop(j, None)

RULES = [
 ("survey", r"\bsurvey\b|\breview\b"),
 ("eval-bench", r"benchmark|LongMemEval|RULER|LoCoMo|HELMET|NoLiMa|evaluation of|MemBench|KEEP-Bench|Compact-Bench|lifecycle evaluation"),
 ("context-folding", r"context folding|AgentFold|context management|compaction|compress(ing)? (agent|conversation|interaction)|folding|context (usage|budget)|context render"),
 ("agent-memory", r"agent memory|memory (system|construction|management|agent|for agents|recall|consolidation)|MemGPT|Mem0|MemOS|A-MEM|MIRIX|AriGraph|MemoryBank|lifelong|procedural memory|episodic|experience|sleeping agent"),
 ("kv-cache", r"KV cache|KV-cache|attention sink|sparse attention|KV eviction|StreamingLLM"),
 ("prompt-compression", r"prompt compression|LLMLingua|token (pruning|reduction)|soft prompt|In-context Autoencoder|gist|500x|compress prompts|selective context|context compression|marginal information gain"),
 ("rl-policy", r"reinforcement learning|policy learning|bandit|reward"),
 ("rag", r"retrieval[- ]augmented|\bRAG\b|retriever|retrieval"),
 ("structured", r"state tracking|knowledge graph|graph memory|structured (memory|state)|state-aware"),
 ("long-stream", r"long context|long-context|infinite|streaming|long-term|million-token"),
 ("summarization", r"summariz"),
]
def cluster(p):
    t = (p["title"] + " " + p["abstract"][:600]).lower()
    for name, pat in RULES:
        if re.search(pat, t): return name
    return "other"
for p in cur.values(): p["cluster"] = cluster(p)

sel = sorted(cur.values(), key=lambda p: (p["cluster"], p["foundational"], p["published"]))
(DATA/"papers.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in sel), encoding="utf-8")
with open(DATA/"selected.tsv","w",encoding="utf-8") as f:
    for p in sel:
        f.write(f"{p['cluster']}\t{p['published']}\t{p.get('citations') or '-'}\t{p['id']}\t{p['title'][:120]}\n")
from collections import Counter
print("final:", len(sel), dict(Counter(p["cluster"] for p in sel)))
