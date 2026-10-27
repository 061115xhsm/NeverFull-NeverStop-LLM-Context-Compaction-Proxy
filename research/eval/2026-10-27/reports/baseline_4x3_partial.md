# Baseline 4 conversations x 3 questions (partial: endpoint timeouts)

Run: baseline_4x3 | reader: llm | commit: 68007cb

| strategy | views | compression | evidence recall | QA | summary s/conv |
|---|---|---|---|---|---|
| full | 3 | 0.000 | 1.000 | 0.444 | 0.0 |
| truncate | 3 | 0.650 | 0.944 | 0.222 | 0.0 |
| llm | 2 | 0.978 | 0.375 | 0.000 | 12.1 |
| v11 | 2 | 0.913 | 0.917 | 0.167 | 15.6 |

Note: llm/v11 have 2 views (conv-41 summary timed out); full/truncate have 3.
