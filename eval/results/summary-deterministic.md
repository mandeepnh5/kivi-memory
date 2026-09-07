# Evaluation summary

mode: deterministic only
cases: 54 run, 54 passed, 0 failed, 0 degraded (judge unreachable), 9 skipped (need LLM)

## Interventions (reported separately, never blended)
- useful corrections: 12 / 12 expected
- missed corrections: 0
- HARMFUL interventions (wrong edit made): 0 across 45 do-nothing runs and 12 correction runs

## Efficiency
- pipeline runs: 57, LLM calls: 0 (0.0% of runs)
- LLM tokens: 0, cost: $0.0
- latency, deterministic path (57 runs): p50 0.27 ms, p95 0.49 ms
- latency, LLM path (0 runs): p50 0.0 ms, p95 0.0 ms

## Storage
- seed persona rows: {'events': 7, 'misspellings': 2, 'terms': 5, 'retired': 0, 'traces': 0}
- growth per 100 observations of ordinary use: {'events': 100, 'misspellings': 0, 'terms': 0, 'retired': 0, 'traces': 0}

## Failures
- none

skipped (rerun with --llm and an API key): corr-02, corr-07, hing-01, llm-01, llm-02, llm-03, guard-02, noop-10, hard-05
