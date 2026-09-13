# Veyra Phase 5.1 — E9 Scoping & Correction (`e8-errata` + `e9-design`)

Experiment status: **design/correction only — no code changed, no run executed.**
Frozen control: `phase5-baseline-v1`. Generated: 2026-09-03.

> This document does two things: **(1)** it corrects a material mis-statement in
> `reports/phase5_1/e8_stop_target_geometry.md` found during a full source audit,
> and **(2)** it pre-registers the single production experiment (E9) that the
> audit isolates as the highest-leverage move toward a working Veyra. It changes
> nothing.

---

## 1. Why this document exists (integrity rule 4: do not fabricate statistics)

During a full read of the production code path
(`strategy/detectors/*.py` → `strategy/setup_engine.py::_promote` →
`backtest/simulator.py::_levels`/`enter` → `backtest/execution.py::compute_exit`),
a claim in the E8 report was found to be imprecise and is corrected here before
any further action is taken on it.

## 2. What the source audit actually establishes

Every decision that affects a trade's geometry:

- **STOP** = `interest_area.low` (LONG) / `interest_area.high` (SHORT) — i.e. the
  broken swing level / support, set in `backtest/simulator.py::_levels`. For
  BREAKOUT / BREAKOUT_RETEST this is ≈ the current swing level, a small distance
  from entry (confirms E8 G1: tight stop).
- **TARGET** = `setup.targets[0].high/low`, set by each detector and **validated
  at entry time** in `backtest/simulator.py::enter`:

  ```python
  if target is not None:
      if side == "LONG"  and target <= raw_open: target = None
      if side == "SHORT" and target >= raw_open: target = None
  ```

  `raw_open` is the **next-bar** open (entry is `next_open`, one bar after the
  detection bar). Any target that the next-bar open gaps *through* is discarded.

### Correction 1 — E8 §G2 "BREAKOUT has no target by construction" is imprecise
`breakout.py` **does attempt** to set a target:
```python
move = abs(price - level); cand.targets = [PriceZone(price + move, price + 2*move)]
```
Yet on every dataset, **0% of BREAKOUT trades carry a usable target**
(`used_target=0`; E7 data). So BREAKOUT trades are *effectively* targetless — but
the cause is not "detector emits no target," it is that the emitted target does
not survive into the traded population. The E8 wording ("no target by
construction") was too strong and is withdrawn.

### Correction 2 — the precise, dominant defect is BREAKOUT_RETEST entry-nulling
| Dataset | BREAKOUT_RETEST trades | target nulled at entry | then exits as |
|---|---|---|---|
| BTC 1D | 60 | 26 (43%) | STOP 15 / EXPIRED 11 |
| ETH 1D | 50 | 24 (48%) | STOP 14 / EXPIRED 10 |
| BTC 4H | 820 | **453 (55%)** | STOP 239 / EXPIRED 214 |
| ETH 4H | 693 | **339 (49%)** | STOP 180 / EXPIRED 159 |

**Half of BREAKOUT_RETEST positions are structurally targetless** — their target
(`level*1.03..1.06`, `breakout_retest.py:89`) sits on the wrong side of the next
bar's open, so `enter()` nulls it and the trade can only STOP or EXPIRED. This is
the concrete, code-level source of E7's "~half of trades can only stop/expire"
observation, and it is fixable in one place.

By contrast PULLBACK / TREND_CONTINUATION rarely null (2–12%).

## 3. Why this is the 10/100 lever (not "widen the stop")

- E7/E8 showed the *symptom*: STOP losses dominate; ~half of trades have no
  target; realized RR is inverted (TARGET ~0.3–0.6, STOP ~3–4).
- The audit shows the *cause* at the code level: **the target is defined off the
  detection snapshot but validated off the entry bar, and the one-bar gap nulls it
  — mostly for the family with the most STOP losses (BREAKOUT_RETEST).** Widening
  the stop (E8 G1) is secondary to *restoring a survivor target* because a
  targetless position cannot win on the profit side at all.
- The score (E5/E6) is a dead ranking label — irrelevant here. Entry timing / the
  timeout (E3/E1) were already ruled out. The binding, code-confirmed defect is
  target survival.

## 4. Proposed E9 (pre-registered — NOT RUN)

**E9 — Target-Survival Fix (single production change, one hypothesis):**

> Change the target so it **cannot be nulled at entry** while remaining causal and
> conservative: for any family that carries a target, the entry-time check is
> replaced by re-anchoring the target to the *fill/open* on the trade side (so it
> is always on the profit side), with the distance preserved exactly as the
> detector defined it. Nothing else changes.

Constraints honoured:
- **Causal only:** the re-anchor uses only the entry fill (already known at
  decision time) — no realized path, no counterfactual, no look-ahead.
- **Pre-registered:** the rule is fixed as stated; it is **not** fit against E9's
  own results or the E8 realized-MFE distributions.
- **One knob:** only target survival changes. Stop, timeout, fees, entry, overlap,
  detectors, score — all frozen at `phase5-baseline-v1`.
- **IN / VAL / OOS discipline:** run on IN first; if IN is not clearly improved
  (net-positive expectancy + PF>1 and monotone) the mechanic hypothesis is
  rejected (no re-fit). VAL/OOS held untouched for final confirmation.

**Falsifier:** the identical rule must produce a material, forward-consistent lift
on IN that persists to VAL/OOS. If it does not, the exit-mechanics root cause
(E7/E8) is rejected and we conclude the current detection has no
spontaneously-reachable edge and Veyra is not yet deployable as a trading system.

## 5. Deliverables / state

- **Changed:** nothing. `src/veyra/strategy/detectors/*.py`,
  `src/veyra/backtest/simulator.py`, `frozen_config.py` all byte-identical.
- **This document** records the correction and the E9 pre-registration for
  approval before any code change is made.

## 6. Explicit approval request

Proceeding requires a **documented departure from the strict freeze** for the
single E9 target-survival rule above (a production change to the exit geometry
only). It will be version-stamped (e.g. `phase5-e9-v1`) and does not alter the
recorded `phase5-baseline-v1` semantics for any other question. Awaiting sign-off
to implement and run E9.