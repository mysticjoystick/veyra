# Veyra — Phase 4 Architecture Decision Record

Phase 4 built the **Backtesting + Validation Engine**: a deterministic,
**look-ahead-safe** historical replay that applies the Phase 3
detection/scoring/lifecycle hypotheses chronologically to historical candles,
with explicit execution semantics, metrics with breakdowns, train/validation/
test splits, walk-forward validation, research persistence, reporting, a CLI
command, and a full test suite.

This phase is a **measurement / falsification tool, not a strategy optimizer.**
It never claims profitability. Its job is to tell us, honestly, whether the
Phase 3 hypotheses survive history — and when they do not, to show it clearly.

The backtester is **explicitly not** a live trader, not a paper-trading engine,
and makes no ML/AI calls. It produces *auditable measurements of a hypothesis*.

---

## 1. Final folder structure (additions in Phase 4)

```text
src/veyra/
└── backtest/
    ├── __init__.py        # public exports (engine, metrics, report, splits…)
    ├── engine.py          # BacktestEngine: chronological replay driver
    ├── simulator.py       # OpenPosition/TrackedSetup -> trades, events, outcomes
    ├── execution.py       # deterministic entry/exit + ambiguous-candle semantics
    ├── outcomes.py        # SetupOutcome classification (never-Triggered != loss)
    ├── metrics.py         # MetricsEngine: trade/risk/opportunity + breakdowns
    ├── normalize.py       # ScoreNormalizer: raw 0..sum(weights)*100 -> 0..100
    ├── split.py           # chronological split + walk-forward windows
    ├── validation.py      # out-of-sample discipline guards (no test leakage)
    ├── walk_forward.py    # WalkForwardRunner over chronological windows
    ├── report.py          # BacktestReport / BacktestReporter (text + JSON)
    └── models.py          # BacktestRun/Trade/Event/SetupRecord/SimulationResult
```

Persistence (still in `database/`):

```text
src/veyra/database/
├── backtest_models.py      # BacktestBase, BacktestRunRow/TradeRow/EventRow
└── backtest_repository.py  # BacktestRepository (save/load, research series)
```

---

## 2. Chronological replay (engine.py)

`BacktestEngine.run(symbol, timeframe, df)` replays the candle frame **one bar at
a time**. For bar `i` the engine analyses **only `df.iloc[:i+1]`**:

```
available = candles[:i+1]
    -> pipeline.analyze(available)   # indicators computed ONLY from data <= i
    -> SetupEngine.detect + advance  # lifecycle decisions
    -> Simulator.exit checks         # stop/target/expiry on bar i
    -> arm QUALIFIED setups          # entered at the OPEN of bar i+1
```

The **single most important invariant (§2, §29):**

> **PAST DATA → DECISION. FUTURE DATA → OUTCOME ONLY.**

Because `MarketAnalysisPipeline.analyze()` computes every indicator
(EMA, RSI/MACD, swings, volume, regime) from the slice it is given, passing
`candles[:i+1]` guarantees no future candle can influence a present decision.
Entries armed on bar `i` are filled at the **open** of bar `i+1` (`next_open`),
never inside the decision bar.

Analysis begins after warm-up (`process_start = min(warmup, len(df))`), so early
bars form history but produce no setups. Entries/exits are evaluated on subsequent
bars only.

---

## 3. Look-ahead protections (regression-tested)

- **Slice-per-bar:** every indicator uses `df.iloc[:i+1]`, so EMAs, RSI/MACD,
  volume and regime never see a future candle.
- **Confirmed-at-time pivots:** `StructureEngine._find_pivots` never classifies a
  pivot until it is confirmed by `k` bars on each side; with slicing, a pivot at
  index `p` is never used before bar `p+k` (see `tests/unit/test_backtest_engine.py`
  `test_advancing_one_bar_at_a_time_matches_bulk_run`).
- **ID stability:** setup/trade IDs derive from a monotonic per-run counter keyed
  on detection order, so **appending future candles does not reshuffle past
  decisions** (`test_appending_future_candles_does_not_change_past_decisions`).
- **Determinism:** the same frame produces byte-identical trades/ids across runs
  (`test_run_is_deterministic`).

---

## 4. Entry semantics

- A `QUALIFIED` setup is **armed** on the decision bar and **filled at the open
  of the following bar** (`next_open` policy). Entry price for a LONG is the open
  (buy) and for a SHORT the open (sell), then adjusted for slippage/spread
  **against the trader**.
- Deadline + agin units: holding is capped at `position_max_bars` (default 60);
  over-aged positions are force-closed (reason `EXPIRED`).
- At end-of-data, any still-open position is closed at the last bar's close
  (reason `END_OF_DATA`).

---

## 5. Exit semantics

`compute_exit(bar, side, stop, target, policy)` is pure/deterministic:

- LONG stop = `interest_area.low`; SHORT stop = `interest_area.high`.
- LONG target = `targets[0].high`; SHORT target = `targets[0].low`.
- A target is usable **only if it sits on the profit side of the fill price**
  (LONG target > raw open; SHORT target < raw open). Otherwise the target is
  treated as absent and the position exits by stop/expiry.
- **Ambiguous candle policy (§24, conservative):** if one candle's range touches
  **both** stop and target (intrabar order unknown), resolve to the **worst
  outcome for the strategy** — the stop hit first (`stop_first`). Never choose
  the better result.
- Fees/slippage are applied on both entry and exit; every price adjustment moves
  against the trader.

---

## 6. Setup lifecycle accounting (§10: no phantom trades)

A setup that **never triggers is not a losing trade.** `SetupRecord.outcome` uses
`classify_outcome`, which maps:

| final_state | trade_id | Outcome |
|---|---|---|
| any, `trade_id` set | set | `COMPLETED` (it traded; exit reason is on the trade) |
| `EXPIRED` | none | `EXPIRED` |
| `INVALIDATED` | none | `INVALIDATED` |
| `QUALIFIED` | none | `QUALIFIED_NO_TRADE` |
| `DETECTED`/`DEVELOPING` | none | `DETECTED_ONLY` |

So the backtest preserves the distinction between *candidates* and *realized
trades* and never inflates win-rate with setups that never entered.

---

## 7. Metrics (metrics.py)

`MetricsEngine.compute(trades, setups, candle_count)` returns deterministic
measures that handle zero/edge cases (return `0.0`/`None`, never NaN):

- **Trade stats:** total setups, qualified, triggered, completed, wins/losses/
  breakeven, win rate, avg/median return, largest win/loss.
- **Risk / performance:** expectancy, profit factor (guards `0`, `None`, `inf`),
  cumulative return `∏(1+r) − 1`, max/average drawdown from equity, longest
  win/loss streaks, average/max holding bars, total fees & slippage.
- **Opportunity:** qualification / trigger / invalidation / expiry rates,
  setups-per-bar and trades-per-bar.
- **Breakdowns:** by setup type, timeframe, regime, and **normalised score
  bucket** (0-100 bands).

Expectancy and profit factor describe the **sample**; they are not calibrated
probability claims.

---

## 8. Score normalisation (§19)

The Phase 2/3 `WeightedScoreAggregator` weights sum to **0.84**, so each
component contributes a 0-100 alignment score and the weighted **overall score
lives on the scale 0..sum(weights)×100 (i.e. 0..84)**, not 0..100.

`ScoreNormalizer.normalized(raw)` maps 0..84 → 0..100 exactly via
`raw / sum(weights)` (e.g. raw 42 → 50), **without changing the underlying
weights or component semantics.** We report both the raw score
(round-trip-equivalent to Phase 3) and the normalised score, and the
`weight_sum`/`raw_max` are exposed. A regression test (`test_live_setup_score_normalizes_sensibly`)
guards against a double-scaling bug that would clamp all scores to 100.

Scores are a **quality ranking, never a probability** — no calibration has been
performed.

---

## 9. Fees & slippage (execution.py)

All cost parameters come from config and are **never assumed zero** unless the
caller sets them:

| Setting | Default |
|---|---|
| `backtest_entry_fee_pct` | 0.001 |
| `backtest_exit_fee_pct` | 0.001 |
| `backtest_slippage_pct` | 0.0 |
| `backtest_spread_pct` | 0.0 |

Entry and exit prices are adjusted by spread/slippage **against the trader**;
fees are added as a fraction of notional on entry and exit. Total fees and total
slippage are recorded per run and surfaced in the risk stats. Every result
records the exact `ExecutionConfig` used (see `run.execution`) so a measurement
is never re-interpreted with different cost assumptions.

The user-spec repeatedly aims at *not assuming zero or favourable slippage*: the
design keeps costs explicit and unflattering, per the out-of-scope discipline on
real execution.

---

## 10. Overlap policy

- `ALLOW_OVERLAP` (default): a new entry may open while another position is open
  (per-symbol portfolio modeling is **out of scope**).
- `ONE_POSITION_PER_SYMBOL`: blocks a new entry while a position is open.

Applied at arming/entering via `Simulator.can_enter()`. The backtest is a
**per-trade outcome** measure, not a portfolio/equity-curve model.

---

## 11. Splits (split.py) & out-of-sample discipline (validation.py)

- `chronological_split(n, config)` returns a contiguous, non-overlapping
  **training / validation / test** split (default 0.6 / 0.2 / 0.2). Data is
  **never shuffled**; the test partition absorbs rounding remainder at the tail.
- `report_split_boundaries` records boundaries for provenance.
- **Out-of-sample discipline:** `assert_not_test(index, test)` raises
  `TestSetLeakError` if a candle index lands in the reserved test window, making
  accidental test-period fitting explicit. Boundaries are reported but **never**
  used for fitting.

---

## 12. Walk-forward validation (walk_forward.py)

`WalkForwardRunner.run(...)` sweeps a chronological
`train → validation → test` window across the dataset in steps. Each window:
- runs the full backtest over **all data up to the test end** (so the pipeline
  has warm-up history for the test lane),
- then measures metrics **only on trades/setups whose entry/detection falls
  within the window's test period** (true out-of-sample per window).

Test windows are strictly non-overlapping and advance monotonically. Walk-forward
tests **whether the current hypotheses remain stable across periods** — it is
**not** a parameter optimizer.

---

## 13. Persistence (database/backtest_models.py, backtest_repository.py)

Research runs are kept **separate from the live setup repository**; the backtest
never mutates live setup state. We store **run metadata, trades, and setup
records** — candles are **not** stored in SQLite (they remain in the Parquet
candle store).

`BacktestRepository` provides `save()`, `get_run()`, `trades_for_run()`,
`find_runs()` and reconstruction. `init_db()` now creates both `models.Base`
and `backtest_models.BacktestBase` tables. Each `BacktestRunRow` is keyed by a
deterministic `run_key` and stores `config_snapshot_json`, `execution_json`,
`split_json`, `strategy_version`, `engine_version`, and `dataset_hash` for full
reproducibility.

---

## 14. CLI (`veyra backtest`)

New subcommand in `src/veyra/cli.py`:

```
veyra backtest --symbol BTC/USDT --timeframe 4H \
    [--start S] [--end E] [--run-key KEY] \
    [--entry-fee F] [--exit-fee F] [--slippage S] [--spread P] \
    [--ambiguous stop_first|target_first] \
    [--overlap ALLOW_OVERLAP|ONE_POSITION_PER_SYMBOL] \
    [--persist] [--walk-forward] [--json]
```

It loads candles from the `CandleStore`, runs `BacktestEngine`, computes metrics,
and renders a text/JSON report (optional `--persist` writes the run to the
research backtest tables; `--walk-forward` emits per-window summaries).
`BacktestEngine` remains fully callable programmatically.

---

## 15. Config additions

Added to `Settings` (env prefix `VEYRA_`):

| Setting | Default |
|---|---|
| `backtest_entry_fee_pct` / `exit_fee_pct` | 0.001 / 0.001 |
| `backtest_slippage_pct` / `spread_pct` | 0.0 / 0.0 |
| `backtest_entry_policy` | `next_open` |
| `backtest_ambiguous_candle_policy` | `stop_first` |
| `backtest_overlap_policy` | `ALLOW_OVERLAP` |
| `backtest_position_max_bars` | 60 |
| `backtest_split_train`/`validation`/`test` | 0.6 / 0.2 / 0.2 |
| `walk_forward_train_bars`/`validation_bars`/`test_bars`/`step_bars` | 500 / 200 / 200 / 200 |

The backtester uses these defaults; per-run overrides are possible via
`BacktestEngine(..., execution_overrides=...)` (used by the CLI).

---

## 16. Tests

**65 new Phase 4 tests** (deterministic synthetic data; no live network):

| File | Coverage |
|---|---|
| `test_backtest_engine.py` | chronological replay, determinism, ID stability, next-open fill |
| `test_backtest_normalizer.py` | score normalisation + live-scale regression |
| `test_backtest_execution.py` | entry/exit, ambiguous candle, short side, fees/slippage |
| `test_backtest_outcomes.py` | never-triggered is not a loss; outcome classification |
| `test_backtest_metrics.py` | win rate, expectancy, PF, drawdown, streaks, edge cases, breakdowns |
| `test_backtest_split.py` | chronological split, leak guard, walk-forward geometry |
| `test_backtest_walkforward.py` | independent out-of-sample windows, determinism |
| `test_backtest_repository.py` | save/load round-trip, config/execution verbatim, isolation |
| `test_backtest_report.py` | JSON/text output; never claims profitability |
| `test_backtest_integration.py` | invariants across market regimes; score scale |

**Full suite: 210 tests passing** (Phase 0/1 + Phase 2 101 + Phase 3 43 + Phase 4 65).

---

## 17. Known limitations

- **Whole-bar execution only:** stop/target use bar high/low; no intrabar
  ordering is assumed (handled conservatively by the ambiguous-candle rule).
- **No intrabar path modelling:** pivots use whole-bar high/low (Phase 2
  limitation).
- **Per-trade outcomes, not a portfolio:** no position sizing, no portfolio
  optimization, no margin/leverage, no equity-curve trading logic
  (`ALLOW_OVERLAP` just opens concurrent positions).
- **Synthetic test data:** example runs use synthetic frames; they demonstrate
  the tooling, not a real strategy's performance.
- **Costs are approximations:** fees/slippage are flat fractions; no
  per-exchange fee tables or partial fills.
- **Test-size caveat:** low trade counts make win-rate/expectancy noisy; the
  report does not fabricate statistical significance.
- **No path to live:** results are measurements only; nothing here executes.

---

## 18. Research risks (reported honestly)

- **Hypothesis fragility:** the Phase 3 weights/thresholds are calibratable
  hypotheses. A backtest can *refute* a hypothesis; it cannot *prove* future
  profit.
- **Selection/examining bias:** many detector variations were explored; reported
  numbers must be read as in-sample-ish unless explicitly limited to held-out
  test windows. Walk-forward is used to reduce this.
- **Look-ahead must be re-audited on every change.** The slice-per-bar contract
  is the guardrail; a future refactor that stops slicing would silently
  reintroduce bias.
- **Ambiguous-candle conservatism lowers headline results by design.** Treat
  better-looking numbers as suspect, worse-looking numbers as more trustworthy.

---

## 19. Phase 4 example results (synthetic, illustrative)

On synthetic `bull_continuation_frame`/`higher_highs_higher_lows_frame` (used to
exercise the pipeline, **not** to assert profitability), typical output shows
`BREAKOUT`/`BREAKOUT_RETEST`/`TREND_CONTINUATION`/`PULLBACK` trades with a mix of
`STOP`/`TARGET`/`EXPIRED` exits. Many breakout setups on a strict HH-HL zigzag
resolve to `STOP`, which is expected and is **not** a claim about any real
instrument.

**Explicit disclaimer:** all Phase 4 results shown are deterministic-synthetic
measurements used to validate the machinery. **Veyra makes no profitability
claim.** If a backtest looks poor, the honest reading is that the hypothesis
fails on that sample.

---

## 20. Phase 5 recommendation

Phase 5 should move from measurement to **paper trading / research
extrapolation only** if—and only if—backtest numbers on **held-out real data**
justify it:

- Cut over the replay to **real historical candles** across many
  symbols/timeframes and report per-lane walk-forward stability.
- **Cross-timeframe confirmation** (1D trend + 4H setup) once per-TF numbers are
  stable.
- Add **position-sizing / portfolio** on top of the per-trade outcomes, then a
  **paper-trading hook** in `paper/` (Phase 5 scaffold already reserved in the
  repo layout).
- Only after a defensible, out-of-sample edge is shown, consider surfacing
  qualified setups to a watchlist/notifier (Phase 7) or probability calibration.
  If no edge is found, the honest Phase 5 is to **keep tuning hypotheses**, not
  to ship a live trader.