#!/usr/bin/env python3
"""Real memory-benchmark evaluation for V11 (LoCoMo + synthetic).

For each conversation we build four context views of the same history:
  full      - every session verbatim
  truncate  - the last 35% of characters
  llm       - generic LLM summary + last session verbatim
  v11       - state snapshot + fold digest + sectioned summary + last session

Questions are answered by either an extractive reader (deterministic) or an
LLM reader. Metrics: compression, evidence recall (gold dia_ids), QA accuracy,
context size, summary latency and token cost.

Usage:
  python benchmark/memory_eval.py --data /tmp/locomo10.json \
      --convs 3 --questions 6 --reader llm --strategies full,truncate,llm,v11 \
      --json research/data/memory_eval.json --markdown research/EVAL-2026-10-26.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folding import build_fold_digest, render_fold_block  # noqa: E402

CFG_PATH = "/home/qq/.openclaw/openclaw.json"

NF_PROMPT = """You are a context summarization assistant. Summarize the conversation so another assistant can answer questions about it later.

Use exactly these sections:
### FACTS
Concrete facts: people, places, dates, numbers, events, preferences.
### DECISIONS
Plans, decisions, and commitments, with status if known.
### OPEN
Anything unresolved.
### COMPRESSOR_INFERENCE
Your own inferences, clearly labelled.
### COMMITMENTS
Names, dates, exact phrases and relationships that must survive.

Preserve exact names, dates, numbers and places. Do not invent facts."""

GENERIC_PROMPT = """Summarize the conversation for an assistant that must answer questions about it later.
Preserve exact names, dates, places, numbers, and relationships. Use concise bullet points.
Do not invent facts."""


def endpoint():
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    p = cfg["models"]["providers"]["tierpool"]
    return p["baseUrl"].rstrip("/"), p["apiKey"]


def chat(messages, max_tokens=1200, timeout=60, model="tierflow"):
    """Call the local endpoint; one real fallback (502s/slow calls happen)."""
    base, key = endpoint()
    last = None
    for m in (model, "tierflow_pro"):
        try:
            body = json.dumps({"model": m, "messages": messages, "max_tokens": max_tokens,
                               "temperature": 0.2}).encode()
            req = urllib.request.Request(base + "/chat/completions", data=body,
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            return d["choices"][0]["message"].get("content") or "", d.get("usage", {})
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0)
    raise RuntimeError(f"model call failed: {last}")


def est(text: str) -> int:
    return max(1, int(len(text) / 4.0))


def norm(s: str) -> str:
    s = (s or "").lower().replace("`", "").replace("*", "")
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> list:
    return re.findall(r"[a-z0-9]+", norm(s))


def token_f1(pred: str, gold: str) -> float:
    p, g = tokens(pred), tokens(gold)
    if not p or not g:
        return 0.0
    common = set(p) & set(g)
    if not common:
        return 0.0
    prec = len(common) / len(p)
    rec = len(common) / len(g)
    return 2 * prec * rec / (prec + rec)


def score_answer(pred: str, gold: str) -> float:
    if norm(gold) and norm(gold) in norm(pred):
        return 1.0
    return 1.0 if token_f1(pred, gold) >= 0.5 else 0.0


def is_refusal(pred: str) -> bool:
    p = norm(pred)
    return any(x in p for x in ("not mentioned", "unknown", "no information",
                                "not provided", "cannot", "can t tell", "not stated",
                                "no mention", "isn t mentioned"))


# ── dataset ───────────────────────────────────────────────────────────────
def load_locomo(path: str) -> list:
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for sample in data:
        conv = sample["conversation"]
        sessions = []
        n = 1
        while f"session_{n}" in conv:
            sessions.append({
                "n": n,
                "date": conv.get(f"session_{n}_date_time", ""),
                "turns": conv[f"session_{n}"],
            })
            n += 1
        out.append({
            "id": sample.get("sample_id", f"conv-{len(out)}"),
            "speakers": [conv.get("speaker_a", ""), conv.get("speaker_b", "")],
            "sessions": sessions,
            "qa": sample.get("qa", []),
        })
    return out


def synthetic() -> list:
    s1 = [{"speaker": "Alice", "dia_id": "D1:1", "text": "I adopted a dog named Momo in June 2023."},
          {"speaker": "Bob", "dia_id": "D1:2", "text": "Nice! I moved to Berlin last year."}]
    s2 = [{"speaker": "Alice", "dia_id": "D2:1", "text": "Momo had surgery on 12 August 2023 and is recovering."},
          {"speaker": "Bob", "dia_id": "D2:2", "text": "I started a new job at Acme in July."}]
    qa = [
        {"question": "What is the dog's name?", "answer": "Momo", "evidence": ["D1:1"], "category": 1},
        {"question": "When did Bob move to Berlin?", "answer": "last year", "evidence": ["D1:2"], "category": 2},
        {"question": "When did Momo have surgery?", "answer": "12 August 2023", "evidence": ["D2:1"], "category": 2},
    ]
    return [{"id": "synthetic-1", "speakers": ["Alice", "Bob"],
             "sessions": [{"n": 1, "date": "June 2023", "turns": s1},
                          {"n": 2, "date": "August 2023", "turns": s2}],
             "qa": qa}]


def session_text(sess: dict) -> str:
    lines = [f"[Session {sess['n']} — {sess['date']}]"]
    for t in sess["turns"]:
        lines.append(f"{t.get('dia_id','')} {t.get('speaker','')}: {t.get('text','')}")
    return "\n".join(lines)


def full_text(conv: dict) -> str:
    return "\n\n".join(session_text(s) for s in conv["sessions"])


DATE_RE = re.compile(r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b|\b(?:19|20)\d{2}\b")
NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")


def extract_facts(text: str, limit: int = 40) -> list:
    """Verbatim facts worth preserving in a dialogue: dates, names, numbers."""
    facts = []
    for m in DATE_RE.findall(text):
        if m not in facts:
            facts.append(m)
    names = {}
    for m in NAME_RE.findall(text):
        if m.lower() in ("the", "and", "but", "she", "he", "they", "session", "may", "june", "july"):
            continue
        names[m] = names.get(m, 0) + 1
    for n, _ in sorted(names.items(), key=lambda kv: -kv[1])[:18]:
        if n not in facts:
            facts.append(n)
    nums = re.findall(r"\b\d{1,4}\b", text)
    for n in nums:
        if n not in facts and len(facts) < limit:
            facts.append(n)
    return facts[:limit]


def salient_lines(text: str, limit: int = 22, max_chars: int = 150) -> list:
    """Verbatim lines likely to carry answers: dates, numbers, proper nouns."""
    scored = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 24 or line.startswith("[Session"):
            continue
        score = 0
        score += 3 * len(DATE_RE.findall(line))
        score += len(re.findall(r"\b\d+\b", line))
        score += min(3, len(NAME_RE.findall(line)))
        if score:
            scored.append((score, line[:max_chars]))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, line in scored:
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def dia_index(conv: dict) -> dict:
    idx = {}
    for s in conv["sessions"]:
        for t in s["turns"]:
            idx[t.get("dia_id", "")] = f"{t.get('speaker','')}: {t.get('text','')}"
    return idx


# ── context views ────────────────────────────────────────────────────────
def view_full(conv):
    return full_text(conv)


def view_truncate(conv):
    text = full_text(conv)
    keep = max(600, int(len(text) * 0.35))
    return text[-keep:]


def _last_session(conv, budget_chars=1200):
    if not conv["sessions"]:
        return ""
    text = session_text(conv["sessions"][-1])
    return text[-budget_chars:]


def view_llm(conv):
    t0 = time.monotonic()
    text = full_text(conv)
    summary, usage = chat([
        {"role": "system", "content": GENERIC_PROMPT},
        {"role": "user", "content": text[:120000]},
    ], max_tokens=1200)
    out = summary.strip() + "\n\n--- most recent session ---\n" + _last_session(conv)
    return out, {"seconds": round(time.monotonic() - t0, 2), "usage": usage}


def view_v11(conv, ablate=None):
    t0 = time.monotonic()
    msgs = [{"role": "user", "content": session_text(s)} for s in conv["sessions"]]
    digest = build_fold_digest(msgs, commitments=[])
    fold = render_fold_block(digest, f"fold:locomo-{conv['id']}")
    dates = [s["date"] for s in conv["sessions"] if s["date"]]
    date_map = "; ".join(f"S{s['n']}: {s['date']}" for s in conv["sessions"] if s["date"])
    snapshot = "\n".join([
        "<state_snapshot>",
        f"[SESSIONS] {len(conv['sessions'])} sessions: {date_map}",
        f"[SPEAKERS] {', '.join([x for x in conv['speakers'] if x])}",
        "[NOTE] Resolve relative dates with the session headers; recent session kept verbatim.",
        "</state_snapshot>",
    ])
    ablate = ablate or set()
    facts = extract_facts(full_text(conv))
    prompt = GENERIC_PROMPT if "sections" in ablate else NF_PROMPT
    summary, usage = chat([
        {"role": "system", "content": prompt},
        {"role": "user", "content": full_text(conv)[:120000]},
    ], max_tokens=1500)
    evidence = ("[EVIDENCE] " + "; ".join(facts)) if (facts and "facts" not in ablate) else ""
    salient = salient_lines(full_text(conv)) if "salient" not in ablate else []
    salient_block = "[SALIENT LINES]\n" + "\n".join(f"- {l}" for l in salient) if salient else ""
    tail = ("" if "last_session" in ablate else
            "\n\n--- most recent session ---\n" + _last_session(conv))
    out = (snapshot + "\n\n" + fold + ("\n" + evidence if evidence else "")
           + ("\n\n" + salient_block if salient_block else "")
           + "\n\n" + summary.strip() + tail)
    return out, {"seconds": round(time.monotonic() - t0, 2), "usage": usage}


# ── readers ───────────────────────────────────────────────────────────────
def ask_extractive(view: str, question: str) -> str:
    q = set(tokens(question))
    best, best_score = "", 0
    for line in re.split(r"\n+", view):
        line = line.strip()
        if len(line) < 8:
            continue
        score = len(set(tokens(line)) & q)
        if score > best_score:
            best, best_score = line, score
    return best[:300]


def ask_llm(view: str, question: str, model: str = "tierflow") -> str:
    ans, _ = chat([
        {"role": "system", "content": (
            "Answer with the shortest exact phrase from the context. "
            "Resolve relative time expressions (yesterday, last week, next month) using the "
            "[Session N - date] header of the session where they appear. If unknown, say unknown."
        )},
        {"role": "user", "content": f"CONTEXT:\n{view[:60000]}\n\nQUESTION: {question}"},
    ], max_tokens=300, model=model)
    return ans.strip()


def evidence_recall(view: str, evidence: list, idx: dict) -> float:
    if not evidence:
        return 1.0
    hits = 0
    v = norm(view)
    for dia in evidence:
        text = idx.get(dia, "")
        content = re.sub(r"^[^:]+:\s*", "", text)
        toks = tokens(content)
        if not toks:
            continue
        # distinctive 3-grams: at least one must survive
        grams = [" ".join(toks[i:i + 3]) for i in range(max(1, len(toks) - 2))]
        hits += 1 if any(g in v for g in grams) else (1 if sum(1 for t in toks if t in v) / len(toks) >= 0.7 else 0)
    return hits / len(evidence)


# ── main ─────────────────────────────────────────────────────────────────
def pick_questions(qa: list, n: int) -> list:
    by_cat = {}
    for q in qa:
        by_cat.setdefault(q.get("category", 0), []).append(q)
    out, cats = [], sorted(by_cat)
    while len(out) < n and any(by_cat[c] for c in cats):
        for c in cats:
            if by_cat[c] and len(out) < n:
                out.append(by_cat[c].pop(0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="LoCoMo JSON path; omit for synthetic")
    ap.add_argument("--convs", type=int, default=3)
    ap.add_argument("--questions", type=int, default=6)
    ap.add_argument("--reader", choices=["extractive", "llm"], default="extractive")
    ap.add_argument("--reader-model", default="tierflow")
    ap.add_argument("--ablate", default="", help="comma list: facts,salient,sections,last_session")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--strategies", default="full,truncate,llm,v11")
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    args = ap.parse_args()

    ablate = {x.strip() for x in args.ablate.split(",") if x.strip()}
    convs = load_locomo(args.data)[: args.convs] if args.data else synthetic()
    strategies = args.strategies.split(",")
    rows = {s: {"views": 0, "compression": 0.0, "evidence": 0.0, "qa": 0.0,
                "summary_s": 0.0, "cost": 0} for s in strategies}
    details = []
    qa_details = []

    for conv in convs:
        idx = dia_index(conv)
        qs = pick_questions(conv["qa"], args.questions)
        full = view_full(conv)
        for s in strategies:
            if s == "full":
                view, meta = full, {"seconds": 0, "usage": {}}
            elif s == "truncate":
                view, meta = view_truncate(conv), {"seconds": 0, "usage": {}}
            elif s in ("llm", "v11"):
                try:
                    view, meta = view_llm(conv) if s == "llm" else view_v11(conv, ablate)
                except Exception as e:  # noqa: BLE001
                    print(f"[{conv['id']}] {s} summary failed ({e}); skipping", flush=True)
                    meta = None
            else:
                continue
            if meta is None:
                continue
            comp = 1.0 - est(view) / max(1, est(full))
            rec = sum(evidence_recall(view, q.get("evidence", []), idx) for q in qs) / max(1, len(qs))
            correct = 0
            for q in qs:
                try:
                    ans = ask_extractive(view, q["question"]) if args.reader == "extractive" else ask_llm(view, q["question"], model=args.reader_model)
                except Exception:
                    ans = ""
                qa_details.append({
                    "conv": conv["id"], "strategy": s, "question": q["question"],
                    "expected": q.get("answer"), "answer": ans,
                    "evidence": q.get("evidence", []),
                })
                if q.get("answer") is None:
                    # LoCoMo category-5 adversarial items: the gold behavior is refusal.
                    correct += 1.0 if is_refusal(ans) else 0.0
                else:
                    correct += score_answer(ans, str(q["answer"]))
            qa = correct / max(1, len(qs))
            cost = int(meta.get("usage", {}).get("prompt_tokens", 0)) + int(meta.get("usage", {}).get("completion_tokens", 0))
            rows[s]["views"] += 1
            rows[s]["compression"] += comp
            rows[s]["evidence"] += rec
            rows[s]["qa"] += qa
            rows[s]["summary_s"] += meta["seconds"]
            rows[s]["cost"] += cost
            details.append({"conv": conv["id"], "strategy": s, "compression": round(comp, 3),
                            "evidence": round(rec, 3), "qa": round(qa, 3), "cost": cost,
                            "seconds": meta["seconds"]})
            print(f"[{conv['id']}] {s:8s} comp={comp:.3f} ev={rec:.3f} qa={qa:.2f}", flush=True)
            if args.outdir:
                try:
                    _d = Path(args.outdir); _d.mkdir(parents=True, exist_ok=True)
                    _partial = {"tag": args.tag, "partial": True, "summary": rows,
                                "details": details, "qa_details": qa_details[-200:]}
                    (_d / f"{args.tag}.partial.json").write_text(
                        json.dumps(_partial, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

    for s in rows:
        n = max(1, rows[s]["views"])
        for k in ("compression", "evidence", "qa", "summary_s"):
            rows[s][k] = round(rows[s][k] / n, 3)
    md = ["# Memory benchmark evaluation", "",
          f"Conversations: {len(convs)}, questions/conv: {args.questions}, reader: {args.reader}", "",
          "| strategy | compression | evidence recall | QA acc | summary s/conv | summary tokens |",
          "|---|---|---|---|---|---|"]
    for s, r in rows.items():
        md.append(f"| {s} | {r['compression']:.3f} | {r['evidence']:.3f} | {r['qa']:.3f} | {r['summary_s']:.1f} | {r['cost']} |")
    md += ["", "Evidence recall = share of gold dia_ids whose content survives; QA acc = exact/F1>=0.5 against gold answers."]
    print("\n".join(md))
    import datetime, subprocess
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        commit = ""
    out = {
        "tag": args.tag, "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "commit": commit, "dataset": args.data or "synthetic",
        "conversations": len(convs), "questions_per_conv": args.questions,
        "reader": args.reader, "reader_model": args.reader_model, "ablate": sorted(ablate),
        "summary": rows, "details": details, "qa_details": qa_details,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.outdir:
        d = Path(args.outdir); d.mkdir(parents=True, exist_ok=True)
        (d / f"{args.tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text("\n".join(md) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
