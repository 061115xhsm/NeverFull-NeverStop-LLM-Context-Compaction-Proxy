# Multi-instance deployment (current limits and path)

## Today

The proxy is designed as a **single process**:

- SessionStore, FoldArchive, StateTable and the policy/calibration stores are local SQLite/JSON files.
- Compaction cache, thrash detector, circuit breaker and policy state live in process memory.
- IP rate limiting is an in-process token bucket.
- `attic/storage_backend.py` sketches PostgreSQL/Redis but is **not wired**.

Consequences: a second instance would have its own caches and could re-compact history the first instance already folded, and `/fold/{ref}` refs created by one instance would 404 on another unless they share `COMPACTION_PROXY_FOLD_DB`.


## Implemented in V11 (2026-10)

| Piece | How |
|---|---|
| Tenant scoping | `X-Tenant-Id` header (or `?tenant=`) is normalized and scopes session ids (`acme:s1`), fold access (`/fold/{ref}` returns 404 across tenants), rate-limit buckets and cost events |
| Instance identity | `COMPACTION_PROXY_INSTANCE_ID` (default hostname); `X-Instance-Id` on every response; `/health.storage` reports it |
| Shared compaction cache | `COMPACTION_PROXY_REDIS_URL` → Redis `SETEX`; otherwise process-local; keys include tenant + history hash |
| Maintenance owner | `COMPACTION_PROXY_MAINTENANCE_OWNER=0` disables the periodic prune on secondary instances |
| Storage adapters | `storage_backend.py` (SQLite/Postgres/Redis) is at the repo root; `session_store_pg.py` adds a full PostgreSQL `SessionStore` (sessions, prior summaries, transcripts, ARC, search) — set `COMPACTION_PROXY_STORAGE=postgres` + `COMPACTION_PROXY_PG_DSN` to use it |
| Per-tenant quotas | `COMPACTION_PROXY_TENANT_MAX_FOLDS` / `_MAX_SESSIONS` / `_DAILY_TOKENS` + `COMPACTION_PROXY_ENFORCE_QUOTA=1` (exceeded → compaction skipped, logged) |
| Key rotation | `POST /maintenance/rotate-key` re-encrypts the whole fold archive under a fresh key |
| Session stickiness | Response echoes `X-Session-Id`; configure your load balancer to hash on it |

Still required for true multi-host operation: wire `SessionStore` (including FTS transcripts) to PostgreSQL, share the encryption key, and move compaction cache/metrics to the shared services above.

## Minimum viable multi-instance

1. **Sticky routing by `X-Session-Id`.** One session must always hit the same instance; this is the cheapest correct option and requires no storage changes. Put it in your load balancer.
2. **Shared retention owner.** Run `POST /maintenance/prune` from one scheduler only; concurrent VACUUMs on the same SQLite file are wasteful.
3. **Shared fold/state volume.** If instances run on the same host, mount the same directory for `COMPACTION_PROXY_FOLD_DB` / `STATE_DB` / `SESSION_DB`. Do not put SQLite on NFS.
4. **Archive key distribution.** With `COMPACTION_PROXY_FOLD_ENCRYPT=1`, every instance needs the same `fold.key` (0600). Rotate with `FoldArchive.rotate_key()` during a maintenance window, then redistribute.

## Full shared-storage path

- Replace SQLite with PostgreSQL for sessions/folds/state; keep `ref` unique and add `tenant_id`.
- Move compaction cache and policy state to Redis (`session_id` + history hash as key).
- Make retention a single background worker with advisory locks.
- Emit metrics to a shared sink (Prometheus) and aggregate cost by `session_id`/`model`.
- Revisit `attic/storage_backend.py`: it has the right shape but no tests; wire behind `COMPACTION_PROXY_STORAGE=sqlite|postgres`.

## Risks to validate before scaling out

- Duplicate compaction: two instances compacting the same session produce different summaries; sticky routing avoids this.
- Fold ref leakage: refs are content-addressed and non-secret, but retrieval is still gated by `require_auth`.
- Policy learning: per-instance bandits diverge; share the policy file or accept local learning.
- Cost accounting: `/health` is per-instance; sum across instances for a true bill.
