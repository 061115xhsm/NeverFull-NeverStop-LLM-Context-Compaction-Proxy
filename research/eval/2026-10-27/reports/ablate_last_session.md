# Memory benchmark evaluation

Conversations: 2, questions/conv: 3, reader: llm

| strategy | compression | evidence recall | QA acc | summary s/conv | summary tokens |
|---|---|---|---|---|---|
| v11 | 0.935 | 0.750 | 0.167 | 15.1 | 31963 |
| full | 0.000 | 1.000 | 0.500 | 0.0 | 0 |

Evidence recall = share of gold dia_ids whose content survives; QA acc = exact/F1>=0.5 against gold answers.
