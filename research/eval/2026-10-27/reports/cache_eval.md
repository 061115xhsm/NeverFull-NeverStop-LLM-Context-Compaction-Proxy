# Cache layout measurement

Model: tierflow; prefix chars: 30000; turns: 5

| layout | avg prompt | avg cached | hit ratio | avg latency |
|---|---|---|---|---|
| head | 6729.8 | 3993.6 | 0.594 | 1.924s |
| tail | 6726 | 6656 | 0.99 | 1.508s |
| control | 6706.6 | 5324.8 | 0.795 | 1.531s |

`head` prepends a changing state frame to the system prompt; `tail` appends it as a trailing message.
