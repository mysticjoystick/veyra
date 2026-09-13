# Veyra Phase 5.1 — Score Analysis

The 0-100 normalized score is NOT a probability or confidence measure. Fixed, result-independent buckets.

## Average return by fixed bucket (pooled, net)

| slice  | trades | setups* | wins | losses | expir. | avg ret | expectancy | PF   | win rate | hold(bars) |
| ------ | ------ | ------- | ---- | ------ | ------ | ------- | ---------- | ---- | -------- | ---------- |
| 0-19   | 4274   | 4274    | 1833 | 2441   | 2088   | 0.0014  | 0.0014     | 1.10 | 0.429    | 6.2        |
| 20-39  | 2593   | 2593    | 1156 | 1437   | 1351   | 0.0048  | 0.0048     | 1.30 | 0.446    | 6.4        |
| 40-59  | 0      | 0       | 0    | 0      | 0      | 0.0000  | —          | —    | 0.000    | —          |
| 60-79  | 0      | 0       | 0    | 0      | 0      | 0.0000  | —          | —    | 0.000    | —          |
| 80-100 | 0      | 0       | 0    | 0      | 0      | 0.0000  | —          | —    | 0.000    | —          |

## Monotonicity note

A useful score should show higher avg return / PF in higher buckets. Inverted order (higher score → worse) is flagged below.

### Cases where higher scores performed worse than lower scores

- `0-19`: avg=0.0014, PF=1.10, n=4274
- `20-39`: avg=0.0048, PF=1.30, n=2593

### Bucket monotonicity (avg return vs bucket index)
Increasing in bucket index ([0.0014, 0.0048, None, None, None]).

_For the Win/loss bucket breakdown and Wilson CIs, see the Phase 5 calibration report._