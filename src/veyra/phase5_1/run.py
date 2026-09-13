"""Phase 5.1 run driver: validates all frozen datasets once and writes reports.

Usage:  python -m veyra.phase5_1.run [--symbol BTC/USDT --timeframe 1D ...]

Without args, validates the full grid (BTC/ETH × 4H/1D). The frozen baseline
engine is run once per dataset (progressive single pass); results are captured
in memory for slicing, then the 9 diagnostic reports are written to
``reports/phase5_1/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import get_settings
from ..data.candle_store import CandleStore
from ..phase5.frozen_config import PHASE5_BASELINE_VERSION
from .diagnostics import DEFAULT_DATASETS_SPEC, DatasetData, DiagnosticsSet, validate_dataset
from .report import Phase51Reporter

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "reports" / "phase5_1"


def _load_df(symbol: str, timeframe: str, settings):
    store = CandleStore(settings)
    df = store.load(symbol, timeframe)
    return df.sort_values("open_time").reset_index(drop=True)


def run_datasets(
    specs: List[Tuple[str, str]], dest: Path = REPORTS_DIR
) -> List[Path]:
    settings = get_settings()
    datasets: List[DatasetData] = []
    for symbol, timeframe in specs:
        print(f"[phase5_1] validating {symbol} {timeframe} …")
        df = _load_df(symbol, timeframe, settings)
        d = validate_dataset(symbol, timeframe, settings, df=df)
        datasets.append(d)
        print(f"  -> {len(d.trades)} trades, {len(d.setups)} setups")

    diset = DiagnosticsSet(datasets, settings)
    reporter = Phase51Reporter(diset, datasets)
    paths = reporter.write_all(dest)
    print(f"[phase5_1] baseline={PHASE5_BASELINE_VERSION} reports -> {dest}")
    for p in paths:
        print(f"  {p.name}")
    return paths


def parse_specs(args: Optional[list] = None) -> List[Tuple[str, str]]:
    ap = argparse.ArgumentParser(description="Phase 5.1 diagnosis (frozen baseline)")
    ap.add_argument("--symbol", action="append", help="symbol like BTC/USDT")
    ap.add_argument("--timeframe", action="append", help="timeframe like 1D")
    ap.add_argument("--dest", type=str, default=None, help="output dir")
    ns = ap.parse_args(args)
    if ns.symbol or ns.timeframe:
        symbols = ns.symbol or ["BTC/USDT", "ETH/USDT"]
        tfs = ns.timeframe or ["4H", "1D"]
        return [(s, t) for s in symbols for t in tfs]
    return DEFAULT_DATASETS_SPEC


def main(argv: Optional[list] = None) -> int:
    specs = parse_specs(argv)
    dest = REPORTS_DIR
    ap_args = argv or []
    if "--dest" in ap_args:
        dest = Path(ap_args[ap_args.index("--dest") + 1])
    run_datasets(specs, dest)
    return 0


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    raise SystemExit(main())