"""Veyra developer CLI for the market-data pipeline.

Commands:
    download   Fetch/update candles for a market & timeframe.
    validate   Validate a stored dataset and report gaps/issues.
    inspect    Print a structured summary of a stored dataset.
    update     Incrementally update an existing dataset.

The CLI wires the provider, candle store, normalizer, validator, and
dataset repository together. It is a developer workflow tool - the
production UI comes later.
"""

from __future__ import annotations

import argparse
import logging
import sys


def _timeframe_choices() -> list[str]:
    from .domain import Timeframe

    return Timeframe.supported()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veyra",
        description="Veyra market-data pipeline CLI",
    )
    parser.add_argument(
        "--provider",
        default="binance",
        help="Market data provider name (default: binance)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bk = sub.add_parser(
        "backup",
        help="Phase 10: create a WAL-safe SQLite backup of the Veyra database",
    )
    bk.add_argument(
        "--out", default=None,
        help="Destination path (default: data/backup/veyra-<timestamp>.db)",
    )
    bk.add_argument(
        "--json", action="store_true",
        help="Emit the backup result as JSON to stdout",
    )

    dl = sub.add_parser("download", help="Download and store candles")
    dl.add_argument("--symbol", required=True, help="Market symbol, e.g. BTC/USDT")
    dl.add_argument(
        "--timeframe",
        required=True,
        choices=_timeframe_choices(),
        help="Candle timeframe",
    )
    dl.add_argument("--start", type=int, help="Start epoch seconds (UTC)")
    dl.add_argument("--end", type=int, help="End epoch seconds (UTC)")
    dl.add_argument(
        "--no-incremental",
        action="store_true",
        help="Force a fresh (non-incremental) download",
    )
    dl.add_argument(
        "--require-no-gaps",
        action="store_true",
        help="Fail if gaps are detected",
    )

    va = sub.add_parser("validate", help="Validate a stored dataset")
    va.add_argument("--symbol", required=True)
    va.add_argument("--timeframe", required=True, choices=_timeframe_choices())

    ins = sub.add_parser("inspect", help="Inspect a stored dataset")
    ins.add_argument("--symbol", required=True)
    ins.add_argument("--timeframe", required=True, choices=_timeframe_choices())

    upd = sub.add_parser("update", help="Incrementally update a stored dataset")
    upd.add_argument("--symbol", required=True)
    upd.add_argument("--timeframe", required=True, choices=_timeframe_choices())
    upd.add_argument("--end", type=int, help="End epoch seconds (UTC)")

    bt = sub.add_parser(
        "backtest",
        help="Run a look-ahead-safe historical backtest on a stored dataset",
    )
    bt.add_argument("--symbol", required=True)
    bt.add_argument("--timeframe", required=True, choices=_timeframe_choices())
    bt.add_argument("--start", type=int, help="Start epoch seconds (UTC)")
    bt.add_argument("--end", type=int, help="End epoch seconds (UTC)")
    bt.add_argument("--run-key", default=None, help="Optional label for the run")
    bt.add_argument("--entry-fee", type=float, help="Entry fee fraction (default off)")
    bt.add_argument("--exit-fee", type=float, help="Exit fee fraction (default off)")
    bt.add_argument("--slippage", type=float, help="Slippage fraction")
    bt.add_argument("--spread", type=float, help="Spread fraction")
    bt.add_argument(
        "--ambiguous",
        choices=["stop_first", "target_first"],
        default="stop_first",
        help="Ambiguous-candle policy (default: stop_first, conservative)",
    )
    bt.add_argument(
        "--overlap",
        choices=["ALLOW_OVERLAP", "ONE_POSITION_PER_SYMBOL"],
        default="ALLOW_OVERLAP",
        help="Overlap policy for concurrent positions",
    )
    bt.add_argument(
        "--persist",
        action="store_true",
        help="Persist the run + trades to the research backtest tables",
    )
    bt.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward validation across the dataset",
    )
    bt.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of text",
    )

    # Phase 5 real-data validation & paper commands.
    vr = sub.add_parser(
        "validate-real",
        help="Phase 5: run frozen-baseline validation over real data",
    )
    vr.add_argument("--symbol", default=None, help="Restrict to a symbol")
    vr.add_argument("--timeframe", default=None, choices=_timeframe_choices(),
                    help="Restrict to a timeframe")

    wf = sub.add_parser(
        "walk-forward-real",
        help="Phase 5: walk-forward validation over a real dataset",
    )
    wf.add_argument("--symbol", required=True)
    wf.add_argument("--timeframe", required=True, choices=_timeframe_choices())

    pr = sub.add_parser(
        "paper-start",
        help="Phase 5: run a simulation-only paper session",
    )
    pr.add_argument("--symbol", required=True)
    pr.add_argument("--timeframe", required=True, choices=_timeframe_choices())
    pr.add_argument("--cash", type=float, default=100_000.0)
    pr.add_argument("--fraction", type=float, default=0.25)

    ps = sub.add_parser(
        "paper-report",
        help="Phase 5: render the latest paper session for a dataset",
    )
    ps.add_argument("--symbol", required=True)
    ps.add_argument("--timeframe", required=True, choices=_timeframe_choices())

    pl = sub.add_parser(
        "paper-live",
        help="Step 3: realtime paper checkpoint (simulation only, live prices)",
    )
    pl.add_argument("--symbol", required=True)
    pl.add_argument("--timeframe", required=True, choices=_timeframe_choices())
    pl.add_argument("--amount", type=float, default=1_000.0)
    pl.add_argument("--no-refresh", action="store_true", help="use the stored candle tail only")
    pl.add_argument("--report", action="store_true", help="just render the ledger, no live check")

    pla = sub.add_parser(
        "paper-live-all",
        help="Step 3: realtime paper checkpoint for ALL markets (scheduler-friendly)",
    )
    pla.add_argument("--amount", type=float, default=1_000.0)
    pla.add_argument("--no-refresh", action="store_true", help="use stored tails only")
    pla.add_argument("--loop", type=int, default=0,
                     help="repeat every N seconds until interrupted (0 = single pass)")
    pla.add_argument("--log", default=None,
                     help="append a one-line-per-pass summary to this file")

    pc = sub.add_parser(
        "paper-costs",
        help="Step-3 cost-drift report: realized vs assumed 0.3% round-trip",
    )
    pc.add_argument(
        "--trades", type=int, default=None,
        help="Only report entries since the last N closed trades",
    )

    bc = sub.add_parser(
        "best-catch",
        help="Best Catch: re-rank every live market's eligible setups by "
             "risk-adjusted edge (simulation only, never opens anything)",
    )
    bc.add_argument(
        "--top-n", type=int, default=None,
        help="Show the top N catches (default: settings.best_catch_top_n)",
    )
    bc.add_argument(
        "--amount", type=float, default=None,
        help="Reference trade size for the dollar estimates "
             "(default: settings.best_catch_amount)",
    )
    bc.add_argument(
        "--json", action="store_true",
        help="Emit the ranked cards as JSON to stdout",
    )
    bc.add_argument(
        "--no-refresh", action="store_true",
        help="Reuse the alerts disk cache instead of refreshing market data",
    )

    ar = sub.add_parser(
        "alert",
        help="Compute selective setup alerts over real data (simulation only)",
    )
    ar.add_argument(
        "--symbol",
        default=None,
        help="Restrict to a symbol (default: all datasets)",
    )
    ar.add_argument(
        "--timeframe",
        default=None,
        choices=_timeframe_choices(),
        help="Restrict to a timeframe (default: all)",
    )
    ar.add_argument(
        "--min-score", type=int, default=None,
        help="Override the alert selectivity score threshold",
    )
    ar.add_argument(
        "--json", action="store_true",
        help="Emit alert bundles as JSON to stdout",
    )
    ar.add_argument(
        "--force", action="store_true",
        help="Ignore the disk cache and recompute",
    )

    db = sub.add_parser(
        "dashboard",
        help="Launch the Veyra alert dashboard (FastAPI/uvicorn, simulation only)",
    )
    db.add_argument("--host", default="127.0.0.1", help="Bind host")
    db.add_argument("--port", type=int, default=8000, help="Bind port")
    db.add_argument("--reload", action="store_true", help="Enable uvicorn reload")

    op = sub.add_parser(
        "opportunity",
        help="Project 24h returns + P&L estimate from an alert's trade size (simulation only)",
    )
    op.add_argument("--symbol", default="BTC/USDT", help="Market symbol")
    op.add_argument("--timeframe", default="1D", choices=_timeframe_choices(), help="Timeframe")
    op.add_argument("--amount", type=float, default=None, help="Trade size to estimate P&L")
    op.add_argument("--json", action="store_true", help="Emit the projection as JSON")

    wf = sub.add_parser(
        "walkforward",
        help="Step-1 gate: does the 24h edge survive unseen (out-of-sample) data? (simulation only)",
    )
    wf.add_argument("--fraction", type=float, default=0.30, help="Fraction of history held out as unseen test (default 0.30)")
    wf.add_argument("--json", action="store_true", help="Emit the full report as JSON")

    nt = sub.add_parser(
        "notify",
        help="Phase 7: send selective setup alerts on state change (dry-run by default)",
    )
    nt.add_argument(
        "--symbol", default=None,
        help="Restrict to a symbol (default: all datasets)",
    )
    nt.add_argument(
        "--timeframe", default=None, choices=_timeframe_choices(),
        help="Restrict to a timeframe (default: all)",
    )
    nt.add_argument(
        "--min-change", default=None,
        help="Only notify for states at or beyond this lifecycle stage (e.g. QUALIFIED)",
    )
    nt.add_argument(
        "--json", action="store_true",
        help="Emit the notifications as JSON to stdout",
    )
    nt.add_argument(
        "--send", action="store_true",
        help="Allow a real Telegram send (requires a configured token+chat AND dry-run off)",
    )

    subsub = sub.add_parser(
        "subscription",
        help="Phase 9/10: manage subscription entitlements + Telegram Stars billing",
    )
    subsub.add_argument("--grant", metavar="EMAIL", default=None,
                        help="Mark an account as premium (paid) for N --days")
    subsub.add_argument("--revoke", metavar="EMAIL", default=None,
                        help="Downgrade an account to free")
    subsub.add_argument("--days", type=int, default=None,
                        help="Days until premium lapses (default: perpetual)")
    subsub.add_argument("--status", metavar="EMAIL", default=None,
                        help="Show entitlement status for an account")
    subsub.add_argument("--link", nargs=2, metavar=("TG_USER_ID", "EMAIL"), default=None,
                        help="Link a Telegram user_id to a Veyra account (Stars billing)")
    subsub.add_argument("--invoice-link", nargs="?", const=True, metavar="TITLE", default=None,
                        help="Create a monthly Telegram Stars invoice deep link")
    subsub.add_argument("--stars-poll", action="store_true",
                        help="Long-poll Telegram for Stars payments (email link, pre-checkout, successful_payment)")
    subsub.add_argument("--stars-reconcile", action="store_true",
                        help="Downgrade lapsed premium subscriptions past their expiry")
    subsub.add_argument("--once", action="store_true",
                        help="With --stars-poll: poll a single batch of updates then exit")

    return parser


def main(argv: list[str] | None = None) -> int:
    return main_with_service(argv, service_factory=None)


def main_with_service(argv, service_factory) -> int:
    """Entrypoint that optionally accepts a service factory for testing.

    service_factory(settings, args) -> MarketDataService
    """
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s: %(message)s"
    )
    _install_file_logging(args)
    try:
        return _dispatch(args, service_factory)
    except Exception as exc:  # noqa: BLE001 - CLI reports errors plainly
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1


def _install_file_logging(args) -> None:
    """Phase 10: persist durable logs to data/logs for every CLI run.

    Skipped for lightweight commands (download/inspect/validate) to keep the
    data pipeline fast, and never in tests (environment=test writes nothing).
    """
    cmd = getattr(args, "command", None)
    if cmd in {"download", "inspect", "validate", "update"}:
        return
    try:
        from .config import get_settings
        from .database.backup import configure_file_logging

        settings = get_settings()
        if settings.environment == "test":
            return
        configure_file_logging(settings)
    except Exception:  # noqa: BLE001 - file logging is best-effort
        pass


def _dispatch(args: argparse.Namespace, service_factory=None) -> int:
    if service_factory is not None:
        service = service_factory(args)
    else:
        from .config import get_settings
        from .database.dataset_repository import DatasetRepository
        from .database.engine import build_engine, init_db, make_session_factory
        from .data.candle_store import CandleStore
        from .data.provider import get_provider as _get_provider
        from .data.service import MarketDataService

        settings = get_settings()
        engine = build_engine(settings)
        init_db(engine)
        factory = make_session_factory(engine)
        provider = _get_provider(args.provider)
        store = CandleStore(settings)
        dataset = DatasetRepository(factory)
        service = MarketDataService(provider, store, dataset, settings=settings)

    if args.command == "download":
        result = service.ingest(
            symbol=args.symbol,
            timeframe=args.timeframe,
            start_time=args.start,
            end_time=args.end,
            incremental=not args.no_incremental,
            require_no_gaps=args.require_no_gaps,
        )
        _print_ingest(result)
        if result.error:
            return 1
        return 0

    if args.command == "validate":
        info = service.inspect(args.symbol, args.timeframe)
        ok = (info["count"] > 0) and not info["issues"]
        print(f"VALID" if ok else f"ISSUES FOUND ({len(info['issues'])})")
        print(f"  candles     : {info['count']}")
        print(f"  start       : {info['start_time']}")
        print(f"  end         : {info['end_time']}")
        print(f"  gaps        : {info['gap_count']}")
        for issue in info["issues"][:20]:
            print(f"  - {issue['type']}: {issue['message']}")
        return 0 if ok else 1

    if args.command == "inspect":
        info = service.inspect(args.symbol, args.timeframe)
        print(f"symbol      : {args.symbol}")
        print(f"timeframe   : {args.timeframe}")
        print(f"provider    : {info.get('provenance')}")
        print(f"count       : {info['count']}")
        print(f"start       : {info['start_time']}")
        print(f"end         : {info['end_time']}")
        print(f"gaps        : {info['gap_count']}")
        return 0

    if args.command == "update":
        result = service.ingest(
            symbol=args.symbol,
            timeframe=args.timeframe,
            end_time=args.end,
            incremental=True,
        )
        _print_ingest(result)
        return 0 if result.error is None else 1

    if args.command == "backtest":
        return _run_backtest(args)

    if args.command == "validate-real":
        return _run_validate_real(args)

    if args.command == "walk-forward-real":
        return _run_walk_forward_real(args)

    if args.command == "paper-start":
        return _run_paper_start(args)

    if args.command == "paper-report":
        return _run_paper_report(args)

    if args.command == "paper-live":
        return _run_paper_live(args)

    if args.command == "paper-live-all":
        return _run_paper_live_all(args)

    if args.command == "paper-costs":
        return _run_paper_costs(args)

    if args.command == "best-catch":
        return _run_best_catch(args)

    if args.command == "alert":
        return _run_alert(args)

    if args.command == "dashboard":
        return _run_dashboard(args)

    if args.command == "opportunity":
        return _run_opportunity(args)

    if args.command == "walkforward":
        return _run_walkforward(args)

    if args.command == "notify":
        return _run_notify(args)

    if args.command == "subscription":
        return _run_subscription(args)

    if args.command == "backup":
        return _run_backup(args)

    parser = _parser()  # pragma: no cover - unreachable with required subcommand
    parser.error(f"Unknown command {args.command!r}")
    return 2


def _run_backtest(args) -> int:
    from .backtest import (
        BacktestEngine,
        BacktestReporter,
        MetricsEngine,
        WalkForwardRunner,
    )
    from .config import get_settings
    from .data.candle_store import CandleStore
    from .database.backtest_repository import BacktestRepository
    from .database.engine import build_engine, init_db, make_session_factory

    settings = get_settings()
    df = CandleStore(settings).load(args.symbol, args.timeframe)
    if df is None or df.empty:
        print(f"No candles found for {args.symbol} {args.timeframe}.")
        return 1

    # Build an engine with the requested execution overrides.
    overrides = {}
    if args.entry_fee is not None:
        overrides["entry_fee_pct"] = args.entry_fee
    if args.exit_fee is not None:
        overrides["exit_fee_pct"] = args.exit_fee
    if args.slippage is not None:
        overrides["slippage_pct"] = args.slippage
    if args.spread is not None:
        overrides["spread_pct"] = args.spread
    overrides["ambiguous_candle_policy"] = args.ambiguous
    overrides["overlap_policy"] = args.overlap
    engine = BacktestEngine(settings, execution_overrides=overrides)

    if args.walk_forward:
        runner = WalkForwardRunner(settings=settings, engine=engine)
        wf_df = df.copy()
        if args.start is not None:
            wf_df = wf_df[wf_df["open_time"] >= args.start].reset_index(drop=True)
        if args.end is not None:
            wf_df = wf_df[wf_df["open_time"] <= args.end].reset_index(drop=True)
        if wf_df.empty:
            print(f"No candles in the requested range.")
            return 1
        wf = runner.run(args.symbol, args.timeframe, wf_df)
        for meta, m in zip(wf.window_meta, wf.windows):
            print(f"Window {meta['index']}: test={meta['test']} "
                  f"trades={meta['trades']} win_rate={m.trades.win_rate:.3f} "
                  f"expectancy={m.risk.expectancy:.4f}")
        print(f"{len(wf.windows)} walk-forward window(s) over {len(wf_df)} candles.")
        return 0

    result = engine.run(
        args.symbol, args.timeframe, df,
        start_time=args.start, end_time=args.end, run_key=args.run_key,
    )
    metrics = MetricsEngine().compute(result.trades, result.setups, result.run.candle_count)
    report = BacktestReporter().build(result, metrics)
    if args.json:
        print(report.to_json())
    else:
        print(report.render_text())

    if args.persist:
        eng = build_engine(settings)
        init_db(eng)
        factory = make_session_factory(eng)
        row = BacktestRepository(factory).save(result)
        print(f"Persisted run id={row.id} key={result.run.run_key} "
              f"({len(result.trades)} trades).")
    return 0


def _run_alert(args) -> int:
    from .alerts import AlertPolicy, AlertService

    policy = None
    if args.min_score is not None:
        policy = AlertPolicy(min_score=args.min_score)
    svc = AlertService(policy=policy)
    datasets = svc.datasets()
    if args.symbol:
        datasets = [d for d in datasets if d["symbol"] == args.symbol]
    if args.timeframe:
        datasets = [d for d in datasets if d["timeframe"] == args.timeframe]
    if not datasets:
        print("No datasets matched the filters.")
        return 1

    if args.symbol or args.timeframe:
        # Restrict the service to the requested datasets only.
        svc = AlertService(policy=policy, datasets=datasets)

    payload = svc.compute_all(force=args.force)
    if args.json:
        import json as _json

        print(_json.dumps(payload, indent=2))
        return 0

    print("Selective setup alerts (simulation only; no real execution)")
    print(f"  strategy : {svc.datasets() and 'frozen-baseline' or ''}")
    for ds in payload["datasets"]:
        sym = ds["symbol"]
        tf = ds["timeframe"]
        print(f"\n[{sym} {tf}] capable={ds.get('capable', 0)} "
              f"qualified={ds.get('qualified', 0)} alerts={ds.get('alert_count', 0)}")
        if ds.get("error"):
            print(f"  error: {ds['error']}")
            continue
        for a in ds.get("alerts", []):
            print(f"  {a['timestamp']} {a['level']:8s} {a['setup_type']:22s} "
                  f"{a['side']:5s} score={a['score_normalized']} {a['reasoning']}")
    if payload.get("last_error"):
        return 1
    return 0


def _run_dashboard(args) -> int:
    import socket

    import uvicorn

    from .config import get_settings, validate_production
    from .web.dashboard import dashboard_app

    # Friendly port pre-flight: the launcher (run_dashboard.cmd) already holds
    # the port, so instead of a bare uvicorn traceback, tell the user.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((args.host, args.port)) == 0:
            print(f"[veyra] port {args.port} is already in use on {args.host}.")
            print("[veyra] The dashboard may already be running (e.g. via run_dashboard.cmd).")
            print(f"[veyra] Open http://{args.host}:{args.port} in your browser, or pick "
                  f"--port to bind a different port.")
            return 1

    settings = get_settings()
    validate_production(settings)
    seeded = bool(settings.admin_email and settings.admin_password)
    print(f"Veyra dashboard (simulation only) -> http://{args.host}:{args.port}")
    print("Sign-in required. " + (
        "Seed an admin via VEYRA_ADMIN_EMAIL / VEYRA_ADMIN_PASSWORD."
        if not seeded else
        f"Admin seeded: {settings.admin_email} (set VEYRA_ADMIN_PASSWORD to change)."
    ))
    uvicorn.run(
        dashboard_app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _run_opportunity(args) -> int:
    from .alerts import AlertService

    svc = AlertService(datasets=[{"symbol": args.symbol, "timeframe": args.timeframe}])
    result = svc.project(args.symbol, args.timeframe, amount=args.amount)

    if args.json:
        import json as _json

        print(_json.dumps(result, indent=2))
        return 0

    projections = result.get("projections", [])
    print(f"24h opportunity projection (simulation only; no real execution)")
    print(f"  market      : {args.symbol} {args.timeframe}")
    print(f"  trade size  : ${args.amount:,.2f}" if args.amount else "  trade size  : (none given)")
    from collections import defaultdict

    agg = defaultdict(lambda: {"n": 0, "pnl": []})
    for p in projections:
        a = agg[p["band"]]
        a["n"] += 1
        if p.get("pnl_at_amount") is not None:
            a["pnl"].append(p["pnl_at_amount"])
    if not projections:
        print("  no projections available (run `veyra alert` to pre-warm the cache)")
        return 1
    for band, a in agg.items():
        mean_pnl = (sum(a["pnl"]) / len(a["pnl"])) if a["pnl"] else None
        mean_pnl_s = f"${mean_pnl:+,.2f}" if mean_pnl is not None else "n/a"
        print(f"  {band:12s} n={a['n']:4d}  mean est. P&L @ given size: {mean_pnl_s}")
    return 0


def _run_walkforward(args) -> int:
    from .alerts import AlertService

    svc = AlertService()
    result = svc.walkforward(fraction=args.fraction)

    if args.json:
        import json as _json

        print(_json.dumps(result, indent=2))
        return 0

    print("Walk-forward out-of-sample gate (simulation only)")
    print(f"  split      : train first {int((1 - args.fraction) * 100)}% / test last {int(args.fraction * 100)}% (unseen)")
    costs = result.get("costs", {})
    if result.get("net_of_costs"):
        print(f"  returns    : NET of {costs.get('round_trip', 0) * 100:.2f}% round-trip fees+slippage ({costs.get('fee_per_side', 0)*100:.2f}%+{costs.get('slippage_per_side', 0)*100:.2f}%/side·2)")
    gate = result.get("gate", "INSUFFICIENT")
    print(f"  overall    : [{gate}]  {result.get('gate_reason', '')}")
    print()
    for m in result.get("markets", []):
        print(f"  {m['symbol']} {m['timeframe']}  train={m['n_train']} test={m['n_test']}  -> [{m['gate']}]")
        for b in m.get("bands", []):
            t = b.get("test", {})
            tr = b.get("train", {})
            t_wr = f"{t.get('win_rate', 0) * 100:.0f}%" if t.get("n") else "n/a"
            tr_wr = f"{tr.get('win_rate', 0) * 100:.0f}%" if tr.get("n") else "n/a"
            t_mean = f"{t.get('mean_return', 0) * 100:+.2f}%" if t.get("n") else "n/a"
            print(f"    {b['band']:12s} [{b['gate']}]  train wr {tr_wr:>5} / test wr {t_wr:>5}  test 24h {t_mean}  ({b.get('note', '')})")
    return 0


def _run_backup(args) -> int:
    from pathlib import Path

    from .config import get_settings
    from .database.backup import backup_database

    settings = get_settings()
    try:
        dest = backup_database(settings, Path(args.out) if args.out else None)
    except FileNotFoundError as exc:
        print(f"error: {exc}")
        return 1
    import json as _j

    result = {"backup": str(dest), "ok": True, "bytes": dest.stat().st_size}
    if args.json:
        print(_j.dumps(result, indent=2))
    else:
        print(f"backup written: {result['backup']} ({result['bytes']} bytes)")
    return 0


def _run_notify(args) -> int:
    from .alerts.live import LiveScan
    from .alerts.service import DEFAULT_DATASETS
    from .config import get_settings
    from .notify import NotifyClerk, TelegramNotifier, render_message

    datasets = DEFAULT_DATASETS
    if args.symbol:
        datasets = [d for d in datasets if d["symbol"] == args.symbol]
    if args.timeframe:
        datasets = [d for d in datasets if d["timeframe"] == args.timeframe]
    if not datasets:
        print("No datasets matched the filters.")
        return 1

    # The notifier watches LIVE setups (state-changing), not historical replay
    # backfill. Nothing is sent unless a live setup actually changes state.
    scan = LiveScan()
    live_markets = scan.live_all(datasets, refresh=False)

    clerk = NotifyClerk(min_change=args.min_change)
    rows = clerk.scan_live(live_markets)

    settings = get_settings()
    allow_send = bool(args.send) and not settings.telegram_dry_run

    # Phase 9: Telegram delivery is a paid entitlement. The operator account
    # must hold an active premium plan; otherwise sends are gated (no network).
    entitled = True
    if settings.telegram_require_subscription:
        entitled = _resolve_operator_entitlement(settings)

    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token or "",
        chat_id=settings.telegram_chat_id or "",
        dry_run=not allow_send,
        require_entitlement=settings.telegram_require_subscription,
        entitled=entitled,
    )

    if args.json:
        import json as _json

        out = {
            "dry_run": notifier._dry_run,
            "configured": notifier.configured,
            "gated": notifier.gated,
            "count": len(rows),
            "live_setups": sum(len(m.get("setups") or []) for m in live_markets),
            "notifications": rows,
            "note": "simulation notice only - never an order",
        }
        print(_json.dumps(out, indent=2))
        return 0

    print(f"Notifications (dry-run={notifier._dry_run}, configured={notifier.configured})")
    print(f"  markets scanned : {len(live_markets)}")
    print(f"  live setups     : {sum(len(m.get('setups') or []) for m in live_markets)}")
    print(f"  state changes   : {len(rows)}")
    if notifier.gated:
        print("  delivery        : BLOCKED (telegram requires a paid subscription)")
    print("  simulation notice only - never an order")
    for r in rows:
        msg = render_message(r)
        if notifier.gated:
            print(f"  [gated] {msg}")
        elif notifier.configured:
            result = notifier.send(msg)
            print(f"  [sent={result['sent']}] {msg}")
        else:
            notifier.send(msg)
            print(f"  [dry] {msg}")
    if not rows:
        print("  (no live setup state change since the last run - nothing to send)")
    return 0


def _run_subscription(args) -> int:
    from .config import get_settings, validate_production
    from .database.engine import build_engine, init_db, make_session_factory
    from .subscribe import EntitlementError, SubscriptionService

    settings = get_settings()

    if args.stars_poll or args.invoice_link or args.stars_reconcile:
        validate_production(settings)

    engine = build_engine(settings)
    init_db(engine)
    factory = make_session_factory(engine)
    svc = SubscriptionService(factory)

    if args.stars_poll:
        return _run_stars_poll(settings, svc, once=args.once)

    if args.invoice_link:
        return _run_stars_invoice(settings, svc)

    if args.stars_reconcile:
        return _run_stars_reconcile(svc)

    if args.link is not None:
        tg_id, email = args.link
        try:
            user = svc.link_telegram(int(tg_id), email)
        except (EntitlementError, ValueError) as exc:
            print(f"error: {exc}")
            return 1
        print(f"linked telegram {tg_id} -> {user.email}")
        return 0

    if args.grant:
        try:
            user = svc.grant_premium(args.grant, days=args.days)
        except EntitlementError as exc:
            print(f"error: {exc}")
            return 1
        print(f"granted premium -> {user.email} "
              f"(expires {user.subscription_expires_at or 'never'})")
        return 0

    if args.revoke:
        try:
            user = svc.revoke_premium(args.revoke)
        except EntitlementError as exc:
            print(f"error: {exc}")
            return 1
        print(f"revoked premium -> {user.email} (plan now {user.plan})")
        return 0

    email = args.status
    if not email:
        email = settings.admin_email
    if not email:
        print("Provide --status EMAIL (or set VEYRA_ADMIN_EMAIL).")
        return 1
    user = svc.find_by_email(email)
    st = svc.status(user.id) if user else {
        "authenticated": False, "plan": None, "premium": False, "note": "no such account",
    }
    import json as _j

    print(_j.dumps(st, indent=2))
    return 0


def _stars_service(settings, svc):
    from .notify import StarsBiller

    return StarsBiller(
        settings.telegram_bot_token,
        lambda: svc,
        price_xtr=settings.stars_price_xtr,
        period_seconds=settings.stars_subscription_period,
        payload_prefix=settings.stars_payload_prefix,
    )


def _run_stars_invoice(settings, svc) -> int:
    biller = _stars_service(settings, svc)
    try:
        link = biller.create_monthly_link()
    except Exception as exc:  # noqa: BLE001
        print(f"error creating invoice link: {exc}")
        return 1
    print("Telegram Stars monthly premium invoice link:")
    print(link)
    return 0


def _run_stars_poll(settings, svc, once: bool = False) -> int:
    biller = _stars_service(settings, svc)
    print("Long-polling Telegram for Stars payments... (Ctrl-C to stop)")
    if once:
        import json as _json

        summary = biller.poll_once(timeout=30)
        print(_json.dumps(summary, indent=2, default=str))
        return 0
    try:
        biller.poll_forever(interval=30)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def _run_stars_reconcile(svc) -> int:
    """Downgrade lapsed premium subs (a missed Stars renewal means no payment)."""
    count = svc.revoke_lapsed()
    print(f"reconciled: downgraded {count} lapsed premium subscription(s); "
          f"{svc.count_premium()} still premium")
    return 0


def _resolve_operator_entitlement(settings) -> bool:
    """Resolve whether the operator account may receive paid Telegram alerts.

    Telegram is a paid feature (Phase 9). Until billing is wired (Phase 10),
    the operator is the intended receiver, so this returns True when:
      * an admin account is provisioned (admin is always premium), or
      * the account is a manually-granted premium subscriber.

    With no account provisioned yet, returns False (honest: nothing sends).
    """
    from .database.engine import build_engine, init_db, make_session_factory
    from .subscribe import SubscriptionService

    if not settings.admin_email:
        return False
    engine = build_engine(settings)
    init_db(engine)
    svc = SubscriptionService(make_session_factory(engine))
    user = svc.find_by_email(settings.admin_email)
    return svc.is_active_premium(user)


def _run_paper_report(args) -> int:
    from pathlib import Path
    from .paper import PaperEngine

    session = PaperEngine().run(args.symbol, args.timeframe)
    print(f"Paper session: {args.symbol} {args.timeframe} "
          f"(simulation only)")
    print(f"  strategy      : {session.strategy_version}")
    print(f"  start cash    : {session.start_cash:,.2f}")
    print(f"  end equity    : {session.cash:,.2f}")
    ret = (session.cash / session.start_cash - 1.0) * 100.0
    print(f"  compound ret  : {ret:.1f}%")
    print(f"  trades        : {len(session.trades)}")
    for t in session.trades[:20]:
        print(f"    {t.entry_ts} {t.setup_type:16s} {t.side:5s} "
              f"net={t.net_return:+.4f} pnl={t.pnl:+,.2f}")
    if len(session.trades) > 20:
        print(f"    ... and {len(session.trades) - 20} more")
    return 0


def _run_paper_start(args) -> int:
    from .paper import PaperEngine

    session = PaperEngine().run(
        args.symbol, args.timeframe,
        start_cash=args.cash, position_fraction=args.fraction,
    )
    print(f"Paper session complete (simulation only; no real execution).")
    return 0


def _run_paper_live(args) -> int:
    from .paper.live import LivePaperEngine

    engine = LivePaperEngine()
    if args.report:
        r = engine.report(args.symbol, args.timeframe)
        _print_live_paper_report(r)
        return 0

    ledger = engine.run_checkpoint(
        args.symbol, args.timeframe, amount=args.amount, refresh=not args.no_refresh
    )
    r = engine.report(args.symbol, args.timeframe)
    print(f"Step 3 realtime paper checkpoint (SIMULATION ONLY — no real orders, no money)")
    print(f"  {args.symbol} {args.timeframe}  check=#{ledger.check_ct}")
    print(f"  start cash   : {ledger.start_cash:,.2f}")
    print(f"  account value: {ledger.cash:,.2f}")
    print(f"  open         : {len(r['open'])}  closed/trades: {len(r['closed'])}")
    for p in r["open"]:
        mark = f"  Mark ${'' if p['mark_price'] is None else round(p['mark_price'] or 0, 2)}  unrealized {p['unrealized_pnl']}"
        stop = f"  stop {p['stop_price']}" if p.get('stop_price') else ""
        print(f"    OPEN {p['side']:5s} {p['band']:11s} entry={p['entry_price']}  proj 24h {p['projected_return']}{stop}{mark}")
    for t in r["closed"][-10:]:
        print(f"    DONE {t['side']:5s} {t['band']:11s} net={t['net_return']}  pnl={t['pnl']}  ({t['exit_reason']})")
    _print_live_paper_report(r)
    return 0


def _print_live_paper_report(r: dict) -> None:
    print(f"  live paper ledger (simulation only)")
    print(f"    strategy : {r['strategy_version']}")
    print(f"    trades   : {r['trades']}  wins {r['wins']}  win-rate {r['win_rate']}")
    print(f"    realized PnL (net)     : {r['sum_pnl']}  vs projected {r['sum_projected']}")


def _run_best_catch(args) -> int:
    import json as _json

    from .market.best_catch import BestCatchScanner

    scanner = BestCatchScanner(top_n=args.top_n, amount=args.amount)
    result = scanner.scan(refresh=not args.no_refresh)
    if args.json:
        print(_json.dumps(result.to_dict(), indent=2))
    elif result.empty:
        print(f"Best Catch: {result.note}  (scanned {result.scanned_markets} markets)")
    else:
        print(f"Best Catch: {result.note}  (SIMULATION ONLY)")
        print(f"  scanned markets : {result.scanned_markets}")
        for i, c in enumerate(result.cards, 1):
            print(f"  #{i} {c.market:<10s} {c.timeframe:<4s} {c.band:<11s} "
                  f"{c.direction:<5s} wr={c.posterior_win_rate:.3f} "
                  f"n={c.n_observed_at_decision_time:3d} "
                  f"ev/risk={c.ev_per_risk:.3f} "
                  f"est_make=${c.est_make_usd:7.0f} "
                  f"risk=${-c.est_risk_usd:5.0f}")
    return 0


def _run_paper_costs(args) -> int:
    from pathlib import Path

    from .config import get_settings
    from .costs.live_cost_tracker import LiveCostTracker

    settings = get_settings()
    tracker = LiveCostTracker(
        Path(settings.absolute_data_dir) / "paper_live" / "cost_tracker.jsonl",
        alert_pct=float(settings.cost_drift_alert_pct or 0.20),
        alert_weeks=int(settings.cost_drift_alert_weeks or 2),
    )
    rows = None
    if args.trades is not None:
        rows = tracker._rows()[-int(args.trades):]
    report = tracker.report(rows=rows)
    print("Step 3 cost-drift report (realized vs assumed execution cost)")
    print(f"  assumed round-trip : {report['assumed_round_trip']:.4f} "
          f"({report['assumed_round_trip'] * 100:.2f}%)")
    print(f"  closed trades      : {report['n_trades']}")
    avg = report["avg_realized_cost"]
    print(f"  avg realized cost  : "
          f"{'n/a' if avg is None else f'{avg:.6f} ({avg * 100:.3f}%)'}")
    drift = report["avg_fill_slippage"]
    print(f"  avg fill slippage  : "
          f"{'n/a' if drift is None else f'{drift:+.6f}'}")
    for wk in report["weeks"]:
        pct = wk["avg_realized_cost"] * 100
        print(f"    {wk['week']}  trades={wk['trades']:3d}  avg cost={pct:.3f}%")
    alert = report["alert"]
    if alert:
        print(f"  ALERT [{alert['level']}]: {alert['message']}")
        return 1
    print("  no drift alert - the 0.30% round-trip assumption still holds")
    return 0


def _run_paper_live_all(args) -> int:
    import time as _time

    from .paper.live import LivePaperEngine

    engine = LivePaperEngine()
    refresh = not args.no_refresh
    first = True
    try:
        while True:
            if not first:
                print(f"\n--- checkpoint pass @ {_time.strftime('%H:%M:%S')} ---")
            first = False
            agg = engine.run_all(amount=args.amount, refresh=refresh)
            print(f"Step 3 all-markets paper checkpoint (SIMULATION ONLY)")
            print(f"  trades={agg['trades']} wins={agg['wins']} "
                  f"win-rate={agg['win_rate']} open={agg['open_positions']}")
            print(f"  realized PnL (net): {agg['sum_pnl']}  vs projected {agg['sum_projected']}")
            for m in agg["markets"]:
                if m.get("error"):
                    print(f"    {m['symbol']} {m['timeframe']}: ERROR {m['error']}")
                else:
                    print(f"    {m['symbol']} {m['timeframe']}: check #{m.get('check_ct')} "
                          f"open={len(m.get('open') or [])} closed={len(m.get('closed') or [])}")
            if args.log:
                _append_paperlog(args.log, agg)
                print(f"  -> appended {args.log}")
            if args.loop <= 0:
                break
            _time.sleep(args.loop)
    except KeyboardInterrupt:
        print("\nStopping paper-live-all loop.")
    return 0


def _append_paperlog(path, agg) -> None:
    """Append one CSV-ish line per pass so the projected-vs-realized trend is visible."""
    import os

    from .paper.live import fmt_log

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(fmt_log(agg) + "\n")


def _run_walk_forward_real(args) -> int:
    from .backtest import WalkForwardRunner
    from .config import get_settings
    from .data.candle_store import CandleStore
    from .phase5.frozen_config import build_baseline_engine

    settings = get_settings()
    df = CandleStore(settings).load(args.symbol, args.timeframe)
    if df is None or df.empty:
        print(f"No candles for {args.symbol} {args.timeframe}.")
        return 1
    engine = build_baseline_engine(settings, progressive=True)
    runner = WalkForwardRunner(settings=settings, engine=engine)
    wf = runner.run(args.symbol, args.timeframe, df.copy())
    print(f"Walk-forward: {args.symbol} {args.timeframe} "
          f"({len(wf.windows)} windows)")
    agg_wr = []
    for meta, m in zip(wf.window_meta, wf.windows):
        wr = m.trades.win_rate
        agg_wr.append(wr or 0.0)
        print(f"  w{meta['index']}: trades={meta['trades']} "
              f"win_rate={wr:.3f} expectancy={m.risk.expectancy:.4f}")
    if agg_wr:
        print(f"  mean window win_rate: {sum(agg_wr)/len(agg_wr):.3f}")
    return 0


def _run_validate_real(args) -> int:
    from .phase5 import Phase5Workflow

    datasets = Phase5Workflow().datasets
    if args.symbol:
        datasets = [d for d in datasets if d["symbol"] == args.symbol]
    if args.timeframe:
        datasets = [d for d in datasets if d["timeframe"] == args.timeframe]
    if not datasets:
        print("No datasets matched.")
        return 1
    outs = Phase5Workflow().run_all(datasets)
    print(f"\nReports written to reports/phase5/")
    for o in outs:
        print(o.brief())
    return 0


def _print_ingest(result) -> None:
    print(f"symbol        : {result.symbol}")
    print(f"timeframe     : {result.timeframe}")
    print(f"provider      : {result.provider}")
    print(f"fetched       : {result.fetched_count}")
    print(f"stored total  : {result.stored_count}")
    print(f"new this run  : {result.new_count}")
    print(f"range         : {result.range_start} .. {result.range_end}")
    if result.validation is not None:
        print(f"issues        : {result.validation.n_invalid_rows}")
        print(f"gaps detected : {result.gaps_detected}")
    if result.error:
        print(f"ERROR         : {result.error}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())