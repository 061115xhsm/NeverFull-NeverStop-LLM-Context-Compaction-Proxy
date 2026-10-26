#!/usr/bin/env python3
"""第二轮: 补抓 429 失败的定向查询(长退避) + S2 引用 + 聚类选篇."""
import json, time, re, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent
RAW = ROOT / "raw"; DATA = ROOT / "data"
NS = {"a": "http://www.w3.org/2005/Atom"}

RETRY = [
 ("LoCoMo",'ti:"Evaluating Very Long-Term Conversational Memory"'),
 ("LongMemEval",'ti:"LongMemEval"'),
 ("RULER",'ti:"RULER" AND abs:"long context"'),
 ("NoLiMa",'ti:"NoLiMa"'),
 ("HELMET",'ti:"HELMET" AND abs:"long context"'),
 ("InfiniteBench",'ti:"InfiniteBench"'),
 ("MemBench",'ti:"MemBench"'),
 ("MemoryR1",'ti:"Memory-R1"'),
 ("MemAgent",'ti:"MemAgent"'),
 ("AWM",'ti:"Agent Workflow Memory"'),
 ("MemAlpha",'abs:"memory construction" AND abs:"reinforcement learning"'),
 ("MIRIX",'ti:"MIRIX"'),
 ("AriGraph",'ti:"AriGraph"'),
 ("InfiniRetri",'all:"InfiniRetri"'),
 ("Titans",'ti:"Titans: Learning to Memorize"'),
 ("CtxEngSurvey",'ti:"Context Engineering" AND abs:"survey"'),
 ("MemSurvey",'abs:"survey" AND (abs:"agent memory" OR abs:"memory of large language models")'),
 ("StateTrack",'abs:"state tracking" AND abs:"agent" AND abs:"LLM"'),
 ("Folding2",'ti:"ContextFolding"'),
 ("Compaction",'all:"compaction" AND abs:"LLM" AND abs:"agent"'),
]

def fetch(q, tag, maxr=10, timeout=25):
    url = ("https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q)
           + f"&start=0&max_results={maxr}&sortBy=submittedDate&sortOrder=descending")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "neverfull-research/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            xml = r.read()
        (RAW / f"t2_{tag}.xml").write_bytes(xml)
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
        print(f"  !! retry {tag}: {ex}", flush=True); return []

pool = {}
for line in (DATA / "pool.jsonl").read_text(encoding="utf-8").splitlines():
    p = json.loads(line); pool[p["id"]] = p
print(f"loaded pool: {len(pool)}", flush=True)

got = 0
for tag, q in RETRY:
    res = fetch(q, tag)
    print(f"[retry] {tag:14s} -> {len(res)}", flush=True)
    for p in res:
        if p["id"] not in pool:
            pool[p["id"]] = {**p, "via": "T:" + tag}; got += 1
    time.sleep(6)
print(f"retry added: {got}, pool now: {len(pool)}", flush=True)
(DATA / "pool.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pool.values()), encoding="utf-8")

papers = list(pool.values())
for p in papers: p["foundational"] = p["published"] < "2024-01-01"

# ---- S2 citations, best-effort ----
ids = [p["id"] for p in papers]
got_c = 0
for i in range(0, len(ids), 90):
    chunk = ids[i:i+90]
    body = json.dumps({"ids": ["ARXIV:" + x for x in chunk]}).encode()
    req = urllib.request.Request(
        "https://api.semanticscholar.org/graph/v1/paper/batch?fields=citationCount,year",
        data=body, headers={"Content-Type": "application/json", "User-Agent": "neverfull-research/0.1"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                arr = json.load(r)
            for p, s in zip(papers[i:i+90], arr):
                if s and s.get("citationCount") is not None:
                    p["citations"] = s.get("citationCount"); p["s2year"] = s.get("year"); got_c += 1
            break
        except Exception as ex:
            print(f"  s2 chunk {i} attempt {attempt}: {ex}", flush=True); time.sleep(10)
    time.sleep(2)
print(f"s2 citations matched: {got_c}/{len(ids)}", flush=True)
(DATA / "pool.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in papers), encoding="utf-8")

RULES = [
 ("survey",        r"\bsurvey\b|\breview\b"),
 ("eval-bench",    r"benchmark|LongMemEval|RULER|LoCoMo|HELMET|NoLiMa|evaluation of|InfiniteBench|MemBench"),
 ("context-folding", r"context folding|AgentFold|context management|compaction|compress(ing)? (agent|conversation|interaction)|SECOM|context (usage|budget)"),
 ("agent-memory",  r"agent memory|memory (system|construction|management|agent|for agents)|MemGPT|Mem0|MemOS|A-MEM|MIRIX|AriGraph|MemoryBank|lifelong|experience (reuse|library)|procedural memory|episodic"),
 ("kv-cache",      r"KV cache|KV-cache|attention sink|sparse attention|KV eviction|cache eviction|StreamingLLM|snap|pyramid"),
 ("prompt-compression", r"prompt compression|LLMLingua|token (pruning|reduction)|soft prompt|In-context Autoencoder|gist|500x|compress prompts|selective context"),
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
recent.sort(key=lambda p: -(p.get("citations") or 0))
CAPS = {"agent-memory":26,"context-folding":16,"prompt-compression":12,"eval-bench":14,"kv-cache":10,
        "rl-policy":9,"rag":6,"structured":7,"long-stream":6,"summarization":4,"survey":4,"other":8}
sel, used = [], {}
for p in recent:
    c = p["cluster"]; cap = CAPS.get(c, 6)
    if used.get(c, 0) < cap: sel.append(p); used[c] = used.get(c, 0) + 1
sel.sort(key=lambda p: (p["cluster"], p["published"]))
sel += found[:14]
(DATA / "selected.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in sel), encoding="utf-8")
with open(DATA / "selected.tsv", "w", encoding="utf-8") as f:
    for p in sel:
        f.write(f"{p['cluster']}\t{p['published']}\t{p.get('citations') or '-'}\t{p['id']}\t{p['title'][:110]}\n")
print("\n=== selected:", len(sel), " clusters:", dict(sorted(used.items())), flush=True)
