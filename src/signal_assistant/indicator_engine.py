from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class IndicatorSnapshot:
    price: float
    ma20: float
    ma50: float
    prev_ma20: float
    rsi14: float
    prev_rsi14: float
    swing_high: float
    swing_low: float
    volume_ratio: float
    atr: float
    atr_pct: float


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    gain_ema = pd.Series(gain, index=close.index).ewm(alpha=1 / period, adjust=False).mean()
    loss_ema = pd.Series(loss, index=close.index).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain_ema / loss_ema.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def build_indicator_snapshot(data: pd.DataFrame, swing_lookback_bars: int, atr_period: int) -> IndicatorSnapshot:
    if len(data) < 60:
        raise ValueError("Not enough bars to compute indicators (need >= 60)")

    close = data["close"]
    volume = data["volume"]
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    rsi14 = _compute_rsi(close, period=14)
    vol_mean20 = volume.rolling(20).mean()
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (data["high"] - data["low"]).abs(),
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(atr_period).mean()

    if len(data) < swing_lookback_bars + 2:
        raise ValueError("Not enough bars for swing high/low")

    prev_window = data.iloc[-(swing_lookback_bars + 1) : -1]
    swing_high = float(prev_window["high"].max())
    swing_low = float(prev_window["low"].min())

    return IndicatorSnapshot(
        price=float(close.iloc[-1]),
        ma20=float(ma20.iloc[-1]),
        ma50=float(ma50.iloc[-1]),
        prev_ma20=float(ma20.iloc[-2]),
        rsi14=float(rsi14.iloc[-1]),
        prev_rsi14=float(rsi14.iloc[-2]),
        swing_high=swing_high,
        swing_low=swing_low,
        volume_ratio=float(volume.iloc[-1] / vol_mean20.iloc[-1]) if vol_mean20.iloc[-1] else 0.0,
        atr=float(atr.iloc[-1]),
        atr_pct=float(atr.iloc[-1] / close.iloc[-1]) if close.iloc[-1] else 0.0,
    )
