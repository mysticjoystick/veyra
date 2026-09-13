# Phase 5 — Step 3: Real-data quality report

Date: 2026-09-02
Source: Binance public kline API (no API key), provider `binance` / `binance-v3`.

## 1. Datasets retrieved

| Symbol    | Timeframe | Candles | Status          | Content hash | Range (UTC) |
|-----------|-----------|---------|-----------------|--------------|------------------------------|
| BTC/USDT  | 4H        | 19,804  | VALID_WITH_GAPS | `43744a2be05385fd` | 2017-08-17 → 2026-09-02 |
| BTC/USDT  | 1D        | 3,304   | VALID           | `3d6b9ac0d60f3598` | 2017-08-17 → 2026-09-02 |
| ETH/USDT  | 4H        | 19,804  | VALID_WITH_GAPS | `fb66554488c8da65` | 2017-08-17 → 2026-09-02 |
| ETH/USDT  | 1D        | 3,304   | VALID           | `c513405827b53ef7` | 2017-08-17 → 2026-09-02 |

Multi-year history (~8.8 years of 4H, ~9 years of daily). Manifest provenance
lives alongside data in `data/manifests/{SYMBOL}_{TIMEFRAME}.json` with the
retrieval timestamp, provider version, counts, range, content hash and the
integrity metrics in this report.

## 2. Integrity checks performed (each dataset)

For every (symbol, timeframe) the raw fetched series was checked for:

- **Chronological ordering** — strictly increasing `open_time`.
- **Duplicate timestamps** — repeated `open_time`.
- **Gap count** — of missing bars, from spacings that are integer multiples of
  the timeframe interval (genuine exchange suspension periods).
- **OHLC validity** — `high >= max(open, close)`, `low <= min(open, close)`.
- **Negative volume** — `volume >= 0`.
- **Timeframe spacing** — any spacing NOT an integer multiple of the interval
  (would indicate misaligned/corrupt timestamps).
- **Suspicious zeros** — zero high/low.

## 3. Results

| Check (BTC 4H / BTC 1D / ETH 4H / ETH 1D)            | Value           |
|------------------------------------------------------|-----------------|
| chronological*                                      | all True        |
| duplicate_count                                     | 0, 0, 0, 0      |
| gap_count (missing bars)                            | 16, 0, 16, 0    |
| invalid_ohlcv                                       | 0, 0, 0, 0      |
| negative_volume                                     | 0, 0, 0, 0      |
| bad_spacing (non-multiple)                          | 0, 0, 0, 0      |
| suspicious_zero                                     | 0, 0, 0, 0      |

\* Reversed/out-of-order detection: all datasets strictly chronological.

## 4. Note on the 4H gaps

4H datasets carry 16 missing bars each, all between 2018-02-08 and
2020-02-19 (see table below), equal to ~0.08% of the series. These coincide
with periodic Binance spot system-maintenance pauses and are **genuine
exchange data gaps**, not provider errors and not fabrication on our side.
There are **no** gaps in the 2021-2026 window.

| Missing bars | Window (UTC)                     |
|--------------|----------------------------------|
| 7            | 2018-02-08 00:00 → 2018-02-09 08:00 |
| 2            | 2018-06-26 00:00 → 2018-06-26 12:00 |
| 1            | 2018-07-04 00:00 → 2018-07-04 08:00 |
| 1            | 2018-11-14 00:00 → 2018-11-14 08:00 |
| 1            | 2019-03-12 00:00 → 2019-03-12 08:00 |
| 2            | 2019-05-15 00:00 → 2019-05-15 12:00 |
| 1            | 2019-08-15 00:00 → 2019-08-15 08:00 |
| 1            | 2020-02-19 08:00 → 2020-02-19 16:00 |

## 5. Disposition for Phase 5 validation

- **1D datasets are VALID** and usable as full evidence.
- **4H datasets are VALID_WITH_GAPS** and remain usable as evidence. Gaps are
  reported honestly and are never repaired or interpolated; the backtester
  processes the candles in chronological order as-is and stops/targets are
  evaluated only on candles that exist.
- No dataset has integrity problems that would exclude it from evidence; none
  are marked INVALID.

## 6. Reproducibility

Re-downloading is reproducible from the manifests (provider, start/end
timestamps, content hash). The retrieval module `src/veyra/realdata/retrieve.py`
recomputes identical (or superset) data and the manifest hash lets any later
validation run confirm it is reading the frozen input recorded here.