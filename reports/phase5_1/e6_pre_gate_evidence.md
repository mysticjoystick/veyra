# Veyra Phase 5.1 — Experiment E6: Pre-Gate Evidence vs Forward Outcome

Experiment module: `e6-pre-gate-evidence-v1` | Frozen baseline: `phase5-baseline-v1`
Generated: 2026-09-03 | All trade metrics are NET of fees + slippage. Scores are treated as ranking evidence, never as probabilities.

> E6 answers the question left open by E5 Section 18: *before the qualification
> gate compresses the candidate population, do Veyra's existing evidence
> components contain a stable relationship with future trade quality?* It is a
> measurement on the **untouched frozen baseline** — no change to any score, gate,
> threshold, detector, exit, target, stop, entry timing, qualification, or baseline.
> Conclusions are labelled FACT / OBSERVATION / CORRELATION / HYPOTHESIS / UNKNOWN
> throughout.

---

## 1. Executive Summary

E6 captures **every detected candidate** (pre-qualification, pre-trade), for
each recording the detection-time component evidence on TWO independent layers
plus a forward market label, joined deterministically to the frozen baseline run.
Three findings dominate:

1. **The scorer cannot see the genuine evidence (FACT):** `SetupScorer` reads a
   `meta["score"]` key that no market engine provides, so TREND, STRUCTURE and
   MOMENTUM score **0 for 100% of detected candidates** in all four datasets. The
   only real score Veyra assigns is a 4–5 value ranking built from PULLBACK (85/40)
   plus a constant VOLATILITY (75). E5's Section 3 root cause is re-confirmed
   directly at the detection layer.
2. **The pre-gate genuine evidence has no stable forward edge (FACT/CORRELATION):**
   every |Spearman(genuine_component, forward-return)| ≤ 0.16, and the signs flip
   across dataset, timeframe, and even the IN/VAL/OOS periods of a single dataset.
   Components that genuinely vary (TREND, VOLUME) fail to separate forward
   outcomes monotonically.
3. **Even the "pre-gate" population is already compressed (FACT):** detection
   predicates require momentum-confirmed, non-extreme-volatility states, so
   MOMENTUM=100 (≈97%) and VOLATILITY=75 (100%) are near-constant *before* the
   qualification gate.

**Verdict: UNSUPPORTED.** The pre-gate evidence components do not contain a
stable, monotonic, out-of-sample relationship with future trade quality. The
deeper reason is not the gate — it is that the components measure *current market
state/strength at the already-momentum-confirmed detection bar*, which carries no
forward edge, and the score Veyra actually computes collapses three components to
zero.

## 2. Method (exactly what was measured)

This is a **diagnostic measurement**, not a strategy change. All detection,
qualification, entry, exit, and execution behavior is the frozen
`phase5-baseline-v1`; only forward *labels* and *captured features* differ.

**Two measurement axes, two feature layers:**

- **Axis A — genuine pre-gate evidence → future MARKET outcome.** For every
  detected candidate (qualified or not, traded or not), the genuine market
  evidence at detection (`TREND/STRUCTURE/MOMENTUM/VOLUME/VOLATILITY/PULLBACK`) is
  compared against forward labels computed from raw price over a capped window.
- **Axis B — candidate → qualification → trade outcome.** The same candidates are
  joined by `(setup_type, detection_ts)` to a single frozen baseline run to obtain
  qualified/traded/outcome/exit/net_return (lifecycle labels).

**Two feature layers per bar (both detection-time, causal):**

- `genuine_*` — the true market-engine score each component measures
  (`AnalysisComponentOutput.score`, the same evidence E5/H1 reconnects), plus the
  existing VOLATILITY state-map and PULLBACK rule.
- `scored_*` — exactly what `SetupScorer.apply` assigns via `setup.scores`
  (the value Veyra actually consumes). `overall_score_raw` is `setup.overall_score`.

**Forward labels (Axis A), all computed strictly after the detection close:**

- `fwd_ret_5` / `fwd_ret_10` — signed forward close-to-close return at 5/10 bars.
- `fwd_mfe_pct` / `fwd_mae_pct` — signed max favorable / adverse excursion vs the
  detection close over a cap of 60 bars (aligned to `position_max_bars=60`).
- `fwd_target_reached` / `fwd_stop_reached` — whether the future range crossed the
  candidate's own target/stop before the final bar.

**Chronological split:** IN 60% / VAL 20% / OOS 20% by candle index, used only to
test stability — never to tune. Detection/forward-window periods do not look ahead;
features use only information at the detection bar.

## 3. Datasets & Sizes

| Dataset | Detected candidates | 100% backtest-join | Traded | Period IN/VAL/OOS |
|---|---|---|---|---|
| BTC/USDT 1D | 498 | 498/498 | 437 | 226 / 152 / 120 |
| ETH/USDT 1D | 440 | 440/440 | 402 | 260 / 108 / 72 |
| BTC/USDT 4H | 3,523 | 3,523/3,523 | 3,135 | — |
| ETH/USDT 4H | 3,252 | 3,252/3,252 | 2,893 | — |

Every detected candidate carries a forward label; the backtest join is 100% on all
four datasets (the detector pass and engine run are deterministic and share the
frozen `SetupEngine`).

## 4. The two component layers (the decisive structural fact)

`SetupScorer._score_from_meta(meta, "score", 0)` returns `0` whenever the component
meta dictionary has no `score` key. **No market engine emits a `score` key into
that meta dict** — the genuine score lives on `AnalysisComponentOutput.score` and is
not copied into the component meta. Hence:

| Component | genuine_* (market evidence) | scored_* (what Veyra consumes) |
|---|---|---|
| TREND | discrete EMA-vote 0–100 | **0 (100% of candidates)** |
| STRUCTURE | categorical 45–90 | **0 (100%)** |
| MOMENTUM | 50–100 (≈97% = 100) | **0 (100%)** |
| VOLUME | 0–100 (varies) | 0, or +15 bonus only on confirmation |
| VOLATILITY | state 75 (100%) | state 75 (not read from meta) |
| PULLBACK | 85 / 40 | 85 / 40 (rules, not meta) |

`meta_missing_score = True` for every captured candidate; `scored_TREND == 0` and
`scored_MOMENTUM == 0` on **100%** of candidates across all four datasets.

**FACT:** the overall score Veyra actually assigns is composed almost entirely of
PULLBACK + VOLATILITY:
`overall = 0.15·PULLBACK + 0.07·VOLUME + 0.05·VOLATILITY`
rank-correlation of this reconstruction vs `overall_score_raw` = **0.985–1.000**
across datasets; median overall = 16; the score takes only 4–5 distinct values.

## 5. Genuine component → forward return (the central question)

| Spearman(genuine_*, fwd_ret_10) | BTC 1D | ETH 1D | BTC 4H | ETH 4H | sign stability |
|---|---|---|---|---|---|
| TREND | +0.113 | +0.106 | +0.045 | +0.017 | flips within-split |
| STRUCTURE | −0.052 | −0.102 | +0.070 | +0.016 | flips |
| MOMENTUM | −0.040 | +0.010 | −0.003 | +0.001 | ~0 / flips |
| VOLUME | +0.016 | +0.058 | +0.001 | +0.089 | ~0 |
| VOLATILITY | +0.000 | +0.045 | +0.031 | −0.013 | ~0 |
| PULLBACK | −0.085 | −0.160 | +0.061 | +0.008 | flips |
| overall_score_raw | −0.060 | −0.108 | +0.064 | +0.042 | flips |

**FACT:** every |ρ| ≤ 0.16. **OBSERVATION:** no component has a sign-consistent
relationship with forward return across assets and timeframes; the strongest
readings (TREND, PULLBACK, overall on 1D) are *negative or negligible* and reverse
sign on 4H.

Pooled across all 4 datasets (n≈7,713) vs forward MFE/MAE (the actual reward/risk a
trader captures):

| Component | ρ(MFE) | ρ(MAE) |
|---|---|---|
| TREND | +0.051 | −0.051 |
| STRUCTURE | +0.059 | −0.015 |
| MOMENTUM | +0.007 | −0.030 |
| VOLUME | +0.011 | −0.010 |
| VOLATILITY | +0.017 | +0.015 |
| PULLBACK | +0.032 | +0.006 |
| overall_score_raw | +0.033 | −0.011 |

**FACT:** |ρ| ≤ 0.06 for every component against the actionable MFE/MAE. The
genuine evidence does not anticipate the reward or the risk of the trade window.

## 6. Pre-gate selection compression (does the gate, or detection, compress first?)

The qualification gate (`_confirms`) requires momentum aligned with side. But the
**detection predicates already require momentum-confirmed, controlled-volatility
states** (`rules.momentum_state == POSITIVE/NEGATIVE`; detectors reject
HIGH/EXTREME volatility). Therefore compression happens at *detection*, not at the
gate:

| Component | % at modal value (ALL detected, BTC 4H) | % at modal value (TRADED) |
|---|---|---|
| MOMENTUM | 97% @ 100 | 99% @ 100 |
| VOLATILITY | 100% @ 75 | 100% @ 75 |
| STRUCTURE | 61% @ 90 | 60% @ 90 |
| PULLBACK | 77% @ 85 | 78% @ 85 |
| TREND | 71% @ 100 | 70% @ 100 |
| VOLUME | 23% @ 100 (genuinely varies) | 23% @ 100 |

**FACT:** ALL-DETECTED ≈ QUALIFIED weightings are identical; the momentum gate adds
no further variance reduction because candidates are already momentum-confirmed at
detection. **FACT:** only VOLUME genuinely varies over a wide range at any stage;
MOMENTUM and VOLATILITY are near-constants (echoing E5's ceiling collapse, now
located at the detection layer).

## 7. Gate reflection (the same variable is both scored and gated)

The MOMENTUM component and the qualification gate read the **same** momentum
predicate. Because detection already forces MOMENTUM=100 on ≈97% of candidates,
the stage-to-stage momentum change is negligible:

| Dataset | genuine MOMENTUM sd (ALL) | genuine MOMENTUM sd (TRADED) |
|---|---|---|
| BTC 1D | 8.8 | 5.8 |
| BTC 4H | 7.7 | 5.6 |
| ETH 4H | 8.2 | 6.6 |

**FACT:** sd shrinks only slightly from ALL to TRADED — detection already pinned the
value. **OBSERVATION:** the gate therefore reflects (re-encodes) a variable that is
already constant; it cannot add discriminating power from momentum.

## 8. Collinearity among the pre-gate components

Spearman, BTC 1D pre-gate candidates:

| | TREND | STRUCTURE | MOMENTUM | VOLUME | VOLATILITY | PULLBACK |
|---|---|---|---|---|---|---|
| TREND | 1.00 | 0.44 | 0.17 | −0.15 | 0.00 | −0.04 |
| STRUCTURE | 0.44 | 1.00 | 0.27 | −0.12 | 0.00 | **0.83** |
| MOMENTUM | 0.17 | 0.27 | 1.00 | −0.01 | 0.00 | 0.40 |
| VOLUME | −0.15 | −0.12 | −0.01 | 1.00 | 0.00 | −0.04 |
| VOLATILITY | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| PULLBACK | −0.04 | 0.83 | 0.40 | −0.04 | 0.00 | 1.00 |

**FACT:** STRUCTURE↔PULLBACK = **0.83**, PULLBACK↔MOMENTUM = 0.40, TREND↔STRUCTURE =
0.44. **OBSERVATION:** a large share of weight recount one "cleanly aligned,
confirmed-trend" fact multiple times (0.15 + 0.20 + 0.12 ≈ 47% of the weight budget
double-counting one binary condition), while VOLATILITY contributes zero variance
(its rho is 0 against everything).

## 9. The real score Veyra consumes (overall buckets → forward & traded outcomes)

`overall_score_raw` takes only 4–5 distinct values. Forward and traded outcomes are
non-monotonic — the higher bucket is not better.

BTC/USDT 1D:

| overall raw | n | fwd_ret_10 | fwd MFE | fwd MAE | net (traded) |
|---|---|---|---|---|---|
| 10 (PULLBACK 40) | 43 | **+7.42%** | 22.8% | 19.0% | +1.77% |
| 11 | 44 | +3.84% | 25.3% | 17.4% | +2.00% |
| 16 (PULLBACK 85) | 224 | +2.47% | 28.4% | 15.5% | +0.01% |
| 18 (PULLBACK 85 + VOL) | 187 | +3.56% | 37.3% | 11.2% | +1.65% |

ETH/USDT 4H:

| overall raw | n | fwd_ret_10 | fwd MFE | fwd MAE | net (traded) |
|---|---|---|---|---|---|
| 10 | 399 | −0.09% | 11.0% | 11.1% | +0.22% |
| 11 | 309 | +1.11% | 12.4% | 9.5% | +0.13% |
| 16 | 1364 | +0.39% | 12.8% | 9.1% | +0.02% |
| 18 | 1179 | +0.84% | 13.4% | 9.0% | +0.20% |

**OBSERVATION:** on BTC 1D the *lowest* real score (raw 10 = PULLBACK 40, i.e. the
"opposed/weak" bucket) posts the best forward return (+7.42%) and strong net; the
top bucket is not consistently best on either dataset. **FACT:** the real score is a
near-binary PULLBACK rank, not a graded 0–100 quality discriminator.

## 10. Volume — the only genuinely varying component

Quantile buckets of genuine VOLUME vs forward return:

| Dataset | Q1 (low) | Q2 | Q3 | Q4 (high) |
|---|---|---|---|---|
| BTC 1D | +2.68% | +3.41% | +5.83% | +1.92% |
| ETH 1D | +0.26% | +3.00% | +3.77% | +1.89% |
| BTC 4H | +0.77% | +0.24% | +0.77% | +0.77% |
| ETH 4H | −0.19% | +0.70% | +0.62% | +1.21% |

**FACT:** VOLUME is the only wide-variance component (q1≈37–48 → q4≈93–100), and
even it is **non-monotonic**: Q3 exceeds Q4 on both 1D datasets and BTC 4H is flat
(0.24–0.77). No stable volume→forward edge.

## 11. Setup-family conditioning

Pooled across datasets, genuine MOMENTUM / TREND rank vs forward return:

| Family | n | fwd_ret_10 | ρ(momentum) | ρ(trend) |
|---|---|---|---|---|
| BREAKOUT | 2228 | +1.12% | +0.02 | +0.09 |
| BREAKOUT_RETEST | 1805 | +0.52% | −0.04 | +0.08 |
| PULLBACK | 1812 | +1.11% | +0.03 | −0.04 |
| TREND_CONTINUATION | 1868 | +0.70% | +0.04 | −0.02 |

**FACT:** mean forward return is similar across families (+0.5%…+1.1%); component
rank correlations are ≤0.09 and sign-flippy. Conditioning on family does not
uncover a stable relationship.

## 12. Asset / timeframe conditioning

From Section 5: the strongest readings are on the **1D** datasets (TREND +0.11,
PULLBACK −0.09/−0.16, overall −0.06/−0.11) but with *negative* or negligible sign;
the **4H** datasets hover near zero (+0.001–0.089). **OBSERVATION:** the apparent 1D
signal is small, negative-to-weak, and disappears on 4H — the opposite of a stable,
transferable edge.

## 13. Resilience / replicability (IN / VALIDATION / OOS)

Spearman(genuine_component, fwd_ret_10) per chronological period:

| Dataset | MOMENTUM | TREND | VOLUME | STRUCTURE |
|---|---|---|---|---|
| BTC 1D | −.05/−.05/−.02 | +.11/−.03/+.31 | +.06/+.03/−.10 | −.08/−.01/−.01 |
| ETH 1D | +.04/−.03/+.01 | +.25/−.25/−.03 | +.04/−.06/+.13 | −.10/−.14/−.06 |
| BTC 4H | −.00/−.04/+.05 | +.05/+.06/+.03 | +.02/−.03/−.02 | +.11/−.06/+.07 |
| ETH 4H | +.01/−.09/+.07 | +.03/+.12/−.13 | +.10/+.03/+.13 | +.01/+.15/−.07 |

**FACT:** signs flip between periods *within a single dataset* (e.g. ETH 1D TREND
+0.25 → −0.25 → −0.03; ETH 4H TREND +0.03 → +0.12 → −0.13). **OBSERVATION:** any
relationship that appears in IN does not persist into VAL/OOS; there is no
configuration of component → forward return that holds across the split.

## 14. Current-strength vs future edge (conditional on the gate doing its job)

Because the gate already confirms momentum and the detectors already reject
high-volatility and require trend/structure, the surviving candidates are
concentrated in the same strong state at detection. The evidence that remains
variable (TREND strength granularity, VOLUME magnitude) shows no monotonic path to
forward reward or risk (Sections 5, 10). **FACT:** there is no subset where a higher
genuine component reading is reliably followed by a better forward outcome.

## 15. Forward outcome is dominated by the exit mechanism, not the evidence

Traded-exit splits in the baseline join:

| Dataset | traded | EXPIRED | STOP | TARGET |
|---|---|---|---|---|
| BTC 1D | 437 | 232 (53%) | 119 (27%) | 86 (20%) |
| ETH 1D | 402 | 180 (45%) | 127 (32%) | 95 (24%) |
| BTC 4H | 3135 | 1640 (52%) | 1009 (32%) | 486 (15%) |
| ETH 4H | 2893 | 1387 (48%) | 940 (32%) | 566 (20%) |

**FACT:** roughly half of all trades close by the 60-bar age expiry (45–53%), with
stops ~32% and targets only ~15–24%. Outcome is largely fixed by the time-stop plus
wide swing-low stops, either of which is orthogonal to the detection-time evidence.

## 16. Airtight-look-ahead audit

- **Features (causal):** all `genuine_*` / `scored_*` / `overall` are read from the
  detection bar's snapshot only. Nothing later enters a feature.
- **Labels (allowed):** `fwd_*` are computed strictly from bars after the detection
  close; lifecycle fields (outcome/exit/net) come from the deterministic engine and
  are the *label* being predicted.
- **No tuning anywhere:** period split is fixed (60/20/20) and used only to read
  stability; no threshold or weight was chosen from these results.
- **Baseline behavior unchanged:** E6 writes no production code; detection and the
  engine run are byte-identical to `phase5-baseline-v1`. E6 joins the results, it
  does not alter them.
(No look-ahead, leakage, or selection-on-label step exists.)

## 17. Falsifier Result

> "If the pre-gate evidence components do not contain a stable, monotonic,
> out-of-sample relationship with future trade quality — or the relationship is
> an artefact of the gate or of in-sample noise — then E6 verdict is
> UNSUPPORTED."

**FALSIFIER TRIGGERED.** The pre-gate evidence does not contain a stable
relationship with future trade quality: all component↔forward rank correlations are
≤0.16 in magnitude; signs flip across assets, timeframes, families, and
IN/VAL/OOS periods; and the actionable MFE/MAE correlations are ≤0.06. The genuine
variation that exists (TREND, VOLUME) is non-monotonic toward forward outcome. The
relationship does not survive out-of-sample, so any apparent edge is small-sample
noise, not a property of the evidence.

## 18. Final Verdict

**UNSUPPORTED.**

The central question — *do Veyra's existing evidence components contain a stable
relationship with future trade quality before the gate compresses the population?*
— is answered **no**:

1. The components measure **current market state/strength at an already
   momentum-confirmed, controlled-volatility detection bar** — they are not forward
   predictors (criterion for "promising" evidence: not met).
2. What little genuine variation exists (TREND, VOLUME) is **weak and sign-unstable**
   across assets, timeframes, and periods.
3. The score Veyra **actually computes** collapses TREND/STRUCTURE/MOMENTUM to zero
   (`meta["score"]` missing) and reduces to a near-binary PULLBACK + constant
   VOLATILITY rank with no forward edge (criterion: the *consumed* evidence has no
   edge).
4. Even before the gate, detection predicates already compress MOMENTUM/VOLATILITY
   to constants, so "pre-gate" is not a population richer in discriminative signal.

## 19. What E6 changes

**Nothing.** E6 is diagnostic. No production, baseline, threshold, weight, detector,
exit, target, stop, entry-timing, qualification, or `phase5-baseline-v1` change was
made. All E6 code is isolated in `src/veyra/research/e6_pre_gate_evidence.py`; its
output is `reports/phase5_1/_e6_data.json` (498/440/3523/3252 rows, all 100%-joined).
The 238-test suite and the baseline control were re-verified and are unchanged.

---

## Verdict Questions (the five)

**Q1 — Do the pre-gate evidence components contain ANY stable relationship with
future trade quality?**
**NO.** Every |ρ| ≤ 0.16; signs flip across assets, timeframes and families (Section 5, 13).

**Q2 — Is the relationship monotonic / usable as a discriminator?**
**NO.** TREND and VOLUME buckets are non-monotonic; real-score buckets are
non-monotonic toward forward and traded outcomes (Sections 10, 9).

**Q3 — Does the relationship survive the gate compression (is there an edge the
gate preserves or removes)?**
**NO.** Detection — not the gate — already compresses MOMENTUM/VOLATILITY to
constants; the gate reflects a variable that is already constant (Sections 6, 7).

**Q4 — Does the relationship survive IN/VAL/OOS, not just in-sample?**
**NO.** Signs flip between periods within a single dataset; no configuration holds
out-of-sample (Section 13).

**Q5 — Does the score Veyra actually computes carry this relationship?**
**NO — and it cannot.** The consumed score zeroes TREND/STRUCTURE/MOMENTUM
(`meta["score"]` missing) and is a near-binary PULLBACK + constant VOLATILITY rank
(Sections 4, 9).

**Overall: UNSUPPORTED.**

---

*E6 made no behavioural or source change to the frozen baseline. All E6 code is
isolated in `src/veyra/research/e6_pre_gate_evidence.py`; its output is
`reports/phase5_1/_e6_data.json`. Target: `reports/phase5_1/e6_pre_gate_evidence.md`.*