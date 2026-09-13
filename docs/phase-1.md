# Veyra — Phase 1 Architecture Decision Record

Phase 1 built the **trustworthy market-data engine** that all later
phases depend on. It delivers clean, validated, reproducible OHLCV
candles for 4H and 1D, without leaping into strategy development.

## 1. Final folder structure (additions in Phase 1)

```text
src/veyra/
├── data/
│   ├── provider/
│   │   ├── base.py            # MarketDataProvider abstraction + ProviderResult
│   │   ├── binance.py         # Binance public API provider (keyless, paginated, retry)
│   │   └── __init__.py        # provider registry (get_provider / available_providers)
│   ├── candle_store.py        # (from Phase 0) Parquet persistence
│   ├── normalizer.py          # provider payload -> canonical Candle list
│   ├── service.py             # MarketDataService ingestion pipeline + provenance
│   ├── timeframe.py           # canonical timeframe <-> interval seconds + aliases
│   └── validator.py           # structural + OHLC + gap/integrity checks
├── database/
│   └── dataset_repository.py  # provenance records (DatasetRecord)
├── domain/                    # (Phase 0) Candle, enums (Timeframe, ...)
├── error.py                   # DataError / ProviderError / RateLimitError ...
├── cli.py                     # CLI: download / validate / inspect / update
└── api/                       # (Phase 0) FastAPI health endpoints
```

## 2. Provider abstraction

`MarketDataProvider` is the single interface all data enters through:

```python
get_ohlcv(symbol, timeframe, start_time=None, end_time=None) -> List[Candle]
is_symbol_supported(symbol, timeframe) -> bool
```

Providers are registered in `data/provider/__init__.py` and selected by
name via `get_provider("binance")`. Veyra's core never couples to a
specific exchange API.

## 3. Provider selected for development

**Binance public klines API** (`https://api.binance.com/api/v3/klines`).

- **Free and keyless** — no API key required.
- Covers 4H and 1D history with pagination (max 1000 candles/request).
- Suitable for development and testing; other providers can be added.

The provider implements bounded retries (default 3), exponential backoff,
rate-limit (HTTP 429/5xx) handling, network-error retries, and clear
`ProviderError`/`RateLimitError` failures rather than silent incomplete
data. Every network-dependent behavior is injectable and unit-tested
without live internet.

## 4. Canonical candle schema

The canonical format is `veyra.domain.candle.Candle`:

| Field        | Type  | Semantics                                        |
|--------------|-------|--------------------------------------------------|
| `symbol`     | str   | canonical market symbol, e.g. `BTC/USDT`         |
| `timeframe`  | str   | canonical timeframe value, e.g. `4H` / `1D`      |
| `open_time`  | int   | **UTC epoch seconds** (the start of the candle)  |
| `open`       | float |                                                   |
| `high`       | float |                                                   |
| `low`        | float |                                                   |
| `close`      | float |                                                   |
| `volume`     | float |                                                   |

Timestamp semantics are fixed: `open_time` is the start of the candle, in
UTC epoch seconds. Providers' differing timestamp semantics (e.g. epoch
ms) are normalised away so the internal representation is always provider-
independent.

## 5. Normalization rules (`normalizer.py`)

- Accepts records (list of dicts) or a DataFrame with canonical **or**
  aliased columns (`open_time`/`timestamp`/`openTime`, `o/h/l/c`, `v`...).
- Converts timestamps to UTC epoch seconds (auto-detects s/ms/ns; a fixed
  resolution may be forced).
- Converts OHLCV to float.
- Sorts ascending by `open_time`.
- Drops duplicate `open_time` rows (keeps the last occurrence).
- Raises `DataNormalizationError` on missing columns, non-numeric OHLCV,
  or invalid timestamps — never silently coerces.

## 6. Validation rules (`validator.py`)

`Validator.verify(df) -> ValidationReport` performs:

- **Structure:** required columns present; correct/coercible types.
- **Timestamps:** valid, sorted ascending, unique.
- **OHLC invariants** per candle: `high >= max(open, close)`,
  `low <= min(open, close)`, `high >= low`.
- **Volume:** reject negative volume.
- **Timeframe spacing:** gap/integrity detection against the expected
  interval (14400s for 4H, 86400s for 1D) from config.

`ValidationReport` is a structured result with `valid`, `issues` (typed:
`MISSING_COLUMN`, `BAD_TYPE`, `OHLC_INVARIANT`, `NEGATIVE_VOLUME`,
`DUPLICATE_TIMESTAMP`, `OUT_OF_ORDER_TIMESTAMP`, `UNEXPECTED_INTERVAL`),
and `gaps`. It is not console logging - callers act on it explicitly.

## 7. Gap-detection behavior

Gaps are **reported, not fabricated**. The validator emits a list of
`Gap{after_open_time, expected_open_time, expected_interval_seconds}` for
every missing expected interval. Datasets are classified via
`ValidationReport`:

- **VALID:** no structural/invariant errors.
- **VALID_WITH_GAPS:** structurally valid but with missing intervals
  (`report.has_gaps()`).
- **INVALID:** row-level structural/OHLC violations.

`MarketDataService.ingest(require_no_gaps=True)` raises `DataValidationError`
when gaps are present; by default gaps are recorded but tolerated (since
markets may have provider-specific interval behaviour). Gaps are never
silently filled with synthetic candles.

## 8. Incremental-update behavior

`MarketDataService.ingest(..., incremental=True)`:

1. Looks up the stored dataset's latest `open_time` (provenance).
2. Fetches **only** data newer than that (plus a one-interval margin).
3. Normalises, validates, merges, de-duplicates, and re-stores via
   `CandleStore`.
4. Updates the dataset provenance record.

It does **not** re-download the entire history each update. Verified live
against Binance (a 33-candle dataset extended to 976 candles by fetching
only the 943 newer ones).

## 9. Dataset / provenance approach

`DatasetRecord` (SQLite) stores: symbol, timeframe, provider, inclusive
`start_time`/`end_time`, `candle_count`, `normalization_version`, and
`provider_version`. Identification of a dataset is:

```
Market + Timeframe + Start + End + Provider + Normalization rules
```

This makes a historical dataset reproducible and independent of whatever
a live provider returns today.

## 10. CLI commands

```
veyra download --symbol BTC/USDT --timeframe 4H [--start <s>] [--end <s>] [--no-incremental] [--require-no-gaps]
veyra validate --symbol BTC/USDT --timeframe 4H
veyra inspect --symbol BTC/USDT --timeframe 4H
veyra update  --symbol BTC/USDT --timeframe 4H [--end <s>]
```

## 11. Dependencies added

No new runtime dependencies were required beyond Phase 0 (Binance provider
uses stdlib `urllib`). The `[dev]` set (pytest, pytest-cov, httpx) was
already present. A console script `veyra = veyra.cli:main` was registered
in `pyproject.toml`.

## 12. Tests created

| Area      | File                         | Coverage                                                                 |
|-----------|------------------------------|--------------------------------------------------------------------------|
| Provider  | `test_binance_provider.py`   | valid/empty/malformed responses, pagination, end_time stop, retries, rate limit, network failure |
| Normalize | `test_normalizer.py`         | column mapping, aliases, ms conversion, order, dedupe, empty, errors     |
| Validate  | `test_validator.py`          | valid, OHLC invariants, negative volume, duplicates, ordering, gaps      |
| Timeframe | `test_timeframe.py`          | alias normalisation, interval seconds, unknown/undefine handling         |
| Service   | `test_service.py`            | ingest, incremental, merge, dedupe, provider failure, gap policy, reload integrity |
| CLI       | `test_cli.py`                | subcommand parsing, dispatch (injected fake service), inspect output     |

## 13. Test results

`python -m pytest` → **67 tests pass**, 0 failures.

Also verified end-to-end against the **live Binance API** (one-off smoke,
not part of the suite): download / validate / inspect / incremental update
for `BTC/USDT 1D` all succeeded with 0 issues and 0 gaps.

## 14. Known limitations

- **Binance as the only provider:** sufficient for Phase 1; more providers
  are intended and supported by the abstraction.
- **Timestamp resolution auto-detection** is heuristic; a provider-specific
  fixed resolution may need to be declared in future.
- **Gap tolerance:** by default gaps are recorded not rejected; consumers
  must opt in via `require_no_gaps`. Real production policies for
  partial/volatile markets can be tuned later.
- **No backfill repair:** gaps are not auto-repaired against future data
  (per spec, do not use future data to repair history); a repair mode can
  be added as an explicit, documented operation.
- **Provider availability:** relies on Binance public API uptime; no
  multi-provider failover yet.

## 15. Recommendation for Phase 2

**Market Intelligence Engine.** With a trustworthy 4H/1D candle stream
(CandleStore -> Parquet), Phase 2 should implement, as pure functions over
candle DataFrames via the existing `AnalysisEngine` contract:

1. **Trend** — EMA 50/200 alignment + slope + price position + structure.
2. **Structure** — swing highs/lows, higher/lower structures, break of
   structure, support/resistance zones.
3. **Momentum** — RSI, MACD-style measurement, divergence, confirmation.
4. **Volume** — current vs rolling average, relative volume, expansion.
5. **Volatility** — ATR, volatility regime (normal/high/low).

Then wire each into the `MarketAnalysisPipeline` (currently placeholder
engines) and implement the **Regime Engine** (BULL/BEAR/RANGE/
HIGH_VOLATILITY/UNKNOWN). Each engine returns an `EngineResult` with a
scalar, a 0–100 score, and human-readable detail. This is a natural,
self-contained next increment that stays strictly within the intelligence
layer (no signals, backtesting, UI, or ML until the engine itself is
validated in later phases).