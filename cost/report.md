# 100-review cost & runtime report (offline replay)

Rates file: `cost/rates.csv` (editable). Rate multiplier: x1.0. Usage file: `cost/usage.csv` (raw provider usage).

## Measured: real 100-review pilot (API spend only)

| stage | model | requests | failed | reviews sent | input tok | output tok | cost USD |
|---|---|---|---|---|---|---|---|
| enrich | claude-haiku-5-5 | 2 | 0 | 100 | 6576 | 1549 | 0.001432 |
| verify | claude-haiku-5-5 | 1 | 0 | 20 | 1416 | 210 | 0.000247 |
| group | claude-haiku-5-5 | 16 | 0 | 51 | 6989 | 2417 | 0.001907 |
| memo | claude-sonnet-5-5 | 2 | 0 | 0 | 9601 | 2755 | 0.046752 |

**Cold run total API cost: $0.050338**  ·  cost per 1,000 input rows: $0.5034  ·  per completed record: $0.000503

**Warm run (saved result cache): 0 enrichment calls; incremental API cost $0.000000** (downstream stages cached by input fingerprint).

Wall-clock (not summed call time): cold 54.4s, warm 0.1s. Stage seconds cold: {'enrich': 7.269, 'group': 23.501, 'memo': 21.735, 'prepare': 0.006, 'profile': 0.001, 'rank': 0.012, 'records': 0.011, 'verify': 1.827}; warm: {'enrich': 0.0, 'prepare': 0.003, 'profile': 0.001, 'rank': 0.001, 'records': 0.001}.
Enrichment throughput (1 worker): 13.76 texts/s.

Local compute / electricity / hardware: **unknown, not measured** (orchestration is CPU-light; not included in API spend).

## Projection: full corpus

Source rows 660,622 = 660,609 nonempty to classify + 13 empty quarantines. Distinct nonempty texts with exact-text reuse: 484,189.
Controls: budget $20.0, output cap 40 tok/review, max workers 4, fallback fraction 0.0 (claude-sonnet-5-5).

| scenario | texts classified | enrich | verify | fallback | group | memo | **total USD** | hours | workers | vs budget |
|---|---|---|---|---|---|---|---|---|---|---|
| base, exact-text reuse | 484,189 | 7.07 | 0.01 | 0.00 | 0.00 | 0.05 | **7.14** | 3.2 | 4 | within |
| base, NO reuse | 660,609 | 9.65 | 0.01 | 0.00 | 0.00 | 0.05 | **9.71** | 4.4 | 4 | within |
| conservative (more retries/fallback, slower), reuse | 484,189 | 7.63 | 0.01 | 4.27 | 0.00 | 0.05 | **11.96** | 7.7 | 2 | within |
| base, reuse, target_rows=100,000 (this project's run) | 78,100 | 1.14 | 0.01 | 0.00 | 0.00 | 0.05 | **1.20** | 0.5 | 4 | within |

## Measurement refresh at 100 -> 500 -> 10,000 -> actual runs incl. Message Batches (enrichment, from saved call logs)

| checkpoint | reviews sent | input tok/review | output tok/review | failed API attempts | enrich cost USD at current rates | USD per 1,000 reviews |
|---|---|---|---|---|---|---|
| pilot_100_cold | 100 | 65.76 | 15.49 | 0 | 0.0014 | 0.0143 |
| checkpoint_500 | 481 | 69.33 | 15.51 | 0 | 0.0071 | 0.0147 |
| analysis_10000 | 8,465 | 68.07 | 15.49 | 0 | 0.1232 | 0.0146 |
| official_run_batch_api_103k_texts | 103,681 | 68.7 | 15.49 | 0 | 0.7578 | 0.0073 |
| final_run_sync_calls_(incl. first interrupted/resumed 100k-row portion) | 78,864 | 70.38 | 15.44 | 0 | 1.1637 | 0.0148 |

Per-review usage is stable from 100 to 78,631 reviews, so the 100-review estimate extrapolates well for this prompt; rare failure modes (provider refusals on ~0.1% of texts) only appeared at scale and are handled by bounded bisection.

Assumptions are in `cost/scenario.json` (retry rate, fallback fraction, parallel efficiency, verify sample size). The first 100 reviews are an initial estimate; see the measurement-refresh table above (`cost/measurements/summary.json`).
