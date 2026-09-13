# Veyra

**AI market intelligence and swing-trading analysis platform.**

Veyra behaves like an AI market analyst, not a signal spammer. It monitors
markets, filters aggressively, explains its reasoning, and alerts only
when a setup is genuinely qualified.

> **Monitor everything. Filter aggressively. Explain clearly. Alert selectively.**

This repository currently contains **Phase 0 (foundation)**, **Phase 1 (market data engine)**, **Phase 2 (market intelligence engine)**, **Phase 3 (setup detection & scoring engine)**, and **Phase 4 (backtesting & validation engine)**.
See the [roadmap](#roadmap) below for what comes next.

---

## Project status: Phase 2 — Market Intelligence Engine

Phase 2 added deterministic, configurable, testable analysis engines and wired
them through the pipeline into a serializable `MarketSnapshot`:

- **`TrendEngine`** — EMA fast/slow cross, price vs fast/slow EMA, EMA-slope,
  alignment strength 0–100.
- **`StructureEngine`** — swing high/low detection (fixed lookback), structural
  labels (HH_HL / LH_LL / HH_LL / LH_HL / NEUTRAL), break-of-structure and
  failed-break actions; no look-ahead bias (pivots confirmed only with `k`
  bars on each side, classification never uses future data to label the present).
- **`MomentumEngine`** — Wilder RSI + MACD histogram → POSITIVE/NEGATIVE/NEUTRAL.
- **`VolumeEngine`** — relative volume vs a moving average, expansion/contraction,
  price–volume confirmation.
- **`VolatilityEngine`** — ATR and ATR% → LOW/NORMAL/HIGH/EXTREME plus a
  range-expansion ratio.
- **`RegimeEngine`** — resolves BULL / BEAR / RANGE / HIGH_VOLATILITY / UNKNOWN
  from trend + structure + volatility via explicit precedence rules
  (high volatility wins; else trend+structure alignment).
- **Data-quality state** — each engine and the pipeline report
  `INSUFFICIENT_DATA`/other quality states rather than fabricating values.
- **`MarketSnapshot`** — now carries per-component outputs with structured
  evidence, the resolved regime output, component scores, an overall score,
  system state, and data quality; fully serializable.

**Explicitly out of scope for Phase 2:** signals, setup detection, backtesting,
Telegram, subscriptions, and all ML. Every value is deterministic,
configurable via `Settings`, and reproducible from stored candles. Scores are
described as **alignment measures, not calibrated probabilities**.

See [`docs/phase-2.md`](docs/phase-2.md) for the full architecture, formulas,
regime rules, snapshot schema, and design decisions.

---

## Project status: Phase 3 — Setup Detection & Scoring Engine

Phase 3 turned the Phase 2 `MarketSnapshot` into deterministic, machine-testable
**setup candidates** with explicit detection, scoring, and a legal lifecycle:

- **Five setup types** — `TREND_CONTINUATION`, `PULLBACK`, `BREAKOUT`,
  `BREAKOUT_RETEST`, `RANGE_REJECTION` — each an explicit rule set over the
  encoded snapshot (never recalculates indicators).
- **Scoring** — each component maps to a 0–100 alignment score combined by the
  shared `WeightedScoreAggregator` (weights sum to 0.84 ⇒ overall caps at 84).
  Scores are a **quality ranking, never a probability**.
- **Lifecycle state machine** — `DETECTED → DEVELOPING → QUALIFIED → TRIGGERED`
  with terminal `COMPLETED / INVALIDATED / EXPIRED`; illegal transitions raise
  and terminal states are locked.
- **Invalidation & expiration** — opposing structure/regime invalidates; age
  beyond `setup_max_lifetime_bars` expires.
- **Persistence** — `Setup` rows plus an append-only `SetupEvent` lifecycle log
  via `SetupRepository`.
- **Multi-timeframe** — `1D` and `4H` computed independently (no cross-timeframe
  strategy yet).
- **Gated & honest** — `detect()` returns `[]` on insufficient/invalid/unknown
  data and never fabricates a price or a level.

**Explicitly out of scope for Phase 3:** backtesting, simulated/real execution,
Telegram, and ML. No entry/TP/SL prices are produced — only qualified setups for
a later phase.

See [`docs/phase-3.md`](docs/phase-3.md) for detection rules, lifecycle,
invalidation/expiration rules, price-zone logic, and design decisions.

---

## Project status: Phase 4 — Backtesting & Validation Engine

Phase 4 added a deterministic, **look-ahead-safe** historical replay that applies
the Phase 3 setup hypotheses chronologically to historical candles, and measures
them honestly:

- **Chronological replay** — one bar at a time, analysing only `df.iloc[:i+1]` so
  no future candle can influence a decision (`PAST → DECISION; FUTURE → OUTCOME`).
- **Conservative execution** — entries at the next bar's open; stop/target
  derivation; worst-case ambiguous-candle rule; explicit fees & slippage (never
  assumed zero).
- **Honest outcomes** — a setup that never triggers is a candidate, **not** a
  losing trade; invalidated/expired setups are counted separately.
- **Metrics with breakdowns** — win rate, expectancy, profit factor, drawdown,
  streaks, opportunity rates, and breakdowns by setup type / regime / timeframe /
  normalised score bucket.
- **Splits & walk-forward** — chronological train/validation/test (never
  shuffled), an out-of-sample leak guard, and walk-forward windows over
  independent test periods.
- **Score normalisation** — a documented layer maps the Phase 3 raw 0–84 score to
  0–100 without changing the underlying weights.
- **Research persistence** — runs/trades/events stored separately from the live
  setup repository; candles stay in the Parquet store.
- **`veyra backtest` CLI** — run a backtest (or walk-forward) on a stored dataset
  and render a text/JSON report.

**Explicitly out of scope for Phase 4:** live trading, paper trading, position
sizing, portfolio optimization, ML, and any profitability claim. Results of a
backtest are **measurements of a hypothesis**, never a proof of future profit.

See [`docs/phase-4.md`](docs/phase-4.md) for replay semantics, look-ahead
protections, execution rules, metrics, splits, walk-forward, persistence,
research risks, and the Phase 5 recommendation.

---

## Project status: Phase 0 — Foundation

Phase 0 established:

- Clean `src/` layout with separate namespaces for data, indicators,
  market intelligence, strategy, backtest, paper trading, alerts, API,
  and database.
- Domain models (candles, snapshots, setups) independent of UI and I/O.
- Persistence layer: SQLAlchemy + SQLite for structured entities and a
  Parquet-backed candle store for fast historical replay.
- Configuration via `pydantic-settings` (env-prefixed `VEYRA_`) so tuning
  values are configurable, not magic numbers.
- Engine contracts for trend/structure/momentum/volume/volatility/regime,
  and a weighted score aggregator whose weights are a hypothesis to be
  validated — never treated as optimal.
- A wiring test for the analysis pipeline (placeholder engines until
  Phase 2) and database/API smoke tests.

This phase intentionally does **not** build trading signals, backtesting,
Telegram, auth, subscriptions, the dashboard, or machine learning.

---

## Stack decision

| Layer        | Choice                                  | Why                                                        |
|--------------|-----------------------------------------|------------------------------------------------------------|
| Language     | Python 3.11+                            | Numerical analysis, backtesting, API, future ML            |
| Backend API  | FastAPI (+ Uvicorn)                     | Async, modern, already present in environment              |
| Analysis     | numpy / pandas                          | Vectorised OHLCV computation                               |
| Storage      | SQLite + SQLAlchemy ORM                 | Zero-setup structured store for setups/history/audits       |
| Candle store | Parquet (pyarrow)                       | Columnar, fast replay for backtesting                      |
| Tests        | pytest                                  | Foundation and per-phase unit tests                        |
| Frontend     | *TBD in Phase 6* (kept cleanly separate)| Not built yet                                              |

---

## Repository layout

```text
veyra/
├── data/                     # raw + processed data (gitignored)
├── config/                   # env config (.env not committed)
├── docs/
├── src/veyra/
│   ├── domain/               # clean domain models & enums
│   │   ├── candle.py         #    OHLCV candle
│   │   ├── snapshot.py       #    MarketSnapshot + component scores
│   │   ├── data_quality.py   #    DataQuality state model
│   │   └── setup.py          #    Setup, PriceZone, lifecycle
│   ├── data/
│   │   └── candle_store.py   #    Parquet candle persistence
│   ├── database/
│   │   ├── engine.py         #    SQLAlchemy engine/session bootstrap
│   │   ├── models.py         #    ORM entities (auditable history)
│   │   ├── backtest_models.py      #    Research backtest ORM (runs/trades/events)
│   │   └── backtest_repository.py  #    Backtest save/load (research series)
│   ├── market/
│   │   ├── engine.py         #    AnalysisEngine contract
│   │   ├── trend.py          #    TrendEngine (EMA cross, slope, strength)
│   │   ├── structure.py      #    StructureEngine (swings, BOS, labels)
│   │   ├── momentum.py       #    MomentumEngine (RSI + MACD)
│   │   ├── volume.py         #    VolumeEngine (rel vol, expansion)
│   │   ├── volatility.py     #    VolatilityEngine (ATR, states)
│   │   ├── regime.py         #    RegimeEngine (resolution rules)
│   │   └── pipeline.py       #    Orchestrates engines -> MarketSnapshot
│   ├── strategy/
│   │   └── setup_engine.py   #    SetupEngine contract + WeightedScoreAggregator
│   ├── api/
│   │   └── main.py           #    FastAPI entrypoint (health)
│   ├── backtest/             #    Phase 4: engine, simulator, execution, metrics,
│   │   │                     #      splits, walk-forward, report, persistence
│   ├── paper/                #    Phase 5
│   ├── alerts/               #    Phase 7
│   ├── web/                  #    Phase 6
│   └── config.py             #    pydantic-settings config
└── tests/
    ├── unit/                 # unit tests per phase
    └── conftest.py           # isolated tmp_path fixtures
```

---

## Setup & run tests

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Run the API locally:

```powershell
uvicorn veyra.api.main:app --reload
```

---

## Configuration

Config lives in `src/veyra/config.py` and is overridable via env vars
prefixed with `VEYRA_` (e.g. `VEYRA_PRIMARY_TIMEFRAME`, `VEYRA_EMA_FAST`).
Layer a local `config/.env` file (gitignored) over the defaults.

Score weights (`weight_trend`, …) are **hypotheses** to be validated by
backtesting, not optimal values.

---

## Roadmap

| Phase | Scope                                                        |
|-------|--------------------------------------------------------------|
| 0     | **Foundation** — structure, domain, DB, config, contracts (done) |
| 1     | **Market data engine** — ingestion, validation, gaps, storage, CLI (done) |
| 2     | **Market intelligence** — trend, structure, momentum, volume, volatility, regime (**done**) |
| 3     | Setup engine — detection, lifecycle states, 0–100 scoring (**done**) |
| 4     | **Backtesting & validation** — no look-ahead bias, execution, metrics, walks-forward, persistence (**done**) |
| 5     | Validation — out-of-sample, paper trading                     |
| 6     | Web application — dashboard, scanner, setups, history, analyst |
| 7     | Telegram — selective alerts, subscriptions                    |
| 8     | Users — auth, profiles, watchlists                            |
| 9     | Subscriptions — free/premium (no payments yet)                |
| 10    | Production — security, monitoring, deployment                 |

---

## Domain rules (from the spec)

1. Do not build everything at once — one phase at a time.
2. Do not build the UI before the market engine is validated.
3. Do not claim a strategy is profitable without evidence.
4. Do not fabricate statistics.
5. Do not create fake AI reasoning — explain what deterministic engines actually found.
6. No ML until there is sufficient data; ML must be evidence-based.
7. Keep market analysis independent from UI.
8. Keep backtesting independent from live execution.
9. Every setup must have a machine-testable lifecycle.
10. Record every result, including invalidations and periods with **no signal** (avoid bias).