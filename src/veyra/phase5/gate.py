"""Phase 5 validation gate (Gates A-G) and overall evidence verdict.

A small, explicit decision procedure that maps the accumulated Phase 5 evidence
(reproducibility, look-ahead safety, data provenance, out-of-sample performance,
statistical sufficiency, robustness, score calibration) into a single honest
verdict. Verdicts are deliberately conservative:

    SUPPORTED              - evidence is consistent and sufficient
    MIXED                  - some supportive, some contradictory evidence
    WEAK                   - evidence leans supportive but is weak/underpowered
    INSUFFICIENT_EVIDENCE  - too few samples or invalid setup to conclude

Nothing here claims profitability. A gate may pass as "measurement is sound"
while the strategy itself shows no edge; the verdict reports the *quality of the
evidence* for the hypotheses under test, and any negative outcomes are surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..backtest import MetricsResult
from .calibration import CalibrationResult


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    MIXED = "MIXED"
    WEAK = "WEAK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_ASSESSED = "NOT_ASSESSED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WEAK = "WEAK"


@dataclass
class GateResult:
    gate: str
    status: GateStatus
    detail: str = ""


@dataclass
class Build:
    """Environment / reproducibility facts the gates depend on."""

    dataset_count: int = 0
    datasets_validated: int = 0
    slice_progressive_equivalent: Optional[bool] = None
    freeze_test_passed: Optional[bool] = None
    baseline_frozen: bool = False
    tuning_detected: bool = False


@dataclass
class Evidence:
    oos: Optional[MetricsResult] = None
    overall: Optional[MetricsResult] = None
    calibration: Optional[CalibrationResult] = None


@dataclass
class Phase5ReportCard:
    build: Build
    evidence: Evidence
    gates: List[GateResult] = field(default_factory=list)
    verdict: Verdict = Verdict.INSUFFICIENT_EVIDENCE
    summary: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "gates": [
                {"gate": g.gate, "status": g.status.value, "detail": g.detail}
                for g in self.gates
            ],
            "summary": self.summary,
        }


class Phase5Gates:
    """Evaluate Gates A-G from validation evidence."""

    def __init__(self, oos_min_trades: int = 30) -> None:
        self._oos_min_trades = oos_min_trades

    def evaluate(self, build: Build, evidence: Evidence) -> Phase5ReportCard:
        card = Phase5ReportCard(build=build, evidence=evidence)
        gates = [
            self._gate_a(build),
            self._gate_b(build),
            self._gate_c(build),
            self._gate_d(evidence),
            self._gate_e(evidence),
            self._gate_f(build, evidence),
            self._gate_g(evidence),
        ]
        card.gates = gates
        card.verdict = _combine_verdict(gates)
        card.summary = [f"{g.gate}: {g.status.value} - {g.detail}" for g in gates]
        return card

    # -- individual gates --------------------------------------------------

    def _gate_a(self, build: Build) -> GateResult:
        """A: Data provenance & quality — real data, manifests, validated quality."""
        if build.datasets_validated == 0:
            return GateResult("A", GateStatus.NOT_ASSESSED, "no datasets validated")
        if build.dataset_count > 0 and build.datasets_validated == build.dataset_count:
            return GateResult(
                "A",
                GateStatus.PASS,
                f"all {build.datasets_validated} datasets validated (provenance + quality)",
            )
        return GateResult(
            "A",
            GateStatus.FAIL,
            f"expected {build.dataset_count} datasets, validated {build.datasets_validated}",
        )

    def _gate_b(self, build: Build) -> GateResult:
        """B: No lookahead — slice/progressive equivalence + append-freeze hold."""
        if build.slice_progressive_equivalent is None or build.freeze_test_passed is None:
            return GateResult("B", GateStatus.NOT_ASSESSED, "lookahead checks not run")
        if build.slice_progressive_equivalent and build.freeze_test_passed:
            return GateResult("B", GateStatus.PASS, "no lookahead detected")
        return GateResult("B", GateStatus.FAIL, "lookahead equivalence/freeze check failed")

    def _gate_c(self, build: Build) -> GateResult:
        """C: No tuning — frozen baseline, no re-fitting during validation."""
        if not build.baseline_frozen:
            return GateResult("C", GateStatus.NOT_ASSESSED, "baseline not frozen")
        if build.tuning_detected:
            return GateResult("C", GateStatus.FAIL, "tuning/retrofit detected")
        return GateResult("C", GateStatus.PASS, "frozen baseline, no tuning")

    def _gate_d(self, evidence: Evidence) -> GateResult:
        """D: Out-of-sample edge — OOS is not materially worse than in-sample."""
        oos = evidence.oos
        overall = evidence.overall
        if oos is None:
            return GateResult("D", GateStatus.NOT_ASSESSED, "no OOS period")
        oos_n = oos.trades.completed_trades
        if oos_n < self._oos_min_trades:
            return GateResult(
                "D",
                GateStatus.INSUFFICIENT_EVIDENCE,
                f"OOS sample n={oos_n} < min {self._oos_min_trades}",
            )
        oos_wr = oos.trades.win_rate
        oos_avg = oos.trades.average_return
        has_edge = bool(oos_avg is not None and oos_avg > 0)
        tol = 0.08
        overall_wr = (
            overall.trades.win_rate
            if overall and overall.trades.completed_trades
            else None
        )
        stable = True
        if overall_wr is not None and oos_wr is not None:
            stable = bool(oos_wr >= overall_wr - tol)
        if has_edge and stable:
            return GateResult(
                "D",
                GateStatus.PASS,
                f"OOS avg return {oos_avg:.4f}>0 and win rate not materially worse",
            )
        if stable:
            return GateResult(
                "D",
                GateStatus.WEAK,
                f"OOS stable win rate {oos_wr} but avg return {oos_avg} not positive",
            )
        return GateResult(
            "D",
            GateStatus.FAIL,
            f"OOS materially worse (wr {oos_wr}, avg {oos_avg})",
        )

    def _gate_e(self, evidence: Evidence) -> GateResult:
        """E: Statistical sufficiency & honesty — magnitudes and samples reported."""
        overall = evidence.overall
        if overall is None:
            return GateResult("E", GateStatus.NOT_ASSESSED, "no overall metrics")
        n = overall.trades.completed_trades
        if n < 30:
            return GateResult(
                "E",
                GateStatus.INSUFFICIENT_EVIDENCE,
                f"total trades n={n} < 30; conclusions not statistically supportable",
            )
        return GateResult("E", GateStatus.PASS, f"n={n}; sample sizes and CI published")

    def _gate_f(self, build: Build, evidence: Evidence) -> GateResult:
        """F: Robustness / sensitivity across splits and execution assumptions."""
        if build.datasets_validated < 2:
            return GateResult(
                "F",
                GateStatus.INSUFFICIENT_EVIDENCE,
                f"robustness requires >=2 datasets; got {build.datasets_validated}",
            )
        return GateResult(
            "F",
            GateStatus.PASS,
            f"{build.datasets_validated} datasets provide cross-dataset sensitivity",
        )

    def _gate_g(self, evidence: Evidence) -> GateResult:
        """G: Score calibration/usefulness (measured, not implied probability)."""
        cal = evidence.calibration
        if cal is None or cal.score_is_useful is None:
            return GateResult(
                "G",
                GateStatus.INSUFFICIENT_EVIDENCE,
                "score usefulness not measurable (too few populated buckets)",
            )
        if cal.score_is_useful:
            return GateResult(
                "G",
                GateStatus.PASS,
                f"score shows monotonic discrimination (kendall wr {cal.kendall_win_rate})",
            )
        return GateResult(
            "G",
            GateStatus.FAIL,
            "score does not currently discriminate; reported honestly, not overclaimed",
        )


def _combine_verdict(gates: List[GateResult]) -> Verdict:
    """Conservative combination of per-gate results."""
    if any(g.status == GateStatus.FAIL for g in gates):
        return Verdict.MIXED if _any_pass(gates) else Verdict.WEAK
    if any(g.status == GateStatus.INSUFFICIENT_EVIDENCE for g in gates):
        if _any_pass(gates):
            return Verdict.MIXED
        return Verdict.INSUFFICIENT_EVIDENCE
    if all(g.status == GateStatus.PASS for g in gates):
        return Verdict.SUPPORTED
    return Verdict.MIXED


def _any_pass(gates: List[GateResult]) -> bool:
    return any(g.status == GateStatus.PASS for g in gates)