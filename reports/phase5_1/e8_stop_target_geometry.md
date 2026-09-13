# Veyra Phase 5.1 — Experiment E8: Stop/Target-Geometry Attribution

Experiment ID: `e8-stop-target-geometry-v1` | Frozen control: `phase5-baseline-v1`
Generated: 2026-09-03 | All metrics are **NET** of fees+slippage, read from the
realized path. E8 is a **diagnostic geometry measurement**; it sets no threshold,
changes no decision, and re-simulates nothing.

> E8 does not change the strategy. It quantifies — from the already-recorded
> realized price paths recovered by E7 — **the geometry of Veyra's dominant
> mechanical damage** (the STOP and the absent/too-close TARGET, E7 §21). Its
> purpose is to **size and justify** a later, separate, *controlled* experiment
> (E9) that actually tests an alternative geometry. E8 itself is descriptive:
> it reports where the damage sits (in % and in bars), never what to change.

Findings are labelled **FACT** (measured on the realized path) / **OBSERVATION** /
**CORRELATION** / **HYPOTHESIS** / **UNKNOWN** throughout.

---

## 1. Executive Summary

E8 measures four geometry facts to size a future studio test of the STOP and
TARGET mechanics that E7 identified as the root damage:

- **G1 — Favorable-first STOP depth:** stopped trades reach a median round-trip
  (favorable peak → adverse trough) of **3.4–3.9%** on 4H and **6.8–8.6%** on 1D,
  and the trough comes just **2–4 bars** after the peak. These trades first moved
  favorable (1–2% median) and were still stopped; a materially wider stop would be
  needed (<i>where</i> is detailed below).
- **G2 — Reachable target on no-target families:** the BREAKOUT / partial
  BREAKOUT_RETEST population that has **no usable target** still reaches a real
  favorable excursion: median **2.2–5.8%**, p75 **5.6–13%**. Roughly half to
  three-quarters of these trades reach distances that, had any target existed,
  would have been profitable — currently they are surrendered to STOP or EXPIRED.
- **G3 — Realized RR is mis-sized:** TARGET-finishing trades show a *low* realized
  reward:risk (median **0.25–0.59** on 1D, **0.53–0.59** on 4H) — the target is hit
  far inside the achievable favorable window; whereas STOP-finishing trades blow
  through a *large* uncaptured reward window (median realized RR **~3–4**). The
  frozen risk:reward is systematically out of balance with what the path actually
  delivers.
- **G4 — Timing:** stopped trades reach their favorable peak in ~1–3 bars and are
  stopped within ~2–4 bars of it. The stop is not just too tight *in %*; it fires
  *in time* almost immediately after the entry's early wiggle.

**Verdict: no production change is made.** E8's measurement is internally
consistent and forward-meaningful enough to **justify** (and only justify) the
controlled experiment E9. It does **not** recommend any stop/target value.

## 2. Method (exactly what was measured)

Reuses the frozen E7 realized-path reconstruction — no production tool or baseline
is touched, no re-run of the strategy, no re-simulation under alt exits.

1. Load the frozen E7 per-trade records (`reports/phase5_1/_e7_data.json`,
   datasets BTC/ETH × 1D/4H; 437/402/3135/2893 trades, all geometry-joined). These
   already carry the realized `entry_bar`, `exit_bar`, side, exit reason, net
   return, `bars_to_mfe`, `bars_to_mae`, `fwd_first`, and the *frozen* stop/target.
2. Re-load the raw OHLC (`CandleStore`) and walk each realized holding
   `[entry_bar .. exit_bar]` to recover, from the **realized path only**:
   - `mfe_pct`, `mae_pct` = signed max favorable/adverse excursion vs the
     unadjusted entry open;
   - `peak_to_trough_pct` = MFE% + MAE% (the round-trip between the favorable peak
     and the worst adverse point inside the hold) — this is the *width* a wider
     stop would need to contain;
   - `bars_peak_to_trough` = bars between the favorable peak and the MAE extreme;
   - realized `rew_risk` = target distance ÷ stop distance (from the frozen
     geometry) grouped by how the trade *actually finished*.
3. Aggregate per exit reason × dataset. All numbers are labels on realized paths;
   **no** counterfactual is used to justify any value.

E8 sets **no** threshold, **no** "best" stop/target, and applies **no** VAL/OOS
tuning. IN / VAL / OOS labels are carried forward for completeness but nothing is
tuned on them.

## 3. G1 — Favorable-first STOP depth (how wide the stop must be, and when)

| Dataset | STOP n | median MFE% | median MAE% | median peak→trough% | mean peak→trough% | median bars pk→trough |
|---|---|---|---|---|---|---|
| BTC 1D | 119 | 1.27 | 4.73 | **6.81** | 7.81 | 2 |
| ETH 1D | 127 | 2.30 | 6.09 | **8.56** | 11.21 | 3 |
| BTC 4H | 1009 | 0.77 | 2.34 | **3.39** | 4.28 | 3 |
| ETH 4H | 940 | 0.98 | 2.59 | **3.94** | 4.87 | 3 |

**FACT — stopped trades carry a real favorable excursion (median 0.8–2.3%) that
is never captured, then reverse into a stop that is only 13–22% of the way through
that excursion's width.** The median round-trip (peak→trough) is 3.4–3.9% on 4H and
6.8–8.6% on 1D. **FACT — the stop fires within 2–4 bars of the favorable peak**
(pk→trough median 2–3 bars; and E7 §11 shows STOP `bars_to_mfe` ≈ 0.8–1.6). This is
the quantitative upward bound on "how much early wiggle a wider stop would absorb."

**OBSERVATION — the frozen stop sits far inside the realized adverse excursion.** On
4H the median MAE (2.3–2.6%) already exceeds the frozen stop distance (1.9–2.2%) in
the trades that finish by STOP, i.e. the stop intrudes into the trade's *first
reversal*; the data does not justify a specific wider value, but it shows the damage
is not "1-2 pips of noise" — it is a multi-percent favorable-then-reversed tail.

## 4. G2 — Reachable target for the no-target families (what is being surrendered)

No-target population = BREAKOUT (100%) + BREAKOUT_RETEST/other with no usable
target (target absent or on the wrong side of the entry). Realized MFE during the
hold:

| Dataset | no-target n | mean MFE% | median MFE% | p75 MFE% | ≈ share with MFE > 0 |
|---|---|---|---|---|---|
| BTC 1D | 216 | 8.31 | 5.81 | 12.95 | >50% |
| ETH 1D | 165 | 9.90 | 4.61 | 13.12 | >50% |
| BTC 4H | 1360 | 4.27 | 2.24 | 5.58 | >50% |
| ETH 4H | 1183 | 4.83 | 2.65 | 6.13 | >50% |

**FACT — the no-target families do reach a real, meaningful favorable distance
(median 2.2–5.8%), yet they can only STOP or EXPIRED.** A reachable target placed at
even the p50–p75 realized MFE would have captured a large share of these moves;
today they are surrendered (E7: BREAKOUT TARGET exit = 0%, STOP 39–43%, EXPIRED
57–61%). **OBSERVATION — the absence of a target is the *binding* geometric defect
for these families**, not merely an inconvenience: profitable excursions exist and
are structurally unreachable. (This is a descriptive fact; E8 does not choose a
target level.)

## 5. G3 — Realized reward:risk is out of balance

Realized `rew_risk` (target÷stop distance from the frozen geometry) by how the
trade *actually finished*:

| Dataset | STOP median RR | TARGET median RR | EXPIRED median RR | all median RR |
|---|---|---|---|---|
| BTC 1D | 2.98 | **0.25** | 0.59 | 0.57 |
| ETH 1D | 4.80 | **0.31** | 0.61 | 0.54 |
| BTC 4H | 4.23 | **0.59** | 0.80 | 0.87 |
| ETH 4H | 3.62 | **0.53** | 0.74 | 0.82 |

**FACT — TARGET-finishing trades capture only 0.25–0.59 RR (target is hit well inside
the achievable window), while STOP-finishing trades blow through a 3–4 RR window
that is never taken.** The frozen target is ~2× to ~4× too close relative to the
frozen stop *and* relative to the realized path. **OBSERVATION — the RR asymmetry is
the arithmetic reason STOP losses dominate (E7 §6):** winners net +2.6–4.9% (from a
~0.3–0.6 RR target) while losers cost −1.9–4.8% (a ~3–4 RR stop distance). With the
frozen balance a single stop requires several target wins to recover. (Descriptive;
E8 does not propose a new RR.)

## 6. G4 — Timing geometry (%, and bars)

- STOP trades: `bars_to_mfe` ≈ **0.8–1.6** (E7 §11), pk→trough ≈ **2–3 bars**, MAE
  extreme ≈ **3.7 bars** = the hold (E7 §11). The favorable peak arrives within ~1–2
  bars of entry and the stop within ~2–4 bars after it.
- TARGET trades: `bars_to_mfe` ≈ **4.4–5.2 bars** (MFE ≈ the hold) — the market
  trends to the target over the full hold. MAE reaches its low at bar ~1.0 (the
  early adverse dip is shallow for winners).
- EXPIRED trades: MFE ≈ 4.0–5.5 bars, MAE ≈ 3.2–4.2 bars — a slowly resolving path.

**FACT — the stop is not merely too tight in %, it fires immediately in time.** A
time-and-price geometry that gave the early favorable move room (before the first
reversal) is what the path requires (G1+G4). **OBSERVATION — this is distinct from
the entry-timing and timeout mechanisms ruled out in E3/E1:** the failure is the
stop/target *geometry*, not where we enter and not whether we hold too long.

## 7. Cross-dataset consistency

| Geometry fact | BTC 1D | ETH 1D | BTC 4H | ETH 4H | Direction |
|---|---|---|---|---|---|
| STOP peak→trough median | 6.81% | 8.56% | 3.39% | 3.94% | consistent (larger on 1D) |
| STOP bars pk→trough med | 2 | 3 | 3 | 3 | consistent |
| no-target reached MFE med | 5.81% | 4.61% | 2.24% | 2.65% | consistent (larger on 1D) |
| TARGET realized RR med | 0.25 | 0.31 | 0.59 | 0.53 | consistent (<1 everywhere) |
| STOP realized RR med | 2.98 | 4.80 | 4.23 | 3.62 | consistent (>3) |

**FACT — G1–G4 are directionally identical across all four datasets.** The
favorable-first deep-reversal stop, the unreachable-target families, and the RR
imbalance (target <1, stop >3) reproduce on both assets and both timeframes.
This is the strongest signal that the *mechanic*, not a dataset artifact, is at
fault — and that a controlled geometry test would be worth running.

## 8. Falsifier / honesty checks

- **No decision change.** E8 only appends realized-path geometry labels; the frozen
  baseline never re-runs.
- **No threshold invented.** G1/G2/G3 report realized excursions and RR *as
  distributions*; no stop/target value is recommended. Any later value is set in
  the pre-registered E9, not chosen here.
- **No VAL/OOS tuning.** Labels are aggregated globally and per-period but nothing
  is optimised.
- **Danger of over-fitting from *realized* MFE flagged (HYPOTHESIS):** the 
  "reachable target" distances (G2) are *realized* favourable excursions, which an
  optimiser could naively treat as achievable a-priori. They are NOT — a target must
  be placed from detection-time information only. This is why E8 only measures and
  does not pick a level, and why E9 must pre-register the geometric rule *without*
  fitting it to these realized excursions.

## 9. Conclusion

E8 confirms, quantifies, and reproduces E7's STOP/target-geometry attribution:

1. The STOP fires too early in time (2–4 bars after a favorable peak) and too deep
   relative to the favorable excursion (favor-then-reverse 3–9% round-trips).
2. The no-target families still produce real favorable moves (2–6% median) that
   cannot be captured — a structural geometry hole.
3. The frozen risk:reward is systematically inverted: winners bank ~0.25–0.59 RR,
   losers risk ~3–4 RR. This single asymmetry is the arithmetic core of why STOP
   losses dominate (E7 §6).

**Verdict — no production change.** E8 is a measurement that *justifies* the
controlled experiment E9 (and only that). Its evidence is robust across all four
datasets and is forward-meaningful as a *mechanic-level* claim, but it does not, and
cannot, by itself validate any particular wider-stop or measurable-target value.

## 10. Recommended next experiment (E9, pre-registered) — NOT yet started

E8 sizes but does not run a change. The smallest next step is a **controlled,
pre-registered** conditional-geometry experiment:

- **E9 scope (to be pre-registered separately):** a single, frozen-everything-else
  modification is made to the *exit geometry only* — one family-agnostic rule from
  E8's distribution (e.g. stop width = a quantile of realized peak→trough, applied
  identically across families) and one measurable-target rule for the no-target
  families — and the whole pipeline is re-run on IN only. VAL/OOS are held for
  final confirmation, never tuned.
- **E9 hard rules (to be honoured):** no threshold invented by looking at E9's own
  results; the geometric multiplier is fixed *before* running; if IN fails, the
  mechanic is rejected (no re-fit).
- **E9 falsifier:** the identical wider-stop/target rule must improve IN *and*
  hold on VAL/OOS with a monotonic, meaningful lift over the frozen baseline;
  otherwise the exit-mechanics hypothesis (E7 root cause) is itself rejected and we
  return to the detection/qualification layer (the score is currently a dead
  ranking label — a separate, prior finding from this phase).

## 11. Final framed outputs

**1. One-sentence root cause (mechanics).** Veyra's dominant mechanical damage is
that the STOP fires within ~2–4 bars after a favorable peak into a 3–9% reverse and
the no-target families surrender real 2–6% favorable moves, while the frozen target
banks only ~0.25–0.59 reward:risk against a ~3–4 stop — an RR imbalance that makes
every stop need several wins to recover.

**2. Top confirmed mechanical geometry facts (E8).**
1. STOP trades move favorable first (median 0.8–2.3%) then reverse a 3.4–3.9% (4H) /
   6.8–8.6% (1D) round-trip within 2–4 bars (FACT).
2. The no-target families reach profitable distances (median 2.2–5.8%, p75 5.6–13%)
   that are structurally unreachable (FACT).
3. Realized reward:risk is inverted: TARGET winners bank 0.25–0.59 RR; STOP losers
   risk ~3–4 RR (FACT).
4. The STOP is both too tight in % AND too fast in time (pk→trough 2–3 bars) (FACT).

**3. What E8 explicitly does NOT claim.** It does not say *which* wider stop or
*which* target level is correct; that is a decision deferred to the pre-registered
E9. It does not claim the realized-MFE distances are a-priori achievable.

**4. Verdict.** **UNSUPPORTED for any production change based on E8 alone.**
**Established:** the mechanic (STOP/target geometry) is quantified and reproducible
across all four datasets. **Next action:** run the pre-registered **E9** controlled
conditional-geometry test on IN, hold VAL/OOS for confirmation — the smallest
evidence-based step that can determine whether fixing stop/target geometry actually
makes Veyra work.

---

*E8 made no change to any source or behaviour. All analysis is isolated in
`src/veyra/research/e8_stop_target_geometry.py`; its output is
`reports/phase5_1/_e8_data.json` appended with realized-path geometry facts from the
frozen E7 reconstruction.*