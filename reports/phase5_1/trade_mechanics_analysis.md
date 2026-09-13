# Veyra Phase 5.1 — Trade Mechanics Analysis

Execution model unchanged. Concerns entry timing, stop/target placement, expiration, and cost sensitivity.

## Exit reason distribution (pooled, n total)

| exit reason | count | share |
| ----------- | ----- | ----- |
| STOP        | 2195  | 32.0% |
| TARGET      | 1233  | 18.0% |
| EXPIRED     | 3439  | 50.1% |
| INVALIDATED | 0     | 0.0%  |
| END_OF_DATA | 0     | 0.0%  |

## Adverse vs favorable excursion

- Avg |MAE| (worst adverse move): 482.7914
- Avg |MFE| (best favorable move): 626.7815
- MAE/MFE ratio: 0.7703

A ratio near/exceeding 1 indicates adverse travel roughly matches favorable travel — poor timing.

## Cost sensitivity (existing robustness assumptions)

- Gross PnL (sum gross returns): 31.9032
- Net PnL (sum net returns): 18.1692
- Cost drag (fees+slippage): 13.7340 across 6867 trades → 13.733999999999984 net basis points
- Avg cost/trade: 0.0020