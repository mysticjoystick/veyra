"""Tests for the developer CLI argument parsing and dispatch."""

from __future__ import annotations

import pytest

from veyra.cli import _parser, main_with_service


def test_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        _parser().parse_args([])


@pytest.mark.parametrize("cmd", ["download", "validate", "inspect", "update", "backtest"])
def test_parser_accepts_each_subcommand(cmd):
    parse = _parser()
    # Provider flag is a global option, not per-subcommand.
    base = [cmd, "--symbol", "BTC/USDT", "--timeframe", "4H"]
    args = parse.parse_args(base)
    assert args.command == cmd
    assert args.symbol == "BTC/USDT"
    assert args.timeframe == "4H"


def test_parser_accepts_backtest_flags():
    args = _parser().parse_args(
        [
            "backtest", "--symbol", "BTC/USDT", "--timeframe", "4H",
            "--entry-fee", "0.001", "--slippage", "0.002", "--spread", "0.0005",
            "--ambiguous", "stop_first", "--overlap", "ALLOW_OVERLAP",
            "--persist", "--walk-forward", "--json",
        ]
    )
    assert args.command == "backtest"
    assert args.entry_fee == 0.001
    assert args.slippage == 0.002
    assert args.ambiguous == "stop_first"
    assert args.overlap == "ALLOW_OVERLAP"
    assert args.persist is True
    assert args.walk_forward is True
    assert args.json is True


class _FakeService:
    def __init__(self, capture):
        self.capture = capture

    def ingest(self, **kwargs):
        self.capture["ingest"] = kwargs
        from veyra.data.service import IngestResult

        return IngestResult(
            symbol=kwargs["symbol"], timeframe=kwargs["timeframe"], provider="fake"
        )

    def inspect(self, symbol, timeframe):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": 5,
            "start_time": 1_000_000,
            "end_time": 1_000_144,
            "gap_count": 0,
            "has_gaps": False,
            "issues": [],
            "provenance": "fake",
        }


def _factory(capture):
    def factory(args):
        return _FakeService(capture)

    return factory


def test_cli_download_runs_ingest(monkeypatch, capsys):
    capture = {}
    rc = main_with_service(
        ["download", "--symbol", "BTC/USDT", "--timeframe", "4H", "--start", "1000000"],
        _factory(capture),
    )
    assert rc == 0
    assert capture["ingest"]["symbol"] == "BTC/USDT"
    assert capture["ingest"]["incremental"] is True
    out = capsys.readouterr().out
    assert "BTC/USDT" in out


def test_cli_download_no_incremental_and_gap_flag(monkeypatch):
    capture = {}
    rc = main_with_service(
        [
            "download",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1D",
            "--no-incremental",
            "--require-no-gaps",
        ],
        _factory(capture),
    )
    assert rc == 0
    assert capture["ingest"]["incremental"] is False
    assert capture["ingest"]["require_no_gaps"] is True


def test_cli_inspect_reports(monkeypatch, capsys):
    rc = main_with_service(
        ["inspect", "--symbol", "BTC/USDT", "--timeframe", "4H"],
        _factory({}),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "count       : 5" in out


def test_parser_accepts_notify_subcommand():
    args = _parser().parse_args(["notify", "--min-change", "QUALIFIED", "--json"])
    assert args.command == "notify"
    assert args.min_change == "QUALIFIED"
    assert args.json is True
    assert args.send is False


def test_parser_accepts_subscription_subcommand():
    parser = _parser()
    args = parser.parse_args(["subscription", "--grant", "a@x.co", "--days", "30"])
    assert args.command == "subscription"
    assert args.grant == "a@x.co"
    assert args.days == 30
    assert args.revoke is None
    assert args.status is None