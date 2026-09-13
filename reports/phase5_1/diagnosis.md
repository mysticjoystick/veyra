# Veyra Phase 5.1 — Diagnosis

Evidence-based answers (each with sample size). The frozen baseline is not modified; no optimisation was performed.

**Pooled context:** - ALL (all four datasets): n=6867 avg ret=0.0026 PF=1.18 win=0.435 expirations=3439

## 1. Where is the baseline losing?

- Both 4H corners are net-negative OOS (BTC 4H −0.23%, ETH 4H −0.83%); both 1D are near-0 or positive (BTC 1D +1.49%). Supported by all four dataset corners.

## 2. Which setup types contribute most to the weakness?
- `TREND_CONTINUATION`: n=1541 avg ret=-0.0003 PF=0.98 win=0.474 expirations=802
- `PULLBACK`: n=1621 avg ret=0.0012 PF=1.09 win=0.499 expirations=912
- `BREAKOUT`: n=2082 avg ret=0.0052 PF=1.28 win=0.412 expirations=1228
- `BREAKOUT_RETEST`: n=1623 avg ret=0.0036 PF=1.33 win=0.364 expirations=497
- `RANGE_REJECTION`: no completed trades.

## 3. Does the score actually rank setup quality?
Increasing in bucket index ([0.0014, 0.0048, None, None, None]).
Per-bucket:
- `0-19`: n=4274 avg ret=0.0014 PF=1.10 win=0.429 expirations=2088
- `20-39`: n=2593 avg ret=0.0048 PF=1.30 win=0.446 expirations=1351
- `40-59`: no completed trades.
- `60-79`: no completed trades.
- `80-100`: no completed trades.
- The score is NOT treated as a probability or confidence percentage.

## 4. Which regimes help or hurt?
- `BEAR`: n=1635 avg ret=0.0024 PF=1.13 win=0.339 expirations=849
- `BULL`: n=5232 avg ret=0.0027 PF=1.21 win=0.465 expirations=2590

## 5. Is the problem concentrated in BTC/ETH or 4H/1D?
- BTC: n=3572 avg=0.0028 PF=1.22
- ETH: n=3295 avg=0.0025 PF=1.15
- 4H: n=6028 avg=0.0015 PF=1.12
- 1D: n=839 avg=0.0105 PF=1.49
- The 4H timeframe is the primary source of the pooled weakness; BTC vs ETH alone does not explain it.

## 6. Are execution mechanics contributing materially?
| exit reason | count | share |
| ----------- | ----- | ----- |
| STOP        | 2195  | 32.0% |
| TARGET      | 1233  | 18.0% |
| EXPIRED     | 3439  | 50.1% |
| INVALIDATED | 0     | 0.0%  |
| END_OF_DATA | 0     | 0.0%  |
- Avg MAE/MFE ratio: 0.7703 — see mechanics report.

## 7. Are costs responsible, or just worsening an already-weak strategy?
- Total cost drag (fees+slippage across 6867 trades): 13.7340.
- Compare net vs gross per corner in the mechanics report to judge whether costs flip signs.

## 8. Is there evidence of persistent behavior across time?
- See historical_stability.md. Look for consistency vs isolated favorable periods (e.g. a single positive slice dominating a dataset's net result).

## 9. Supported hypotheses
- See candidate_hypotheses.md; only those with direct evidence are listed as supported.

## 10. NOT-supported hypotheses
- See candidate_hypotheses.md.

## Verdict
BASELINE_STATUS = RESEARCH_REQUIRED

_Frozen baseline. Measurements only; runtime diagnostics._