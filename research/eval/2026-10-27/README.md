# Evaluation archive — 2026-10-27

All evaluation artifacts are stored here. Raw JSON keeps per-question answers and
metadata; reports are Markdown summaries.

## Environment

- Commit: `68007cb`
- Dataset: LoCoMo `benchmark/data/locomo10.json`
- Summarizer/reader: local `tierflow` (OpenAI-compatible, 127.0.0.1:8330)
- Reader prompt: shortest exact phrase; relative dates resolved via session headers

## Runs

| tag | config | status | files |
|---|---|---|---|
| `historical_4conv` | 4 convs x 4 q, full/truncate/llm/v11 | complete (2026-10-26) | `raw/historical_memory_eval_4conv.json`, `reports/historical_4conv.md` |
| `baseline_4x3` | 4 convs x 3 q, full/truncate/llm/v11 | partial (conv-41 summaries timed out) | `raw/baseline_4x3.partial.json`, `reports/baseline_4x3_partial.md` |
| `cache_eval` | head vs tail state frame, 5 turns each | complete | `raw/cache_eval.json`, `reports/cache_eval.md` |

## Results

### Evidence retention vs compression (historical 4-conv)

| strategy | compression | evidence | QA |
|---|---|---|---|
| full | 0.000 | 0.979 | 0.500 |
| truncate | 0.650 | 0.948 | 0.125 |
| generic LLM summary | 0.980 | 0.199 | 0.083 |
| V11 fold | 0.921 | 0.919 | 0.167 |

### Cache layout (real provider `cached_tokens`)

| layout | avg prompt | avg cached | hit ratio | avg latency |
|---|---|---|---|---|
| head | 6729.8 | 3993.6 | 0.594 | 1.924s |
| tail | 6726 | 6656 | 0.99 | 1.508s |
| control | 6706.6 | 5324.8 | 0.795 | 1.531s |

`tail` appends the changing state frame after the history and keeps the 30k-char
static prefix byte-stable (0.99 hit ratio); `head` prepends it to the system prompt
and drops to 0.594.

## Caveats

- The upstream endpoint was intermittently slow/timing out; the baseline stopped at
  conv-41 and llm/v11 have 2 views. Ablations were not completed in this window.
- QA accuracy is bounded by the local reader (full-context QA 0.500 on LoCoMo).
- Single run per configuration; no confidence intervals.

## Reproduce

```bash
python benchmark/memory_eval.py --data benchmark/data/locomo10.json \
  --convs 4 --questions 3 --reader llm --tag baseline_4x3 \
  --outdir research/eval/2026-10-27/raw \
  --markdown research/eval/2026-10-27/reports/baseline_4x3_partial.md

python benchmark/cache_eval.py \
  --outdir research/eval/2026-10-27/raw \
  --markdown research/eval/2026-10-27/reports/cache_eval.md
```
