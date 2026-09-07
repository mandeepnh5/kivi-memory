# Evaluation summary

mode: with LLM
cases: 63 run, 63 passed, 0 failed, 0 degraded (judge unreachable), 0 skipped (need LLM)

## Interventions (reported separately, never blended)
- useful corrections: 18 / 18 expected
- missed corrections: 0
- HARMFUL interventions (wrong edit made): 0 across 48 do-nothing runs and 18 correction runs

## Efficiency
- pipeline runs: 66, LLM calls: 23 (34.8% of runs)
- LLM tokens: 15115, cost: $0.0
- latency, deterministic path (43 runs): p50 0.52 ms, p95 0.98 ms
- latency, LLM path (23 runs): p50 1100.11 ms, p95 1921.45 ms

## Storage
- seed persona rows: {'events': 7, 'misspellings': 2, 'terms': 5, 'retired': 0, 'traces': 0}
- growth per 100 observations of ordinary use: {'events': 100, 'misspellings': 0, 'terms': 0, 'retired': 0, 'traces': 0}

## Failures
- none
