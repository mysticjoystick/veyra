# Veyra Phase 5.1 — Deep Root-Cause Audit: Why Genuine Evidence Scores Fail to Discriminate Setup Quality

Audit module: `root-cause-audit-1` | Run on the frozen `phase5-baseline-v1` + H1 re-scoring | Generated: 2026-09-03
Scope: **diagnosis only**. No production code modified, no thresholds, no H2, no scoring redesign, no new indicators, no selection from VAL/OOS.

Question under audit:

> **Why can Veyra calculate genuine evidence scores, yet high evidence scores still fail to predict better trades?**

Every claim below is labelled **FACT** (directly from code), **OBSERVATION** (from measured output), **CORRELATION** (statistical association), **HYPOTHESIS** (proposed mechanism), or **UNKNOWN** (cannot be established). Every root-cause claim cites an exact file + function.

---

## 1. Trace of Every Component to Its Original Formula

The full evidence path for the six components is identical in structure:

```
RAW CANDLES (open/high/low/close/volume)
  -> engine.analyze_each(df)          [market/<engine>.py]
  -> EngineResult{value, score, meta, evidence}
  -> pipeline._record_component()     [market/pipeline.py:181]
  -> AnalysisComponentOutput(score=result.score, meta=result.meta)  [stores genuine score]
  -> snapshot.scores[name]            [domain/snapshot.py:96 set_component]
  -> SetupScorer.apply(setup, view)   [strategy/scoring.py:26] (BASELINE: reads meta["score"], always 0-missing)
  -> H1SetupScorer.apply              [research/e5_score_architecture.py:58] (reads comp.score)
  -> WeightedScoreAggregator.aggregate [strategy/aggregation.py:21] -> raw
  -> ScoreNormalizer.normalized(raw/0.84) [backtest/normalize.py:25]
  -> SetupRecord.score / score_normalized [backtest/simulator.py:320-322]
```

The genuine numeric score lives on **`AnalysisComponentOutput.score`** (top-level), never in `meta`. The baseline `SetupScorer._score_from_meta(meta,"score",default)` reads `meta["score"]`, which **no** engine emits — hence TREND/STRUCTURE/MOMENTUM/VOLUME = 0 in the baseline and the score only reflected VOLATILITY + PULLBACK (the two that do not read a meta `score`).

### 1.1 TREND
- **File/class/function:** `market/trend.py` → `TrendEngine` → `_strength()`.
- **Inputs:** `close` candles; EMA50, EMA200 (`ewm(span, adjust=False)`), slope over `slope_lookback=3`.
- **Formula:** direction from `_decide_direction` (bullish ⇔ EMA50>EMA200 AND close>EMA200). `_strength`: 5 boolean votes, `aligned/5*100`, ×0.6 discount if `ema_fast_above_ema_slow` is False. Scores → {0,20,40,60,80,100} (or {12,24,36,48,60,72} when the 0.6 discount applies).
- **Range:** 0–100. **Continuous?** **No — discrete**, only 6–12 possible values (boolean votes). **Directional?** Yes — strength is scored toward the trend's own direction. **Conditioned on qualification?** No — computed for every bar; but *detection* further requires `trend_strength >= 55`.
- **What "80" means:** ~4/5 structural-EMA alignment factors hold in the trend's direction; i.e. price above fast & slow EMA, fast above slow, fast slope up, slow slope up (with the few that don't align). 100 ⟺ all 5.

### 1.2 STRUCTURE
- **File/class/function:** `market/structure.py` → `StructureEngine` → `_structure_score()`.
- **Inputs:** `high/low/close`; swing pivots at lookback `k` (`_find_pivots`), classify from the last 2 swing highs/lows (`_classify_structure`).
- **Formula/conditions:** a fixed label→score map: HH_HL=90, LH_LL=85, HH_LL=50, LH_HL=45, NEUTRAL=40, UNKNOWN=0. Added penalty in `scoring.py:_structure` −40 if the structure opposes the side.
- **Range:** {0,40,45,50,85,90}. **Continuous?** **No — purely categorical (6 buckets).** **Directional?** Yes (90 for bullish HH_HL vs 85 for bearish LH_LL; a single-score numeric but it is really two side-labels). **Conditioned on qualification?** No per-bar, but detection predicates require specific states (below).
- **What "80" means:** literally impossible — the score takes only {40,45,50,85,90}. "80" has no meaning; the score saturates at fixed labels.

### 1.3 MOMENTUM
- **File/class/function:** `market/momentum.py` → `MomentumEngine` → `_momentum_score()`.
- **Inputs:** `close`; RSI14, MACD(12,26,9) histogram.
- **Formula:** NEUTRAL=50; else `score = 50 + min(50, |macd_hist|*1000)`, capped 100. Direction from `_decide_momentum` (sign of MACD histogram). Opposed penalty −50 in `scoring.py:_momentum`.
- **Range:** 0–100. **Continuous?** Partially — jumps by the `|macd_hist|` magnitude but `min(50, |hist|*1000)` saturates at |hist|≥0.05. **Directional?** Yes. **Conditioned on qualification?** **Yes — by far the most important.** Qualification (`setup_engine._confirms`, line 183) requires `momentum==POSITIVE` for LONG, `NEGATIVE` for SHORT. So any setup that *qualifies* had positive MACD histogram → score ≥ ~55+, and on qualifying bars the histogram is typically large → score drives to 100.
- **What "80" means:** the MACD histogram is ~+0.03 with the two MACD lines separated enough to push the score above 55. On qualifying setups this is almost always ≥ 90–100.

### 1.4 VOLUME
- **File/class/function:** `market/volume.py` → `VolumeEngine` → `_volume_score()`.
- **Inputs:** `volume`, `close`; 20-bar volume MA; relative volume = cur/avg; price↔volume confirmation.
- **Formula:** `score = min(100, rel_vol/2*100)` = rel_vol×50 (capped); +15 if `price_volume_confirmation`. Bonus also applied in `scoring.py:_volume`.
- **Range:** 0–100; continuous-ish but coarsely stepped. **Directional?** No — volume has no side; the +15 confirmation is direction-agnostic (both up and down moves get it, `volume.py:109-113`). **Conditioned on qualification?** No.
- **What "80" means:** relative-volume ~1.3× the 20-bar average (optionally +15 if the last bar confirmed price+volume). It measures *activity/participation*, not setup quality or direction.

### 1.5 VOLATILITY
- **File/class/function:** `market/volatility.py` → `VolatilityEngine` → `_volatility_score()` AND the *separate* inverse state-map in `scoring.py:_volatility`.
- **Inputs:** `high/low/close`; ATR14, ATR% of price, ATR expansion ratio vs 14 bars ago.
- **Formula (engine):** `score = min(100, atr_pct/3*100)` + up to +20 per expansion unit — **higher ATR% ⇒ higher score**.
- **Formula (setup scorer, `scoring.py:107-119`):** **NOT the engine score.** It maps `volatility_state` via `inverse={EXTREME:15,HIGH:40,NORMAL:75,LOW:90}` — **LOW volatility ⇒ HIGH score, inverted from the engine**.
- **Range:** engine 0–100 continuous; setup-scorer effectively {15,40,75,90}. **Directional?** The engine is direction-agnostic (magnitude only). The setup-scorer adds a *desirability* direction (low is "good"). **Conditioned on qualification?** Yes — every directional detector rejects HIGH/EXTREME, so qualified setups have state ∈ {LOW, NORMAL} → setup score ∈ {90,75}.
- **What "80" means, as used in the setup score:** "volatility_state == NORMAL-ish" (between the 75 NORMAL and 90 LOW labels). It is a 4-5-state categorical desirability label, not a continuous quality measure.

### 1.6 PULLBACK
- **File/class/function:** `strategy/scoring.py:_pullback` (line 121); uses `rules.structure_state` + `rules.momentum_state`.
- **Inputs:** the STRUCTURE state and MOMENTUM state booleans (recycled from components 1.2/1.3). **No independent indicator.**
- **Formula:** `85 if (struct==HH_HL AND mom==POSITIVE) for LONG (or symmetric) else 40`.
- **Range:** {40,85}. **Continuous?** No — binary-ish (2 values). **Directional?** Yes. **Conditioned on qualification?** Yes — detection of TREND_CONTINUATION/BREAKOUT requires HH_HL+POSITIVE → 85 for those.
- **What "80" means:** an artifact — the score is only ever 40 or 85 (or in the E5 H1 family scan, 85 when structure/momentum align). It is basically the detector gate re-encoded as a score.

---

## 2. What Each Score Actually Means

| Component | Real meaning | Category | Was it designed to predict win/loss? |
|---|---|---|---|
| TREND | Strength of EMA-alignment in trend direction (indicator magnitude) | **(A) strength of current market condition** | **No.** It grades trend intensity, not outcome. |
| STRUCTURE | Categorical label of swing progression (HH_HL etc.) | **(A/D)** — a detector state, not a graded quality | **No.** It names the structure type. |
| MOMENTUM | Signed MACD-histogram magnitude below saturation | **(A/C)** — strength *and* distance above the ≥0 gate | Partly — but it is bidirectional (a huge negative histogram scores 100 too). **It does not encode "will the move continue," only "is momentum strong now."** |
| VOLUME | Relative-volume participation (no direction) | **(A)** — activity level | **No.** High relative volume is not causally "good" for a trend continuation; it can mean climactic volume (exhaustion). |
| VOLATILITY | In setup usage: categorical volatility-regime desirability (LOW best) | **(C/D)** — distance-from-state label | **No.** It encodes regime, whose ideal is setup-family dependent. |
| PULLBACK | 85/40 flag = "structure & momentum aligned" | **(B/C)** — literally the gate state | **No.** It is the detector's acceptance predicate re-branded as a score. |

**Core conclusion (FACT):** Of the six components, **none was built to predict trade outcome.** Each is a *state or magnitude classifier of the current market*. The score is a weighted sum of "how strong/clean the current market condition is," which — as E5 showed — does not monotonically map to a "better trade." This is **not a bug in the wiring alone; the very semantics of the components are "condition strength," not "edge."** This is criterion **(A)/(E)**.

---

## 3. Ceiling / Saturation Sources

Empirically (H1, BTC 1D detections, `_e5_fam` scan):

| Component | Value at detection | % of detections | Why it saturates |
|---|---|---|---|
| MOMENTUM | **100** | 100% (BREAKOUT/PULLBACK/T.CONT) | Detector requires momentum to confirm; `_momentum_score` = 50 + min(50,|hist|·1000); qualifying bars have large hist → 100 |
| VOLATILITY | **75** | 100% (all families) | Every directional detector rejects HIGH/EXTREME → state is NORMAL(75)/LOW(90); NORMAL dominates |
| PULLBACK | **85** | 100% (BREAKOUT/T.CONT) | Requires struct==HH_HL AND mom==POSITIVE → exactly the accepted state → 85 |
| STRUCTURE | **90** | 100% (T.CONT) | T.CONT requires struct==HH_HL → `_structure_score`=90 fixed |
| TREND | 100 (84–95%) / 48 (rest) | — | Detection requires trend_strength≥55; strongest bars → all-5 aligned=100; otherwise the ×0.6 discount yields 48 |
| VOLUME | varies (13–100) | — | **The only genuinely varying component among detected setups.** Not gated by detection. |

Answers to the four sub-questions:

1. **Why does it saturate?** Because each detector *predicate* already selects the exact condition that maps to the max (or a constant) component value.
2. **What produces 90–100?** MOMENTUM: MACD hist positive & magnitude ≥0.05. STRUCTURE: HH_HL. PULLBACK: HH_HL+POSITIVE. VOLATILITY: NORMAL (75). TREND: 5/5 EMA alignment.
3. **Are those conditions common among qualified setups?** **They are *necessary* for qualification.** Every qualified LONG setup had momentum==POSITIVE (hist>0), i.e. MOMENTUM≥~55 at least; in practice 100.
4. **Score before or after the qualifying condition?** The score is computed **at detection** (`_promote`→`_scorer.apply`), which is ≤ the qualification bar. But because detection itself *also* gates on structure/momentum/volatility for most families, the score is already high at detection.
5. **Does qualification mathematically guarantee a high component score?** **Yes for MOMENTUM** (`_confirms` forces POSITIVE), **yes for VOLATILITY** (detectors reject HIGH/EXTREME), **yes for STRUCTURE/PULLBACK** in TREND_CONTINUATION/BREAKOUT.

**MOMENTUM — the decisive selection-bias (FACT + OBSERVATION):**
`_momentum_score` is high **because only high-momentum setups are allowed to qualify**, not because high momentum makes a better trade. Exact path:
- `market/momentum.py:184` `_momentum_score` → 50 + magnitude.
- `strategy/setup_engine.py:183` `_confirms`: `momentum=="POSITIVE"` gates DETECTED→DEVELOPING→QUALIFIED.
- Net: **the momentum score is structurally confounded with the qualification event.** A momentum score that is low bars the setup; only momentum scores near 100 survive to be measured. **You cannot rank qualified setups by a variable you used to filter them.** This is the single strongest cause of the score's inability to discriminate among *qualified* setups.

---

## 4. Double-Counting / Collinearity

Shared-input table (by component inputs):

| Component | Main inputs | Shared inputs | Conceptual signal |
|---|---|---|---|
| TREND | close, EMA50/200 | close | trend alignment |
| STRUCTURE | high/low/close swings | high/low/close | swing progression |
| MOMENTUM | close, RSI/MACD | close | MACD magnitude |
| VOLUME | volume, close | close | participation |
| VOLATILITY | high/low/close, ATR | high/low/close | regime breadth |
| PULLBACK | **STRUCTURE state + MOMENTUM state** | (reuses those two) | gate re-encoded |

Measured pairwise correlation on the **ungated** LONG BTC 4H sample (n=3251):

```
             TREND  STRUCTURE MOMENTUM  VOLUME  VOLATILITY PULLBACK
TREND         1.00    0.07    -0.13    -0.01    -0.06      -0.03
STRUCTURE     0.07    1.00     0.04     0.01    -0.10       0.66
MOMENTUM     -0.13    0.04     1.00     0.02    -0.02       0.44
VOLUME       -0.01    0.01     0.02     1.00    -0.14       0.03
VOLATILITY   -0.06   -0.10    -0.02    -0.14     1.00      -0.13
PULLBACK     -0.03    0.66     0.44     0.03    -0.13       1.00
```

**FINDINGS:**
- **STRUCTURE ↔ PULLBACK = 0.66 (CORRELATION)** — nearly the same evidence scored twice under two weights (0.20 + 0.15 = 0.35 of the whole). Both derive from the same `structure_state`/`momentum_state` booleans (`current market condition`, not independent evidence).
- **MOMENTUM ↔ PULLBACK = 0.44 (CORRELATION)** — momentum state feeds `_pullback`'s 85 branch.
- So the aggregation, via PULLBACK, double-counts the structural-alignment evidence. **Adding genuine STRUCTURE and MOMENTUM scores (H1) partially re-counts what PULLBACK already encoded** (all three peak at the same HH_HL + POSITIVE gate).
- TREND, VOLUME, VOLATILITY are otherwise weakly/negatively correlated with the rest, i.e. more independent (CORRELATION).
- These are correlations in the *ungated* population; **after detection the components are near-constant** (§3), so their inter-correlation among qualified setups is structurally compressed (OBSERVATION).

**Conclusion (FACT/CORRELATION):** The weights assume six independent evidence sources, but three of them (STRUCTURE, MOMENTUM via its state, PULLBACK) describe the *same* market event (structure trend + momentum sign). Adding their genuine scores counts one underlying fact (strong, confirmed trend) ~2–3 times, inflating the total without adding independent predictive information.

---

## 5. Is Score Direction Actually Correct?

Inspect each for "higher = more desirable for THIS setup":

- **TREND:** higher = stronger trend in the same direction as the setup. Desirable for TREND_CONTINUATION / BREAKOUT; **irrelevant-to-harmful for BREAKOUT_RETEST** (a retest is calmer); neutral for PULLBACK. **Direction is context-dependent** (HYPOTHESIS from setup semantics, not established).
- **STRUCTURE:** only monotonic within a side. 90 = HH_HL, 85 = LH_LL. Adding the ± penalty aside, a single higher numeric is not comparable across bull/bear. **Not a single "more is better" axis** (FACT).
- **MOMENTUM:** a big *negative* histogram also scores 100 (magnitude-based). For a LONG it would then be −50 (opposed) in H1 — but for a LONG the *qualification* gate makes opposed momentum impossible post-qualification. **Net:** among qualified setups the momentum "direction" is always with the side, so its only residual variance is magnitude = "how extended/strong," which is ambiguous for a continuation (strength vs. exhaustion). **Direction is not a monotone quality axis** (FACT/CORRELATION).
- **VOLUME:** direction-agnostic; high relative volume is treated as good but there is no consensus that climactic volume is good for continuation. **Desirability ambiguous** (HYPOTHESIS).
- **VOLATILITY — the clearest inversion (FACT):** The engine's `_volatility_score` scores **high ATR% ⇒ high score** (`volatility.py:126-133`). The setup scorer *overrides* it with an **inverse** map `{EXTREME:15,HIGH:40,NORMAL:75,LOW:90}` (`scoring.py:107-119`). So the setup's "volatility score" is **not** the genuine engine score; it is the inverse (LOW is best). The E5 observation of VOLATILITY−total correlation ≈ −0.07 is a *symptom* of scoring VOLATILITY against the trend rather than with it, and of the LOW-best mapping fighting the other components' HIGH-best semantics. This is a **semantic inconsistency** (the component's desirability axis is inverted relative to the engine's own magnitude score) and is **setup-family dependent** (high volatility may be desirable for BREAKOUT, undesirable for PULLBACK — UNKNOWN which is true).
- **PULLBACK:** 85 = "structure+momentum aligned," 40 = otherwise. For a PULLBACK the *detection* requires no full reversal, and this flag mostly mirrors the gate. Its "direction" is binary, not a quality gradient (FACT).

**Net:** At least two desirability inversions/mismatches exist (VOLATILITY inverted vs engine; MOMENTUM magnitude-is-not-quality), and desirability is **regime/family dependent** for TREND and VOLATILITY (HYPOTHESIS).

---

## 6. Temporal Alignment

Replay timeline (from `backtest/engine.py:189-247` and `setup_engine.py:137-159`):

```
bar i   : detect -> _promote -> scorer.apply(setup, snapshot_i)   [SCORE FROZEN at detection]
bar i   : register, state = DETECTED
bars i+k: advance: _confirms(momentum==POSITIVE)  => DETECTED->DEVELOPING (time k)
                   _confirms again                => DEVELOPING->QUALIFIED (time k')
bar k'  : step 7 arms QUALIFIED
bar k'+1: step 1 fills at OPEN of the bar AFTER qualification
```

- **Every component score is computed exactly once, at the detection bar** (`_promote` → `_scorer.apply`). **`advance()` never re-scores** — it only checks `_expired/_invalidated/_confirms` (state rules), none of which read component scores (FACT). 
- Therefore the score reflects **detection-time evidence**, but the **entry happens at the open of the bar after qualification**, which is typically ≥1 bar later (and can be many bars later if momentum takes time to confirm, e.g. BREAKOUT_RETEST which is detected on a quiet retest then must wait for momentum to turn positive).
- **This is a temporal mismatch:** the score is "as-of detection," the outcome is "as-of entry." Between detection and entry the market's momentum/volatility/structure **can diverge from what the score recorded** (FACT from the code; magnitude UNKNOWN). In E4's terms, the qualification-time momentum (`q_momentum`) is what *actually* gates entry, yet the score still carries the detection-time value.
- **Momentum is the specific stale-variable hazard:** a setup detected while momentum was mild, then confirming strongly at qualification, is scored by detection momentum — i.e. **the score under-weights the very momentum that triggered entry**, and conversely over-weights already-changing conditions. This is a candidate mechanism for weak score↔outcome correlation (HYPOTHESIS).

---

## 7. Conditioning / Selection Bias

The system scores **detected → developing → qualified → entered**, and every later stage has a stricter selection filter than the scores can then re-order:

- **P(score | detected):** scores are assigned at detection. Already conditioned by each detector's predicates (§3): most components near-constant.
- **P(score | qualified):** filters through momentum confirmation (`_confirms`). This **forces** MOMENTUM≥threshold and keeps VOLATILITY/STRUCTURE/PULLBACK near their gates. 
- **P(score | entered):** `enter()` additionally requires a valid stop (`simulator.py:108-109`). That doesn't touch scores but removes setups with no valid stop.

**Consequence (FACT):** Because qualification is momentum-gated and the components that are high are exactly the ones the gates select, **the score distribution among qualified setups is strongly compressed**, and the residual score variance is dominated by non-gated components (VOLUME mostly, and TREND's 48-vs-100 dichotomy, and family-specific STRUCTURE). **A score that is frozen/constant across the very population it is meant to rank cannot rank it.** This is the essence of why H1 "fixed" the wiring yet still cannot order outcomes: **the score measures pre-qualification "clean condition," but the system only trades post-qualification, where that condition has already been enforced and is therefore no longer informative.**

This is the **P1 confirmed major measurement problem** underlying the whole phenomenon.

---

## 8. Score = Current State or Future Edge?

For each component, asked "what outcome should this predict?":

- **TREND →** should predict trend continuation quality (target continuation above structure high). Plausible causal link, but only once the move has already begun; and it is a *current* magnitude, with no promise the move continues (a mature strong trend is more likely to exhaust). **Plausible but non-unique mechanism; UNKNOWN predictive strength.**
- **STRUCTURE →** HH_HL should support continuation. Present *now*, but it names the shape, not buying pressure. Plausible weak link.
- **MOMENTUM →** strength now does **not** logically predict continuation; it can predict *continuation* OR *mean reversion/exhaustion* (a strong MACD spike on a qualifying bar is as compatible with a blow-off top as a sustained push). **Ambiguous causal direction → the feature's semantics do not match an unambiguous "edge."**
- **VOLUME →** high participation could confirm a breakout, but for a *continuation* at extended prices it can be climactic. Intermediate. **Ambiguous.**
- **VOLATILITY →** LOW/NORMAL is claimed good by the scorer (desirable), but for BREAKOUT one wants a volatility *expansion* to follow through. **Regime-dependent causality → a single "low is good" axis is semantically wrong across families.**
- **PULLBACK →** it is the gate re-encoded; it cannot predict edge beyond what the gate already enforces. **No independent predictive content.**

**Conclusion (FACT/HYPOTHESIS):** The score aggregates *current-state strengths*, and several of these have **ambiguous or family-dependent** causality to *future* outcome (esp. MOMENTUM magnitude, VOLUME, VOLATILITY). The intended "score→ quality" mapping assumes monotone causality that the features do not carry. **The score is not measuring edge; it is measuring today's condition strength, and condition strength is not a reliable proxy for edge.**

---

## 9. PULLBACK and VOLATILITY (the two that already worked)

- **PULLBACK (`_pullback`, scoring.py:121-132):** 85 vs 40. 85 requires `struct==HH_HL AND mom==POSITIVE` (LONG). On detected setups this is the *accepted* state, so 85. **It is a categorical setup-state flag disguised as a score**, not a graded "quality of the pullback." It carries no retracement-depth/quality content (the detector's own retrace is computed but **not** used in the score). **`OBSERVATION/FACT`:** it contributes a near-constant 85 under its weight (0.15), inflating the total without discrimination.
- **VOLATILITY:** In the setup score it is a 4-state desirability map (`LOW=90,NORMAL=75,HIGH=40,EXTREME=15`), *inverted* from the engine's magnitude score. On detected setups it is frozen at 75 (NORMAL). **It is a categorical regime label, with desirability family-dependent**, not a continuous quality measure. Its negative correlation to the total is the *expected symptom* of scoring "low volatility = good" against a score where high trend/momentum are "good."

Both are **categorical labels / gate flags, not continuous quality scores**, and both are near-constant on the exact population they are supposed to help rank. **FACT.**

---

## 10. Setup-Family Conditioning

- A single global weighting is applied to all four families, but the *meaning* of components is family-dependent:
  - **BREAKOUT:** wants a *fresh* break; strong VOLUME/volatility *expansion* is plausibly good; a mature "100 TREND" is less relevant (the break is the event), and PULLBACK=85/STRUCTURE=90 are just confirming it. 
  - **PULLBACK:** wants a *controlled* retrace; strong MOMENTUM (100) at the moment of a pullback is contradictory (a pullback by definition has eased momentum); LOW volatility is desirable. Here the high-MOMENTUM=100 gate actively rewards what a pullback conceptually is *not*. 
  - **TREND_CONTINUATION:** high TREND+STRUCTURE is directly on-thesis. 
  - **BREAKOUT_RETEST:** the setup is "calm after a break"; MOMENTUM is conditionally required at *confirmation*, but at detection the score reads whatever momentum was at detection (potentially weak).
- **FACT/CORRELATION:** The global score mixes incompatible contexts — e.g. it *rewards* MOMENTUM=100 and *penalizes* high volatility across all families, but the optimal momentum/volatility differs by family (BREAKOUT wants expansion; PULLBACK wants contraction). Without family-specific (or at least family-consistent) conditioning, the same numeric score means different things per family, weakening any single monotone score→outcome relationship.
- This is **diagnosis only** — no family-specific scoring implemented.

---

## 11. Asset + Timeframe Conditioning

- **4H vs 1D:** Case count and holding differ enormously. On 4H, `setup_max_lifetime_bars=24` and `backtest_position_max_bars=60`; **52% of 4H trades close via the 60-bar position-age `EXPIRED` exit** (measured: BTC 4H 1640/3135 = 52%; only 486/3135 = 15.5% hit TARGET). On 1D expiry via the same 60-bar rule closes 232/437 (53%). So **the dominant outcome driver on both is the fixed position-age exit, which is orthogonal to the score.** The score predicts nothing about the time-stop.
- **BTC vs ETH:** identical component wiring; any cross-asset difference is price scale (MAE/MFE in price points differ by 10× between BTC and ETH) and data volume. The score-normalised buckets are comparable, but the *outcome* (fees+slippage in %) is scale-free while MAE in price points is not — so interpreting "MAE by bucket" across assets needs care (OBSERVATION).
- **Conclusion (FACT):** A single global score + single global position-life is applied to materially different environments (different holding periods, stop widths, target availability). The score cannot "rescue" the 4H trades where the real determinant is the 60-bar expiry, and it is not normalized/contextualized for the asset's volatility scale. This is an environment-conditioning gap (diagnosis only).

---

## 12. Why High Score Can Coincide With High MAE

The fitted mechanism, code+data grounded:

1. **The driving outcome is the time-stop, not score.** On 4H, 52% of trades exit by 60-bar `EXPIRED` (`close_overaged`, simulator.py:296) at bar close — these positions are forcibly held the full lifetime, exposing them to the full bar-to-bar range. **MAE accumulates over the held window**, so high-MAE trades are precisely the many long-held timeout trades. The score does nothing to shorten or shield them (H1 funnel identical; trade list identical).
2. **Stops are the full swing-low distance, not tight risk.** `_levels` (simulator.py:149) sets LONG stop = `interest_area.low` (support/last swing low). Invalidation (`_invalidated`) almost never fires (E4: 0/7713), so the effective risk is wide — MAE in price points equals the distance from entry down to a far support. **A high component score (clean trend, strong structure) does not imply a tight stop.** In fact a strong clean trend has already run far from the swing low, giving a *wide* stop and thus a *large* MAE for a LONG (CORRELATION: BTC 1D H1 80-100 bucket had the highest avg MAE 1927, lowest MFE/MAE 4.8).
3. **Late/exhausted entries.** "Strong momentum + strong trend + 5/5 EMA aligned" (score~100) is, by construction, a condition that has *already* moved. Entries into such conditions are late relative to the move → residual reward/risk is poor, target often unreached before the time-stop → STOP or EXPIRED. The residual near-monotone pattern "highest bucket → worst PF / highest MAE" (BTC 1D OOS 80-100 PF 0.79, max MAE; ETH 4H OOS 80-100 PF 0.63) is consistent with **scoring rewarding the already-extended condition (CORRELATION).**
4. **Regime transition expiry.** `_expired` also kills setups/positions on regime change (`setup_engine.py:201`). High-score setups in a *trending* regime can be unseated by an abrupt volatility spike → closed at loss. Not score-controllable.

**Bottom line (OBSERVATION/CORRELATION):** High score ≈ "market already clean and strong" ≈ "move already happened / stop already wide / lifetime uncontrolled." The score is not predicting forward edge; it is rewarding current extension, the opposite trade-off from exit-quality. This is why high score can carry high MAE and poor PF.

---

## 13. Information-Loss / Semantic-Mismatch Map

```
RAW EVIDENCE (candles + volume)
   │  kept: OHLCV
   ▼
INDICATOR LAYER (EMA/RSI/MACD/swings/ATR/rel-volume)
   │  (a) computed causally, look-ahead-safe (FACT)
   ▼
ENGINE SCORES (0-100 per component)
   │  (b) LOSS: each engine compresses a rich signal to 1 number
   │       - STRUCTURE: 45/50/85/90 (5 labels)         [heavy compression]
   │       - PULLBACK(score): 40/85                    [near-total compression]
   │       - VOLATILITY(score): 15/40/75/90            [categorical]
   │       - MOMENTUM: magnitude-saturated at 50+      [saturation loss]
   │       - VOLUME: participates only via rel-vol     [ignores flowing/∫vol]
   ▼
AnalysisComponentOutput.score
   │  (c) baseline LOSS: SetupScorer never reads it (reads missing meta)  -> 0
   │  (d) H1: reconnected (comp.score)
   ▼
SETUP SCORE (weighted sum)
   │  (e) DUPLICATION: STRUCTURE/PULLBACK/MOMENTUM re-count the same event
   │       (STRUCTURE↔PULLBACK r=0.66, __§4)
   │  (f) TIMING: score frozen at DETECTION, but trade is at qualify+1   (§6)
   │  (g) CONTEXT: single global weighting applied to 4 families / 2 TFs /
   │       2 assets with different optimal component meanings             (§10/11)
   ▼
QUALIFICATION GATE (momentum must confirm)
   │  (h) SELECTION: gate enforces high momentum/calm-volatility, so the
   │       surviving population has compressed scores; the score is
   │       measuring the gate's own condition, no longer discriminating (§7)
   ▼
ENTRY (valid stop required)
   │  (i) the score does not affect stop/target/lifetime → no effect on risk
   ▼
OUTCOME (net_return / MAE / MFE)
   │  (j) dominated by time-stop(60-bar) and wide swing-low stops ---
   │       independent of score                                        (§12)
```

**The point where "market evidence" stops equalling "setup quality" (FACT):** between the **engine scores** and the **setup score → entry**, i.e. step (e)–(h). The evidence is real, but once funneled through (a) heavy compression, (b) duplication, (c) detection-time freezing, and (d) the qualification gate, the remaining variance among the trades the system actually takes is not informative slope; it is mostly noise + VOLUME + the 48-vs-100 TREND split + family labels. **The score, as consumed, is a smoothed, duplicated, early-frozen, gate-reflected summary of current condition — not a lever, a risk, or an edge measure.**

---

## 14. Root-Cause Ranking

| # | ROOT CAUSE | CODE LOCATION | MECHANISM | OBSERVED EFFECT | CONFIDENCE |
|---|---|---|---|---|---|
| P0 | Component scores structurally **confounded with the qualification gate** (esp. MOMENTUM) | `setup_engine.py:_confirms` (183) + `momentum.py:_momentum_score` (184) + each detector `_rules_hold` | Only momentum-confirmed setups qualify; momentum score = 50+|hist|≥100 on exactly those | MOMENTUM=100 on ~100% of detections; score cannot rank qualified setups (§3/§7) | **HIGH (FACT)** |
| P1 | **Detection further compresses scores**: most components near-constant on the exact population scored | `strategy/detectors/*_detectors _rules_hold` + `scoring.py` | Detector predicates select HH_HL, momentum POSITIVE, non-high volatility → STRUCTURE=90, PULLBACK=85, VOLATILITY=75 frozen | Score distribution collapsed to 80-100; no monotone bucket PF (E5) | **HIGH (FACT/OBS)** |
| P1 | **Double-counting** of the structure/trend-confirmation evidence | `aggregation.py` weights + `scoring.py:_pullback` (85 identically from structure+momentum) | STRUCTURE↔PULLBACK r=0.66; MOMENTUM↔PULLBACK r=0.44; 3 components ≈ 1 fact | Weighted 0.35 double-counted; inflated, non-independent total | **HIGH (CORRELATION)** |
| P1 | **Temporal mismatch**: score frozen at detection, entry at qualify+1 bar | `setup_engine.py:_promote`(apply once) + `backtest/engine.py` fill-at-next-open | `advance()` never rescoring; entry 1+ bars later | Score represents stale detection state vs entry/outcome | **MEDIUM (HYPOTHESIS)** |
| P2 | **VOLATILITY desirability inverted** vs engine & family-dependent | `scoring.py:_volatility` (inverse map) vs `volatility.py:_volatility_score` | Setup-score maps LOW→90; engine maps high-ATR→high; BREAKOUT wants expansion | VOLATILITY−total r≈−0.07; inconsistent "goal" | **MEDIUM (FACT on inversion; family-dependence HYP)** |
| P2 | **Score is "current condition strength," not "edge"** | all `_*_score` in market/*.py | Components grade magnitude/state of *now*, not forward expectation; MOMENTUM magnitude is exhaustion-ambiguous | High score ≈ already-extended → wide stop/late entry → high MAE (BTC 1D OOS 80-100 PF 0.79) | **MEDIUM-HIGH (HYPOTHESIS/CORR)** |
| P2 | **Outcome dominated by time-stop + wide stops**, orthogonal to score | `simulator.py:close_overaged` (60-bar), `_levels` (swing-low stop) | 60-bar lifetime closes 52% of 4H trades + boundless MAE window | Highest score bucket has worst MAE/PF on focus windows (§12) | **HIGH (FACT mechanics / CORRELATION pattern)** |
| P3 | Global score across families/assets/TFs with no conditioning | `config.py` single weights | Same semantics applied to different optimal regimes | Family/TF/asset-dependent score meaning | **MEDIUM (diagnosis)** |
| P3 | Baseline wiring bug (meta["score"]) | `scoring.py:_score_from_meta` | reads missing key | Components = 0; also grounds the fix | **FIXED by H1 (real but not the root discrimination cause)** |

---

## 15. Most Important Final Question

> Is the current Veyra score fundamentally capable of being a quality score with small fixes, or is the current architecture measuring the wrong things?

**Conclusion by evidence (choose all that are proven):**

The answer is a combination of **B, C, and D**, with **E** (in its present purpose) as the unavoidable truth:

- **C — Conditioning/selection destroys discrimination (proven, the dominant cause).** The moment the qualification gate requires momentum/calm-volatility, the surviving population's component scores are compressed to near-constants (MOMENTUM=100, VOLATILITY=75, STRUCTURE=90/PULLBACK=85 for continuation/breakout families). **The score measures, at detection, the exact conditions the gate has already enforced; it is therefore structurally incapable of ranking the trades the system actually takes.** This alone explains "genuine scores but no usable discrimination." It is **not a small wiring fix — it is a measurement-vs-selection architecture problem.**

- **D — Components are correlated / semantically mismatched (proven).** STRUCTURE↔PULLBACK 0.66 and MOMENTUM↔PULLBACK 0.44 mean the aggregation double-counts one underlying "clean confirmed trend" fact; VOLATILITY and MOMENTUM have direction/magnitude semantics that are ambiguous-to-inverted for outcome prediction.

- **B — Poor normalization/aggregation contributes (partial).** A 6-weighted sum with non-independent, near-constant inputs and a detection-time freeze yields a total dominated by duplication and family labels rather than independent evidence.

- **E — The current score is fundamentally not a quality (edge) model (proven in purpose).** None of the six components was designed to predict win/loss; they measure *current strength/state*. The intended "score = setup quality" mapping is not supported by the features' semantics. So as currently *defined and consumed*, the score **does not measure setup quality** — it measures current market condition strength.

**Therefore:** The wiring bug (meta["score"]) was real and H1 reconnects it, **but fixing the wiring does not make the score a quality discriminator, because the deeper problems are (C) measurement-after-selection, (D) duplicate/ambiguous evidence, and (B) aggregation.** These are **not correctable with small validation-side fixes to the existing score**; they require either (i) changing *what* is computed (score must be measured on a population/purpose where it predicts forward outcome and is not confounded by the gate), or (ii) separating the *gate* from the *evidence* (measure the evidence independently of the filters that select it, and evaluate its predictive power on that basis). That is a redesign of the score's *measurement target*, not a bug fix.

A **plausible minimal, architecture-preserving direction** (to be validated by design, not implemented here): measure each component's score against **forward outcome on the raw candidate population** (before the momentum gate compresses it) and/or make the qualification gate **not** be the same variable being scored; and treat PULLBACK/STRUCTURE/MOMENTUM as one evidence cluster rather than three independent weights. But that is a hypothesis requiring experiment — see §"Next Experiment."

---

## ONE-SENTENCE ROOT CAUSE

**The Veyra score is a weighted sum of "current market condition strength" whose strongest inputs (MOMENTUM, STRUCTURE, PULLBACK, VOLATILITY) are the exact variables already enforced by the qualification/filtering gate, so among the only setups it ever trades those inputs are collapsed to near-constants and the score carries duplicated, detection-time, gate-reflected information that has no forward edge — which no amount of re-wiring the genuine scores corrects.**

## TOP 5 CONFIRMED PROBLEMS

1. **Gate confounds the measurement (P0):** MOMENTUM is the qualification criterion *and* a scored component; because only momentum-confirmed setups qualify, MOMENTUM=100 on ~100% of detections — the score cannot rank the population it is used on. (`setup_engine.py:_confirms`, `momentum.py:_momentum_score`.)
2. **Post-detection score compression (P1):** detector predicates also fix STRUCTURE=90, PULLBACK=85, VOLATILITY=75 on the very setups that get scored, so most components carry no rank information. (`detectors/*`)
3. **Evidence double-counted (P1):** STRUCTURE↔PULLBACK r=0.66 and MOMENTUM↔PULLBACK r=0.44 mean ~0.35 of the weights re-count one "clean confirmed trend" fact. (`aggregation.py`, `scoring.py:_pullback`.)
4. **Score is detection-frozen but entry is later (P1):** `_promote` scores once; `advance` never re-scores; entry is at the open after qualification. Score reflects stale pre-entry state. (`setup_engine.py`, `backtest/engine.py`.)
5. **Outcome is dominated by mechanics the score doesn't influence (P1/P2):** 52% of 4H trades exit by the 60-bar time-stop with wide swing-low stops; high "clean/strong" scores are late-and-extended → high MAE (BTC 1D OOS 80-100 PF 0.79, max MAE). (`simulator.py:close_overaged/_levels`.)

## TOP 3 HYPOTHESES

1. Measuring a component score **after** it has been used as a gate cannot be predictive; if the score were computed on the **pre-qualification candidate population** and evaluated against forward outcome, a usable score→outcome gradient (if any) would emerge. **[testable]**
2. The residual "higher bucket = worse" effect is driven by **rewarding already-extended conditions** (strong trend+momentum at detection ⇒ late entries, wide stops, long-lived open positions with high MAE) rather than by quality — i.e. the score is anti-quality for continuation because it proxies "how far the move has already run." **[testable]**
3. **VOLUME is the only truly varying component** among qualified setups and is not a quality signal (it is direction-agnostic participation); the "spread" H1 adds is largely VOLUME noise + the TREND 48/100 split + family labels, not independent edge. If VOLUME's desirability is inverted (climactic = exhaustion), the residual score is noise or anti-signal. **[testable]**

## SMALLEST LIKELY FIX (diagnosis, not implemented)

Do **not** try to rescue the existing score. The smallest architecturally-preserving change that could restore "genuine scores → usable ranking" is to **decouple measurement from selection**: compute component evidence on the **raw, unfiltered** candidate population (or on a hold-out forward window) and evaluate each component’s *independent* forward-outcome relationship, collapsing the STRUCTURE/MOMENTUM/PULLBACK cluster into a single evidence axis — i.e. turn the score into a measure of *forward edge conditioned on entry mechanics*, rather than a summary of current condition that the gate has already enforced. That is a measurement redesign, not a wiring fix.

## NEXT EXPERIMENT

The smallest experiment that would *falsibly test* the central claim (gate-confounding + current-vs-edge) without touching production:

- **E6 — Pre-gate evidence vs. forward outcome (correlation/independence).** On the frozen baseline, compute each component's genuine score at the **detection bar** on **all detected setups (not just qualified)**, and regress/bin the *forward* outcome (net_return, hit-of-target, MAE, holding) against each component *and* against the pooled score — with quality defined only by outcome ($\neq$ score). This tells us whether any component carries predictive signal *before* the gate compresses it, testing H1/H2 ("gate removals the signal") directly.
- Optionally **E7 — entry-mechanics attribution**: bin outcomes by bucket *and* by exit-reason/time-stop to confirm that the score's weakness is the time-stop dominance, isolating score-signal from mechanics-noise.

Both are measurement-only (no strategy change, no thresholds selected from VAL/OOS). Recommend running at least E6.

---

*This audit modified no production code. It adds a diagnostic only. All data: `reports/phase5_1/_e5_data.json` (E5); component-family scan (BTC 1D); pairwise correlations (BTC 4H, n=3251).*