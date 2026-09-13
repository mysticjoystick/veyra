# Veyra — Phase 0 Architecture Decision Record

This document captures the architectural decisions made in Phase 0, in
accordance with the master project specification. It is the authoritative
reference for module boundaries and the development roadmap.

## 1. Current project structure

Phase 0 established a clean, empty-src layout. The repository was empty
before this phase; everything here is new scaffolding.

## 2. Technology stack

| Layer        | Choice                                            |
|--------------|---------------------------------------------------|
| Language     | Python 3.11+                                      |
| Analysis     | numpy / pandas                                    |
| Backend API  | FastAPI + Uvicorn                                  |
| Structured DB| SQLite + SQLAlchemy 2.0 (ORM)                     |
| Candle store | Parquet (pyarrow), partitioned per symbol/timeframe |
| Config       | pydantic-settings (env prefix `VEYRA_`)           |
| Tests        | pytest (+ httpx TestClient for API)               |
| Frontend     | TBD in Phase 6 (kept fully separate)              |

Rationale: Python is already installed with numpy/pandas/fastapi. It is
the strongest fit for numeric analysis, backtesting, API, background
jobs, and future ML. The frontend stays cleanly separated.

## 3. Existing functionality

Phase 0 delivers the foundation only:
- Domain models and canonical enums
- Candle store (Parquet read/write with dedupe)
- SQLAlchemy ORM + SQLite bootstrap
- Market analysis pipeline wiring with placeholder engines
- Weighted score aggregator (weights are a validated hypothesis)
- FastAPI health/status endpoints

No signals, backtesting, Telegram, auth, subscriptions, dashboard, or ML
are built yet.

## 4. Existing problems / notes

- Score weights currently sum to 0.84, so the overall score caps at 84,
  reproducing the spec's `TOTAL 84` example. This is intentional for now
  and marked as a hypothesis to be validated.
- Regime engine is a placeholder returning `UNKNOWN` until Phase 2.
- `make_candles`/`candles_to_frame` test helpers already handle the empty
  case to exercise the pipeline's own input validation.

## 5. Recommended Veyra architecture

```
DATA SOURCES -> DATA PIPELINE -> MARKET STORAGE
                                      |
                               INDICATOR ENGINE
                                /     |      \
                          TREND STRUCTURE MOMENTUM
                                \     |      /
                                 REGIME ENGINE
                                 SETUP ENGINE
                                 SCORE ENGINE
                                /            \
                            BACKTEST        LIVE / PAPER
                                              ALERT ENGINE
                                             /            \
                                        WEB APP         TELEGRAM
```

## 6. Proposed folder structure

Defined under `src/veyra/`. Placeholders for later phases already exist
as empty packages (`backtest`, `paper`, `alerts`, `web`, `indicators`).

## 7. Domain models

- `Candle` — OHLCV with validity invariants
- `MarketSnapshot` — regime, per-component scores, overall score, state
- `ComponentScore`, `PriceZone` — reusable structured value types
- `Setup` — full lifecycle state (DETECTED .. COMPLETED/INVALIDATED)
- Enums: `Timeframe`, `Regime`, `SetupType`, `SetupState`, `SignalType`,
  `MarketSide`, `SystemState`, `AnalyticsComponent`
- ORM entities: `Candle`, `MarketSnapshot`, `Setup`, `SetupEvent`

## 8. Module boundaries

| Module        | Responsibility                                 | Depends on         |
|---------------|-----------------------------------------------|--------------------|
| `domain`      | Pure data models & enums                      | stdlib             |
| `data`        | Candle storage (Parquet)                      | domain, config     |
| `database`    | SQLAlchemy engine + ORM entities              | config             |
| `market`      | Analysis engines, regime, pipeline            | domain, config     |
| `strategy`    | Setup engine contract, score aggregator       | domain             |
| `api`         | FastAPI endpoints via pipeline                | market, config     |
| `backtest`    | (Phase 4) historical simulation                | domain, market     |
| `paper`       | (Phase 5) paper trading                        | domain, backtest   |
| `alerts`      | (Phase 7) Telegram                            | domain             |
| `web`         | (Phase 6) dashboard/scanner/UI                | api                |

Guiding rules honoured: analysis is UI-independent, backtesting is
independent of live execution, setup lifecycle is mandatory, every result
(including invalidations and no-signal periods) is recorded.

## 9. Dependencies required

Installed via `pip install -e ".[dev]"`:
- Runtime: numpy, pandas, fastapi, uvicorn, sqlalchemy, pydantic,
  pydantic-settings, python-dotenv, pyarrow
- Dev: pytest, pytest-cov, httpx

## 10. Testing strategy

- Unit tests under `tests/unit/`, isolated to `tmp_path` via conftest.
- Cover: domain invariants, candle-store persistence/dedupe, score
  aggregation, database ORM persistence, API health, pipeline wiring.
- Tests shared helpers in `tests/unit/helpers.py`.
- Run: `python -m pytest` (currently `20 passed`).

## 11. Development roadmap

Defined in `README.md`. Next phase is **Phase 1 — Market Data Engine**
(OHLCV ingestion, validation, normalization, storage with consistent
timestamps, duplicate/missing/malformed handling, replay).

## Principles kept

- No look-ahead bias (enforced in the future backtester, design prepared).
- Do not claim a strategy is profitable without evidence.
- Do not fabricate statistics or AI reasoning.
- Machine learning only after sufficient data, based on evidence.
- Avoid destructive rewrites; understand code before changing it.
- Configuration instead of magic numbers.
- Test important calculations; never silently swallow data errors.