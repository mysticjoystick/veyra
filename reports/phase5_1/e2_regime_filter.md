# Veyra Phase 5.1 — Experiment E2: Regime Entry Filter

Experiment version: `e2-regime-filter-v1` | Frozen baseline: `phase5-baseline-v1`
Generated: 2026-09-02 | All trade metrics are NET of fees + slippage. Scores are never treated as probabilities.

---

## 1. Hypothesis

Filtering entries by the regime of the current setup — blocking regimes whose
in-sample (IN) expectancy is negative — improves the robustness of the baseline
by removing the worst-regime trades.

**Scope of the claim.** E2 tests whether a *regime-based entry filter* improves
robustness (expectancy/PF held on a forward window). It does **NOT** test, and
does **NOT** imply, that regime changes are the causal reason setups expire.

## 2. Exact pre-registered change

One single change to the **decision to enter**, nothing else:

> BLOCK entry when the setup's regime (BULL / BEAR) is not in the ALLOW set.
> The ALLOW set is derived **exclusively from IN-split trades** and frozen before
> VAL/OOS are inspected.

Unchanged: setup detection, targets, stops, scoring, confirmation rules, overlap
policy, fees/slippage, execution model, and the frozen baseline engine itself.
No parameters were swept; no other filters were combined.

## 3. Data split methodology

Chronological, non-overlapping 60/20/20 (train/validation/test) per dataset
(`chronological_split`). Slicing is applied to the **results** of a single frozen
baseline run, so all periods share identical historical engine state:

| Dataset | candles | IN (train) | VAL | OOS (test) |
|---|---|---|---|---|
| BTC USDT 4H | 19804 | [0, 11882) | [11882, 15843) | [15843, 19804) |
| BTC USDT 1D | 3304 | [0, 1982) | [1982, 2643) | [2643, 3304) |
| ETH USDT 4H | 19804 | [0, 11882) | [11882, 15843) | [15843, 19804) |
| ETH USDT 1D | 3304 | [0, 1982) | [1982, 2643) | [2643, 3304) |

Roles: **IN = design/freeze the filter**, **VAL = primary forward decision window**,
**OOS = reference only (never informs the filter)**.

## 4. Regime classification / filter methodology

- Regime label is stamped on each setup at detection time (computed from past
  candles only — no future information).
- For each dataset, the filter computes, from **IN-split completed trades only**:
  - `n` = number of IN trades in the regime,
  - `expectancy` = mean `net_return` of those IN trades.
- Rule: **ALLOW** a regime iff `expectancy > 0 AND n >= 30`; otherwise **BLOCK**.
- The derived ALLOW set is frozen before any VAL/OOS metric is computed.
- One filter per dataset (not pooled across assets/timeframes).

### Resulting filters

| Dataset | ALLOW | BLOCK | Action |
|---|---|---|---|
| BTC USDT 1D | BEAR, BULL | — | none |
| ETH USDT 1D | BEAR, BULL | — | none |
| BTC USDT 4H | BEAR, BULL | — | none |
| ETH USDT 4H | **BULL** | **BEAR** | blocks BEAR (IN exp −0.0050) |

Only **ETH 4H** had a regime with negative IN expectancy at sufficient sample,
so **only ETH 4H is a testable case**. The other three datasets are filter no-ops.

## 5. Leakage audit

Verified against the code (`src/veyra/research/e2_regime_filter.py`):

- **Filter construction input:** `_build_filter(in_trades)` uses IN-split trades only.
- **VAL untouched during construction:** yes — VAL trades are referenced only
  *after* `allowed_regimes` is built, for comparison metrics.
- **OOS untouched:** yes — OOS is printed as "reference only"; it feeds nothing
  into the filter.
- **Regime label:** assigned at detection from past candles only; no future info.
- **IN return boundary leakage:** empirically, IN-entry trades do not exit past the
  train boundary on the datasets audited (avg hold ~6 bars, entries spaced well
  before train end), so IN net-returns do not materially incorporate VAL/OOS prices.
- **Explicitly in-sample:** the IN comparison is by construction in-sample; it is
  NOT treated as unbiased validation here.
- **Selection caveat:** the `n >= 30` and `expectancy > 0` thresholds are authored
  choices, frozen before VAL/OOS — not leakage, but noted.

**Verdict: no look-ahead or selection leakage found.** Discipline is sound.

*(Mechanics note: blocking is applied by intercepting `Simulator.enter` and
returning without opening a position when the regime is blocked. Blocked setups
never produce trades and cannot later slip in, because both the ALLOW set and each
setup's regime label are fixed for the run. Only `enter` is patched — the overlap
policy and all other simulator behaviour are untouched.)*

## 6. Baseline results (frozen baseline, net)

| Dataset | Split | Trades | Expect | PF | Win% | Exp% | Stop% | Tgt% | Hold | MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC 1D | IN | 196 | +0.0073 | 1.31 | 51.0 | 48.5 | 32.1 | 19.4 | 7.0 | −0.79 |
| BTC 1D | VAL | 136 | +0.0083 | 1.50 | 47.1 | 58.8 | 20.6 | 20.6 | 5.6 | −0.65 |
| BTC 1D | OOS | 105 | +0.0149 | 2.32 | 53.3 | 54.3 | 26.7 | 19.0 | 7.9 | −0.38 |
| ETH 1D | IN | 237 | +0.0277 | 2.24 | 55.7 | 46.4 | 28.3 | 25.3 | 5.8 | −0.74 |
| ETH 1D | VAL | 95 | −0.0120 | 0.47 | 40.0 | 49.5 | 32.6 | 17.9 | 4.5 | −0.73 |
| ETH 1D | OOS | 70 | −0.0103 | 0.69 | 41.4 | 32.9 | 41.4 | 25.7 | 7.6 | −0.80 |
| BTC 4H | IN | 1879 | +0.0042 | 1.34 | 44.9 | 54.3 | 31.4 | 14.3 | 6.3 | −0.97 |
| BTC 4H | VAL | 591 | −0.0007 | 0.93 | 39.6 | 45.3 | 35.0 | 19.6 | 5.2 | −0.83 |
| BTC 4H | OOS | 665 | −0.0023 | 0.80 | 37.6 | 52.8 | 31.9 | 15.3 | 7.5 | −0.93 |
| ETH 4H | IN | 1646 | +0.0010 | 1.07 | 44.4 | 47.5 | 31.7 | 20.8 | 5.4 | −0.99 |
| ETH 4H | VAL | 600 | +0.0118 | 2.20 | 45.8 | 52.3 | 31.0 | 16.7 | 7.5 | −0.81 |
| ETH 4H | OOS | 647 | −0.0083 | 0.55 | 36.6 | 45.0 | 36.0 | 19.0 | 7.1 | −1.00 |

## 7. E2 results

Because the filter is a no-op on BTC 1D / ETH 1D / BTC 4H, E2 results equal the
baseline there (0 blocked). Only **ETH 4H** yields a real filter:

### ETH 4H — Baseline vs E2

| Metric | IN baseline | IN E2 | VAL baseline | VAL E2 | OOS baseline | OOS E2 |
|---|---|---|---|---|---|---|
| Trades | 1646 | 1289 | 600 | 460 | 647 | 487 |
| Expectancy | +0.0010 | +0.0027 | **+0.0118** | **+0.0043** | −0.0083 | −0.0044 |
| Profit factor | 1.07 | 1.20 | **2.20** | **1.50** | 0.55 | 0.73 |
| Win rate | 44.4% | 47.4% | 45.8% | 48.7% | 36.6% | 41.3% |
| Expiration | 47.5% | 47.2% | 52.3% | 50.2% | 45.0% | 43.7% |
| Stop hits | 31.7% | 26.2% | 31.0% | 28.0% | 36.0% | 31.0% |
| Target hits | 20.8% | 26.6% | 16.7% | 21.7% | 19.0% | 25.3% |
| Avg hold (bars) | 5.4 | 4.6 | 7.5 | 6.9 | 7.1 | 5.9 |
| MaxDD | −0.99 | −0.97 | −0.81 | −0.85 | −1.00 | −0.99 |
| Avg MAE | 31.38 | 31.89 | 37.55 | 38.12 | 79.79 | 80.00 |
| Avg MFE | 38.19 | 38.48 | 75.78 | 63.62 | 66.81 | 63.83 |

## 8. IN comparison

On **ETH 4H**, the filter improves IN (expectancy +0.0010 → +0.0027, PF 1.07 →
1.20). This is expected and **in-sample** — the filter was selected to remove the
IN-negative BEAR regime, so it must improve IN by construction. **This is not
unbiased validation.**

## 9. VAL comparison (primary decision window)

On **ETH 4H**, the improvement does **NOT** survive to the forward VAL window:

- Expectancy **drops** +0.0118 → +0.0043 (−64%).
- Profit factor **drops** 2.20 → 1.50.
- Win rate rises 45.8% → 48.7% and expiration falls 52.3% → 50.2%, but **the
  average trade is materially worse** and opportunity is destroyed (140 trades
  blocked, a 23% cut in VAL trade count).

The VAL-blocked BEAR trades were **profitable** in the baseline VAL window
(see §11), so removing them removed good trades that happened to be IN-negative.

## 10. OOS reference results

On **ETH 4H**, OOS improves from −0.0083 → −0.0044 (PF 0.55 → 0.73), but remains
**negative**. This is reference-only and not used for selection; it is mixed and
does not rescue the failed VAL window. On the three no-op datasets, OOS is
identical to baseline and already negative on the 4H/ETH-1D corners.

## 11. Regime-level breakdown (Baseline IN+VAL)

| Dataset | Regime | n | Expect | PF | Win% | Exp% | Stop% | Tgt% |
|---|---|---|---|---|---|---|---|---|
| BTC 1D | BEAR | 61 | +0.0121 | 1.48 | 39.3 | 39.3 | 60.7 | 0.0 |
| BTC 1D | BULL | 271 | +0.0067 | 1.34 | 51.7 | 55.7 | 19.9 | 24.4 |
| ETH 1D | BEAR | 49 | +0.0323 | 2.04 | 49.0 | 59.2 | 40.8 | 0.0 |
| ETH 1D | BULL | 283 | +0.0136 | 1.65 | 51.6 | 45.2 | 27.6 | 27.2 |
| BTC 4H | BEAR | 644 | −0.0002 | 0.99 | 32.3 | 49.2 | 50.8 | 0.0 |
| BTC 4H | BULL | 1826 | +0.0041 | 1.41 | 47.6 | 53.2 | 25.7 | 21.0 |
| **ETH 4H** | **BEAR** | **497** | **+0.0066** | **1.31** | 34.4 | 51.7 | 48.3 | **0.0** |
| ETH 4H | BULL | 1749 | +0.0031 | 1.25 | 47.7 | 48.0 | 26.7 | 25.3 |

**Key:** on ETH 4H the baseline BEAR regime was *net positive* (+0.0066, PF 1.31,
n=497) in IN+VAL combined, even though its IN-only slice was negative (−0.0050).
The regime edge **flips sign** between IN and IN+VAL/VAL — a classic sign of
regime-regime instability, not a stable filterable edge.

## 12. Setup-family impact (ETH 4H, IN+VAL)

| Family | Baseline n | Baseline Exp | Baseline PF | E2 n | E2 Exp | E2 PF |
|---|---|---|---|---|---|---|
| BREAKOUT | 674 | +0.0056 | 1.30 | 433 | +0.0075 | 1.56 |
| BREAKOUT_RETEST | 535 | +0.0058 | 1.51 | 279 | +0.0011 | 1.13 |
| PULLBACK | 531 | +0.0014 | 1.11 | 531 | +0.0014 | 1.11 |
| TREND_CONTINUATION | 506 | +0.0023 | 1.18 | 506 | +0.0023 | 1.18 |

The regime filter removes trades unevenly across families (BREAKOUT and
BREAKOUT_RETEST lose BEAR entries; PULLBACK and TREND have no BEAR presence so are
unchanged). BREAKOUT improves modestly under E2, BREAKOUT_RETEST degrades. There
is no evidence of a coherent family-level improvement.

## 13. Opportunity cost / blocked setups

| Dataset | Blocked IN | Blocked VAL | Blocked OOS | Filter active |
|---|---|---|---|---|
| BTC 1D | 0 | 0 | 0 | no |
| ETH 1D | 0 | 0 | 0 | no |
| BTC 4H | 0 | 0 | 0 | no |
| ETH 4H | 357 | 140 | 160 | yes |

On ETH 4H, blocking destroyed 23% of VAL opportunity (140 trades) while *lowering*
VAL expectancy and PF. The filter "worked" only by removing trade count, which is
not a robustness improvement.

## 14. Verdict

**UNSUPPORTED (FALSIFIED on the only testable dataset).**

Rationale, conservatively:

- E2 is testable only on **ETH 4H**; the other three datasets are filter no-ops,
  so there is no evidence of benefit across markets.
- On ETH 4H the improvement **does not survive IN → VAL**: IN improves (in-sample),
  but the forward VAL window's expectancy and PF **deteriorate** (−64% expectancy,
  PF 2.20 → 1.50). Per the pre-registration, this alone means the hypothesis is
  unsupported.
- The apparent win-rate/expiration improvements are **cosmetic**: they come from
  suppressing opportunity while degrading outcome quality, which the instructions
  explicitly forbid counting as success.
- The baseline BEAR regime was net-positive outside IN, so the IN-selected filter
  removed good trades — the regime edge flips sign across windows.
- OOS improves only within a still-negative range.

**Explicit scoping:** E2 tests whether a regime-based entry filter improves
robustness. It does **NOT** prove that regime changes are the causal reason setups
expire. No conclusion about causality is drawn.

**Recommendation:** do **not** adopt the regime-only entry filter. The regime label
(BULL/BEAR only) is not a stable, forward-discriminating filter dimension with the
current IN-only selection rule and threshold. Further research is required before
the baseline is approved for any deployment.

---

*Frozen baseline untouched. E2 ran exactly as defined (`e2-regime-filter-v1`).*
*Measurements only; not a profitability claim.*