"""Reversible context folding for the NeverFull compaction proxy (V11 P1).

Research basis
--------------
- ReFold (arXiv:2610.07863): training-free, *reversible* inter-turn folding beats
  predictive "when to compact" policies that add runtime overhead and cannot undo
  a bad fold.
- Persistent Context Graphs (arXiv:2609.40118): compaction must stay addressable.
  A later request changes which history matters, so never destroy the source.
- Prune the Summary, Not the History (arXiv:2610.20927): summaries mix facts
  established by the environment with inference written by the compressor, and the
  agent cannot tell them apart. Keep the two labelled.
- Keep the Commitments (arXiv:2605.17304): compression is correct iff the
  commitments (goals, constraints, decisions, artifacts, identifiers) survive.
- Compaction Laundering (arXiv:2610.29869): flattening typed messages into a plain
  string destroys provenance and is an exploitable trust boundary. Always keep
  typed markers and refs.
- KEEP-Bench (arXiv:2610.26827): QA accuracy hides unfaithful compression; verify
  evidence preservation explicitly.

Design
------
1. Folded turns are archived verbatim (SQLite) and addressed by an opaque ref.
2. The model sees a compact, typed placeholder: digest + ref (+ optional summary).
3. Every compaction is checked against a commitment contract. Misses are surfaced
   to the agent in an explicit block instead of being silently lost.

Pure standard library; no dependency on the proxy module (safe to unit test).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB = os.environ.get(
    "COMPACTION_PROXY_FOLD_DB",
    os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "folds.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS folds (
    ref         TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    kind        TEXT NOT NULL,
    n_messages  INTEGER NOT NULL,
    approx_tokens INTEGER NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_folds_session ON folds(session_id, created_at);
"""

FILE_RE = re.compile(
    r"(?<![\w/])(?:/[\w.\-]+){2,}\.\w{1,10}"
    r"|(?<![\w/])[\w.\-]+\.(?:py|js|ts|tsx|jsx|json|ya?ml|toml|md|sh|go|rs|java|cpp|cc|c|h|html|css|sql|txt|log)\b"
)
CMD_KEYS = ("command", "cmd", "shell_command")
TOOL_USE_TYPES = {"tool_use", "tool_call", "function_call"}
ERROR_RE = re.compile(r"(?i)\b(?:traceback|exception|error\b|failed\b|exit code [1-9]\d*)")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _shingles(text: str, n: int = 2) -> set:
    s = re.sub(r"\s+", "", (text or "").lower())
    if len(s) <= n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _approx_tokens(messages: list) -> int:
    raw = json.dumps(messages, ensure_ascii=False)
    # CJK characters are roughly 1.5 chars/token; ASCII ~4 chars/token.
    cjk = len(re.findall(r"[\u4e00-\u9fff]", raw))
    other = max(0, len(raw) - cjk)
    return int(cjk / 1.5 + other / 4.0)


class FoldArchive:
    """Verbatim, addressable store for folded history (SQLite, WAL)."""

    def __init__(self, path: str | os.PathLike | None = None, encrypt: bool = False,
                 key_path: str | os.PathLike | None = None) -> None:
        self.path = Path(path or DEFAULT_DB).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = None
        if encrypt:
            self._init_encryption(key_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _init_encryption(self, key_path) -> None:
        try:
            from cryptography.fernet import Fernet
        except Exception:
            return  # optional dependency; stays plaintext with a warning at call site
        kp = Path(key_path or (self.path.parent / "fold.key")).expanduser()
        if kp.exists():
            key = kp.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            kp.parent.mkdir(parents=True, exist_ok=True)
            kp.write_bytes(key)
            try:
                os.chmod(kp, 0o600)
            except OSError:
                pass
        self._fernet = Fernet(key)

    @property
    def encrypted(self) -> bool:
        return self._fernet is not None

    def put(self, session_id: str, kind: str, messages: list, ref: Optional[str] = None) -> str:
        payload = json.dumps(messages, ensure_ascii=False)
        if self._fernet is not None:
            payload = "enc:v1:" + self._fernet.encrypt(payload.encode("utf-8")).decode("ascii")
        if ref is None:
            raw = json.dumps(messages, ensure_ascii=False)
            ref = "fold:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO folds(ref, session_id, created_at, kind, n_messages, approx_tokens, payload) "
                "VALUES (?,?,?,?,?,?,?)",
                (ref, session_id, time.time(), kind, len(messages), _approx_tokens(messages), payload),
            )
            self._conn.commit()
        return ref

    def get(self, ref: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT ref, session_id, created_at, kind, n_messages, approx_tokens, payload FROM folds WHERE ref=?",
                (ref,),
            ).fetchone()
        if row is None:
            return None
        return {
            "ref": row[0],
            "session_id": row[1],
            "created_at": row[2],
            "kind": row[3],
            "n_messages": row[4],
            "approx_tokens": row[5],
            "messages": self._decode(row[6]),
        }

    def _decode(self, payload: str):
        if isinstance(payload, str) and payload.startswith("enc:v1:"):
            if self._fernet is None:
                return []
            try:
                return json.loads(self._fernet.decrypt(payload[7:].encode("ascii")).decode("utf-8"))
            except Exception:
                return []
        return json.loads(payload)

    def stats(self) -> dict:
        try:
            with self._lock:
                n = self._conn.execute("SELECT COUNT(*) FROM folds").fetchone()[0]
                tokens = self._conn.execute("SELECT COALESCE(SUM(approx_tokens),0) FROM folds").fetchone()[0]
        except sqlite3.Error:
            return {"entries": 0, "tokens_archived": 0, "path": str(self.path)}
        return {"entries": n, "tokens_archived": tokens, "path": str(self.path)}

    def rotate_key(self, new_key_path: str | os.PathLike | None = None) -> int:
        """Re-encrypt every folded payload under a fresh Fernet key.

        Returns the number of rows rotated. No-op on plaintext archives.
        """
        if self._fernet is None:
            return 0
        from cryptography.fernet import Fernet

        new_path = Path(new_key_path or (self.path.parent / "fold.key.new")).expanduser()
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_key = Fernet.generate_key()
        new_path.write_bytes(new_key)
        try:
            os.chmod(new_path, 0o600)
        except OSError:
            pass
        new_fernet = Fernet(new_key)

        rotated = 0
        with self._lock:
            rows = self._conn.execute("SELECT ref, payload FROM folds").fetchall()
            for ref, payload in rows:
                if not (isinstance(payload, str) and payload.startswith("enc:v1:")):
                    continue
                try:
                    plain = self._fernet.decrypt(payload[7:].encode("ascii"))
                except Exception:
                    continue
                token = "enc:v1:" + new_fernet.encrypt(plain).decode("ascii")
                self._conn.execute("UPDATE folds SET payload=? WHERE ref=?", (token, ref))
                rotated += 1
            self._conn.commit()

        key_file = self.path.parent / "fold.key"
        if key_file.exists():
            try:
                key_file.replace(key_file.with_suffix(".key.bak"))
            except OSError:
                pass
        new_path.replace(key_file)
        self._fernet = new_fernet
        return rotated

    def prune(self, max_entries: int = 0, max_age_days: float = 30.0) -> int:
        """Retention: drop old folds, then trim to max_entries (newest kept)."""
        removed = 0
        with self._lock:
            if max_age_days and max_age_days > 0:
                cutoff = time.time() - max_age_days * 86400
                cur = self._conn.execute("DELETE FROM folds WHERE created_at < ?", (cutoff,))
                removed += cur.rowcount or 0
            if max_entries and max_entries > 0:
                cur = self._conn.execute(
                    "DELETE FROM folds WHERE ref IN ("
                    " SELECT ref FROM folds ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                    (max_entries,),
                )
                removed += cur.rowcount or 0
            self._conn.commit()
            if removed:
                try:
                    self._conn.execute("VACUUM")
                except sqlite3.Error:
                    pass
        return removed

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except sqlite3.Error:
            pass


def build_fold_digest(messages: list, commitments: Optional[list] = None) -> dict:
    """Deterministic structural digest of a block of messages (no LLM call)."""
    roles: Counter = Counter()
    tools: Counter = Counter()
    files: Counter = Counter()
    commands: list = []
    errors = 0
    first_ts = None
    last_ts = None

    for msg in messages or []:
        roles[str(msg.get("role", "?"))] += 1
        ts = msg.get("timestamp") or msg.get("created_at")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts

        content = msg.get("content")
        texts = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    texts.append(block.get("text", ""))
                elif btype in TOOL_USE_TYPES:
                    name = block.get("name") or block.get("function", {}).get("name") if isinstance(block.get("function"), dict) else block.get("name")
                    if name:
                        tools[str(name)] += 1
                    payload = block.get("input") or block.get("arguments") or {}
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except Exception:
                            payload = {}
                    if isinstance(payload, dict):
                        for k in CMD_KEYS:
                            v = payload.get(k)
                            if isinstance(v, str) and v.strip():
                                commands.append(v.strip()[:160])
                                break
                        for k in ("path", "file_path", "target_file", "filename"):
                            v = payload.get(k)
                            if isinstance(v, str) and v.strip():
                                files[v.strip()] += 1
                                break
                elif btype == "tool_result":
                    c = block.get("content")
                    if isinstance(c, str):
                        texts.append(c)
                    elif isinstance(c, list):
                        texts.extend(b.get("text", "") for b in c if isinstance(b, dict))
        # OpenAI-style tool calls
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            if name:
                tools[str(name)] += 1
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if isinstance(args, dict):
                for k in CMD_KEYS:
                    v = args.get(k)
                    if isinstance(v, str) and v.strip():
                        commands.append(v.strip()[:160])
                        break
                for k in ("path", "file_path", "target_file", "filename"):
                    v = args.get(k)
                    if isinstance(v, str) and v.strip():
                        files[v.strip()] += 1
                        break

        blob = "\n".join(t for t in texts if t)
        errors += len(ERROR_RE.findall(blob))
        for m in FILE_RE.findall(blob):
            files[m] += 1

    commitments = commitments or []
    ctypes = Counter(c[0] for c in commitments if isinstance(c, (list, tuple)) and len(c) >= 2)

    return {
        "n_messages": len(messages or []),
        "roles": dict(roles),
        "tools": dict(tools.most_common(10)),
        "files": list(files.keys())[:8],
        "files_total": len(files),
        "commands": list(dict.fromkeys(commands))[:6],
        "errors": errors,
        "commitments": dict(ctypes),
        "commitments_total": len(commitments),
        "approx_tokens": _approx_tokens(messages or []),
        "span": (first_ts, last_ts),
    }


def render_fold_block(digest: dict, ref: str, note: str = "") -> str:
    """Render the typed placeholder the model sees instead of the folded messages."""
    roles = digest.get("roles", {})
    role_txt = " / ".join(f"{k} {v}" for k, v in roles.items()) or "n/a"
    lines = [
        f'<folded_context ref="{ref}" messages="{digest.get("n_messages", 0)}" '
        f'approx_tokens="{digest.get("approx_tokens", 0)}">',
        f"[FOLD] {digest.get('n_messages', 0)} messages elided ({role_txt}); "
        f"~{digest.get('approx_tokens', 0)} tokens archived verbatim.",
    ]
    if digest.get("tools"):
        lines.append("[TOOLS] " + ", ".join(f"{k}x{v}" for k, v in digest["tools"].items()))
    if digest.get("files"):
        more = ""
        if digest.get("files_total", len(digest["files"])) > len(digest["files"]):
            more = f" (+{digest['files_total'] - len(digest['files'])} more)"
        lines.append("[FILES] " + ", ".join(digest["files"]) + more)
    if digest.get("commands"):
        lines.append("[COMMANDS] " + "; ".join(digest["commands"]))
    if digest.get("errors"):
        lines.append(f"[OBSERVED] {digest['errors']} error/failure mentions")
    if digest.get("commitments"):
        lines.append(
            "[COMMITMENTS] "
            + ", ".join(f"{k}={v}" for k, v in digest["commitments"].items())
        )
    lines.append(f"[RETRIEVE] GET /fold/{ref} returns the original messages verbatim.")
    if note:
        lines.append(note)
    lines.append("</folded_context>")
    return "\n".join(lines)


def verify_commitments(rendered: str, commitments: Optional[list], identifiers: Optional[list]) -> dict:
    """Check that commitments and exact identifiers survived into the rendered context."""
    commitments = commitments or []
    identifiers = identifiers or []
    missing = []
    kept = 0
    for item in commitments:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            ctype, text = item[0], str(item[1])
        else:
            ctype, text = "unknown", str(item)
        if _kept(text, rendered):
            kept += 1
        else:
            missing.append({"type": ctype, "text": text})

    id_missing = [i for i in identifiers if _norm(i) and _norm(i) not in _norm(rendered)]
    id_kept = len(identifiers) - len(id_missing)
    total = len(commitments)
    return {
        "total": total,
        "kept": kept,
        "missing": missing[:12],
        "score": (kept / total) if total else 1.0,
        "identifiers_total": len(identifiers),
        "identifiers_kept": id_kept,
        "identifiers_missing": id_missing[:12],
    }


def _kept(text: str, hay: str) -> bool:
    t, h = _norm(text), _norm(hay)
    if not t:
        return True
    if t in h:
        return True
    a = _shingles(t)
    if not a:
        return True
    b = _shingles(h)
    return len(a & b) / len(a) >= 0.55


def format_missing_note(report: dict) -> str:
    """Transparent disclosure of commitments the compressor could not verify."""
    rows = []
    for item in report.get("missing", [])[:8]:
        rows.append(f"- ({item.get('type', '?')}) {item.get('text', '')[:160]}")
    for ident in report.get("identifiers_missing", [])[:8]:
        rows.append(f"- (identifier) {ident[:160]}")
    if not rows:
        return ""
    return (
        "<unpreserved_commitments>\n"
        "The compressor could not verify that these survived compaction. "
        "Treat them as possibly lost; re-read files or call tools before relying on them.\n"
        + "\n".join(rows)
        + "\n</unpreserved_commitments>"
    )


SUMMARY_SECTION_PROMPT = """## REQUIRED OUTPUT SECTIONS
Write the summary using exactly these section headers, in this order:
### FACTS
Only facts established by the environment, tools, files, or the user. No guesses.
### DECISIONS
Decisions made, each with status (done / pending / reverted) and the reason.
### OPEN
Unresolved questions, blockers, and the next concrete step.
### COMPRESSOR_INFERENCE
Your own inferences or guesses. Label them as such; they may be discarded later.
### COMMITMENTS
Goals, constraints, user preferences, and exact identifiers/paths that must survive.
"""

SECTION_RE = re.compile(r"^###\s+(FACTS|DECISIONS|OPEN|COMPRESSOR_INFERENCE|COMMITMENTS)\s*$", re.M)


def parse_sections(text: str) -> dict:
    """Split a sectioned summary into a mapping; unlabelled text goes to _preamble."""
    if not text:
        return {}
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return {"_preamble": text}
    out = {"_preamble": text[: matches[0].start()].strip()}
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        out[m.group(1)] = text[m.end() : end].strip()
    return out
