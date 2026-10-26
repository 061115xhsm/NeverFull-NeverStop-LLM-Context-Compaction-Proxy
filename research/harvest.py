#!/usr/bin/env python3
"""arXiv 调研收割器: 主题批量 + 定向标题, 去重, S2 引用增强, 聚类, 选出 ~110 篇."""
import json, time, re, sys, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent
RAW = ROOT / "raw"; DATA = ROOT / "data"
RAW.mkdir(parents=True, exist_ok=True); DATA.mkdir(parents=True, exist_ok=True)
NS = {"a": "http://www.w3.org/2005/Atom"}

TOPICAL = [
 ("ctx-compression",  'abs:"context compression" AND cat:cs.CL'),
 ("prompt-compression",'abs:"prompt compression" AND cat:cs.CL'),
 ("agent-memory",     '(abs:"agent memory" OR abs:"memory for agents" OR (abs:"memory management" AND abs:"language model"))'),
 ("kv-cache",         'abs:"KV cache" AND (cat:cs.CL OR cat:cs.LG)'),
 ("long-ctx-compress",'abs:"long context" AND abs:"compression"'),
 ("ctx-window-agent", 'abs:"context window" AND (abs:"agent" OR abs:"agentic")'),
 ("conv-memory",      '(abs:"conversation memory" OR abs:"conversational memory" OR (abs:"conversation" AND abs:"compression"))'),
 ("rag-compression",  'abs:"retrieval augmented" AND abs:"compression"'),
 ("context-eng",      'abs:"context engineering"'),
 ("rl-agent-memory",  'abs:"reinforcement learning" AND abs:"memory" AND (abs:"agent" OR abs:"language model")'),
 ("folding",          '(all:"AgentFold" OR ti:"context folding" OR all:"SECOM")'),
 ("infinite-stream",  '(abs:"infinite" AND abs:"language model" AND abs:"memory")'),
]
TARGETED = [
 ("MemGPT",'ti:"MemGPT"'),("LLMLingua",'ti:"LLMLingua"'),("RECOMP",'ti:"RECOMP"'),
 ("SelectiveContext",'all:"selective context" AND cat:cs.CL'),("ICAE",'ti:"In-context Autoencoder"'),
 ("Gist",'ti:"Learning to Compress Prompts with Gist Tokens"'),("500x",'ti:"500xCompressor"'),
 ("SnapKV",'ti:"SnapKV"'),("H2O",'ti:"Heavy Hitter Oracle"'),("KIVI",'ti:"KIVI"'),
 ("StreamingLLM",'ti:"Efficient Streaming Language Models with Attention Sinks"'),
 ("Quest",'ti:"Quest: Query-Aware Sparsity"'),("MInference",'ti:"MInference"'),
 ("NSA",'ti:"Native Sparse Attention"'),("MoBA",'all:"MoBA" AND abs:"long context"'),
 ("Mem0",'all:"Mem0"'),("MemOS",'ti:"MemOS"'),("A-MEM",'ti:"A-MEM"'),("MemoryBank",'ti:"MemoryBank"'),
 ("HippoRAG",'ti:"HippoRAG"'),("Graphiti",'all:"Graphiti" AND abs:"memory"'),("AriGraph",'ti:"AriGraph"'),
 ("MIRIX",'ti:"MIRIX"'),("AWM",'ti:"Agent Workflow Memory"'),("MemoryR1",'ti:"Memory-R1"'),
 ("MemAlpha",'abs:"memory construction" AND abs:"reinforcement learning"'),("MemAgent",'ti:"MemAgent"'),
 ("InfiniRetri",'all:"InfiniRetri"'),("Titans",'ti:"Titans: Learning to Memorize"'),
 ("LongMemEval",'ti:"LongMemEval"'),("LoCoMo",'ti:"Evaluating Very Long-Term Conversational Memory"'),
 ("RULER",'ti:"RULER" AND abs:"long context"'),("HELMET",'ti:"HELMET" AND abs:"long context"'),
 ("NoLiMa",'ti:"NoLiMa"'),("MemBench",'ti:"MemBench"'),("InfiniteBench",'ti:"InfiniteBench"'),
 ("StateTrack",'abs:"state tracking" AND abs:"agent" AND abs:"LLM"'),
 ("CtxEngSurvey",'ti:"Context Engineering" AND abs:"survey"'),
 ("MemSurvey",'abs:"survey" AND (abs:"agent memory" OR abs:"memory of large language models")'),
 ("EffSurv",'abs:"survey" AND abs:"long-context"'),
 ("Folding2",'ti:"ContextFolding"'),("Compaction",'all:"compaction" AND abs:"LLM" AND abs:"agent"'),
]

def fetch(q, tag, maxr=40):
    url = ("https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q)
           + f"&start=0&max_results={maxr}&sortBy=submittedDate&sortOrder=descending")
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "neverfull-research/0.1"})
            with urllib.request.urlopen(req, timeout=25) as r:
                xml = r.read()
            (RAW / f"{tag}.xml").write_bytes(xml)
            out = []
            for e in ET.fromstring(xml).findall("a:entry", NS):
                aid = (e.findtext("a:id", "", NS) or "").split("/abs/")[-1]
                aid = re.sub(r"v\d+$", "", aid)
                if not aid: continue
                out.append({
                    "id": aid, "title": re.sub(r"\s+", " ", e.findtext("a:title","",NS)).strip(),
                    "abstract": re.sub(r"\s+"," ", e.findtext("a:summary","",NS)).strip()[:1500],
                    "published": (e.findtext("a:published","",NS) or "")[:10],
                    "updated": (e.findtext("a:updated","",NS) or "")[:10],
                    "authors": [a.findtext("a:name","",NS) for a in e.findall("a:author",NS)][:4],
                    "cats": [c.get("term") for c in e.findall("a:category",NS)][:6],
                })
            return out
        except Exception as ex:
            if attempt == 2:
                print(f"  !! {tag} failed: {ex}", flush=True); return []
            time.sleep(3)

pool = {}
for tag, q in TOPICAL:
    res = fetch(q, "topic_" + tag, 40)
    print(f"[topic] {tag:18s} -> {len(res)}", flush=True)
    for p in res: pool.setdefault(p["id"], {**p, "via": tag})
    time.sleep(0.7)
for tag, q in TARGETED:
    res = fetch(q, "t_" + re.sub(r"\W+","_",tag), 10)
    print(f"[target] {tag:18s} -> {len(res)}", flush=True)
    for p in res: pool.setdefault(p["id"], {**p, "via": "T:" + tag})
    time.sleep(0.7)

papers = list(pool.values())
for p in papers: p["foundational"] = p["published"] < "2024-01-01"
(DATA / "pool.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in papers), encoding="utf-8")
print(f"POOL unique: {len(papers)}  (foundational {sum(p['foundational'] for p in papers)})", flush=True)

# ---- S2 citation enrichment (best-effort) ----
ids = [p["id"] for p in papers]
for i in range(0, len(ids), 90):
    chunk = ids[i:i+90]
    body = json.dumps({"ids": ["ARXIV:" + x for x in chunk]}).encode()
    req = urllib.request.Request(
        "https://api.semanticscholar.org/graph/v1/paper/batch?fields=citationCount,year",
        data=body, headers={"Content-Type": "application/json", "User-Agent": "neverfull-research/0.1"})
    ok = False
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                arr = json.load(r)
            for p, s in zip(papers[i:i+90], arr):
                if s: p["citations"] = s.get("citationCount"); p["s2year"] = s.get("year")
            ok = True; break
        except Exception as ex:
            print(f"  s2 chunk {i} attempt {attempt}: {ex}", flush=True); time.sleep(8)
    if not ok: print("  s2 chunk skipped", flush=True)
    time.sleep(1.5)

# ---- clustering (first match wins) ----
RULES = [
 ("survey",        r"\bsurvey\b|\breview\b"),
 ("eval-bench",    r"benchmark|LongMemEval|RULER|LoCoMo|HELMET|NoLiMa|evaluation of|InfiniteBench|MemBench"),
 ("context-folding", r"context folding|AgentFold|context management|compaction|compress(ing)? (agent|conversation|interaction)|SECOM|context (usage|budget)"),
 ("agent-memory",  r"agent memory|memory (system|construction|management|agent|for agents)|MemGPT|Mem0|MemOS|A-MEM|MIRIX|AriGraph|MemoryBank|lifelong|experience (reuse|library)|procedural memory|episodic"),
 ("kv-cache",      r"KV cache|KV-cache|attention sink|sparse attention|KV eviction|cache eviction|quantiz.*(KV|cache)|StreamingLLM|snap|pyramid"),
 ("prompt-compression", r"prompt compression|LLMLingua|token (pruning|reduction)|soft prompt|In-context Autoencoder|gist|500x|compress prompts"),
 ("rl-policy",     r"reinforcement learning|RL-trained|policy learning|bandit|reward"),
 ("rag",           r"retrieval[- ]augmented|\bRAG\b|retriever|retrieval"),
 ("structured",    r"state tracking|knowledge graph|graph memory|structured (memory|state)"),
 ("long-stream",   r"long context|long-context|infinite|streaming|long-term|million-token|1M token"),
 ("summarization", r"summariz"),
]
def cluster(p):
    t = (p["title"] + " " + p["abstract"][:600]).lower()
    for name, pat in RULES:
        if re.search(pat, t): return name
    return "other"
for p in papers: p["cluster"] = cluster(p)

recent = [p for p in papers if not p["foundational"]]
found  = [p for p in papers if p["foundational"]]
recent.sort(key=lambda p: (-(p.get("citations") or 0), p["published"]), reverse=False)
recent.sort(key=lambda p: (p["published"]), reverse=True)
recent.sort(key=lambda p: -(p.get("citations") or 0))
CAPS = {"agent-memory":26,"context-folding":16,"prompt-compression":12,"eval-bench":14,"kv-cache":10,
        "rl-policy":9,"rag":6,"structured":7,"long-stream":6,"summarization":4,"survey":4,"ctx-compression":8,"other":6}
sel, used = [], {}
for p in recent:
    c = p["cluster"]; cap = CAPS.get(c, 6)
    if used.get(c, 0) < cap: sel.append(p); used[c] = used.get(c, 0) + 1
sel.sort(key=lambda p: (p["cluster"], p["published"]))
sel += [p for p in found][:14]
(DATA / "selected.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in sel), encoding="utf-8")

with open(DATA / "selected.tsv", "w", encoding="utf-8") as f:
    for p in sel:
        f.write(f"{p['cluster']}\t{p['published']}\t{p.get('citations') or '-'}\t{p['id']}\t{p['title'][:110]}\n")
print("\n=== selected:", len(sel), " clusters:", dict(sorted(used.items())), flush=True)
