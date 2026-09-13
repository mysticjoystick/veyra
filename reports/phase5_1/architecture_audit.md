# Veyra — Production Architecture Audit (end-to-end)

Date: 2026-09-03 | Scope: full production pipeline integrity, before any further
strategy change. This audit is **not an experiment** — it traces and verifies the
production wiring `data → detection → qualification → scoring → exits → UI/alerts`
and fixes confirmed defects. Strategy/parameter changes (e.g. E9 target-survival)
are explicitly **deferred until a clean baseline is established**, per plan.

Every claim is labelled **WIRED** (connected and exercised), **DEAD** (a real bug:
writes/reads the wrong thing or feeds no decision), **STUB** (an empty/near-empty
placeholder), or **OK** (verified working).

---

## 1. Data layer

| Item | Status | Notes |
|---|---|---|
| `data/provider/binance.py` | WIRED | Real provider, incremental ingest |
| `data/validator.py` | WIRED | Gap/invalid-row detection |
| `data/candle_store.py` (Parquet) | WIRED | Load/sort by `open_time`, used everywhere |
| `data/service.py` + CLI `download/update` | WIRED | `veyra download/validate/inspect/update` |
| pipeline data-quality pass | OK | `_assess_data_quality` sets state from frame |

**FINDING (OK):** Data layer is sound end-to-end. Candles load correctly, are
sorted ascending, and every backtest/analysis path re-sorts and resets the index
(`engine.run:148`). No gap or look-ahead defect found in loading.

## 2. Detection → Qualification chain

| Item | Status | Notes |
|---|---|---|
| `setup_engine.detect` + 5 detectors | WIRED | all five registered (`_build_detectors`) |
| SnapshotView (causal meta) | WIRED | detectors read per-component meta, never recompute |
| Lifecycle `_promote → advance` | WIRED | DETECTED→DEVELOPING→QUALIFIED→TRIGGERED |
| Qualification gate | WIRED | **momentum-confirmation only** (`_confirms`) |
| `setup_min_qualify_score` | **DEAD** | config exists, serialized to metadata, **never gates anything** |

**FINDING:** all 5 detectors are wired and the lifecycle works. **However,
qualification is gated purely by momentum continuation; the configured
`setup_min_qualify_score` is never consulted.** The score does not affect who
qualifies.

## 3. Scoring — CONFIRMED DEAD-SCORE BUG

| Item | Status | Notes |
|---|---|---|
| Pipeline produces genuine score | WIRED | `AnalysisComponentOutput.score` populated from `EngineResult.score` |
| `SetupScorer._component_scores` | **DEAD** | reads `view.meta(name).get("score")` |
| `SnapshotView.meta` | OK | returns `comp.meta` — the meta dict, **no "score" key** |
| Genuine score reachable via | OK | `snapshot.components[name].score` / `snapshot.scores[name]` |

**THE BUG (code-confirmed, matches E5/E6 empirical finding):**
`scoring.py::_score_from_meta` reads `meta.get("score")`, but the market engine
stores its alignment score in `AnalysisComponentOutput.score`, NOT in the `meta`
dict. `SnapshotView.meta()` returns only `comp.meta`. Therefore `_trend`,
`_structure`, `_momentum`, `_volume` always fall back to their `default` (0), so:

- TREND score = **0** always,
- STRUCTURE score = **0** always,
- MOMENTUM score = **0** always,
- VOLUME score = **0** always,
- only VOLATILITY (state→75/40) and PULLBACK (rule-based 85/40) are real.

The existing unit tests **mask** this: `test_overall_score_is_weighted_sum`
computes `expected` *from the applied (broken, 0) scores*, so it passes while the
genuine evidence is ignored. `setup_synth` intentionally puts real scores in
`components[...].score` but the scorer never looks there.

**Impact:** the `overall_score`/ranking across the whole system (reports, paper,
backtest breakdowns, E5/E6/E7/E8 score buckets) has been a 2-component
(PULLBACK+VOLATILITY) measure and never saw TREND/STRUCTURE/MOMENTUM/VOLUME. It
has no bearing on qualification or entry (which are not score-gated), so it did
not change *events*; it corrupted every *reported* score and any decision the
project might later attach to the score.

**FIX (architecture/correctness, safe — does not change which trades are taken):**
make the scorer read the genuine component score, preserve the existing
side/opposition penalties, and add a regression test that asserts the genuine
values feed through.

## 4. Exits / execution path

| Item | Status | Notes |
|---|---|---|
| `execution.compute_exit` (stop/target/expiry) | WIRED/OK | deterministic, `stop_first` conservative |
| `simulator._levels` (stop/target) | WIRED | stop=interest_area low/high; target=targets[0] |
| `simulator.enter` target validation | OK (conservative) | nulls a target on the wrong side of the entry open |
| `position_max_bars` time stop | WIRED/OK | over-aged close at bar close |
| fees + slippage always applied | OK | never zero |

**FINDING:** execution is correctly wired and conservative. The observed
"half of trades have no target" (E7/E8) is the **entry-time validation** in
`simulator.enter` nulling targets that the next-bar open gaps through — most on
BREAKOUT_RETEST (43–55%). This is **conservative and correct**, not a bug; making
these trades win would be a **strategy change (E9)** and is **deferred** until
after a clean baseline, per plan. No fix applied now.

## 5. UI / Alerts layer

| Item | Status | Notes |
|---|---|---|
| `api/main.py` | STUB-ish | health/root only; no strategy/scan endpoints |
| `alerts/__init__.py` | **STUB** | empty — no alert generator exists |
| `web/__init__.py` | **STUB** | empty — no dashboard/UI exists |
| `paper/` | WIRED | `PaperEngine` exists, `REAL_EXECUTION_FORBIDDEN`, no real orders |
| CLI | WIRED | download/validate/inspect/backtest/validate-real/paper all wired |

**FINDING:** paper trading is real and safe (simulation-only). **There is no
alert system and no web UI** — both remain empty placeholders (roadmap Phases 6–7).
"Nitch" disqualifiers: alerts/web are out of scope for this audit's fix set (they
are unbuilt features, not broken wiring); they are noted as the remaining product
surface, not defects in the existing architecture.

## 6. Consolidated defect/fix list

| # | Layer | Class | Defect | Action |
|---|---|---|---|---|
| 1 | Scoring | **DEAD** | Scorer reads `meta["score"]` (never present) instead of `components[name].score`; TREND/STRUCTURE/MOMENTUM/VOLUME always 0 | **Fix now** (correctness) |
| 2 | Qualification | **DEAD-ish** | `setup_min_qualify_score` never consulted | **Document** (score not a gate; fix #1 makes the score meaningful; wiring a gate = strategy change, deferred) |
| 3 | Exits | OK (conservative) | target nulling at entry | **Deferred → E9** (strategy change) |
| 4 | Alerts/Web | STUB | empty packages | **Noted** (unbuilt feature, Phase 6–7) |

## 7. What "clean baseline" means and what is deferred

- **Now (this audit+fix):** correct the dead-score wiring so every reported score
  reflects genuine evidence; add a regression test; re-run the full test suite;
  confirm trades taken are identical (score is not a gate) so the frozen baseline
  provenance is preserved. This is the clean baseline.
- **Deferred until after the clean baseline:** ANY strategy change — including
  E9 (target survival) which the audit's E9 scope proposes. The decorum here is
  that we first establish a correct, tested, reproducible architecture, then
  decide strategy changes on top of it.

## 8. Acceptance for this audit

1. `SetupScorer` reads genuine component scores (regression test added).
2. Full test suite green (238 existing + new).
3. Number of completed trades unchanged vs frozen baseline (score is not a gate)
   — verified by diffing a baseline run before/after the fix.
4. Reports/`_e*.json` untouched except where they were already written.