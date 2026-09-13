"""Machine-readable and human-readable backtest reports.

Never presents a statistic the metrics engine did not compute, and never labels
scores as probabilities. The report surfaces data-quality and validation
admonitions so unsupported claims are visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .metrics import MetricsResult
from .models import SimulationResult


@dataclass
class BacktestReport:
    result: SimulationResult
    metrics: MetricsResult
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run": self.result.run.to_dict(),
            "metrics": self.metrics.to_dict(),
            "coverage": self.result.coverage(),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def render_text(self) -> str:
        r = self.result.run
        m = self.metrics
        t = m.trades
        risk = m.risk
        opp = m.opportunity
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("VEYRA BACKTEST REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append("Run:")
        lines.append(f"  Symbol            : {r.symbol}")
        lines.append(f"  Timeframe         : {r.timeframe}")
        lines.append(f"  Run key           : {r.run_key}")
        lines.append(f"  Strategy version  : {r.strategy_version}")
        lines.append(f"  Engine version    : {r.engine_version}")
        lines.append(f"  Dataset hash      : {r.dataset_hash}")
        lines.append(f"  Candle count      : {r.candle_count}")
        lines.append("")
        lines.append("Execution assumptions:")
        lines.append(f"  entry fee         : {r.execution.entry_fee_pct}")
        lines.append(f"  exit fee          : {r.execution.exit_fee_pct}")
        lines.append(f"  slippage          : {r.execution.slippage_pct}")
        lines.append(f"  spread            : {r.execution.spread_pct}")
        lines.append(f"  entry policy      : {r.execution.entry_policy}")
        lines.append(f"  ambiguous candle  : {r.execution.ambiguous_candle_policy}")
        lines.append(f"  overlap policy    : {r.execution.overlap_policy}")
        lines.append("")
        lines.append("Setups:")
        lines.append(f"  Detected          : {t.total_setups}")
        lines.append(f"  Qualified         : {t.qualified_setups}")
        lines.append(f"  Triggered         : {t.triggered_setups}")
        cov = self.result.coverage()
        lines.append("Coverage:")
        lines.append(f"  Analyzed bars     : {cov['analyzed_bars']}")
        lines.append(f"  Bars w/ setup     : {cov['bars_with_setup']}")
        lines.append(f"  WAIT (no-signal)  : {cov['wait_bars']}")
        lines.append(f"  Coverage fraction : {cov['coverage_fraction']:.3f}")
        lines.append("Opportunity:")
        lines.append(f"  Qualification rate: {opp.qualification_rate:.3f}")
        lines.append(f"  Trigger rate      : {opp.trigger_rate:.3f}")
        lines.append(f"  Invalidation rate : {opp.invalidation_rate:.3f}")
        lines.append(f"  Expiry rate       : {opp.expiry_rate:.3f}")
        lines.append("")
        lines.append("Trades:")
        lines.append(f"  Completed         : {t.completed_trades}")
        lines.append(f"  Wins              : {t.wins}")
        lines.append(f"  Losses            : {t.losses}")
        lines.append(f"  Breakeven         : {t.breakeven}")
        lines.append(f"  Win rate          : {t.win_rate:.3f}")
        lines.append("")
        lines.append("Performance:")
        lines.append(f"  Expectancy        : {risk.expectancy:.5f}")
        lines.append(f"  Profit factor     : {_pf(risk.profit_factor)}")
        lines.append(f"  Average return    : {t.average_return:.5f}")
        lines.append(f"  Median return     : {t.median_return:.5f}")
        lines.append(f"  Cumulative return : {risk.cumulative_return:.5f}")
        lines.append(f"  Max drawdown      : {risk.max_drawdown:.5f}")
        lines.append(f"  Longest loss run  : {risk.longest_losing_streak}")
        lines.append(f"  Avg holding bars  : {risk.average_holding_bars:.1f}")
        lines.append("")
        self._render_buckets(lines, "By Setup Type", m.by_setup_type)
        self._render_buckets(lines, "By Regime", m.by_regime)
        self._render_buckets(lines, "By Timeframe", m.by_timeframe)
        self._render_buckets(lines, "By Score Bucket", m.by_score_bucket)
        lines.append("")
        lines.append("Score interpretation:")
        lines.append(
            "  Scores are a 0-100 QUALITY RANKING, NOT a probability. "
            "No statistical calibration has been performed in this phase."
        )
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def _render_buckets(lines, title: str, buckets: Dict[str, object]) -> None:
        lines.append(f"{title}:")
        if not buckets:
            lines.append("  (none)")
            return
        for label, b in sorted(buckets.items()):
            pf = _pf(b.profit_factor)
            lines.append(
                f"  {label:<20} trades={b.trades:<4} "
                f"win_rate={b.win_rate:.3f} avg={b.average_return:.5f} "
                f"pf={pf}"
            )


def _pf(pf) -> str:
    if pf is None:
        return "n/a"
    if pf == float("inf"):
        return "inf"
    return f"{pf:.3f}"


class BacktestReporter:
    def build(
        self,
        result: SimulationResult,
        metrics: MetricsResult,
        warnings: Optional[List[str]] = None,
    ) -> BacktestReport:
        return BacktestReport(result=result, metrics=metrics, warnings=list(warnings or []))