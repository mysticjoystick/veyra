# Phase 5 — Real-Data Validation & Paper Trading

Status: **measurement complete for 4 real datasets; verdict MIXED; no
profitability claimed.**

Phase 5 converts Phase 4's hypothesis measurement from synthetic data to **real
Binance candles**, and (if and only if supported) propagates the frozen baseline
into simulation-only paper trading. Every result below is a measurement; Veyra
makes no claim of future returns.

## 1. What was built

- **Frozen baseline** `phase5-baseline-v1` (`phase5/frozen_config.py`) — the exact
  Phase 4 parameterisation, stamped onto every run.
- **Real data** — BTC/USDT & ETH/USDT, 4H + 1D, 2017-08-17 → 2026-09-02, with
  provenance manifests (`data/manifests/*.json`) and a quality report
  (`docs/phase-5-data-quality.md`). 4H data is `VALID_WITH_GAPS` (16 missing
  2018-2020 Binance-maintenance bars); 1D is `VALID`.
- **Look-ahead audit** (`docs/phase-5-audit.md`) — PASSED; the critical freeze
  test (appending future candles never reshuffles past decisions) holds.
- **Progressive single-pass analysis** — the slice-per-bar backtest was O(N²) and
  ran >15 min on full 4H. A provably bit-identical progressive path makes a full
  4H run ~4 minutes. Equivalence is pinned by `tests/unit/test_phase5_progressive.py`.
- **Validation orchestrator** (`phase5/validate.py`) — one engine run per dataset;
  overall metrics + breakdowns (setup type / regime / timeframe / fixed score
  buckets) + chronological IN / VALIDATION / OOS periods.
- **Calibration** (`phase5/calibration.py`) — measures whether the score
  discriminates outcomes; the score is **never** relabelled as a probability.
- **Robustness** (`phase5/robustness.py`) — cost-sensitivity of the fixed trade
  set across fee/slippage scenarios.
- **Gates A–G** (`phase5/gate.py`) → conservative verdicts
  SUPPORTED / MIXED / WEAK / INSUFFICIENT_EVIDENCE.
- **Reports** — `reports/phase5/phase5-<SYM>-<TF>.md|.json` per dataset plus
  `phase5-OVERALL.md`, answering the 14 Phase 5 questions.
- **Paper trading** (`paper/`) — simulation-only offline replay; never executes.

## 2. Real-data results (frozen baseline, net of fees/slippage)

| dataset | trades | OOS trades | OOS avg/trade | verdict |
|---|---|---|---|---|
| BTC/USDT 1D | 437 | 105 | +1.49% | MIXED |
| BTC/USDT 4H | 3135 | 665 | −0.23% | MIXED |
| ETH/USDT 1D | 402 | 70 | −1.03% | MIXED |
| ETH/USDT 4H | 2893 | 647 | −0.83% | MIXED |

## 3. Honest findings (positive and negative)

- **Source/measurement integrity is solid.** Gates A, B, C, E, F pass: provenance
  and quality valid, no lookahead, baseline frozen (no tuning), adequate sample
  sizes reported with CIs, and 4 datasets give cross-asset/cross-timeframe
  sensitivity.
- **On the primary 4H timeframe the strategy shows *no* OOS edge** (negative on
  both symbols). Combined with the shorter 1D (mixed: positive on BTC, negative
  on ETH), the evidence is that the current hypothesis does **not** persist
  across timeframes (Q6 → "no").
- **The score does not usefully discriminate outcomes** (Gate G). On BTC 1D the
  observed relationship is *anti-monotonic* (higher-score buckets had lower win
  rate); `score_is_useful = False`. This is a genuine negative and is reported
  plainly rather than hidden.
- **Robustness:** where an edge exists it is stable under cost assumptions (edge
  does not flip sign across fee scenarios), but this does not rescue the missing
  edge on 4H.
- **Overall verdict: MIXED.** The instrument is honest; the hypothesis under test
  is, at best, marginal and not consistently profitable after costs.

## 4. How to read this (statistical honesty)

- Numbers are measurements on historical candles, **not** predictions.
- Negative results are surfaced (4H no-edge, score non-discrimination) rather
  than reported as promising.
- Small OOS samples (BTC 1D n=105, ETH 1D n=70) are flagged with Wilson CIs;
  conclusions are not fabricated.
- Synthetic data is never used as evidence.

## 5. Recommendation (per Phase 4 §20)

Do **not** proceed to live trading on this baseline. The honest next step is
either:
1. keep refining the setup/score hypotheses before re-validation, or
2. run simulation-only paper (`veyra paper-start --symbol ... --timeframe ...`)
   as a forward watch, strictly for research — never with real money.

The paper engine is present and exercised; it is offline and does not connect to
any brokerage or exchange.

---

_generated for the Veyra Phase 5 milestone — measurements only._