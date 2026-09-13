# Phase 5 — Step 1: Audit of Phase 4/3 mechanics for look-ahead safety & decision freeze

Status: **PASSED** (with regression tests added)

Date: 2026-09-02

## 1. Scope & method

Audited the machinery that real-data validation depends on, focusing on the
must-hold invariant:

> decision at T → only info available at T → qualification → entry at T+1 open
> → outcome begins at T+1.

Approach: static review of the relevant files **and** empirical freeze/prefix
tests against the production `BacktestEngine`. No parameters were changed.

Files reviewed:

- `src/veyra/backtest/engine.py` — replay driver, slice-per-bar, `_analysis_range`
- `src/veyra/backtest/simulator.py` — position/setup/outcome tracking, `_levels()`
- `src/veyra/backtest/execution.py` — `compute_exit` / entry-price semantics
- `src/veyra/backtest/outcomes.py` — `classify_outcome`
- `src/veyra/backtest/normalize.py` — `ScoreNormalizer`
- `src/veyra/strategy/setup_engine.py` — `detect` / `advance` lifecycle
- `src/veyra/domain/setup.py` — `Setup` (frozen-at-detection fields)
- `src/veyra/market/structure.py` — pivot confirmation / BOS
- `docs/phase-4.md` — documented intent

## 2. Look-ahead audit (per link in the chain)

### 2.1 Analysis uses only the prefix up to bar `i`

`engine.py` iterates `range(process_start, process_end)` and passes
`available = df.iloc[:i+1]` to `pipeline.analyze(...)` (engine.py:192). Every
indicator/regime/volume/volatility computation therefore sees only candles up
to and including bar `i`. This is the structural guarantee against all-field
look-ahead. **OK.**

### 2.2 Pivot confirmation (structure)

`structure._find_pivots` finds a pivot at index `p` only over a window
`[p-k, p+k]`, and iterates `range(k, n-k)` (structure.py:121-134). When called
with slice length `n = i+1`, a pivot at index `p` is only discoverable when
`p+k <= n-1`, i.e. once `k` future bars are present in the slice — so no pivot
is ever available to a decision before its confirmation window is inside the
slice. `analyze` labels structure from `swing_highs[-1]`/`swing_lows[-1]`
(the **last confirmed** pivots) and BOS compares `closes[-1]` against the last
confirmed swing level (structure.py:179-191). **OK — no pivot/BOS look-ahead.**

### 2.3 Setup fields are frozen at detection

`SetupEngine._promote` constructs a **new** `DomainSetup` per detection
(setup_engine.py:110-127), copying `interest_area`, `invalidation`, `targets`,
`regime`, `expiry_condition`, `reasoning`, `evidence` from the candidate, and
applies scoring then. The simulator (`sim.register`) stores that object;
`advance()` mutates only `state`, never score/interest-area/invalidation/
targets (setup_engine.py:137-205). `_levels()` reads, never writes
(simulator.py:150-172). Because each bar builds a fresh snapshot from a fresh
slice, re-running on a longer series rebuilds the earlier setups from the same
prefix and yields bit-identical decision-time fields. **OK.**

### 2.4 Entry at T+1 open, outcome from T+1

Entry is armed on the decision bar and filled at the **open** of the following
bar (engine.py:184-188). Exits for the bar are then resolved on that bar's
high/low (engine.py:219-224), so a position entered at the open sees the same
bar's range (no intrabar ordering assumption). **OK.**

### 2.5 Terminal closure & overage

Terminal setups that still hold an open position are force-closed at bar close
(engine.py:209-216); over-aged positions are force-closed (engine.py:224).
Open positions at end-of-data are closed at the last known price with no
future bars (engine.py:231-241). **OK.**

### 2.6 Trade/setup ordering & stable keys

Trades sorted by `(entry_ts, trade_id)` and setups by `(detection_ts, key)`
(engine.py:245-246) for deterministic reporting. Keys are stable across
appended data (verified empirically). **OK.**

## 3. Empirical freeze test (THE critical Phase 5 gate)

Regression tests added in `tests/unit/test_phase5_audit.py`:

1. `test_decision_fields_are_frozen_when_future_candles_appended` — runs the
   production engine on a frame of 400 candles and on a truncation of the
   *same* frame (240 candles). For every historical setup present in the short
   run, the long run reproduces it with **bit-identical** decision-time fields
   (type, side, regime, score, score_normalized, interest area, invalidation)
   and reports **no extras** before the boundary. **PASSES.**
2. `test_freezing_is_order_and_count_preserving` — ordered detection timestamps
   agree up to T. **PASSES.**
3. `test_identical_prefix_does_not_receive_extremal_setup_swaps` — the *set* of
   setups at-or-before T is identical. **PASSES.**
4. `test_generated_frames_share_a_data_prefix` — guards the test prologue so a
   single frame is used and only truncated (not two independent random walks).
   **PASSES.**

All 4 pass. The freeze holds by construction and is now pinned by tests.

## 4. Other verification points

- **Setup lifecycle states**: CANDIDATE/DETECTED → DEVELOPING → QUALIFIED →
  TRIGGERED, with terminal INVALIDATED/EXPIRED/COMPLETED. No ambiguous
  transitions; `classify_outcome` never treats "never triggered" as a loss
  (outcomes.py). Reused unchanged by paper trading.
- **Score normalizer**: `raw 0..84 → 0..100` via `weight_sum`, documented; no
  double-scaling (Phase 4 fix verified).
- **Invalidation & interest-area tiers**: read-only derivations; no re-target.
- **Determinism**: `test_run_is_deterministic` (pre-existing) passes.

## 5. Residual notes / non-issues

- `bull_continuation_frame(n)` produces geometry that depends on `n`; the
  audit tests therefore generate **one** frame and truncate it, rather than
  calling the generator twice. (A pre-existing Phase 4 test
  `test_appending_future_candles_does_not_change_past_decisions` compared two
  *independent* draws and is weaker; Phase 5's is the correct, stricter one.)
- No evidence of look-ahead in stop/target/interest-area derivation. Targets
  validated at entry time (fill not yet known at detection), which is correct.

## 6. Verdict

The `decision at T → only info up to T → qualify at T → entry at T+1 open →
outcome from T+1` chain is **sound**. The critical freeze test passes. No fixes
were required; the added regression tests guard all Phase 5 acceptance evidence.