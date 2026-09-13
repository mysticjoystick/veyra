"""Veyra application configuration.

Configuration is loaded from environment variables (prefixed VEYRA_) and an
optional config file. This centralises all tunable values (timeframes,
indicator parameters, weight defaults) so that magic numbers do not leak
into the market-analysis code.

Rules:
- No magic numbers in analysis modules; pull them from Settings.
- Future strategy weights should be configurable here or per-strategy in a
  strategy registry, never hard-coded in the scoring implementation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py lives at <root>/src/veyra/config.py, so the project root is three
# parents up (src/veyra -> src -> root). This keeps data/, candles/, config/
# and manifests under the repository rather than landing one level above it.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VEYRA_",
        env_file=str(PROJECT_ROOT / "config" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Paths -----------------------------------------------------------
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///data/veyra.db"
    candle_store_dir: Path = Path("data/candles")

    # --- Core timeframes (swing trading focus) ---------------------------
    timeframes: List[str] = Field(default_factory=lambda: ["4H", "1D"])
    primary_timeframe: str = "4H"

    # --- Data pipeline ---------------------------------------------------
    # Expected interval (seconds) per timeframe, used for gap detection.
    # Extendable to new timeframes without rewriting the pipeline.
    timeframe_interval_seconds: dict[str, int] = Field(
        default_factory=lambda: {"3m": 180, "5m": 300, "15m": 900, "4H": 14400, "1D": 86400}
    )

    # --- Trend indicators ------------------------------------------------
    ema_fast: int = 50
    ema_slow: int = 200
    ema_slope_lookback: int = 3

    # --- Market structure ------------------------------------------------
    swing_lookback: int = 3
    structure_min_pivots: int = 3
    structure_use_close_for_break: bool = True

    # --- Momentum --------------------------------------------------------
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0

    # --- Volume ----------------------------------------------------------
    volume_ma_period: int = 20
    volume_expansion_ratio: float = 1.5
    volume_contraction_ratio: float = 0.7

    # --- Volatility ------------------------------------------------------
    atr_period: int = 14
    volatility_low_ratio: float = 0.6
    volatility_high_ratio: float = 1.4
    volatility_extreme_ratio: float = 2.0

    # --- Scoring weights (hypothesis only, must be validated) ------------
    weight_trend: float = 0.25
    weight_structure: float = 0.20
    weight_pullback: float = 0.15
    weight_momentum: float = 0.12
    weight_volume: float = 0.07
    weight_volatility: float = 0.05

    # --- Setup engine (hypothesis thresholds, validated later) -----------
    # Minimum trend strength (0-100) for trend-based setups.
    setup_min_trend_strength: float = 40.0
    # Maximum number of bars a live setup remains eligible before EXPIRING.
    setup_max_lifetime_bars: int = 24
    # Trend-continuation structure gate.
    setup_min_structure_score: int = 70
    # Pullback tolerance: must be retraced at least and at most this fraction.
    setup_pullback_min_retrace: float = 0.01
    setup_pullback_max_retrace: float = 0.40
    # Breakout clear distance / retest tolerance (fraction of the level).
    setup_breakout_distance_pct: float = 0.001
    setup_retest_tolerance_pct: float = 0.02
    setup_retest_max_bars: int = 20
    # Range-rejection boundary tolerance.
    setup_range_boundary_tolerance_pct: float = 0.015
    # Minimum overall score for a setup to be considered QUALIFIED-worthy.
    setup_min_qualify_score: int = 60

    # --- Paper-trading risk (Step 3 checkpoint engine) --------------------
    # Per-trade risk budget in simulated USD: each position is sized so its
    # stop-distance (max(structural, ATR*k)) risk to approximately this amount,
    # capped by the caller's `amount` too. Volatility-aware stops:
    paper_risk_per_trade: float = 10.0
    paper_atr_stop_multiplier: float = 2.0
    # Fallback stop when no structural/ATR level resolves (fraction of entry).
    paper_stop_fallback_pct: float = 0.02
    # Cold-start sizing scale by band observation count: 0-19 obs -> 25%,
    # 20-49 -> 50%, 50+ -> 100% of the per-trade risk budget.
    paper_cold_tier_thresholds: tuple = (20, 50)

    # --- Portfolio-level risk (checked before any entry executes) --------
    # Hard cap on total open notional across all markets as a fraction of
    # account equity; when approached, candidate size scales down.
    portfolio_max_exposure_pct: float = 0.20
    # Account equity used to derive the exposure cap (simulated USD).
    portfolio_equity: float = 10_000.0
    # Same-direction open position in a market correlated at >= this level
    # (trailing 30-day daily-close correlation) reduces candidate size.
    portfolio_correlation_threshold: float = 0.70
    portfolio_correlation_multiplier: float = 0.50

    # --- Execution-cost drift monitoring (Step-3 paper) --------------------
    # The net_return math assumes a fixed 0.30% round-trip. If realized cost
    # (from a real fill feed) exceeds that assumption by these margins for that
    # many consecutive weeks, the tracker raises a drift alert.
    cost_drift_alert_pct: float = 0.20
    cost_drift_alert_weeks: int = 2

    # --- Best Catch scanner (surfacing layer over the same setups) --------
    # How many top candidates the Best Catch card list returns, and the per-trade
    # amount cap used to ESTIMATE the dollar figures on the card. Best Catch
    # never relaxes the quality gate / portfolio caps, and never changes sizing:
    # the estimate uses the same risk-budget notional the paper engine would.
    best_catch_top_n: int = 3
    best_catch_amount: float = 1_000.0

    # --- Backtesting (Phase 4, configurable execution assumptions) --------
    # Fees & slippage are fractions of price (0.001 = 0.1%). Costs are never
    # assumed to be zero; these defaults are deliberately conservative.
    backtest_entry_fee_pct: float = 0.001
    backtest_exit_fee_pct: float = 0.001
    backtest_slippage_pct: float = 0.0
    backtest_spread_pct: float = 0.0
    # Entry fill semantics: always "next_open" (enter at the open of the bar
    # following the decision bar; unambiguous and look-ahead-safe).
    backtest_entry_policy: str = "next_open"
    # Conservative intra-bar ambiguity rule: when a candle touches both the stop
    # and the target (no intrabar path), resolve to the worst outcome for the
    # strategy ("stop_first").
    backtest_ambiguous_candle_policy: str = "stop_first"
    # Overlap policy: ALLOW_OVERLAP allows multiple concurrent positions;
    # ONE_POSITION_PER_SYMBOL blocks a new entry while a position is open.
    backtest_overlap_policy: str = "ALLOW_OVERLAP"
    # Maximum bars a position may be held before forced expiry close.
    backtest_position_max_bars: int = 60
    # Historical split percentages (must sum to <= 1): train / validation / test.
    backtest_split_train: float = 0.6
    backtest_split_validation: float = 0.2
    backtest_split_test: float = 0.2
    # Walk-forward window configuration (fraction of the dataset or explicit).
    walk_forward_train_bars: int = 500
    walk_forward_validation_bars: int = 200
    walk_forward_test_bars: int = 200
    walk_forward_step_bars: int = 200

    # --- Environment -----------------------------------------------------
    environment: Literal["dev", "test", "prod"] = "dev"

    # --- Notifications (Phase 7, SIMULATION-safe by default) -------------
    # Alerts are notices only. Nothing is ever sent unless dry_run=False AND a
    # bot token + chat id are provided. The default is dry-run: print/log the
    # message and never touch the network.
    telegram_dry_run: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Telegram delivery is a PAID feature: senders must hold an active premium
    # entitlement or the notifier refuses to transmit (gated, no network).
    # Set False to allow delivery to any account (never recommended).
    telegram_require_subscription: bool = True

    # --- Telegram Stars subscriptions (Phase 10 billing) ------------------
    # Subscribers pay in Telegram Stars (currency XTR) for a monthly premium.
    # `subscription_period` MUST be 2592000 (30 days) — the only supported value.
    stars_subscription_period: int = 30 * 24 * 60 * 60  # 2592000
    # Price of one premium month, in Stars. 10_000 is the API ceiling.
    stars_price_xtr: int = 500
    # Currency code; Stars uses XTR, hard-coded to avoid accidental fiat billing.
    stars_currency: str = "XTR"
    # Invoice payload prefix; encodes the tier so the bot can validate payments.
    stars_payload_prefix: str = "veyra:premium:"

    # --- Auth (Phase 8, local email+password) -----------------------------
    # Seeding creates a single admin account idempotently on first boot so the
    # operator can log in; general signup stays closed until launch. Set these
    # via env/config (`VEYRA_ADMIN_EMAIL` / `VEYRA_ADMIN_PASSWORD`).
    admin_email: str = ""
    admin_password: str = ""
    # Session lifetime (seconds). Overridable per environment.
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 7

    # --- Marketing / result-postback (optional, additive) -----------------
    # The public invite link appended to generated posts. Leave empty to omit.
    channel_invite_url: str = ""
    # When a live paper trade closes, draft a ready-to-post X (Twitter)
    # message and ping Telegram so the operator can paste it (free, zero API
    # cost, no bot-flags). Set False to disable.
    postback_x_enabled: bool = True
    # Send the same result message to the Telegram channel on each closed
    # trade. Uses the same bot token / chat id as the live alert runner.
    postback_telegram_enabled: bool = False
    # Auto-post the same result to Bluesky via its free API.
    # Needs a Bluesky handle/identifier + an app password (not the login pw).
    postback_bluesky_enabled: bool = False
    bluesky_handle: str = ""       # e.g. handle.bsky.social
    bluesky_app_password: str = "" # App Password, NOT the account password

    @property
    def absolute_data_dir(self) -> Path:
        p = self.data_dir
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def absolute_candle_store_dir(self) -> Path:
        p = self.candle_store_dir
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def absolute_database_path(self) -> Path:
        # sqlite relative URLs are relative to CWD; resolve to absolute.
        if self.database_url.startswith("sqlite:///"):
            raw = self.database_url[len("sqlite:///"):]
            p = Path(raw)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
            return p
        raise ValueError(
            "database_url must be a sqlite URL for absolute path resolution"
        )


def get_settings() -> Settings:
    return Settings()


def redact_secret(value: str, *, visible: int = 4) -> str:
    """Return a tamper-obvious, shortened form of a secret for logs.

    Never reveals more than the tail of the secret. Empty values show as
    ``<unset>``, placeholder-looking values as ``<placeholder>``.
    """
    value = value or ""
    if not value:
        return "<unset>"
    if not visible or visible < 0:
        visible = 0
    if value.strip().lower() in {"changeme", "change-me", "password", "secret", "xxx"}:
        return "<placeholder>"
    if len(value) <= visible:
        return "<redacted>"
    return f"...{value[-visible:]}"


SECRET_FIELDS = ("telegram_bot_token", "admin_password")


def redact_settings(settings: Settings) -> dict:
    """Return settings as a dict with all known secrets redacted."""
    out = dict(settings.model_dump())
    for field in SECRET_FIELDS:
        if field in out:
            out[field] = redact_secret(out[field] or "")
    return out


def validate_production(settings: Settings) -> None:
    """Raise ``RuntimeError`` in prod when required secrets are unset/weak.

    Production hardening: the operator must supply a real admin password and a
    real Telegram bot token (used for notify and Stars billing) or the process
    refuses to start, rather than silently running with defaults. Dev/test
    environments keep permissive defaults so the suite and local use stay easy.
    """
    if settings.environment != "prod":
        return
    problems: list[str] = []
    if not settings.admin_email:
        problems.append("VEYRA_ADMIN_EMAIL must be set")
    if not settings.admin_password or settings.admin_password.strip().lower() in {
        "changeme", "change-me", "password", "secret",
    }:
        problems.append("VEYRA_ADMIN_PASSWORD must be a real (non-placeholder) password")
    if not settings.telegram_bot_token or settings.telegram_bot_token.strip().lower() in {
        "changeme", "change-me", "token", "yyyytoken",
    }:
        problems.append("VEYRA_TELEGRAM_BOT_TOKEN must be a real bot token")
    if not settings.stars_price_xtr or settings.stars_price_xtr <= 0:
        problems.append("VEYRA_STARS_PRICE_XTR must be a positive number of Stars")
    if settings.telegram_require_subscription and not settings.telegram_chat_id:
        # Chat id is only needed for outbound notify; billing can still run.
        pass
    if problems:
        raise RuntimeError(
            "production configuration rejected: " + "; ".join(problems)
        )
