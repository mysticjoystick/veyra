# Veyra — Phase 2 Architecture Decision Record

Phase 2 built the **Market Intelligence Engine**: deterministic, configurable,
reproducible analysis of Trend, Structure, Momentum, Volume, and Volatility,
plus a Regime resolver that combines them. It wires these into the existing
`MarketAnalysisPipeline` to produce a rich, serializable `MarketSnapshot` with
structured evidence and explicit data-quality state.

Phase 2 deliberately produces **no signals, no setups, no entry/exit levels,
and no ML**. It is the "analyst's read" of the market only.

---

## 1. Final folder structure (additions in Phase 2)

```text
src/veyra/
├── domain/
│   ├── snapshot.py         # MarketSnapshot, AnalysisComponentOutput, RegimeOutput
│   ├── data_quality.py     # DataQuality state model
│   └── __init__.py         # + TrendDirection, StructureState/Action, MomentumState,
│                           #   VolumeState, VolatilityState, DataQualityState, IndicatorState
└── market/
    ├── engine.py           # AnalysisEngine contract (+ warmup_required())
    ├── trend.py            # TrendEngine
    ├── structure.py        # StructureEngine
    ├── momentum.py         # MomentumEngine
    ├── volume.py           # VolumeEngine
    ├── volatility.py       # VolatilityEngine
    ├── regime.py           # RegimeEngine.resolve(...)
    └── pipeline.py         # wired to the five real engines
```

---

## 2. The five engines and their formulas

All engines share the `AnalysisEngine` contract: `required_columns()`,
`warmup_required()`, and `analyze(df) -> EngineResult`. Low-data inputs produce
`EngineResult` with `state = INSUFFICIENT_DATA` and scores of `0` — values are
**never fabricated**.

### TrendEngine (`~ema_fast=50, ema_slow=200, slope_lookback=3`)
- `ema_fast = EWM(span=50).mean()`, `ema_slow = EWM(span=200).mean()`.
- **Direction** from EMA cross + price vs slow EMA:
  - `BULLISH` ⇔ `price > ema_slow` **and** `ema_fast > ema_slow`.
  - `BEARISH` ⇔ `price < ema_slow` **and** `ema_fast < ema_slow`.
  - Otherwise `NEUTRAL`.
- **Strength (0–100)** = count of aligned binary factors / 5 × 100, where the
  factors are price vs fast EMA, price vs slow EMA, fast>slow EMA, fast-slope>0,
  slow-slope>0 (direction-oriented). A misaligned fast/slow cross applies a
  small penalty (a documented hypothesis).
- `warmup_required()` = slow period + margin (≥ ema_slow).

### StructureEngine (`~swing_lookback=3, min_pivots=3, use_close_for_break=True`)
- **Swing detection (look-ahead-bias-safe):** a pivot at bar `i` is a
  local extremum over `k` bars before **and** `k` bars after `i`. Requiring
  confirmation on both sides makes swings deterministic. A backtester must only
  use pivots already confirmed at decision time (documented in the module).
- **Classification** from the last two swing highs and the last two swing lows
  (comparing **price levels**, not bar positions):
  - `HH_HL` = higher high **and** higher low (bullish).
  - `LH_LL` = lower high **and** lower low (bearish).
  - `HH_LL` = higher high, lower low (expansion/diverging).
  - `LH_HL` = lower high, higher low (tightening/range).
  - `NEUTRAL` when fewer than two comparable swings on either side.
- **Actions:** `BREAK_OF_STRUCTURE_UP` when structure is HH_HL and the latest
  close exceeds the last swing high; `BREAK_OF_STRUCTURE_DOWN` symmetric for
  LH_LL. `FAILED_BREAK` is reserved for reversal-back-across scenarios.
- **Score map:** HH_HL 90, LH_LL 85, HH_LL 50, LH_HL 45, NEUTRAL 40.
- `warmup_required()` = 2·k + min_pivots.

### MomentumEngine (`~rsi_period=14, macd 12/26/9, rsi_ob=70, rsi_os=30`)
- Wilder-style **RSI(14)** (simple rolling averages for reproducibility).
- **MACD(12/26/9)** histogram (`macd_line − signal_line`).
- **Direction:** `POSITIVE` if histogram > 0, `NEGATIVE` if < 0, else `NEUTRAL`;
  RSI overbought/oversold (70/30) retained as confirming evidence.
- **Score:** directional base 50 + scaled |histogram| up to 50; `NEUTRAL` = 50;
  `UNKNOWN` = 0.
- `warmup_required()` = macd_slow + macd_signal + 5.

### VolumeEngine (`~volume_ma_period=20, expansion_ratio=1.5, contraction_ratio=0.7`)
- **Relative volume** = latest volume / SMA(20) of volume.
- **State:** `EXPANDING` if rel vol ≥ 1.5, `CONTRACTING` if ≤ 0.7, else `NORMAL`.
- **Price–volume confirmation:** boolean — elevated volume accompanied by a move
  in the same direction.
- `warmup_required()` = ma period + 5.

### VolatilityEngine (`~atr_period=14, low=0.6, high=1.4, extreme=2.0`)
- **ATR(14)** via Wilder EWM of true range; **ATR%** = ATR / price × 100.
- **Expansion ratio** = current ATR / ATR `p` bars ago.
- **State:** `EXTREME` ≥ 2.0, `HIGH` ≥ 1.4, `LOW` ≤ 0.6, else `NORMAL`.
- **Score:** maps ATR% into 0–100, bonus for expansion above baseline.
- `warmup_required()` = atr_period + 5.

---

## 3. Default configuration

(all driven by `Settings` in `src/veyra/config.py`, env prefix `VEYRA_`)

| Setting                    | Default | Engine       |
|----------------------------|---------|--------------|
| `ema_fast`                 | 50      | trend        |
| `ema_slow`                 | 200     | trend        |
| `ema_slope_lookback`       | 3       | trend        |
| `swing_lookback`           | 3       | structure    |
| `structure_min_pivots`     | 3       | structure    |
| `structure_use_close_for_break` | True | structure |
| `rsi_period`               | 14      | momentum     |
| `rsi_overbought` / `rsi_oversold` | 70 / 30 | momentum |
| `macd_fast` / `macd_slow` / `macd_signal` | 12 / 26 / 9 | momentum |
| `volume_ma_period`         | 20      | volume       |
| `volume_expansion_ratio` / `volume_contraction_ratio` | 1.5 / 0.7 | volume |
| `atr_period`               | 14      | volatility   |
| `volatility_low_ratio` / `high_ratio` / `extreme_ratio` | 0.6 / 1.4 / 2.0 | volatility |
| `weight_trend` … `weight_volatility` | 0.25/0.20/0.15/0.12/0.07/0.05 | aggregator |

All thresholds are **initial hypotheses**, configurable and to be validated by
backtesting — never treated as optimal.

---

## 4. Regime resolution rules (RegimeEngine)

`resolve(trend, structure, volatility)` applies explicit precedence:

1. **UNKNOWN** if any input component is not `READY` (e.g. `INSUFFICIENT_DATA`).
2. **HIGH_VOLATILITY** wins if volatility state is `HIGH` or `EXTREME`
   (prevents trading a structural signal inside explosive moves).
3. Else **BULL** if trend is `BULLISH` **and** structure is `HH_HL` or `HH_LL`.
4. Else **BEAR** if trend is `BEARISH` **and** structure is `LH_LL` or `LH_HL`.
5. Else **RANGE**.

**Regime score** (0–100, not a probability): base 45, +30 strong structural
sign, +10 trend alignment, +10 volatility premium, capped at 100.

---

## 5. MarketSnapshot schema

`MarketSnapshot` (domain/snapshot.py) serializes via `to_dict()`:

```jsonc
{
  "symbol": "BTC/USDT",
  "timeframe": "4H",
  "timestamp": 1700000000,            // epoch seconds, UTC
  "regime": "BULL",
  "regime_output": { "regime": "BULL", "score": ..., "evidence": {...}, "detail": "..." },
  "scores": { "TREND": 80, "STRUCTURE": 90, ... },           // per-component 0–100
  "overall_score": 42,                // WeightedScoreAggregator output (see note)
  "system_state": "WAIT",
  "components": {                      // per-engine structured output + evidence
    "TREND":     { "name": "TREND", "state": "READY", "score": ..., "value": ...,
                   "detail": "...", "meta": {...}, "evidence": {...} },
    "STRUCTURE": { ... },
    "MOMENTUM":  { ... },
    "VOLUME":    { ... },
    "VOLATILITY":{ ... }
  },
  "data_quality": { "state": "VALID", "row_count": 300,
                    "required_lookback": 210, "gap_count": 0,
                    "validation_valid": true, "issues": [] }
}
```

**Overall score note:** the shared `WeightedScoreAggregator` weights sum to
0.84, so the maximum overall score is 84 (not 100). This is an intentional,
documented hypothesis carried over from earlier phases; it does **not** mean
the market is "84% bullish."

**Data quality:** `DataQuality` resolves `VALID` / `VALID_WITH_GAPS` /
`INSUFFICIENT_DATA` / `INVALID` from row count vs required lookback, gap count,
and Phase 1 validator validity. Engines independently report per-component
`READY` / `INSUFFICIENT_DATA` / `UNKNOWN`.

---

## 6. Data-quality handling

- The **pipeline** is honest about its inputs: `_assess_data_quality` derives
  `required_lookback` from the strictest engine's `warmup_required()` and
  flags the snapshot as `INSUFFICIENT_DATA` when the frame is too short.
- Each **engine** independently enforces its own warm-up and returns
  `INSUFFICIENT_DATA` (score 0) rather than a fabricated value.
- **Gaps** are surfaced as `gap_count` / `VALID_WITH_GAPS`; full gap/integrity
  validation remains the domain of Phase 1's `Validator` (callers may attach it).

---

## 7. Tests

**36 Phase 2 tests** (all deterministic, synthetic data — no live network):

| File                            | Tests | Coverage |
|---------------------------------|------:|----------|
| `tests/unit/test_trend.py`      | 6     | bullish/bearish/downtrend, insufficient data, warmup, score range |
| `tests/unit/test_structure.py`  | 5     | HH_HL, LH_LL, swing evidence, insufficient data, pivot confirmation |
| `tests/unit/test_momentum.py`   | 5     | POSITIVE/NEGATIVE, RSI value, insufficient data, warmup |
| `tests/unit/test_volume.py`     | 5     | expansion/contraction/normal, price-volume, insufficient data |
| `tests/unit/test_volatility.py` | 4     | high/normal/expansion, insufficient data |
| `tests/unit/test_regime.py`     | 6     | bull, bear, high-vol dominance, range, unknown, explainability |
| `tests/unit/test_pipeline.py`   | 5     | full chain to MarketSnapshot, insufficient quality, serialization |

Synthetic frames are generated by `tests/unit/market_synth.py`
(bullish/bearish/sideways, HH_HL & LH_LL zigzags, volatility expansion, etc.) —
no reliance on live Binance data, so results are reproducible.

**Full suite:** 101 tests passing (prior Phase 0/1 + Phase 2).

---

## 8. Performance

Engines are vectorised with pandas/numpy (EMA `ewm`, rolling, numpy arrays).
A full five-engine analysis on a few hundred candles runs in **milliseconds**.
The heaviest cost is the leading `warmup_required()` pass for EMA200/MACD,
which is O(n). There is no per-phase performance regression risk for the small
frames used in analysis; a future backtester should batch/stream rather than
re-run per candle linearly.

---

## 9. Limitations

- **Highs/lows from whole candles:** structure uses high/low for pivots but
  close for break confirmation; intra-bar sequences (e.g. H→L within one bar)
  are not modelled.
- **Fixed lookback swings:** a single `k` cannot adapt to different timeframe
  regimes; very large/small `k` changes labels. Configurable but not adaptive.
- **Threshold hypotheses:** all RSI/MACD/ATR/volume/regime thresholds are
  unvalidated hypotheses to be calibrated by backtesting (Phase 4).
- **RSI simplification:** uses rolling means rather than Wilder averaging,
  a deliberate reproducibility trade-off.
- **No calibration:** scores are alignment measures, **not** probabilities;
  they cannot claim "X% chance of a move."
- **Gap sensitivity:** pipeline quality flags gaps but does not repair them;
  callers must rely on the Phase 1 validator for reconstruction decisions.

---

## 10. Architectural changes

- `AnalysisEngine` base gained `warmup_required()` (default 0) to drive
  pipeline data-quality checks.
- `EngineResult` remains the universal engine output; engines put structured
  `state` / `meta` / `evidence` into it (a short-lived typed-result module was
  considered and removed as unused — the generic result + evidence dicts are
  sufficient and consistent).
- `MarketSnapshot` grew `components` (per-engine outputs), `regime_output`,
  and `data_quality`; it is now the single serializable analytical contract.
- `MarketAnalysisPipeline.default()` wires the five engines + `RegimeEngine`
  + `WeightedScoreAggregator` from `Settings`. No signal/setup logic added.
- Fixed a classification bug in structure: comparisons now use **price levels**
  at pivot indices, not the bar indices (indices always increase with time and
  would make every structure look like HH).
- Removed the dead typed-results module and unused imports.

---

## 11. Phase 3 recommendation

Phase 3 (Setup Engine) should consume the five component outputs + regime from
the Phase 2 snapshot and keep the same discipline:

- Build **setup detection** (detect, develop, qualify, trigger, invalidate,
  expire) purely from encoded component states — still no raw entry/TP/SL.
- Reuse the `WeightedScoreAggregator` for setup qualification, but **add a
  readiness gate**: only `READY` components contribute; any `INSUFFICIENT_DATA`
  component should block qualification (never fabricate).
- Introduce **machine-testable lifecycle states** and record every result,
  including invalidations and "no signal" periods, to feed Phase 4 backtests
  without look-ahead bias.
- Keep probabilities out until Phase 4–5 data justifies calibration.