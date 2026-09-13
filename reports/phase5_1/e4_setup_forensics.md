# Veyra Phase 5.1 — Experiment E4: Setup-Engine Forensics (Diagnosis)

Diagnostic module: `e4-forensics-v1` | Frozen baseline: `phase5-baseline-v1`
Generated: 2026-09-02 | All trade metrics are NET of fees + slippage. Scores are never treated as probabilities.

> **E4 is a diagnostic / forensics study, NOT a strategy change.** It measures *why*
> the frozen baseline produces the setups, scores, and outcomes it does. It changes
> **no** strategy rule, parameter, threshold, detector, or lifecycle code. No E4
> strategy variant exists. The evidential framing follows
> **FACT / OBSERVATION / CORRELATION / HYPOTHESIS / UNKNOWN** throughout.

---

## 1. Objective

Diagnose why the existing setup-generation and qualification logic yields
**low-quality setups** — low and poorly-discriminating scores, high expiration,
and weak 4H edge — under the frozen Phase 5.1 baseline (`phase5-baseline-v1`).
This is measurement only. It does not propose or implement a remedy; any remedy
is a downstream experiment requiring separate approval.

## 2. Scope & constraints (freeze)

The baseline is behaviourally byte-identical to the approved frozen baseline. E4
adds **only** observational instrumentation (see §3). It does **not** change:

- setup definitions / types, thresholds, indicators, weights, score thresholds
- targets, stops, position lifetime, confirmation, regime, invariant handling
- entry timing / execution / lifecycle / expiration rules
- any file under `src/veyra/strategy/`, `src/veyra/market/`, or `src/veyra/backtest/`

E1, E2, E3 modules and reports are untouched. Baseline outputs are unchanged.

## 3. Method / instrumentation (standalone, behavior-neutral)

A new standalone research module `src/veyra/research/e4_forensics.py` wraps the
frozen engine with three **observational** hooks (no behavioural effect):

1. **Snapshot recorder** — wraps `MarketAnalysisPipeline.analyze_each` to retain
   the exact per-bar causal snapshots the baseline already computes
   (`snapshots[i] == analyze(df[:i+1])`). Purely stored; the wrapper also
   delegates `_engines` for the warm-up calc.
2. **`SetupEngine.advance` patch** — logs every lifecycle transition with the
   snapshot timestamp, regime, and momentum at that bar, used to attribute
   `EXPIRED` to age vs regime and to confirm qualifying momentum.
3. **Simulator patches** — `Simulator.register` captures each domain `Setup`
   (component scores, overall score, side, regime, interest area, targets), and
   `Simulator.record_all` captures the final tracked lifecycle state.

Rows are then joined to the **canonical** `SetupRecord` (outcome, final_state)
and `BacktestTrade` (return, MAE/MFE, holding) produced by the unmodified
engine, so the funnel, score buckets, and financial figures are the baseline's
own agreed numbers. Runs used a distinct `run_key` (`e4|…`) to avoid contaminating
any cached run. Four datasets: BTC/ETH × 1D/4H, full history.

Instrumentation integrity: `tr[based on id()]` identity maps key the domain Setup
object the engine already mutates; no lifecycle behaviour is altered.

## 4. Expiration breakdown

Expiration here is split into **setup-level** expiration (the setup object never
trades or is dropped before/after qualifying) and **trade-level** age expiry
(a *trade* closed because the open position hit `position_max_bars = 60`).

Setup-level outcomes (`SetupOutcome.EXPIRED`) and trade-level `exit_reason ==
"EXPIRED"` are two distinct engines of "expiration":

| Dataset | Detected | Setup-level EXPIRED | …before qualification | Position-age EXPIRED exits |
|---|---|---|---|---|
| BTC 1D | 498 | 61 (12.2%) | 61 | 232 (of 437 trades) |
| ETH 1D | 440 | 38 (8.6%) | 38 | 180 (of 402) |
| BTC 4H | 3,523 | 388 (11.0%) | 388 | 1,640 (of 3,135) |
| ETH 4H | 3,252 | 359 (11.0%) | 359 | 1,387 (of 2,893) |

Key observations:

- **Every** setup-level EXPIRED happened **before qualification** (61/38/388/359;
  zero `after_qual_before_entry`). A setup that qualifies is never later dropped
  by expiry — because it enters essentially immediately (§5, §11).
- **Most setup-level expirations are age-based.** Across the four datasets no
  qualified setup is ever invalidated (§14) and, since qualification is near
  instantaneous, regime-transition expiry before qualification is the residual
  after age expiry dominates the 8–12% setup-level rate.
- **The dominant "expiration" is trade-level position-ageing, not setup expiry:**
  on 4H roughly **52% of BTC** (1,640/3,135) and **48% of ETH** (1,387/2,893)
  opened trades are closed by the 60-bar position-timeout. This dwarfs setup-level
  expiry. It is the single largest terminal event in the 4H engine, consistent
  with the prior Phase 5 finding of high expiration.

## 5. Funnel

| Dataset | Detected | Qualified (total) | Trades (QUAL→enter) | EXPIRED (setup) | INVALIDATED | Qualified-but-no-trade |
|---|---|---|---|---|---|---|
| BTC 1D | 498 | 437 (87.8%) | 437 (100%) | 61 | 0 | 0 |
| ETH 1D | 440 | 402 (91.4%) | 402 (100%) | 38 | 0 | 0 |
| BTC 4H | 3,523 | 3,135 (89.0%) | 3,135 (100%) | 388 | 0 | 0 |
| ETH 4H | 3,252 | 2,893 (89.0%) | 2,893 (100%) | 359 | 0 | 0 |

- **Qualification ⇒ entry is 100%.** Every setup that reaches `QUALIFIED` is
  entered (armed in the engine step 7 and filled next open). There is **no**
  additional gate between qualification and entry — in particular **no minimum
  score floor** is enforced (§8, §14). Qualification is therefore not a quality
  filter for score; it is only a momentum-confirmation step.
- Roughly 11–12% of detections expire before qualifying; none invalidate.

## 6. Setup-family metrics

Per setup type (all 4 datasets aggregated for rank; per-dataset detail below).

| Dataset | Type | n | avg score(norm) | trade PF | winrate | avg ret | avg hold |
|---|---|---|---|---|---|---|---|
| BTC 1D | BREAKOUT | 205 | 20.2 | 1.43 | 44.7% | +1.0% | 9.7 |
| BTC 1D | BREAKOUT_RETEST | 75 | 16.5 | 2.37 | 48.3% | +1.5% | 3.8 |
| BTC 1D | PULLBACK | 105 | 16.0 | 1.89 | 62.1% | +1.3% | 5.6 |
| BTC 1D | TREND_CONTINUATION | 113 | 19.5 | 1.06 | 51.1% | +0.1% | 4.0 |
| ETH 1D | BREAKOUT | 149 | 20.1 | 1.23 | 36.9% | +0.8% | 8.5 |
| ETH 1D | BREAKOUT_RETEST | 56 | 15.4 | 1.61 | 44.0% | +1.2% | 3.6 |
| ETH 1D | PULLBACK | 113 | 16.6 | 2.32 | 63.6% | +2.1% | 4.5 |
| ETH 1D | TREND_CONTINUATION | 122 | 19.6 | 1.37 | 54.8% | +0.7% | 4.4 |
| BTC 4H | BREAKOUT | 969 | 20.4 | 1.63 | 43.9% | +0.8% | 7.7 |
| BTC 4H | BREAKOUT_RETEST | 912 | 16.6 | 1.20 | 36.0% | +0.2% | 5.9 |
| BTC 4H | TREND_CONTINUATION | 817 | 19.6 | **0.78** | 44.5% | **−0.2%** | 5.0 |
| BTC 4H | PULLBACK | 825 | 16.4 | **0.80** | 45.5% | **−0.2%** | 6.3 |
| ETH 4H | BREAKOUT | 905 | 20.3 | 1.00 | 38.3% | ±0.0% | 7.8 |
| ETH 4H | BREAKOUT_RETEST | 762 | 16.4 | 1.34 | 35.4% | +0.4% | 5.3 |
| ETH 4H | PULLBACK | 769 | 16.4 | 1.04 | 50.8% | +0.1% | 5.9 |
| ETH 4H | TREND_CONTINUATION | 816 | 19.6 | 1.04 | 48.7% | +0.1% | 5.3 |

- **TREND_CONTINUATION is the weakest family on 4H** (BTC PF 0.78, net negative;
  ETH PF 1.04 flat). It was already flagged in prior diagnostics (§13).
- BREAKOUT_RETEST and PULLBACK are the strongest on 1D but deteriorate on 4H.
- BREAKOUT is the most numerous and roughly breakeven-to-mildly positive.

## 7. Score distribution (normalized, all detected setups)

| Dataset | 0–19 | 20–39 | 40–59 | 60–79 | 80–100 |
|---|---|---|---|---|---|
| BTC 1D | 311 (62%) | 187 (38%) | 0 | 0 | 0 |
| ETH 1D | 293 (67%) | 147 (33%) | 0 | 0 | 0 |
| BTC 4H | 2,176 (62%) | 1,347 (38%) | 0 | 0 | 0 |
| ETH 4H | 2,072 (64%) | 1,180 (36%) | 0 | 0 | 0 |

- **Scores occupy only the bottom two buckets (0–39).** There are **zero** setups
  scoring ≥ 40 on any dataset. Average normalized scores are 15–20.
- **Almost no discrimination.** The declared `setup_min_qualify_score = 60` sits
  above the entire observed distribution, so if it were enforced almost nothing
  would trade (§14). Because it is not enforced, all setups trade anyway.
- Higher buckets (40–59 and above) are **unreachable** given the scoring
  architecture; this explains, at the root, the "weak score discrimination"
  reported in Phase 5.1 §4H.

## 8. Component-score distribution

Component **scores** taken from the domain `Setup.scores` at detection (all
setups, all datasets identical pattern):

| Component | Mean | Always 0? | Comment |
|---|---|---|---|
| TREND | 0.0 | YES (100%) | Contribution is absent at detection |
| STRUCTURE | 0.0 | YES (100%) | Structure is a hard gate, not a score (§14) |
| PULLBACK | 74.9–77.1 | No | Only genuine score in play |
| MOMENTUM | 0.0 | YES (100%) | Scored only at (post-detection) confirmation |
| VOLUME | 6.9–7.0 | No | 52–58% of setups score 0 |
| VOLATILITY | 75.0 | No | Effectively constant 75 |

**Root finding on the score.** The **detected-setup overall score is built almost
entirely from PULLBACK + VOLATILITY**, because TREND and STRUCTURE contribute 0
(they are gates, not scores) and MOMENTUM contributes 0 until the confirmation
step that happens after detection. With weight sum 0.84,

```
raw ≈ PULLBACK·0.15 + VOLATILITY·0.05  →  norm = raw / 0.84
```

So the normalized score set (≈85→15 and 75→4.5 / 0.84) clusters in the **teens**,
does not reflect trend quality, structure quality, or prior momentum, and is
therefore both **low and non-discriminating** relative to the setup family it
feeds. This is a **FACT** of the current architecture, not an opinion.

Momentum at qualification is never NEUTRAL (POS 2,236–2,317 / NEG 657–818 / NEUTRAL 0),
consistent with `_confirms` requiring a supporting signal.

## 9. Qualification → outcome

| Dataset | Qualified | COMPLETED (traded) | QUALIFIED_NO_TRADE | EXPIRED (post-qual) |
|---|---|---|---|---|
| BTC 1D | 437 | 437 (100%) | 0 | 0 |
| ETH 1D | 402 | 402 (100%) | 0 | 0 |
| BTC 4H | 3,135 | 3,135 (100%) | 0 | 0 |
| ETH 4H | 2,893 | 2,893 (100%) | 0 | 0 |

Every qualified setup traded. **A score-qualified setup cannot be separated from
a momentum-confirmed but low-score setup** because qualification does not consult
the score at all. The qualification step is, in effect, a binary "did momentum
confirm for ~2 bars" test (§11), not a quality discriminator.

## 10. MAE/MFE analysis

| Dataset | Bucket | n (trades) | avg MFE (px) | avg MAE (px) | MFE/MAE | avg ret |
|---|---|---|---|---|---|---|
| BTC 1D | 0–19 | 269 | 2,475 | 1,734 | 1.43 | +0.5% |
| BTC 1D | 20–39 | 168 | 2,462 | 1,876 | 1.31 | +1.6% |
| ETH 1D | 0–19 | 272 | 138 | 122 | 1.12 | +0.9% |
| ETH 1D | 20–39 | 130 | 138 | 129 | 1.07 | +1.8% |
| BTC 4H | 0–19 | 1,899 | 870 | 738 | 1.18 | +0.04% |
| BTC 4H | 20–39 | 1,236 | 1,105 | 773 | 1.43 | +0.4% |
| ETH 4H | 0–19 | 1,834 | 52 | 43 | 1.21 | +0.07% |
| ETH 4H | 20–39 | 1,059 | 53 | 44 | 1.20 | +0.2% |

- **The score does not strongly separate MAE.** Higher-bucket (20–39) trades have
  slightly higher MFE and marginally better returns, but the MAE gap is small and
  the 0–19 bucket already carries the bulk of trades. Within the *available*
  0–39 range, score adds only weak ordering — consistent with §7/§8.
- **Expectancy is thin on 4H** (avg returns +0.04% to +0.4%), driven by heavy
  position-age exits (§4), i.e. trades that neither stop out nor reach target in
  60 bars. The MFE/MAE ratio being ≥ 1 does not rescue a strategy when the win
  rate is ~41–45% and the average favourable excursion is not realized as exit.

## 11. Setup age / latency

| Dataset | detection→entry bars | min | max |
|---|---|---|---|
| BTC 1D | 2.29 | 2 | 21 |
| ETH 1D | 2.40 | 2 | 25 |
| BTC 4H | 2.21 | 2 | 23 |
| ETH 4H | 2.28 | 2 | 24 |

- Detection → qualification → entry is **near-instantaneous** (≈2 bars; the
  minimum is 2 by the two-bar confirmation path). `setup_max_lifetime_bars = 24`
  is effectively never the binding constraint *after* detection for setups that
  qualify, which is why setup-level expiry is all pre-qualification (§4).

## 12. 4H vs 1D (the Phase 5.1 focus)

- **4H is strictly weaker than 1D on every family here**, matching the Phase 5.1
  headline. TREND_CONTINUATION on BTC 4H is net negative (PF 0.78); PULLBACK on
  BTC 4H net negative (0.80); 4H aggregate win rates are ~41–45% vs 1D ~48–63%.
- **4H is dominated by position-age exits** (§4): ~50% of 4H trades end in the 60-bar
  timeout, a rate much higher than 1D. This is an OBSERVATION with a clear,
  though not yet tested, causal story: the same 60-bar lifetime is a far shorter
  holding *in time* on 4H strategy terms and *in market-distance terms* the
  average 4H swing does not resolve to target within it.
- The **expiration/scoring problems are not 4H-specific** in mechanism — the
  dead-score/threshold/invalidation findings (§7/§8/§14) are identical across all
  four datasets. 4H merely amplifies them by generating ~7× more setups and
  closing half of them by timeout.

## 13. Regime analysis (baseline flat)

The frozen baseline does not regime-filter (E2 showed a regime-only filter is
UNSUPPORTED). E4 confirms the plumbing: setups carry a stamped `regime` at
detection, `_expired` drops a setup only if the *current* regime transitions away
from the stamped one *before* qualification — but since qualification happens ~2
bars after detection, regime transitions rarely precede qualification. Hence
setup-level regime expiry is a minor contributor vs age (§4). No quantifiable
regime effect on score quality is separable at this stage (the score does not
encode regime).

## 14. Architectural findings (structural diagnosis — the core of E4)

These are **FACTS of the code** (verified on source) that explain the observed
behaviour, not hypotheses:

1. **`setup_min_qualify_score = 60` is dead.** Declared in
   `config.py:106` / `frozen_config.py:90` / documented in `docs/phase-3.md:127` /
   snapshotted in `engine.py`, but **never** referenced by the qualification path
   (`SetupEngine.advance` / `_confirms`). Qualification is driven solely by
   momentum confirmation and age expiry. → Every momentum-confirmed setup trades
   regardless of score (§5/§9). This is the direct mechanism for "low-quality
   setups qualify."
2. **`setup_min_structure_score = 70` is dead.** Stored on
   `TrendContinuationDetector._min_structure_score` (`trend_continuation.py:43`)
   but never used in `_rules_hold`. Structure quality is a discrete hard gate
   (must equal `HH_HL`/`LH_LL`), not a score threshold. → STRUCTURE score is always
   0 and contributes nothing to the normalized score (§8).
3. **Score architecture collapses to PULLBACK+VOLATILITY** because TREND/
   STRUCTURE/MOMENTUM contribute 0 at detection (§8), pinning all normalized
   scores to 0–39 and making ≥40 unreachable (§7).
4. **Invalidation is effectively dead in practice.** Zero setups are INVALIDATED
   and zero trades close for INVALIDATED across all 7,713 detections. The
   `_invalidated` BOS/opposing-structure check (`setup_engine.py:166`) exists but
   the (near-immediate entry + short average hold) combination means it never
   fires. Combined with (1), the engine has effectively **no quality filter** and
   **no protective exit** between detection and entry in practice.
5. **BREAKOUT measured-move target is not set in production.** STRUCTURE meta
   carries no `value`, so the breakout's measured-move objective (§breakout.py)
   never materializes; matches prior diagnostics.
6. **Breakout-retest does not verify a prior break** before treating a retest as
   valid (matches prior diagnostics, `breakout_retest.py`).

These six together are the *structural* explanation of "why setups are low
quality": the score does not measure what entrants care about, the score gate is
not enforced, and protective/resolution machinery is effectively inactive.

## 15. Look-ahead / leakage audit (re-confirmed for E4)

The E4 harness relies on the same causal snapshots as the baseline. Re-verified:

- Causal path `analyze_each[i]` is byte-identical to `analyze(df.iloc[:i+1])` for
  every engine — no forward reference.
- `StructureEngine` pivots are confirmed k bars later and gated by `p <= i-k`;
  no `center=True` windows, no `.shift(-1)`, no forward `.iloc`.
- Only `.shift()` anywhere in market is `volatility.py:96` (`close.shift(1)`, a
  backward reference).
- **No look-ahead bug found** in the causal path. This is a **FACT**.

## 16. Correlation vs causation discipline

- **FACT:** code-level dead-config findings (§14) and the constant component
  scores (§8).
- **FACT:** the funnel/score-bucket numbers (§5/§7) as produced by the frozen engine.
- **OBSERVATION / CORRELATION:** 4H weakness correlates with elevated 60-bar
  position-age exits (§4/§12); score 20–39 correlates with slightly better
  returns than 0–19 (§10). These are correlational, not proven causal.
- **HYPOTHESIS (not tested here):** enforcing a score floor, activating the
  structure/score gates, or setting the breakout measured-move target would raise
  average quality. Each requires its own experiment. E4 does **not** claim any of
  these remedies are correct — that is E5+ scope and needs approval.
- **UNKNOWN:** why exactly the overhead of 60-bar position-age exits is so high on
  4H; why invalidation never triggers (the structural tools exist); whether a
  meaningful score spread could be restored by giving TREND/STRUCTURE/MOMENTUM
  genuine numeric scores.

## 17. Limitations

- Scores/MAE/MFE are computed on the frozen baseline's own definitions; no new
  quality metric was contrived.
- Instrumented rows join to the canonical `SetupRecord`/`BacktestTrade`; the ~2-bar
  latency and 100% qualify→enter hold exactly as the engine recorded them.
- Aggregate per-dataset numbers combine IN/VAL/OOS across full history; no
  period split is re-derived here (the same single-run slices used in validate.py
  remain authoritative).
- Correlation-vs-causation limits noted in §16.

## 18. Diagnosis (summary of why setups are low quality)

1. The engine has **no effective quality gate**: `setup_min_qualify_score` is
   unenforced, and qualification is only a momentum-confirmation step → essentially
   **every** momentum-confirmed setup trades, including low-value ones.
2. The **normalized score cannot exceed 39** by construction and barely
   discriminates, because TREND/STRUCTURE/MOMENTUM contribute 0 and the effective
   weights collapse to PULLBACK+VOLATILITY → the score cannot serve as a filter even
   if it were enforced.
3. On 4H, ~half of opened trades are closed by the 60-bar position timeout (an
   *expiration* in effect), which dominates outcomes and flattens expectancy;
   TREND_CONTINUATION on 4H is net negative and PULLBACK on BTC 4H net negative.
4. Protective/invalidation and measured-move machinery is effectively inactive,
   so the strategy rarely cancels poor setups or resolves toward a defined target.

## 19. Recommended next steps (for approval only — not executed)

These are candidate directions, listed for prioritization; implementing any is a
separate experiment requiring explicit approval:

1. **E5-*ScoreGate***: enforce an evidence-based score floor as a qualification
   requirement (requires first making the score discriminate — see 2).
2. **E5-*ScoreArchitecture***: give TREND/STRUCTURE/MOMENTUM genuine numeric
   contributions at detection so the normalized score spreads across ≥ 40 and can
   separate quality. This is the enabling change behind 1.
3. **E5-*PositionAgeOn4H***: investigate the 60-bar position timeout on 4H
   (shorter lifetime, or re-arm resolution) — directly targets the largest single
   outcome driver found here.
4. **E5-*Invalidation/Protection***: determine why `_invalidated` never fires and
   whether activating it improves protective exits.
5. **E5-*BreakoutTargetRetest***: wire the production measured-move target and
   verify the breakout-retest prior-break condition.

Each proposal must be pre-registered (hypothesis, exact change, primary 4H
validation window, and a commitment that the frozen baseline stays untouched)
before implementation.

---

*E4 made no behavioural change. Frozen baseline `phase5-baseline-v1` is bit-for-bit
unchanged. All instrumentation lives in `src/veyra/research/e4_forensics.py` and
its output `reports/phase5_1/_e4_forensics_data.json`.*