# Veyra — Phase 3 Architecture Decision Record

Phase 3 built the **Setup Detection + Scoring Engine**: deterministic,
machine-testable setup candidates that consume the Phase 2 `MarketSnapshot`
(Trend / Structure / Momentum / Volume / Volatility + Regime) and turn them into
explicit, lifecycle-managed setups with scoring, prioritised execution. It adds
persistence of every lifecycle event.

Phase 3 is **explicitly not** a backtester, not a trade executor, not a Telegram
bot, and makes no ML/AI calls. It does not produce entry/TP/SL prices — it
produces *qualified setups* for a later execution phase.

---

## 1. Final folder structure (additions in Phase 3)

```text
src/veyra/
├── domain/
│   └── setup.py            # Setup, SetupState, PriceZone (+ evidence, to_dict)
└── strategy/
    ├── lifecycle.py        # SetupState machine (transition table)
    ├── snapshot_view.py    # tolerant MarketSnapshot reader for detectors
    ├── candidate.py        # SetupCandidate (pre-score raw detection)
    ├── aggregation.py      # WeightedScoreAggregator (moved here, re-exported)
    ├── scoring.py          # SetupScorer (component metas -> 0-100 -> overall)
    ├── setup_engine.py     # SetupEngine: detect/score/advance + persistence
    └── detectors/
        ├── base.py                  # SetupDetector ABC
        ├── rules.py                 # shared deterministic predicates
        ├── trend_continuation.py    # TREND_CONTINUATION
        ├── pullback.py              # PULLBACK
        ├── breakout.py              # BREAKOUT
        ├── breakout_retest.py       # BREAKOUT_RETEST
        ├── range_rejection.py       # RANGE_REJECTION
        └── __init__.py              # default_detectors()
└── database/
    └── setup_repository.py # Setup / SetupEvent persistence
```

---

## 2. Setup types

Five setup types, each with an explicit detection rule set. Detectors read only
the encoded Phase 2 snapshot (component metas + regime) — they **never
recalculate indicators**.

| Type                  | Regime | Idea |
|-----------------------|--------|------|
| `TREND_CONTINUATION`  | BULL/BEAR | Trend aligned with structure and steady momentum; continuation expected. |
| `PULLBACK`            | BULL/BEAR | Price retraced within tolerance inside an intact uptrend/downtrend. |
| `BREAKOUT`            | BULL/BEAR | Price cleared the last confirmed swing with a break-of-structure action. |
| `BREAKOUT_RETEST`     | BULL/BEAR | Price broke out and is still resting at the old level (confirmation). |
| `RANGE_REJECTION`     | RANGE    | Price at a range boundary with non-trending structure (mean-reversion idea). |

---

## 3. Detection rules (deterministic, configurable)

Each detector gates on shared predicates (`strategy/detectors/rules.py`), then
applies its own explicit conditions. All values are strict deterministic
comparisons; no randomness, no ML, no hidden state.

**Shared predicates:** `trend_direction`, `trend_strength`, `structure_state`,
`structure_action`, `last_swing_high/low`, `momentum_state`, `volume_state`,
`relative_volume`, `price_volume_confirmed`, `volatility_state`, `atr_percent`,
`rsi`, `macd_histogram`, `side_of`, `base_candidate`.

- **TREND_CONTINUATION** (long): regime BULL, trend BULLISH & strength ≥
  `setup_min_trend_strength` (55), structure is `HH_HL`, structure action is
  `NONE` (no fresh breakout), momentum POSITIVE, volatility not extreme, active
  volume confirmation.
- **PULLBACK** (long): regime BULL, trend BULLISH, structure HH_HL, price
  retraced from the last swing high into
  `[setup_pullback_min_retrace, setup_pullback_max_retrace]` (2–35%), momentum
  neutral-to-negative (retrace is happening), unaffected trend above the slow EMA.
- **BREAKOUT** (long): side LONG, structure HH_HL with action
  `BREAK_OF_STRUCTURE_UP`, price above
  `last_swing_high × (1 + setup_breakout_distance_pct)` (clearance 0.1%),
  momentum POSITIVE, elevated relative volume.
- **BREAKOUT_RETEST** (long): same breakout context, but price is *within*
  `setup_retest_tolerance_pct` (2%) of the level and still above it — price
  "round-tripped" the breakout level without collapsing back.
- **RANGE_REJECTION** (long/short): regime RANGE (non-directional), structure
  not trending (`NEUTRAL`/`LH_HL`/`HH_LL`-tightening), price near a boundary
  within `setup_range_boundary_tolerance_pct` (1.5%) of the last swing high/low.

Full asymmetric (short) mirrors of each are defined; a detector returns no
candidate when the conditions do not hold. Every detector records `evidence`
(exact observed values) and `reasoning` for auditability.

---

## 4. Scoring formula

`SetupScorer` maps each component's snapshot meta into a **0–100 component
score**, then combines via the shared `WeightedScoreAggregator`:

| Component | Weight | Scored from |
|-----------|-------:|-------------|
| Trend     |  0.25  | direction alignment + strength |
| Structure |  0.20  | structure-alignment vs. side (HH_HL for long, LH_LL for short) |
| Pullback  |  0.15  | retrace depth vs. ideal band (quality, not direction) |
| Momentum  |  0.12  | momentum sign vs. side + magnitude |
| Volume    |  0.07  | relative volume + price-volume confirmation |
| Volatility|  0.05  | ATR% within tolerable band |

Weights sum to **0.84**, so **overall score caps at 84**. The score is a
**ranking / quality measure, never a probability** — it does not claim likelihood
of success. Component scores are aligned to `side` (opposing structure or
momentum penalises). Overall score is combined only from `READY` components;
`INSUFFICIENT_DATA` components are treated as 0 and block qualification (never
fabricate a value).

---

## 5. Weights & thresholds (configurable)

All in `Settings` (env prefix `VEYRA_`):

| Setting | Default |
|---|---|
| `weight_trend/structure/pullback/momentum/volume/volatility` | 0.25/0.20/0.15/0.12/0.07/0.05 |
| `setup_min_trend_strength` | 55.0 |
| `setup_min_structure_score` | 70 |
| `setup_min_qualify_score` | 60 |
| `setup_max_lifetime_bars` | 24 |
| `setup_pullback_min_retrace` / `max_retrace` | 0.02 / 0.35 |
| `setup_breakout_distance_pct` | 0.001 |
| `setup_retest_tolerance_pct` | 0.02 |
| `setup_retest_max_bars` | 20 |
| `setup_range_boundary_tolerance_pct` | 0.015 |

These are **initial hypotheses** to be recalibrated by Phase 4 backtesting.

---

## 6. Lifecycle state machine (`strategy/lifecycle.py`)

```
DETECTED ──> DEVELOPING ──> QUALIFIED ──> TRIGGERED ──> COMPLETED
   │             │              │              │
   ├─────────────┴──────────────┴──────────────┴───> INVALIDATED
   └─────────────────────────────────────────────────> EXPIRED
```

- **Terminal states** (`INVALIDATED`, `EXPIRED`, `COMPLETED`) have **no outgoing
  edges**.
- `transition(old, new)` returns the new state, is a no-op on identity, and
  **raises `ValueError` on any illegal transition** (e.g. `COMPLETED → DETECTED`).
- `_PATHS` is an explicit adjacency table kept in one place for auditability.

---

## 7. Invalidation rules

A live setup is invalidated when the market no longer supports its hypothesis:

- **Structure opposes the side** (e.g. a LONG setup sees structure flip to
  `LH_LL`, or price closes through the invalidation level).
- **Regime flips** to the opposing direction (BULL→BEAR) or to an incompatible
  state *before* the lifetime expiry check would fire.
- **Setups are self-invalidating** when a pullback turns into a breakout-through
  the invalidation boundary defined at detection.

Invalidations are **persisted as `SetupEvent`s** so Phase 4 can study why setups
died without look-ahead bias.

---

## 8. Expiration rules

- A setup auto-expires once its live age exceeds
  `setup_max_lifetime_bars` (24 bars) without qualifying/triggering.
- `expiry_condition` is attached at detection and re-evaluated deterministically
  on each `advance()` call; exceeding it yields `EXPIRED`.

---

## 9. Price-zone logic

- `PriceZone(low, high)` is the **only** price representation used
  (`interest_area`, `targets`, invalidation levels). Zones arise strictly from
  detector swing levels (`last_swing_high/low`) — **no arbitrary $$$ levels**.
- `interest_area` is a small price band around the actionable swing level; a
  LONG breakout zones the band just above the last swing high, a RANGE rejection
  zones the boundary itself.

---

## 10. Multi-timeframe architecture

- Phase 3 computes setups **independently per timeframe** (`1D` and `4H` both
  feed their own `SetupEngine` from their own `MarketSnapshot`).
- There is **no cross-timeframe strategy** yet — alignment/confirmation between
  the two is intentionally deferred to Phase 4. Each timeframe's setups are
  tracked, scored, and persisted under their own `(symbol, timeframe)` key.

---

## 11. Persistence changes

- `Setup` ORM row (already present from the Phase 0 schema) now stores
  `evidence_json`, `regime`, `overall_score`, `scores_json`, `interest_area`,
  `invalidation`, `targets_json`, `state`, `reasoning`, and `expiry_condition`.
- `SetupEvent` is an **append-only** lifecycle log (`DETECTED`, `DEVELOPING`,
  `QUALIFIED`, `TRIGGERED`, `INVALIDATED`, `EXPIRED`, `COMPLETED`).
- `SetupRepository` (`database/setup_repository.py`) provides `create()`,
  `update_state()`, `get()`, `find_live()`, `events_for()`. Only the initial
  `DETECTED` event is written on create; every state change appends an event.

---

## 12. Tests

**43 new Phase 3 tests** (deterministic synthetic data; no live network):

| File | Tests | Coverage |
|---|---|---|
| `tests/unit/test_setup_detectors.py` | 22 | all 5 setup types + short mirrors + negative cases |
| `tests/unit/test_lifecycle.py` | 7 | legal paths, terminal lock, illegal-transition raises |
| `tests/unit/test_setup_scoring.py` | 4 | weighting, opposing-structure penalty, aggregation |
| `tests/unit/test_setup_engine.py` | 10 | gate on poor data-quality, promote+score, advance, invalidate, expire, **end-to-end candles → pipeline → detect → persist** |

Added `tests/unit/setup_synth.py` (deterministic `MarketSnapshot` builders) and
`bull_continuation_frame` in `tests/unit/market_synth.py` (HH/Hl zigzag ending
on an up-leg so final momentum is positive).

**Full suite: 144 tests passing** (Phase 0/1 + Phase 2 101 + Phase 3 43).

---

## 13. Known limitations

- **Meta-only inputs:** detectors rely on component `meta`/`value` encoded by
  Phase 2 engines; sampling or aggregation choices there propagate here.
- **No execution logic:** qualified/triggered setups record intent only; there
  are no simulated fills, slippage, or position sizing yet.
- **Score ≠ probability:** the 0–84 overall score is a quality ranking,
  uncalibrated; it is verbose by design and cannot support a probability claim.
- **No cross-timeframe confirmation:** 1D and 4H are independent; alignment is a
  Phase 4 concern.
- **Threshold hypotheses:** every weight/threshold is unvalidated and intended
  for Phase 4 calibration.
- **Intra-bar detail absent:** swing pivots use whole-bar high/low (Phase 2
  limitation), so hypothetical intra-bar sequences are not modelled.

---

## 14. Architectural changes

- Moved `WeightedScoreAggregator` from `strategy/setup_engine.py` into
  `strategy/aggregation.py`; re-exported from `setup_engine.py` for
  `pipeline.py` and backward-compatible scoring tests.
- `Domain.Setup` gained a typed `evidence` field and an explicit `to_dict()`
  (no longer `asdict`), and uses a `Regime` enum alongside `SetupType`.
- `SnapshotView` is the stable reader boundary for detectors and now exposes
  `price()` (the STRUCTURE component's last-close value, with
  `meta["value"]` fallback) so detectors never fabricate a price.
- Setup detection runs on `READY`/valid quality only: `detect()` returns `[]`
  for `INSUFFICIENT_DATA` / `INVALID` / `UNKNOWN` snapshots.
- `DataQuality.row_count`/`validation_valid` now feed through the test snapshot
  helper consistently with `resolve()`.

---

## 15. Phase 4 recommendation

Phase 4 should **backtest** the detected setups against our own Phase 2/3 rules
on historical candles to calibrate weights/thresholds and the lifecycle:

- Add a **vectorised/streamed backtester** over confirmed-at-time pivots (using
  the look-ahead-safe swings Phase 2 already defines) that replays
  `detect → score → advance` per candle and records outcomes.
- Calibrate **qualification score**, **retrace band**, **expiry bars**, and
  **invalidation levels** against a defined, conservative success metric
  (e.g. favourable excursion to the interest area).
- Consider **cross-timeframe confirmation** (1D trend + 4H setup) once per-TF
  backtest numbers are stable.
- Only after calibration, decide whether to expose qualified setups to a
  watchlist/notifier. Probability calibration belongs even later, and only if
  the backtest quality justifies it.