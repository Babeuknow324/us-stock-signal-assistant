from __future__ import annotations

import logging
from typing import Dict, List

import pandas as pd
from longbridge.openapi import AdjustType, Config, Period, QuoteContext, TradeSessions

from .config import Settings

LOGGER = logging.getLogger(__name__)

TIMEFRAME_TO_PERIOD: Dict[str, Period] = {
    "1m": Period.Min_1,
    "5m": Period.Min_5,
    "15m": Period.Min_15,
    "1h": Period.Min_60,
}


def normalize_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if "." not in raw:
        return raw
    left, right = raw.split(".", 1)
    if left in {"US", "HK", "CN", "SG"}:
        ticker = right
        market = left
    else:
        ticker = left
        market = right

    if market == "HK":
        ticker = str(int(ticker)) if ticker.isdigit() else ticker

    return f"{ticker}.{market}"


class LongbridgeMarketDataClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._config = Config.from_apikey_env()
        self._quote_ctx = QuoteContext(self._config)

    def subscribe_watchlist(self, symbols: List[str], timeframes: List[str]) -> None:
        normalized = [normalize_symbol(symbol) for symbol in symbols]
        LOGGER.info("Longbridge active symbols: %s", normalized)
        LOGGER.info("Longbridge active timeframes: %s", timeframes)

    def get_latest_price(self, symbol: str) -> float:
        code = normalize_symbol(symbol)
        quotes = self._quote_ctx.quote([code])
        if not quotes:
            raise RuntimeError(f"No quote returned for {code}")
        return float(quotes[0].last_done)

    def get_kline(self, symbol: str, timeframe: str, max_count: int = 300) -> pd.DataFrame:
        code = normalize_symbol(symbol)
        period = TIMEFRAME_TO_PERIOD.get(timeframe)
        if period is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        candles = self._quote_ctx.candlesticks(
            code, period, min(max_count, 1000), AdjustType.NoAdjust, TradeSessions.Intraday
        )
        if not candles:
            raise RuntimeError(f"No candlesticks returned for {code} {timeframe}")

        frame = pd.DataFrame(
            [
                {
                    "time": pd.to_datetime(item.timestamp),
                    "open": float(item.open),
                    "high": float(item.high),
                    "low": float(item.low),
                    "close": float(item.close),
                    "volume": float(item.volume),
                }
                for item in candles
            ]
        ).sort_values("time")
        frame = frame.reset_index(drop=True)
        return frame

    def close(self) -> None:
        try:
            close_fn = getattr(self._quote_ctx, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            LOGGER.exception("Error closing Longbridge quote context")
