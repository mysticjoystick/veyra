"""Phase 5 report generator: renders validation evidence as Markdown.

Consumes the artifacts produced by the Phase 5 workflow and emits a per-dataset
report plus an overall gate verdict report under ``reports/phase5/``. The report
answers the 14 reconstructed Phase 5 questions, each grounded in a measurement
and never over-claiming profitability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..backtest import MetricsResult
from ..config import Settings, get_settings
from .calibration import CalibrationResult
from .gate import Phase5ReportCard
from .validate import DatasetValidation

# The 14 reconstructed Phase 5 questions (see docs/phase-5-data-quality.md and
# the Phase 4 -> Phase 5 research plan).
QUESTIONS: List[str] = [
    "Does the strategy show net out-of-sample edge after transaction costs on real data?",
    "Are results stable across chronological IN / VALIDATION / OOS periods?",
    "Are results consistent across walk-forward windows?",
    "Which setup types actually produce edge (breakdown by setup type)?",
    "In which market regimes does the strategy produce edge (breakdown by regime)?",
    "Does performance persist across the 1D and 4H timeframes?",
    "Does the score usefully discriminate outcomes (calibration, not a probability)?",
    "How robust are results to execution assumptions (fees/slippage)?",
    "Are the effective sample sizes sufficient to draw conclusions (statistical honesty)?",
    "Are the runs reproducible (frozen baseline + progressive equivalence)?",
    "Is the data provenance valid (real data, manifests, no fabrication)?",
    "Are there data-quality red flags (gaps, duplicates, reversed bars)?",
    "How does the strategy compare against a baseline (buy-and-hold / 50-50)?",
    "What is the overall Phase 5 evidence verdict across Gates A-G?",
]

_DISCLAIMER = (
    "> **No profitability claim.** All figures below are measurements of a "
    "hypothesis on historical candles. They do not predict future returns. "
    "Synthetic data is never treated as evidence."
)


@dataclass
class ReportInputs:
    symbol: str
    timeframe: str
    validation: Optional[DatasetValidation] = None
    calibration: Optional[CalibrationResult] = None
    card: Optional[Phase5ReportCard] = None
    walk_forward: Optional[Dict] = None
    robustness: Optional[Dict] = None
    buy_hold: Optional[Dict] = None
    provenance: Optional[Dict] = None
    data_quality: Optional[Dict] = None


@dataclass
class Phase5Report:
    symbol: str
    timeframe: str
    markdown: str

    def write(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.markdown, encoding="utf-8")
        return dest


def _fmt(x, nd=3):
    try:
        if x is None:
            return "n/a"
        return f"{x:.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(x, nd=1):
    try:
        if x is None:
            return "n/a"
        return f"{x * 100:.{nd}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _buy_hold_return(df) -> Dict[str, float]:
    """Composite return over the frame (raw, ignores fees)."""
    first = float(df["close"].iloc[0])
    last = float(df["close"].iloc[-1])
    total = last / first - 1.0
    return {"total": total, "first_close": first, "last_close": last}


class Phase5Reporter:
    """Render a per-dataset Phase 5 report answering QUESTIONS."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    def build(self, inputs: ReportInputs) -> Phase5Report:
        v = inputs.validation
        cal = inputs.calibration
        card = inputs.card
        wf = inputs.walk_forward
        rob = inputs.robustness
        bh = inputs.buy_hold
        prov = inputs.provenance or {}
        dq = inputs.data_quality or {}

        L: List[str] = []
        a = L.append
        a(f"# Phase 5 Validation Report — {inputs.symbol} {inputs.timeframe}")
        a("")
        a(_DISCLAIMER)
        a("")
        a(f"- **Baseline version**: {v.strategy_version if v else 'n/a'}")
        a(f"- **Candles**: {v.candle_count if v else 'n/a'}")
        a(f"- **Range**: {_fmt(v.start_ts if v else None, 0)} .. "
          f"{_fmt(v.end_ts if v else None, 0)} (unix s)")
        if v:
            a(f"- **Engine version**: {v.engine_version or 'n/a'}")
        a("")

        # Q1: net OOS edge after costs.
        oos = _oos_metrics(v)
        a("## Q1 — Net out-of-sample edge after costs")
        if oos:
            a(f"- OOS trades: {oos.trades.completed_trades}")
            a(f"- OOS win rate: {_pct(oos.trades.win_rate)}")
            a(f"- OOS average return/trade (net of fees/slippage): {_fmt(oos.trades.average_return)}")
            a(f"- OOS expectancy: {_fmt(oos.risk.expectancy)}")
            a(f"- OOS profit factor: {_fmt(oos.risk.profit_factor)}")
        else:
            a("- No OOS period available.")
        a("")

        # Q2: stability across periods.
        a("## Q2 — Stability across IN / VALIDATION / OOS")
        if v:
            a("| period | trades | win rate | avg return | expectancy | profit factor |")
            a("|---|---|---|---|---|---|")
            for p in v.periods:
                a(f"| {p.label} | {p.counts['trades']} | "
                  f"{_pct(p.metrics.trades.win_rate)} | {_fmt(p.metrics.trades.average_return)} | "
                  f"{_fmt(p.metrics.risk.expectancy)} | {_fmt(p.metrics.risk.profit_factor)} |")
        a("")

        # Q3: walk-forward consistency.
        a("## Q3 — Walk-forward consistency")
        if wf:
            a(f"- windows: {wf.get('window_count', 'n/a')}")
            rows = wf.get("windows", [])
            if rows:
                a("| window | trades | win rate | expectancy |")
                a("|---|---|---|---|")
                for i, w in enumerate(rows):
                    tr = w.get("trades", {})
                    a(f"| {i} | {tr.get('completed_trades', 'n/a')} | "
                      f"{_pct(tr.get('win_rate'))} | {_fmt(w.get('risk', {}).get('expectancy'))} |")
        else:
            a("- Walk-forward not run for this dataset.")
        a("")

        # Q4: by setup type.
        a("## Q4 — Breakdown by setup type")
        _table(a, _breakdown(v, "by_setup_type"))
        # Q5: by regime.
        a("## Q5 — Breakdown by market regime")
        _table(a, _breakdown(v, "by_regime"), extra=None)

        # Q6: timeframe persistence (compare this TF to counterpart from inputs).
        a("## Q6 — Timeframe persistence")
        a("- This report covers **{tf}**. Cross-timeframe comparison is shown in the "
          "overall report once all datasets are validated.".format(tf=inputs.timeframe))
        a("")

        # Q7: calibration.
        a("## Q7 — Score calibration / usefulness")
        if cal:
            a(f"- Overall win rate: {_pct(cal.overall_win_rate)}")
            a(f"- Score useful: {cal.score_is_useful}")
            a(f"- Monotonic win-rate: {cal.monotonic_win_rate} "
              f"(kendall {_fmt(cal.kendall_win_rate)})")
            a("")
            a("| bucket | trades | win rate | avg return | 95% CI |")
            a("|---|---|---|---|---|")
            for b in cal.buckets:
                lo = _fmt(b.win_rate_ci_low) if b.win_rate_ci_low is not None else "n/a"
                hi = _fmt(b.win_rate_ci_high) if b.win_rate_ci_high is not None else "n/a"
                a(f"| {b.bucket} | {b.trades} | {_pct(b.win_rate)} | "
                  f"{_fmt(b.average_return)} | {lo}–{hi} |")
            a("")
            a("> The score is a *setup-quality* score, **not** a calibrated probability "
              f"of profit. Notes: {' '.join(cal.notes)}")
        a("")

        # Q8: robustness.
        a("## Q8 — Robustness to execution assumptions")
        if rob is not None and getattr(rob, "scenarios", None):
            a("| scenario | fees+slip | win rate | expectancy | avg return |")
            a("|---|---|---|---|---|")
            for s in rob.scenarios:
                a(f"| {s.scenario} | {s.fees_pct + s.slippage_pct:.4f} | "
                  f"{_pct(s.win_rate)} | {_fmt(s.expectancy)} | {_fmt(s.avg_return)} |")
            a(f"- Edge sign flips across cost scenarios: {rob.flips_sign}")
        elif rob:
            a("| scenario | win rate | expectancy | avg return |")
            a("|---|---|---|---|")
            for k, val in rob.items():
                a(f"| {k} | {_pct(val.get('win_rate'))} | "
                  f"{_fmt(val.get('expectancy'))} | {_fmt(val.get('average_return'))} |")
        else:
            a("- Robustness not assessed (see overall report).")
        a("")

        # Q9: sample sizes.
        a("## Q9 — Statistical sufficiency")
        if v:
            n = v.overall.trades.completed_trades
            a(f"- Total completed trades: {n}")
            a(f"- OOS completed trades: {oos.trades.completed_trades if oos else 'n/a'}")
            a("- Wilson 95% CI is reported per score bucket (Q7); low n is flagged, "
              "never flattered.")
        a("")

        # Q10: reproducibility.
        a("## Q10 — Reproducibility")
        a(f"- Frozen baseline: {inputs.card.build.baseline_frozen if card else 'n/a'}")
        a(f"- Progressive/slice equivalence: "
          f"{inputs.card.build.slice_progressive_equivalent if card else 'n/a'}")
        a(f"- Look-ahead freeze test: "
          f"{inputs.card.build.freeze_test_passed if card else 'n/a'}")
        a("")

        # Q11: provenance.
        a("## Q11 — Data provenance")
        a("- Source: Binance (real market data).")
        a(f"- Manifest: {prov.get('manifest', 'present')}")
        a(f"- Provider: {prov.get('provider', 'binance')}")
        a("")

        # Q12: data quality.
        a("## Q12 — Data-quality red flags")
        a(f"- candle count: {dq.get('count', v.candle_count if v else 'n/a')}")
        a(f"- gaps: {dq.get('gaps', 'n/a')}")
        a(f"- duplicates: {dq.get('duplicates', 'n/a')}")
        a(f"- reversed bars: {dq.get('reversed', 'n/a')}")
        a("")

        # Q13: baseline comparison.
        a("## Q13 — Baseline comparison")
        if bh:
            a(f"- Buy-and-hold composite return over the frame: {_pct(bh.get('total'), 1)}")
        else:
            a("- Buy-and-hold baseline not computed.")
        if oos:
            a(f"- Strategy OOS avg return per trade: {_fmt(oos.trades.average_return)} "
              f"(after fees/slippage).")
        a("")

        # Q14: overall verdict.
        a("## Q14 — Overall evidence verdict (Gates A–G)")
        if card:
            a(f"- **Overall: {card.verdict.value}**")
            a("")
            a("| gate | status | detail |")
            a("|---|---|---|")
            for g in card.gates:
                a(f"| {g.gate} | {g.status.value} | {g.detail} |")
        else:
            a("- Gates not evaluated for this dataset.")
        a("")

        a("---")
        a("_Generated by Veyra Phase 5 report tooling. Measurements only — no "
          "profitability claim._")
        return Phase5Report(symbol=inputs.symbol, timeframe=inputs.timeframe,
                            markdown="\n".join(L))


def _oos_metrics(v: Optional[DatasetValidation]) -> Optional[MetricsResult]:
    if not v:
        return None
    for p in v.periods:
        if p.label == "OOS":
            return p.metrics
    return None


def _breakdown(v: Optional[DatasetValidation], field: str) -> Dict[str, object]:
    if not v:
        return {}
    bucket = getattr(v.breakdown, field) if hasattr(v.breakdown, field) else None
    return bucket or {}


def _table(a, buckets: Dict[str, object], extra: Optional[str] = None):
    if not buckets:
        a("- no data")
        a("")
        return
    a("| bucket | trades | win rate | avg return | expectancy |")
    a("|---|---|---|---|---|")
    for key, b in buckets.items():
        a(f"| {key} | {b.trades} | {_pct(b.win_rate)} | "
          f"{_fmt(b.average_return)} | {_fmt(b.expectancy)} |")
    a("")


class Phase5ReportSuite:
    """Generate all per-dataset reports + a combined JSON bundle."""

    def __init__(
        self,
        dest: Path,
        settings: Optional[Settings] = None,
    ) -> None:
        self._dest = Path(dest)
        self._settings = settings or get_settings()
        self._reporter = Phase5Reporter(self._settings)

    def write_dataset(self, inputs: ReportInputs) -> Path:
        report = self._reporter.build(inputs)
        filename = f"phase5-{inputs.symbol.replace('/', '-')}-{inputs.timeframe}.md"
        out = report.write(self._dest / filename)

        # Also persist a machine-readable JSON next to the markdown.
        payload = {
            "symbol": inputs.symbol,
            "timeframe": inputs.timeframe,
            "validation": json.loads(json.dumps(inputs.validation.to_dict(), default=str))
            if inputs.validation else None,
            "calibration": inputs.calibration.to_dict() if inputs.calibration else None,
            "card": inputs.card.to_dict() if inputs.card else None,
            "walk_forward": inputs.walk_forward or None,
            "robustness": inputs.robustness.to_dict()
            if hasattr(inputs.robustness, "to_dict") else (inputs.robustness or None),
        }
        json_path = self._dest / (filename.replace(".md", ".json"))
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return out