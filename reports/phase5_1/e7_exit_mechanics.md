# Veyra Phase 5.1 — Experiment E7: Exit-Mechanics Attribution

Experiment ID: `e7-exit-mechanics-v1` | Frozen control: `phase5-baseline-v1`
Generated: 2026-09-03 | All trade metrics are **NET of fees + slippage**. Scores are ranking evidence, never probabilities.

> E7 asks whether Veyra's weak outcomes are caused by exit/position mechanics
> rather than the detection signal, and — if so — **which** mechanic. It is a
> **diagnostic attribution study**. No production code, baseline, parameter,
> detector, score, qualification, entry, stop, target, or timeout is changed and
> none is re-simulated. Counterfactuals are **descriptive labels** read from the
> already-recorded raw price path; they never feed a decision. Findings are
> labelled FACT / OBSERVATION / CORRELATION / HYPOTHESIS / UNKNOWN throughout.

---

## 1. Executive Summary

E7 reconstructs the frozen baseline on all four datasets and attributes every
completed trade's outcome to its exit reason against its full recorded price path.

The 60-bar timeout was **not** the primary problem, despite being the most common
exit. Attribution shows:

- **STOP is the damage.** STOP is ~30% of trades, win rate 10–18%, PF ≈ 0.03, and
  it is the **single largest negative contributor** (−380%…−2041% across datasets,
  48–64% of all absolute losses). Nearly half of stopped trades moved **favorable
  first** and were still stopped within ~2 bars — the stop converts viable signals
  into fast losses.
- **EXPIRED is frequent but net-POSITIVE** (mean +0.49%…+3.42%; it is the largest
  positive contributor on 1D). Its median is near zero (−0.1%…−0.4%): most expired
  trades sit at break-even while a minority of large winners supply the mean. On
  1D, EXPIRED contributes the *most* profit (+523%, +616%).
- **No reachable target on ~half of trades.** BREAKOUT (and by construction trades
  that survive only in BEAR) have **no target at all**, so they can only STOP or
  EXPIRED — forcing them into the two weakest mechanics.
- **Counterfactual:** of EXPIRED trades that *do* carry a target, 48–70% would
  have reached it within +60 bars after the timeout — the timeout does cut some
  winners short, but only for the ~half of the population that has a target.

**Verdict: UNSUPPORTED for changing the timeout; the evidence points at STOP
placement and (absent) target geometry as the real mechanical culprits.** No
production change is made. The smallest justified next experiment is a controlled
test of stop placement / target geometry, not the timeout.

## 2. Method (exactly what was measured)

Diagnostic measurement only; all decision behavior is the frozen
`phase5-baseline-v1`.

1. **One frozen baseline run per dataset** (`build_baseline_engine`, progressive,
   strategy `phase5-baseline-v1`) → `run.trades` (realized `exit_reason`,
   `net_return`, `holding_bars`, `entry_ts`, `exit_ts`, `side`, `regime`, `score`)
   and `run.setups`.
2. **One independent frozen detection pass** joins each trade to its setup's
   `interest_area` + `targets` on `(setup_type, detection_ts)` (100% join on all
   datasets) to recover the stop/target geometry `BacktestTrade` does not persist
   (using the same `Simulator._levels` semantics + entry-side target validation).
3. **Bar-range mapping:** `entry_ts`→index validated (holding = exit_idx −
   entry_idx, **0 mismatches** in 6,867 trades). The realized path `[entry..exit]`
   is walked from raw OHLC to compute:
   - stop/target geometry (entry→stop, entry→target, reward:risk);
   - `bars_to_MFE` / `bars_to_MAE` (timing of extremes) and favourable-first flag;
   - return at **fractions of the holding** (10/25/50/75/100% of held bars,
     close-based, same fees);
   - **counterfactual** after the realized exit: did the path later reach the
     target/stop, and the max future favourable/adverse excursion within +60 bars
     (descriptive only, never re-simulated, never a decision input).
4. **Chronological split** IN 60 / VAL 20 / OOS 20 by entry bar, used only to read
   stability (no tuning).

Features are read from the entry-bar snapshot or earlier; labels are computed from
realized paths. No future data enters any decision (there are no new decisions).

## 3. Datasets & Sizes

| Dataset | candles | setups | completed trades | 100% geometry-join | splits (trade n) |
|---|---|---|---|---|---|
| BTC/USDT 1D | 3,304 | 498 | 437 | 437/437 | IN 196 / VAL 136 / OOS 105 |
| ETH/USDT 1D | 3,304 | 440 | 402 | 402/402 | IN 237 / VAL 95 / OOS 70 |
| BTC/USDT 4H | 19,804 | 3,523 | 3,135 | 3,135/3,135 | IN 1879 / VAL 591 / OOS 665 |
| ETH/USDT 4H | 19,804 | 3,252 | 2,893 | 2,893/2,893 | IN 1646 / VAL 600 / OOS 647 |

## 4. Exit-reason attribution (per dataset, all periods)

| Dataset | overall | TARGET | STOP | EXPIRED |
|---|---|---|---|---|
| BTC 1D | +0.94%, win 50%, PF 1.51 | 19.7%, **+3.14%**, win 99% | 27.2%, **−3.20%**, win 13% | 53.1%, **+2.26%**, win 52% |
| ETH 1D | +1.17%, win 50%, PF 1.48 | 23.6%, +4.88%, win 99% | 31.6%, **−4.79%**, win 18% | 44.8%, **+3.42%**, win 46% |
| BTC 4H | +0.19%, win 42%, PF 1.16 | 15.5%, +2.62%, win 99% | 32.2%, **−1.94%**, win 10% | 52.3%, **+0.77%**, win 46% |
| ETH 4H | +0.12%, win 43%, PF 1.08 | 19.6%, +3.02%, win 98% | 32.5%, **−2.17%**, win 10% | 47.9%, **+0.49%**, win 43% |

**FACT — STOP is the only exit with a large negative mean everywhere.**
**OBSERVATION — frequency ≠ damage:** EXPIRED is most frequent (45–53%) yet has a
positive mean (esp. 1D); STOP is only ~30% yet is the deepest negative.
**OBSERVATION — EXPIRED median is near zero** (−0.1%…−0.4%): the positive EXPIRED
mean is driven by a minority of large winners; most expired trades close near
break-even.

## 5. Exit-reason attribution by split (avg net %)

| Dataset | period | overall | TARGET | STOP | EXPIRED |
|---|---|---|---|---|---|
| BTC 1D | IN / VAL / OOS | +0.7 / +0.8 / +1.5 | +3.3/+3.3/+2.5 | −3.5/−3.8/−1.8 | +2.5/+1.6/+2.7 |
| ETH 1D | IN / VAL / OOS | +2.8 / −1.2 / −1.0 | +5.3/+3.8/+4.5 | −4.9/−3.9/−5.5 | +6.1/−1.3/+0.3 |
| BTC 4H | IN / VAL / OOS | +0.4 / −0.1 / −0.2 | +2.7/+2.6/+2.4 | −2.1/−1.7/−1.7 | +1.3/−0.0/−0.1 |
| ETH 4H | IN / VAL / OOS | +0.1 / +1.2 / −0.8 | +3.2/+2.8/+2.7 | −2.2/−1.7/−2.5 | +0.3/+2.4/−1.0 |

**FACT — STOP is negative in every dataset × split** (range −1.7…−5.5%); TARGET is
positive in every split. **OBSERVATION — the 4H VAL/OOS weakness concentrates in
EXPIRED** (goes near zero/negative), not in STOP which is stable-negative.

## 6. Largest damage contributor (net × count)

| Dataset | TARGET | STOP | EXPIRED | STOP % of all losses |
|---|---|---|---|---|
| BTC 1D | +270% | **−381%** | +524% | 49% |
| ETH 1D | +464% | **−608%** | +616% | 64% |
| BTC 4H | +1271% | **−1954%** | +1270% | 55% |
| ETH 4H | +1707% | **−2041%** | +679% | 48% |

**FACT — STOP is the largest single negative contributor in all four datasets and
48–64% of all absolute losses.** EXPIRED and TARGET are both net-positive
contributors. **Q1 answer: STOP**, not the timeout.

## 7. Time-stop forensics (EXPIRED)

Return at fractions of the holding (mean net, %):

| Dataset | 10% | 25% | 50% | 75% | 100% |
|---|---|---|---|---|---|
| BTC 1D | +0.80 | +0.62 | +0.84 | +1.95 | +2.24 |
| ETH 1D | +1.81 | +2.55 | +2.93 | +3.73 | +3.45 |
| BTC 4H | +0.24 | +0.35 | +0.52 | +0.88 | +0.78 |
| ETH 4H | +0.15 | +0.25 | +0.47 | +0.88 | +0.49 |

Improving vs stagnant vs deteriorating (Δ return 50%→100%), + MFE timing:

| Dataset | improving | stagnant | deteriorating | MFE in 1st half | profitable at expiry |
|---|---|---|---|---|---|
| BTC 1D | 52% | 12% | 37% | 58% | 51% |
| ETH 1D | 42% | 3% | 55% | 59% | 46% |
| BTC 4H | 32% | 30% | 38% | 61% | 46% |
| ETH 4H | 36% | 19% | 46% | 62% | 43% |

**FACT — the EXPIRED path is MIXED, not uniformly "cutting winners short".** ~1/3
improving, ~1/3–1/2 deteriorating, rest stagnant. **FACT — MFE peaks in the first
half of the hold (58–62%)** and the path then drifts back toward the entry. **Q3
answer: mixed** (on 4H roughly a third each; deteriorating slightly more common).

## 8. Time-stop damage — improving vs stagnant vs deteriorating

The fraction-return paths above show the mean is positive and growing to 75% then
flat/down at 100%, while the median stays near zero. **OBSERVATION:** the common
EXPIRED pattern is "favorably excursionary early, then flat-to-slightly-negative
to expiry"; a minority carry large winners (which is why mean > median). This is
consistent with the timeout mostly *closing unresolved/flattening trades* (Profile C)
with a meaningful minority where it *cuts winners short* (Profile B) and a similar
minority where it is *protective* (Profile D).

## 9. Stop-loss forensics

| Dataset | avg hold (bars) | stopped ≤2 bars | favorable-first | bars_to_MAE | entry→stop | entry→target | RR |
|---|---|---|---|---|---|---|---|
| BTC 1D | 2.3 | 72% | 49% | 2.3 | 3.3% | 5.8% | 1.77 |
| ETH 1D | 3.5 | 65% | 50% | 3.5 | 5.0% | 6.3% | 1.26 |
| BTC 4H | 3.7 | 61% | 55% | 3.7 | 1.9% | 4.6% | 2.41 |
| ETH 4H | 3.5 | 61% | 54% | 3.5 | 2.2% | 4.8% | 2.17 |

**FACT — 49–55% of stopped trades moved favorable FIRST and were still stopped;
61–72% were stopped within 2 bars.** The MAE (adverse) extreme is reached at ~2.3–3.7
bars (≈ the hold). **OBSERVATION — the stop placement is systematically tight
relative to the realization:** signals that first move favorable are killed within a
couple of bars. **Q4 answer:** stopped trades are **not** immediately wrong — ~half
are "favorable first, then stopped" (Classifier B).

## 10. Target forensics + target availability

| Dataset | usable target (post-entry) | detection gave target | TARGET exit % | entry→target | entry→stop | RR |
|---|---|---|---|---|---|---|
| BTC 1D | 47% | 57% | 19.7% | 3.3% | 12.3% | **0.62** |
| ETH 1D | 53% | 65% | 23.6% | 5.1% | 15.7% | **0.59** |
| BTC 4H | 56% | 71% | 15.5% | 2.8% | 5.2% | 1.00 |
| ETH 4H | 57% | 71% | 19.6% | 3.2% | 6.7% | 0.94 |

**FACT — 43–53% of completed trades have NO usable target** (BREAKOUT and
BEAR-survivors have none by construction). **FACT — where a target exists, the
reward:risk is ≤ 1.00 (≈0.6 on 1D):** the target is *not farther than the stop* — on
1D it is **closer** (3.3% vs 12.3% — so a targeted win pays ~1/4 of what one stop
costs). **Q5 answer:** targets are **not realistically rewarding** — they are either
absent (~half) or too close (RR ≈ 0.6–1.0) to overcome the stop cost under a 98%
TARGET win-rate that only 15–24% of trades ever reach.

## 11. Entry → MFE / MAE timing by exit reason

| Dataset | TARGET (t_mfe/t_mae) | STOP (t_mfe/t_mae) | EXPIRED (t_mfe/t_mae) |
|---|---|---|---|
| BTC 1D | 1.7 / 0.4 | 0.8 / 2.3 | 5.5 / 4.2 |
| ETH 1D | 2.0 / 0.6 | 1.6 / 3.5 | 4.7 / 3.2 |
| BTC 4H | 5.2 / 1.0 | 1.6 / 3.7 | 4.0 / 3.6 |
| ETH 4H | 4.4 / 1.0 | 1.0 / 3.5 | 4.3 / 3.9 |

**FACT — the MAE for STOP arrives late (≈ the hold) because the price first moves
the right way, THEN reverses into the stop.** For TARGET, the MFE arrives ≈ holding
(the market trends to the target). **Q7 timing finding:** stopped trades "move
against us most severely just before the stop", but favor us early; EXPIRED trades
"favor us early, then require prolonged flat holding". This is an **entry/stop**
feature, not an entry-timing failure.

## 12. Exit × setup family (pooled 4 datasets)

| Family | n | overall | TGT% (avg) | STOP% (avg) | EXP% (avg) | no-target |
|---|---|---|---|---|---|---|
| BREAKOUT | 2082 | +0.52% | **0%** | 41% (−2.76%) | 59% (+2.79%) | **100%** |
| BREAKOUT_RETEST | 1623 | +0.36% | 8.7% (+3.22%) | **61% (−1.44%)** | 31% (+3.13%) | 52% |
| PULLBACK | 1621 | +0.12% | 36.5% (+2.98%) | 7.2% (−4.41%) | 56% (−1.15%) | 4% |
| TREND_CONTINUATION | 1541 | −0.03% | 32.4% (+2.99%) | 15.5% (−2.90%) | 52% (−1.05%) | 3% |

**FACT — BREAKOUT has zero targets; BREAKOUT_RETEST 52% no-target**, so these two
families (≈54% of all trades) are forced into STOP/EXPIRED only. BREAKOUT_RETEST
stops 61% of the time. **FACT — `RANGE_REJECTION` produces 0 completed trades** on all four datasets. It
did not break (no error); it simply compresses to zero completed trades under the
frozen baseline, consistent with the PRD design where it carries no target geometry
(like BREAKOUT) and rarely produces a reachable exit. Only
BREAKOUT / BREAKOUT_RETEST / PULLBACK / TREND_CONTINUATION supply completed trades.
**OBSERVATION — one exit/cost rule does NOT fit all families:**
geometry (and target availability) is family-specific, and the no-target families
carry the stop damage.

## 13. Exit × timeframe & asset

| Slice | n | overall | TGT% | STOP% | EXP% | EXP mean/med |
|---|---|---|---|---|---|---|
| 1D | 839 | +1.05% | 21.6% | 29.3% | 49.1% | +2.76 / −0.22 |
| 4H | 6028 | +0.15% | 17.5% | 32.3% | 50.2% | +0.64 / −0.26 |
| BTC | 3572 | +0.28% | 16.0% | 31.6% | 52.4% | +0.96 |
| ETH | 3295 | +0.25% | 20.1% | 32.4% | 47.6% | +0.83 |

**FACT — 60 bars has DIFFERENT meaning per timeframe (FACT):** on 1D, 60 bars =
60 days and EXPIRED mean is strongly positive (+2.76%); on 4H, 60 bars = 10 days and
EXPIRED mean is near +0.6% with negative median. **OBSERVATION — the timeout is NOT
"wrong" merely because clock time differs;** the problem is that on 4H the expired
bucket's median is negative (timeout closes slightly-losing/flat trades), while on
1D it closes profitable drift. **Q2 answer:** 4H VAL/OOS EXPIRED goes negative, but
STOP is the consistently negative exit on both timeframes.

## 14. Score × exit

| Dataset | bucket | overall | TGT% | STOP% | EXP% |
|---|---|---|---|---|---|
| BTC 1D | <20 / 20-39 | +0.50 / +1.65 | 27 / 8 | 23 / 33 | 50 / 58 |
| BTC 4H | <20 / 20-39 | +0.04 / +0.42 | 19 / 10 | 30 / 35 | 51 / 55 |
| ETH 4H | <20 / 20-39 | +0.07 / +0.20 | 23 / 14 | 30 / 37 | 48 / 48 |

**FACT — higher-score trades do NOT get better mechanics:** the 20–39 bucket has a
**lower** TARGET% and a **higher** STOP% than <20 in every dataset. **OBSERVATION —
confirms E5/E6:** the (already weak, PULLBACK-driven) score does not select for
different, better, mechanical failure modes.

## 15. Regime × exit

| Regime | BTC 1D | ETH 4H (rep.) |
|---|---|---|
| BULL | TGT 25 / STOP 22 / EXP 53 | TGT 25 / STOP 28 / EXP 47 |
| BEAR | TGT **0** / STOP 46 / EXP 54 | TGT **0** / STOP 49 / EXP 51 |

**FACT — in BEAR there are ZERO target exits** (only BREAKOUT-family — which lack
targets — survive), so BEAR trades are STOP/EXPIRED only and STOP% ≈ 46–49%.
**OBSERVATION — BEAR is stop-dominated; this is a *target-availability* artifact,
not a reason to reject the regime hypothesis** (E2 already falsified hard
regime-filtering). No regime filter is recommended.

## 16. MAE/MFE path profiles (descriptive thresholds fixed before analysis)

Profiles (thresholds: ≤3 bars & |net|>0.5% = FAST; ≥15 bars & |net|>0.5% = SLOW;
|net|<0.5% = FLAT; favorable-first+net<0 = FAVORABLE-THEN-REVERSED;
adverse-first+net>0 = ADVERSE-THEN-RECOVERED):

| Dataset | FAST WIN | FAST LOSS | FAV-THEN-REV | ADV-THEN-REC | FLAT | SLOW |
|---|---|---|---|---|---|---|
| BTC 1D | 24% | 22% | 19% | 18% | 11% | 7% |
| BTC 4H | 12% | 23% | 21% | 21% | 18% | 5% |
| ETH 4H | 17% | 24% | 23% | 19% | 13% | 4% |

**FACT — ~40–44% of all trades REVERSE** (favor-then-reversed + adverse-then-
recovered), and ~22–24% are fast losses. **OBSERVATION — the tight early stop is the
driver of the fast-loss / reversal clusters:** a large share of trades would have
survived their early wiggle had the stop been wider (they first moved favorable).

## 17. Counterfactual — what EXPIRED trades did after the timeout (descriptive)

| Dataset | EXPIRED n | later hit target | later hit stop | of EXPIRED with target: later hit target |
|---|---|---|---|---|
| BTC 1D | 232 | 17% | 46% | **48%** |
| ETH 1D | 180 | 32% | 47% | **70%** |
| BTC 4H | 1640 | 32% | 57% | **63%** |
| ETH 4H | 1387 | 28% | 53% | **57%** |

**FACT — for EXPIRED trades WITH a target, 48–70% would have reached the target
within +60 bars after the timeout** (and ~53–57% eventually hit the stop in that
window). **FACT — these are descriptive labels; they are NOT a re-simulation and do
NOT prove a longer hold would net profit** (many later-hit-the-target trades also
later hit the stop, or the extra hold carries more risk/fees). **OBSERVATION — the
timeout does terminate some viable winners early, but only in the ~half of trades
that have any target**, and the counterfactual is not controlled.

## 18. Signal vs mechanics decomposition

```
DETECTION  -> BREAKOUT/BREAKOUT_RETEST (54% of trades) carry NO or a tight target.
ENTRY      -> next-open fill; stop sits just beyond the broken level (tight).
EARLY PATH -> ~half of stopped trades first move FAVORABLE, then are stopped <=2 bars.
TARGET     -> unreachable for ~half (no target); where present RR<1 on 1D.
TIMEOUT    -> frequent, near-neutral-to-positive; a minority of winners cut short.
STOP       -> ~30% of trades, win ~10-18%, 48-64% of ALL losses.  <-- largest damage
```

**Q6 answer: the dominant mechanical problem is the STOP, in combination with the
absent/too-close TARGET geometry of the BREAKOUT families** — i.e. STOP + TARGET
geometry (a COMBINATION), not the timeout.

## 19. 4H special investigation

| Metric | BTC 4H | ETH 4H |
|---|---|---|
| timeout% | 52% | 48% |
| timeout mean / median | +0.77 / −0.17 | +0.49 / −0.41 |
| stop% / stop mean | 32% / −1.94% | 32% / −2.17% |
| target% / target mean | 16% / +2.62% | 20% / +3.02% |
| avg hold | 6.3 | 6.2 |
| no-target | 44% | 43% |

Family × exit (avg net %):

| Family | BTC 4H | ETH 4H |
|---|---|---|
| BREAKOUT | T0 / S39(−1.9) / E61(+2.6) | T0 / S43(−2.7) / E57(+2.0) |
| BREAKOUT_RETEST | T8 / S59(−1.4) / E33(+2.3) | T9 / S62(−1.4) / E29(+3.5) |
| PULLBACK | T32 / S8(−4.3) / E60(−1.1) | T38 / S7(−4.1) / E55(−1.4) |
| TREND_CONTINUATION | T29 / S17(−3.0) / E55(−0.8) | T36 / S15(−2.5) / E49(−1.4) |

**FACT — the 4H weakness is MIXED but STOP-led:** STOP losses are stable-negative
(−1.4…−4.3%), while the EXPIRED bucket for the *targeted* families (PULLBACK,
TREND_CONTINUATION) turns slightly negative (−0.8…−1.4%) on 4H — these are the only
places the timeout itself is a mild drag. BREAKOUT-family timeout is net-positive.
**Q2 (do 4H trades suffer the timeout most?): NOT primarily** — STOP (32%) and
no-target geometry dominate; the timeout is only mildly negative for the two
targeted families.

## 20. Leakage / integrity audit

- **No future data in any feature.** Features are from the entry bar / earlier; all
  realized-path metrics (bars_to_MAE/MFE, fraction returns, counterfactual) are
  **labels** computed after the trade closed. No counterfactual feeds any decision.
- **No baseline, no threshold, no weight, no detector, no entry, no stop, no
  target, no timeout changed.** No VAL/OOS tuning. No alternate-exit simulation was
  used to select any value.
- **Counterfactuals are descriptive only** — read from the recorded +60-bar price
  window after the realized exit; they never re-run the strategy.
- **Baseline reconstructed byte-identically:** trade counts and exit splits match
  E6 exactly (BTC 1D 437 / ETH 1D 402 / BTC 4H 3135 / ETH 4H 2893; EXPIRED/STOP/
  TARGET counts identical to E6). The ts→index mapping had **0 mismatches** across
  6,867 trades. All geometry joins are 100%.
- Test suite re-run: **238 passed**. Production files untouched (pre-E7 timestamps).

## 21. Final Verdict

### Q1 — Which exit reason contributes most to poor expectancy?
**STOP.** ~30% of trades, win 10–18%, PF≈0.03, the only exit with a large negative
mean in every dataset × split, and 48–64% of all absolute losses.

### Q2 — Are 4H trades disproportionately damaged by the 60-bar age limit?
**No — not primarily.** STOP (32%) and the absent/close-target geometry dominate.
The timeout is only mildly negative for the *targeted* families (PULLBACK,
TREND_CONTINUATION) and is net-positive for the BREAKOUT families; overall EXPIRED
mean is positive on all four datasets.

### Q3 — Do expired trades tend to be stagnant, improving, deteriorating, or mixed?
**MIXED.** Roughly a third improving, a third deteriorating (a little more on 4H),
with 3–30% stagnant. MFE peaks in the first half of the hold (58–62%) then the path
flattens; median net at expiry is ~0 while a minority of large winners lift the mean.

### Q4 — Are stopped trades generally immediately wrong, or do many first move favorably?
**Many first move favorably.** 49–55% of stopped trades moved favorable first, and
61–72% were stopped within 2 bars. These are "favorable first, then reversed into a
tight stop," not "immediately wrong from the open."

### Q5 — Are targets realistically reachable under the current entry/stop geometry?
**No — for two independent reasons.** (i) ~half of trades (BREAKOUT + BEAR
survivors) have **no target at all**, so they can only stop/expire; (ii) where a
target exists, the reward:risk is ≤1.00 (≈0.6 on 1D) — the target is not farther
than the stop, so a 98%-win-rate target pays too little to fund the stop losses.

### Q6 — Is the dominant problem signal, entry, stop, target, timeout, combination, or unknown?
**COMBINATION of STOP placement and TARGET geometry** (the BREAKOUT-family
no-target/tight-target design), which forces ~half of trades into the weakest exit
mechanics. The **timeout is not the dominant problem**. The detection signal itself
(E5/E6: no stable forward edge) remains a separate, prior concern.

### Q7 — Is there enough evidence to justify changing ANY exit mechanic?
**UNSUPPORTED** as a production change now. Attribution is clear that the timeout is
not the culprit and that STOP/target geometry is, but E7 is diagnostic: selecting a
new stop/target would require a *controlled, pre-registered* experiment (E7 cannot
recommend a specific value, and VAL/OOS must not be tuned). There is nevertheless a
**robust, forward-consistent basis to justify investigating STOP/target geometry**
as the next experiment — but not to change production on the strength of this
attribute study alone.

---

## Final framed outputs

**1. One-sentence root cause.** The STOP mechanic — a tight, near-the-entry stop
that triggers within ~2 bars on nearly half the trades *after they first moved
favorable*, combined with the failure of ~half of trades (BREAKOUT-family, BEAR
survivors) to carry any usable target (and RR≈0.6–1.0 where they do) — converts
viable signals into frequent fast losses, which is the largest mechanical
degradation; the 60-bar timeout is frequent but net-positive and is **not** the
culprit.

**2. Top 5 confirmed mechanical problems.**
1. **STOP is the largest damage source** — win rate 10–18%, PF≈0.03, 48–64% of all
   losses, negative in every dataset × split (FACT).
2. **~half of trades have no usable target** (BREAKOUT 100%, BREAKOUT_RETEST 52%,
   total ~43–53% of all trades) → they can only STOP or EXPIRED (FACT).
3. **Where a target exists, RR ≈ 0.6–1.0** — the target is not farther than the
   stop, so target wins cannot fund the stop losses (FACT).
4. **Stops fire within ≤2 bars on 61–72% of stopped trades, after a favorable
   first move in ~half** — the stop placement is incompatible with the entry's
   normal early wiggle (FACT).
5. **Higher scores master worse mechanics** — 20–39 bucket has lower TARGET%, higher
   STOP% (FACT; reinforces E5/E6 no-edge).

**3. Top 3 remaining hypotheses.**
1. A wider stop (before any new level) would let favorable-first signals resolve and
   cut the fast-loss cluster — untested (HYPOTHESIS).
2. Giving BREAKOUT-family setups a reachable target (RR>1) would convert their
   currently stop/expire-only population into a targetable one — untested
   (HYPOTHESIS).
3. On 4H, the slightly-negative EXPIRED bucket for the targeted families would
   improve if holding were shorter — untested (HYPOTHESIS; E7's data shows median
   EXPIRED ≈ 0, mean +, so this is the weak point of the set).

**4. Largest source of performance degradation.** The **STOP** mechanic: its losses
(−381 to −2041 net-units, 48–64% of all losses) directly reduce a strategy that is
otherwise positive (TARGET and EXPIRED are both net-positive every dataset).

**5. Whether 4H needs different mechanics.** Partially. 4H does **not** need a
different timeout (it is mild); it does need the **same stop/target-geometry
fix** as 1D, and the 4H VAL/OOS EXPIRED dip for the targeted families is the one
place a shorter hold might matter — but this is a hypothesis, not a requirement.

**6. Whether any exit change is justified.** **UNSUPPORTED for production now.**
Evidence robustly identifies STOP/target geometry as the mechanical culprit and
exonerates the timeout, which is sufficient to justify a *controlled next
experiment*, but not to change production parameters without that pre-registered
test.

**7. Smallest next experiment.** **E8 — Stop-Placement & Target-Geometry Attribution
(controlled):** a single, pre-registered, per-family experiment that (a) re-uses this
E7 pathway to measure, for the favourable-first stopped trades, the distance from the
entry at which the *favorable excursion reversals* occur (i.e., where a wider stop
would actually sit), and (b) reports for the no-target families what entry→target
distance would have been hit in the realized path — both purely diagnostic, no
baseline change, to size a *later* conditional-geometry test.

**8. Explicit final verdict.** **UNSUPPORTED** for changing any exit mechanic in
production based on E7 alone. The 60-bar-timeout hypothesis is falsified as the
primary cause; the dominant mechanical problem is STOP + target-geometry. Any change
requires the controlled next experiment (E8), per verdict discipline.

---

*E7 made no behavioural or source change to the frozen baseline. All E7 code is
isolated in `src/veyra/research/e7_exit_mechanics.py`; its output is
`reports/phase5_1/_e7_data.json` (437/402/3135/2893 trades, all 100%-geometry-joined).*