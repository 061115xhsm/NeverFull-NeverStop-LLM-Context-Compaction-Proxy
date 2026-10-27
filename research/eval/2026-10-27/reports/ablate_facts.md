# Memory benchmark evaluation

Conversations: 2, questions/conv: 3, reader: llm

| strategy | compression | evidence recall | QA acc | summary s/conv | summary tokens |
|---|---|---|---|---|---|
| v11 | 0.917 | 0.917 | 0.167 | 13.7 | 31963 |
| full | 0.000 | 1.000 | 0.333 | 0.0 | 0 |

Evidence recall = share of gold dia_ids whose content survives; QA acc = exact/F1>=0.5 against gold answers.
