# Memory benchmark evaluation

Conversations: 2, questions/conv: 3, reader: llm

| strategy | compression | evidence recall | QA acc | summary s/conv | summary tokens |
|---|---|---|---|---|---|
| v11 | 0.956 | 0.375 | 0.000 | 14.3 | 31963 |
| full | 0.000 | 1.000 | 0.333 | 0.0 | 0 |

Evidence recall = share of gold dia_ids whose content survives; QA acc = exact/F1>=0.5 against gold answers.
