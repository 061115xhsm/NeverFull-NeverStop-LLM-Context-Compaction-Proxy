"""Evidence probes for compaction output (V11 P2).

Research basis
--------------
- KEEP-Bench (arXiv:2610.26827): QA accuracy hides unfaithful compression; the
  reader can answer correctly from an unfaithful summary. Probe evidence directly.
- Keep the Commitments (arXiv:2605.17304): correctness = commitments survive.
- Prune the Summary (arXiv:2610.20927): separate environment facts from the
  compressor's own inference.

The probes here are deterministic (no LLM): commitment coverage, exact identifiers,
required section presence, and coverage of the last actionable request.
"""

from __future__ import annotations

import re

from folding import verify_commitments

REQUIRED_SECTIONS = ("FACTS", "DECISIONS", "OPEN", "COMMITMENTS")
WEIGHTS = {"commitments": 0.45, "identifiers": 0.25, "sections": 0.15, "last_ask": 0.15}

TOKEN_RE = re.compile(r"[a-z0-9_\-]{4,}|[\u4e00-\u9fff]{2,}")


def _text_of(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _keywords(text: str, limit: int = 16) -> list:
    seen, out = set(), []
    for tok in TOKEN_RE.findall((text or "").lower()):
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def _last_ask(messages: list) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        text = _text_of(msg).strip()
        if len(text) >= 20 and not text.startswith("["):
            return text
    return ""


def run_evidence_probes(
    original_messages: list,
    rendered: str,
    commitments: list,
    identifiers: list,
) -> dict:
    contract = verify_commitments(rendered, commitments, identifiers)
    checks = {}

    checks["commitments"] = contract["score"] if contract["total"] else 1.0
    if contract["identifiers_total"]:
        checks["identifiers"] = (
            1.0 - len(contract["identifiers_missing"]) / contract["identifiers_total"]
        )
    else:
        checks["identifiers"] = 1.0

    present = sum(
        1 for name in REQUIRED_SECTIONS
        if re.search(rf"^###\s+{name}\b", rendered, re.M)
    )
    has_any_section = bool(re.search(r"^###\s+(FACTS|DECISIONS|OPEN|COMMITMENTS)\b", rendered, re.M))
    checks["sections"] = (present / len(REQUIRED_SECTIONS)) if has_any_section else 1.0

    ask = _last_ask(original_messages)
    if ask:
        kws = _keywords(ask)
        hit = sum(1 for k in kws if k in (rendered or "").lower())
        checks["last_ask"] = (hit / len(kws)) if kws else 1.0
    else:
        checks["last_ask"] = 1.0

    score = sum(checks[k] * WEIGHTS[k] for k in WEIGHTS)
    failures = [k for k, v in checks.items() if v < 0.80]

    note = ""
    if failures:
        note = (
            "<evidence_report>\n"
            f"evidence score {score:.2f}; checks below 0.80: {', '.join(failures)}\n"
            "Re-read files or call tools before relying on summarized details.\n"
            "</evidence_report>"
        )

    return {
        "score": round(score, 4),
        "checks": {k: round(v, 4) for k, v in checks.items()},
        "failures": failures,
        "note": note,
        "contract": contract,
    }
