from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from typing import Dict, List

from dotenv import load_dotenv


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(value: str, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _parse_int(value: str, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _parse_hhmm(value: str, default: str) -> time:
    raw = value or default
    hour, minute = raw.split(":")
    return time(hour=int(hour), minute=int(minute))


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    enable_telegram: bool
    notifier_channel: str
    feishu_webhook_url: str

    watchlist: List[str]
    execution_timeframe: str
    trend_timeframe: str
    confirm_timeframes: List[str]

    rsi_recovery_threshold: float
    exit_rsi_threshold: float
    breakout_lookback_bars: int
    swing_lookback_bars: int
    volume_ratio_min: float
    resistance_buffer_pct: float
    max_extension_pct: float
    entry_zone_pct: float
    enable_atr_filter: bool
    atr_period: int
    atr_min_pct: float
    atr_max_pct: float
    enable_relative_strength_filter: bool
    relative_strength_benchmark: str
    relative_strength_lookback_bars: int
    relative_strength_min_excess_return: float
    enable_breakout_retest_filter: bool
    breakout_retest_tolerance_pct: float
    buy_rsi_ceiling: float
    breakout_rsi_ceiling: float
    min_buy_reward_risk: float
    min_trend_ma_gap_pct: float
    min_breakout_close_strength: float
    long_max_ma20_distance_pct: float

    alert_cooldown_minutes: int
    duplicate_price_tolerance_pct: float
    trading_hours_only: bool
    us_market_tz: str
    market_open_time: time
    market_close_time: time
    poll_interval_seconds: int

    sqlite_path: str
    log_level: str

    enable_llm_analysis: bool
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: int
    llm_signal_types: List[str]
    dry_run: bool
    enable_daily_report: bool
    daily_report_send_time: time
    daily_report_top_n: int
    alert_min_priority_tier: str
    enable_symbol_merge_alerts: bool
    symbol_merge_window_minutes: int
    enable_weekly_report: bool
    weekly_report_send_weekday: int
    weekly_report_send_time: time
    weekly_report_top_n: int

    @property
    def all_timeframes(self) -> List[str]:
        ordered = [self.execution_timeframe, self.trend_timeframe] + self.confirm_timeframes
        seen: Dict[str, bool] = {}
        out: List[str] = []
        for tf in ordered:
            if tf not in seen:
                seen[tf] = True
                out.append(tf)
        return out


def load_settings() -> Settings:
    load_dotenv()
    watchlist = [item.strip() for item in os.getenv("WATCHLIST", "").split(",") if item.strip()]
    if not watchlist:
        watchlist = [
            "US.SPY",
            "US.QQQ",
            "US.AAPL",
            "US.MSFT",
            "US.NVDA",
            "US.AMZN",
            "US.META",
            "US.TSLA",
        ]

    confirm_timeframes = [
        item.strip() for item in os.getenv("CONFIRM_TIMEFRAMES", "5m,1m").split(",") if item.strip()
    ]
    llm_signal_types = [
        item.strip() for item in os.getenv("LLM_SIGNAL_TYPES", "long_setup,breakout_candidate").split(",") if item.strip()
    ]
    alert_min_priority_tier = os.getenv("ALERT_MIN_PRIORITY_TIER", "C").strip().upper()
    if alert_min_priority_tier not in {"A", "B", "C"}:
        alert_min_priority_tier = "C"

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        enable_telegram=_parse_bool(os.getenv("ENABLE_TELEGRAM"), True),
        notifier_channel=os.getenv("NOTIFIER_CHANNEL", "telegram").strip().lower(),
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
        watchlist=watchlist,
        execution_timeframe=os.getenv("EXECUTION_TIMEFRAME", "15m"),
        trend_timeframe=os.getenv("TREND_TIMEFRAME", "1h"),
        confirm_timeframes=confirm_timeframes,
        rsi_recovery_threshold=_parse_float(os.getenv("RSI_RECOVERY_THRESHOLD"), 48.0),
        exit_rsi_threshold=_parse_float(os.getenv("EXIT_RSI_THRESHOLD"), 42.0),
        breakout_lookback_bars=_parse_int(os.getenv("BREAKOUT_LOOKBACK_BARS"), 20),
        swing_lookback_bars=_parse_int(os.getenv("SWING_LOOKBACK_BARS"), 20),
        volume_ratio_min=_parse_float(os.getenv("VOLUME_RATIO_MIN"), 1.2),
        resistance_buffer_pct=_parse_float(os.getenv("RESISTANCE_BUFFER_PCT"), 0.008),
        max_extension_pct=_parse_float(os.getenv("MAX_EXTENSION_PCT"), 0.03),
        entry_zone_pct=_parse_float(os.getenv("ENTRY_ZONE_PCT"), 0.004),
        enable_atr_filter=_parse_bool(os.getenv("ENABLE_ATR_FILTER"), True),
        atr_period=_parse_int(os.getenv("ATR_PERIOD"), 14),
        atr_min_pct=_parse_float(os.getenv("ATR_MIN_PCT"), 0.003),
        atr_max_pct=_parse_float(os.getenv("ATR_MAX_PCT"), 0.05),
        enable_relative_strength_filter=_parse_bool(os.getenv("ENABLE_RELATIVE_STRENGTH_FILTER"), True),
        relative_strength_benchmark=os.getenv("RELATIVE_STRENGTH_BENCHMARK", "US.SPY"),
        relative_strength_lookback_bars=_parse_int(os.getenv("RELATIVE_STRENGTH_LOOKBACK_BARS"), 20),
        relative_strength_min_excess_return=_parse_float(
            os.getenv("RELATIVE_STRENGTH_MIN_EXCESS_RETURN"), 0.0
        ),
        enable_breakout_retest_filter=_parse_bool(os.getenv("ENABLE_BREAKOUT_RETEST_FILTER"), True),
        breakout_retest_tolerance_pct=_parse_float(os.getenv("BREAKOUT_RETEST_TOLERANCE_PCT"), 0.004),
        buy_rsi_ceiling=_parse_float(os.getenv("BUY_RSI_CEILING"), 72.0),
        breakout_rsi_ceiling=_parse_float(os.getenv("BREAKOUT_RSI_CEILING"), 78.0),
        min_buy_reward_risk=_parse_float(os.getenv("MIN_BUY_REWARD_RISK"), 1.2),
        min_trend_ma_gap_pct=_parse_float(os.getenv("MIN_TREND_MA_GAP_PCT"), 0.001),
        min_breakout_close_strength=_parse_float(os.getenv("MIN_BREAKOUT_CLOSE_STRENGTH"), 0.55),
        long_max_ma20_distance_pct=_parse_float(os.getenv("LONG_MAX_MA20_DISTANCE_PCT"), 0.025),
        alert_cooldown_minutes=_parse_int(os.getenv("ALERT_COOLDOWN_MINUTES"), 30),
        duplicate_price_tolerance_pct=_parse_float(
            os.getenv("DUPLICATE_PRICE_TOLERANCE_PCT"), 0.003
        ),
        trading_hours_only=_parse_bool(os.getenv("TRADING_HOURS_ONLY"), True),
        us_market_tz=os.getenv("US_MARKET_TZ", "America/New_York"),
        market_open_time=_parse_hhmm(os.getenv("MARKET_OPEN_HHMM"), "09:30"),
        market_close_time=_parse_hhmm(os.getenv("MARKET_CLOSE_HHMM"), "16:00"),
        poll_interval_seconds=_parse_int(os.getenv("POLL_INTERVAL_SECONDS"), 60),
        sqlite_path=os.getenv("SQLITE_PATH", "data/signals.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        enable_llm_analysis=_parse_bool(os.getenv("ENABLE_LLM_ANALYSIS"), False),
        llm_provider=os.getenv("LLM_PROVIDER", "openai"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        llm_timeout_seconds=_parse_int(os.getenv("LLM_TIMEOUT_SECONDS"), 15),
        llm_signal_types=llm_signal_types,
        dry_run=_parse_bool(os.getenv("DRY_RUN"), False),
        enable_daily_report=_parse_bool(os.getenv("ENABLE_DAILY_REPORT"), True),
        daily_report_send_time=_parse_hhmm(os.getenv("DAILY_REPORT_SEND_HHMM"), "16:10"),
        daily_report_top_n=_parse_int(os.getenv("DAILY_REPORT_TOP_N"), 5),
        alert_min_priority_tier=alert_min_priority_tier,
        enable_symbol_merge_alerts=_parse_bool(os.getenv("ENABLE_SYMBOL_MERGE_ALERTS"), True),
        symbol_merge_window_minutes=_parse_int(os.getenv("SYMBOL_MERGE_WINDOW_MINUTES"), 20),
        enable_weekly_report=_parse_bool(os.getenv("ENABLE_WEEKLY_REPORT"), True),
        weekly_report_send_weekday=_parse_int(os.getenv("WEEKLY_REPORT_SEND_WEEKDAY"), 4),
        weekly_report_send_time=_parse_hhmm(os.getenv("WEEKLY_REPORT_SEND_HHMM"), "16:20"),
        weekly_report_top_n=_parse_int(os.getenv("WEEKLY_REPORT_TOP_N"), 8),
    )
