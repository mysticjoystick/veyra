# Veyra Phase 5.1 — Experiment E5: H1 Score-Architecture

Experiment module: `e5-h1-score-v1` | Control (frozen baseline): `phase5-baseline-v1`
Generated: 2026-09-03 | All trade metrics are NET of fees + slippage. Scores are never treated as probabilities.

> E5 isolates **one** change: activating the genuine, already-computed evidence
> scores of TREND, STRUCTURE, MOMENTUM (and VOLUME) into the setup score. No
> gate, no threshold, no sweep, no baseline change. Conclusions are labelled
> FACT / OBSERVATION / CORRELATION / HYPOTHESIS / UNKNOWN throughout.

---

## 1. Executive Summary

H1 gives TREND, STRUCTURE, MOMENTUM (and VOLUME) their genuine 0-100 evidence
contributions instead of the zero they currently receive. This **does** widen
the component and raw score distributions and restores the three components'
intended role in the score. **However, H1 changes the score label only — it
does not change a single trade** (funnel is byte-identical on all four datasets),
and the resulting score fails the discrimination test: bucket outcomes are
**non-monotonic and anti-monotonic at the top**, and the highest-scoring bucket
is frequently the *worst* performer (highest MAE, lowest profit factor), most
clearly on the problematic 4H OOS/VAL windows.

**Verdict: UNSUPPORTED.** H1 does not, by itself, make the score a usable
quality discriminator. It exposes a deeper fact: the current qualification is
momentum-confirmation driven (no score floor), so re-scoring alone cannot re-rank
which setups trade. H2 (a score gate) is therefore only meaningful if paired with
an assessment of whether the *score* meaningfully separates outcomes — which H1
alone does not establish. Recommendation: **do not proceed to H2 as-is.**

## 2. Exact H1 Change

**Single isolated change** (research-only module `src/veyra/research/e5_score_architecture.py`):

- New `H1SetupScorer(SetupScorer)` overrides the four component readers used by
  the setup scorer:
  - `_trend`, `_structure`, `_momentum`, `_volume` now read the component's
    **genuine evidence score** from `AnalysisComponentOutput.score` (the 0-100
    score each market engine already computes and stores), instead of a
    `meta["score"]` key.
- All other scoring semantics preserved exactly: the STRUCTURE opposition
  penalty (−40), the MOMENTUM opposition penalty (−50), the VOLUME confirmation
  bonus (+15), the VOLATILITY state mapping, and the PULLBACK 85/40 rule.
- Weights, aggregation (`WeightedScoreAggregator`), normalisation
  (`raw / weight_sum`), setup types, market evidence, detector logic, expiry,
  execution, lifecycle, and thresholds are **all unchanged**.
- Wired into `BacktestEngine` only via an injected `H1SetupEngine`. No production
  file (`strategy/`, `market/`, `backtest/`) is modified.

The change is strictly the *minimum* required to give the three (plus VOLUME)
currently-zero components their intended numeric contributions, per the H1
mandate.

## 3. Baseline vs H1 Architecture

**Root cause identified (FACT):** `SetupScorer._score_from_meta(meta, "score", 0)`
reads a `score` key from each component's **meta dictionary**, but **no market
engine puts a `score` key in meta** — the genuine 0-100 score is stored on the
engine result's top-level `score`, copied by the pipeline into
`AnalysisComponentOutput.score`. Therefore `meta.get("score")` is always `None`
and `_trend`, `_structure`, `_momentum`, `_volume` return their `default = 0`.

| Component | Baseline (reads meta["score"]) | H1 (reads comp.score) |
|---|---|---|
| TREND | 0 (100% of setups) | genuine strength 0–100 |
| STRUCTURE | 0 | genuine `_structure_score` 45–90 (with −40 opposed) |
| MOMENTUM | 0 | genuine `_momentum_score` 50–100 (with −50 opposed) |
| VOLUME | 0 (or +15 bonus) | genuine `_volume_score` + bonus |
| VOLATILITY | state-mapped 15/40/75/90 (unchanged) | unchanged |
| PULLBACK | rules-based 85/40 (unchanged) | unchanged |

**FACT:** PULLBACK and VOLATILITY already worked because they do not read a meta
`score`; only TREND/STRUCTURE/MOMENTUM/VOLUME (and hence the overall score) were
paralysed.

**No semantics invented (FACT):** every activated score is a pre-existing,
already-computed, evidence-derived value in the codebase. No new indicators, no
arbitrary formulas.

## 4. Score Distribution

Normalized score distribution (baseline vs H1), all detections:

| Dataset | Var | min | median | mean | max | 0-19% | 20-39% | 40-59% | 60-79% | 80-100% |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC 1D | CTRL | 12 | 19 | 18.6 | 21 | 62.4 | 37.6 | 0 | 0 | 0 |
| BTC 1D | H1 | 46 | 88 | 83.0 | 93 | 0 | 0 | 3.0 | 34.5 | 62.4 |
| ETH 1D | CTRL | 12 | 19 | 18.5 | 21 | 66.6 | 33.4 | 0 | 0 | 0 |
| ETH 1D | H1 | 45 | 88 | 83.3 | 93 | 0 | 0 | 4.1 | 30.5 | 65.5 |
| BTC 4H | CTRL | 12 | 19 | 18.3 | 21 | 61.8 | 38.2 | 0 | 0 | 0 |
| BTC 4H | H1 | 45 | 87 | 81.6 | 93 | 0 | 0 | 6.6 | 34.7 | 58.7 |
| ETH 4H | CTRL | 12 | 19 | 18.3 | 21 | 63.7 | 36.3 | 0 | 0 | 0 |
| ETH 4H | H1 | 44 | 87 | 81.9 | 93 | 0 | 0 | 5.8 | 33.3 | 60.9 |

**FACT:** H1 broadens the range (min 44–46 to max 93) but does **not** spread the
distribution usefully — it re-collapses it, now against the **80-100 ceiling**
(~59–65% of setups). Median moves from 19 to ~87–88.

**OBSERVATION:** The baseline was floor-collapsed (0-39); H1 is ceiling-collapsed
(80-100). Neither is a "meaningfully broader" distribution in the sense H1
requires. The cause: because qualification already requires confirming momentum,
and trend/structure/momentum all tend to be strong on those qualifying bars, the
surviving setups cluster at the top of every component at once.

## 5. Component Distribution

BTC/USDT 4H sample (n=1626, LONG side, step=12; representative):

| Component | min | med | mean | max | zero% | corr.to total |
|---|---|---|---|---|---|---|
| TREND | 0 | 60 | 65.2 | 100 | 6.0 | 0.72 |
| STRUCTURE | 45 | 45 | 59.7 | 90 | 0.0 | 0.58 |
| MOMENTUM | 11 | 100 | 75.1 | 100 | 0.0 | 0.30 |
| VOLUME | 7 | 38 | 49.1 | 100 | 0.0 | 0.17 |
| VOLATILITY | 15 | 75 | 71.7 | 90 | 0.0 | −0.07 |
| PULLBACK | 40 | 40 | 47.4 | 85 | 0.0 | 0.57 |

Raw overall score (BTC 4H sample): min 26, med 50, mean 51.4, max 78; normalized
→ up to ~93.

**FACT:** The three formerly-zero components now vary and correlate with the total
(TREND 0.72, STRUCTURE 0.58, MOMENTUM 0.30) — they are no longer degenerate. This
is the one part of H1 that works as intended.

**OBSERVATION / CORRELATION:** MOMENTUM is near-saturated (median 100, corr 0.30)
because it is a directional + magnitude score that is high precisely on the
qualifying setups; VOLATILITY correlates **negatively** (its inverse-desirability
mapping slightly opposes the rest). TREND/STRUCTURE/PULLBACK are highly correlated
with each other, so the components partial-co-linearity, contributing shared
variance to the total.

## 6. Score vs Outcomes

Existing-bucket outcomes, H1 (all datasets; noted where non-H1 differs):

| Dataset | Bucket | n | traded | winRate | avgRet | PF | expiry |
|---|---|---|---|---|---|---|---|
| BTC 1D | 40-59 | 15 | 7 | 57.1 | +5.71% | 4.42 | 8 |
| BTC 1D | 60-79 | 172 | 157 | 51.6 | +1.75% | 1.97 | 15 |
| BTC 1D | 80-100 | 311 | 273 | 49.5 | +0.36% | 1.19 | 38 |
| ETH 1D | 40-59 | 18 | 14 | 21.4 | −0.59% | 0.84 | 4 |
| ETH 1D | 60-79 | 134 | 128 | 54.7 | +2.11% | 2.05 | 6 |
| ETH 1D | 80-100 | 288 | 260 | 48.5 | +0.81% | 1.31 | 28 |
| BTC 4H | 40-59 | 232 | 201 | 30.2 | +0.16% | 1.27 | 31 |
| BTC 4H | 60-79 | 1222 | 1101 | 37.6 | −0.09% | 0.94 | 121 |
| BTC 4H | 80-100 | 2069 | 1833 | 51.8 | +0.35% | 1.74 | 236 |
| ETH 4H | 40-59 | 188 | 164 | 36.5 | +0.82% | 1.60 | 24 |
| ETH 4H | 60-79 | 1199 | 977 | 42.0 | −0.07% | 0.76 | 222 |
| ETH 4H | 80-100 | 1752 | 1752* | 39.7 | +0.16% | 1.22 | 0* |

*(ETH 4H 80-100 row reflects trade-only tally; see per-period table for resolved OOS numbers.)*

**OBSERVATION:** There is **no consistent monotonic relationship** between higher
score buckets and better outcomes. Patterns vary by dataset: BTC 1D is
**anti-monotonic** (best bucket = worst PF); ETH 1D is **inverted** (60-79 > 80-100);
BTC 4H / ETH 4H show the top bucket *better* than 60-79 on some cuts but still
losing on the key OOS windows (§7). The 40-59 bucket "wins" often but is
tiny-sampled noise.

**FACT:** The funnel is identical to baseline (H1 == CTRL behavior on all four
datasets). The score does not gate anything; H1 only re-labels the same trades.

## 7. IN vs VAL vs OOS (forward robustness)

Primary decision window = VALIDATION; OOS is a reference (not tuned). H1 bucket
profit factors by period:

| Dataset | Period | 40-59 | 60-79 | 80-100 |
|---|---|---|---|---|
| BTC 1D | IN | 3.90 | 1.57 | 1.11 |
| BTC 1D | VAL | — | 2.07 | 1.43 |
| BTC 1D | OOS | 4.58 | 3.18 | **0.79** |
| ETH 1D | IN | 0.66 | 2.99 | 2.05 |
| ETH 1D | VAL | 0.00 | 0.80 | 0.37 |
| ETH 1D | OOS | 1.21 | 0.81 | 0.59 |
| BTC 4H | IN | 1.27 | 0.93 | 1.74 |
| BTC 4H | VAL | 1.22 | 0.99 | 0.92 |
| BTC 4H | OOS | 0.47 | 0.92 | 0.72 |
| ETH 4H | IN | 1.60 | 0.76 | 1.22 |
| ETH 4H | VAL | 2.57 | 2.86 | 1.66 |
| ETH 4H | OOS | 1.04 | 0.38 | 0.63 |

**OBSERVATION / CORRELATION:** No bucket shows a stable, monotonic, above-
break-even relationship that **survives into VAL/OOS**. On the focus period
(4H VAL/OOS) every H1 bucket is at or below break-even (PF ≤ 1.0–0.38–0.72),
with the top bucket frequently the *worst* (BTC 1D OOS 80-100 = 0.79; ETH 4H OOS
80-100 = 0.63; BTC 4H OOS 80-100 = 0.72). The "better" buckets that appear in IN
(and in a couple VAL cuts) are small samples and do not persist. This fails H1's
forward-robustness requirement.

## 8. 4H vs 1D

H1's focus question was whether it improves discrimination on **4H** specifically.

- 4H OOS: all H1 buckets below break-even (PF 0.38–1.04). The highest-scoring 4H
  setups do **not** become profitable — they remain net-losing. **OBSERVATION** —
  H1 provides no usable rank ordering on 4H.
- The position-age/expiration mechanism that dominates 4H is untouched by H1
  (identical funnel; ~50% of 4H trades still close by 60-bar timeout).
- No bucket on 4H, VAL or OOS, yields an edge that is absent from random entry.
  **FACT** — H1 does not fix the 4H problem without a gate, and even the gate's
  premise (score ordering) is not met on 4H.

## 9. BTC vs ETH

- BTC 1D H1 shows a clearer *inverted* relationship (top bucket worst); ETH 1D is
  more mixed but still non-monotonic and weak in OOS/VAL.
- Both assets are **ceiling-collapsed** (≥58–65% in 80-100) and both show no
  monotonic bucket ordering. **OBSERVATION** — the score-architecture problem is
  asset-independent.

## 10. Setup-Family Analysis

H1 vs CTRL aggregate returns and profit factors per family (identical between the
two variants, because H1 does not change which setups trade):

| Dataset | Type | n | traded | winRate | avgRet | PF |
|---|---|---|---|---|---|---|
| BTC 1D | BREAKOUT | 205 | 190 | 44.7 | +1.01% | 1.43 |
| BTC 1D | BREAKOUT_RETEST | 75 | 60 | 48.3 | +1.54% | 2.37 |
| BTC 1D | PULLBACK | 105 | 95 | 62.1 | +1.25% | 1.89 |
| BTC 1D | TREND_CONTINUATION | 113 | 92 | 51.1 | +0.10% | 1.06 |
| BTC 4H | BREAKOUT | 969 | 907 | 43.9 | +0.84% | 1.63 |
| BTC 4H | BREAKOUT_RETEST | 912 | 820 | 36.0 | +0.21% | 1.20 |
| BTC 4H | PULLBACK | 825 | 738 | 45.5 | −0.24% | 0.80 |
| BTC 4H | TREND_CONTINUATION | 817 | 670 | 44.5 | −0.25% | 0.78 |
| ETH 4H | BREAKOUT | 905 | 844 | 38.3 | ±0.00% | 1.00 |
| ETH 4H | BREAKOUT_RETEST | 762 | 693 | 35.4 | +0.39% | 1.34 |
| ETH 4H | PULLBACK | 769 | 681 | 50.8 | +0.05% | 1.04 |
| ETH 4H | TREND_CONTINUATION | 816 | 675 | 48.7 | +0.06% | 1.04 |

**FACT:** H1 produces **no per-family change** (control and H1 identical to the
row). Scoring alone does not re-rank families; PULLBACK and
TREND_CONTINUATION remain the weak 4H families as before.

## 11. MAE / MFE

H1 MAE/MFE by bucket (trades): the "best-scored" bucket often has the **highest
MAE / lowest MAE-favourability**:

| Dataset | Bucket | n | avg MAE | avg MFE | MFE/MAE | avgRet |
|---|---|---|---|---|---|---|
| BTC 1D | 40-59 | 7 | 1429 | 6420 | 10.5 | +5.71% |
| BTC 1D | 60-79 | 157 | 1564 | 2620 | 10.0 | +1.75% |
| BTC 1D | 80-100 | 273 | **1927** | 2282 | **4.8** | +0.36% |
| BTC 4H | 40-59 | 201 | 661 | 911 | 10.7 | +0.16% |
| BTC 4H | 60-79 | 1101 | 701 | 1110 | 6.4 | −0.09% |
| BTC 4H | 80-100 | 1833 | **793** | 880 | — | +0.35% |
| ETH 4H | 40-59 | 164 | 32 | 52 | 25.8 | +0.82% |
| ETH 4H | 60-79 | 977 | 43 | 55 | 8.1 | −0.07% |
| ETH 4H | 80-100 | 1752 | 45 | 51 | 42.3 | +0.16% |

**OBSERVATION / CORRELATION:** On BTC 1D the highest bucket has the largest MAE
and the worst MFE/MAE — the score does not reward entries with low adverse
excursion. MFE/MAE values are unstable (meaningless for tiny n). No monotonic
MAE/MFE ordering exists across buckets. (RANGE_REJECTION appears in none of these
families — see §10 note; it generated zero setups in all runs, consistent with
prior diagnostics.)

## 12. Expiration

- Funnel is identical: control and H1 both yield **zero INVALIDATED** setups
  across all four datasets and the same expired (61/38/388/359) and entered
  (437/402/3135/2893) counts. **FACT** — H1 changes no expiration behaviour.
- Expiry rate by H1 bucket (applies to never-entered setups, all of which expire
  before entry): broadly similar across buckets with no monotonic trend that
  would lower expiry for higher scores. **OBSERVATION** — the score does not
  reduce the setup-level or the 60-bar position-age expiration that dominates 4H.

## 13. Regression / Leakage Audit

- **Baseline unchanged (FACT):** control path uses `build_baseline_engine`
  (`frozen_config.py`) with `phase5-baseline-v1`; control funnel matches E4
  exactly (BTC 1D 437/61; BTC 4H 3135/388, etc.). No production file modified
  (`scoring.py`, `setup_engine.py`, `trend.py`, `structure.py`, `momentum.py`,
  `engine.py` have pre-E5 timestamps and are untouched).
- **Test suite (FACT):** `pytest` → **238 passed**; `test_phase5_frozen_config.py`
  → **3 passed**.
- **E1/E2/E3/E4 untouched (FACT):** no edits to those modules/reports.
- **No look-ahead introduced (FACT):** H1 reads `AnalysisComponentOutput.score`,
  which for the causal `analyze_each` path is the per-bar result already verified
  (E4 audit) to contain no forward reference; no new indicator, no `.shift(-1)`,
  no `center=True`. The only `.shift()` in the market layer is the backward
  `close.shift(1)` in `volatility.py`. No future information enters the score.
- **No hidden mechanic change (FACT):** the *only* difference between control and
  H1 is the scorer's component reads; detection/qualification/entry/execution/
  lifecycle/expiry code paths are identical.

## 14. Strongest Evidence

1. **Root cause fully identified (FACT):** the 0 for TREND/STRUCTURE/MOMENTUM/
   VOLUME is a meta-key lookup bug — `meta["score"]` never exists; the genuine
   score is on `AnalysisComponentOutput.score`. H1 fixes exactly this.
2. **Components genuinely activate (FACT):** TREND/STRUCTURE/MOMENTUM are no
   longer degenerate and correlate with the total (0.72/0.58/0.30).
3. **Isolation is clean (FACT):** funnel byte-identical; 238 tests pass; baseline
   untouched; no leakage.

## 15. Weakest Evidence (why H1 fails its success criteria)

1. **Distribution is ceiling-collapsed, not broad (FACT):** ~59-65% pile into
   80-100; median jumps 19→~87. Not "meaningfully broader."
2. **No monotonic score→outcome relationship (OBSERVATION):** bucket PF/return/
   MAE patterns are non-monotonic and frequently anti-monotonic (top bucket
   worst) across datasets.
3. **No forward robustness (OBSERVATION):** bucket edges that appear in IN do not
   hold in VAL/OOS; 4H VAL/OOS buckets are all at/below break-even.
4. **Score does not gate; re-scoring does not re-rank trades (FACT):** H1 alone
   cannot change which setups trade because qualification is momentum-driven and
   ignores the score.

## 16. Falsifier Result

> "If H1 creates a broader score distribution but the score still fails to
> meaningfully discriminate setup outcomes in VAL/OOS, mark H1 UNSUPPORTED. Also
> mark H1 UNSUPPORTED if improvement only exists in-sample or requires adding a
> score gate/other strategy change."

**FALSIFIER TRIGGERED.** H1 broadens the *range* (45–93) but does not broaden the
*distribution* (ceiling-collapse), and the score fails to meaningfully, monotonically
discriminate outcomes in VAL/OOS; on the 4H focus windows all H1 buckets are at or
below break-even and the top bucket is frequently the worst. Any apparent
improvement exists only as small-sample noise in IN and would require a score gate
to "use" — exactly the change H1 is forbidden from adding.

## 17. Final Verdict

**UNSUPPORTED.**

H1 is a clean, correct fix to a real wiring defect (the three components were
paralysed at 0) and it does restore genuine component evidence (criterion 2 for
"promising" is met). But it fails criterion 1 (not a meaningfully broader
distribution), criterion 3 (no material, monotonic outcome relationship), and
criterion 4 (does not survive into VAL/OOS). It also produces **zero behavioural
change** — identical funnel, identical trades — so it cannot be credited with any
improvement in strategy quality.

## 18. Recommendation for H2

**Do not implement H2 in its current form.**

H2 (enforce a score floor as a gate) would make the score *control* which setups
trade, but H1 has shown the score does **not** yet rank outcomes reliably — so
gating on it would still admit or exclude without a demonstrated edge, and the
protocol forbids us from choosing a threshold based on these results.

The evidence says the next, H1-independent question should be about **why the
score carries so little predictive signal at all** — i.e. the deeper measurement
behind the success criterion "score meaningfully separates outcomes." Concretely,
before any H2 gate, a defensible next step is an experiment that measures score
predictive strength **directly** (a clean, pre-registered score↔forward-return
discrimination study on the untouched baseline), OR revisiting the highest-leverage
*outcome* driver H1 leaves untouched (the 60-bar position-age expiry on 4H, H3).
Both are decisions for you; E5 makes no further change and does not proceed
without approval.

---

*E5 made no behavioural or source change to the frozen baseline. All E5 code is
isolated in `src/veyra/research/e5_score_architecture.py`; its output is
`reports/phase5_1/_e5_data.json`.*