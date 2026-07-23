from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from .config import Settings
from .longbridge_client import LongbridgeMarketDataClient
from .signal_engine import SignalEngine


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    return_pct: float
    hold_bars: int
    exit_reason: str
    entry_signal_type: str


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    start_time: datetime
    end_time: datetime
    bars_tested: int
    trade_count: int
    win_rate_pct: float
    total_return_pct: float
    avg_trade_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: Optional[float]
    trades: list[TradeRecord]


def _slice_until(frame: pd.DataFrame, ts: datetime) -> pd.DataFrame:
    return frame[frame["time"] <= ts].copy()


def _annualization_factor(execution_timeframe: str) -> float:
    bars_per_day_map = {
        "1m": 390.0,
        "5m": 78.0,
        "15m": 26.0,
        "1h": 6.5,
        "4h": 1.625,
    }
    bars_per_day = bars_per_day_map.get(execution_timeframe, 26.0)
    return bars_per_day * 252.0


def _compute_sharpe(period_returns: list[float], execution_timeframe: str) -> Optional[float]:
    if len(period_returns) < 2:
        return None
    series = pd.Series(period_returns, dtype="float64")
    std = float(series.std(ddof=1))
    if std <= 0:
        return None
    mean = float(series.mean())
    return (mean / std) * (_annualization_factor(execution_timeframe) ** 0.5)


def run_backtest_for_symbol(
    symbol: str,
    settings: Settings,
    max_count: int = 800,
    max_hold_bars: int = 24,
) -> BacktestResult:
    client = LongbridgeMarketDataClient(settings)
    engine = SignalEngine(settings)
    try:
        bars_by_tf = {
            tf: client.get_kline(symbol, tf, max_count=max_count).sort_values("time").reset_index(drop=True)
            for tf in settings.all_timeframes
        }
        benchmark_by_tf: Optional[dict[str, pd.DataFrame]] = None
        if settings.enable_relative_strength_filter:
            benchmark_by_tf = {
                tf: client.get_kline(settings.relative_strength_benchmark, tf, max_count=max_count)
                .sort_values("time")
                .reset_index(drop=True)
                for tf in settings.all_timeframes
            }
    finally:
        client.close()

    exec_tf = settings.execution_timeframe
    exec_bars = bars_by_tf[exec_tf]
    if len(exec_bars) < 80:
        raise RuntimeError(f"{symbol} execution timeframe bars too short for backtest")

    trades: list[TradeRecord] = []
    period_returns: list[float] = []
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0

    in_position = False
    entry_price = 0.0
    entry_time: Optional[datetime] = None
    stop_price = 0.0
    target_price = 0.0
    entry_signal_type = ""
    entry_idx = -1

    for i in range(len(exec_bars)):
        now_bar = exec_bars.iloc[i]
        now_ts = pd.to_datetime(now_bar["time"]).to_pydatetime()

        sliced_by_tf: dict[str, pd.DataFrame] = {}
        can_evaluate = True
        for tf, frame in bars_by_tf.items():
            part = _slice_until(frame, now_ts)
            if len(part) < 60:
                can_evaluate = False
                break
            sliced_by_tf[tf] = part
        if not can_evaluate:
            continue

        benchmark_slices = None
        if benchmark_by_tf is not None:
            benchmark_slices = {}
            for tf, frame in benchmark_by_tf.items():
                part = _slice_until(frame, now_ts)
                if len(part) < 60:
                    can_evaluate = False
                    break
                benchmark_slices[tf] = part
        if not can_evaluate:
            continue

        signal = engine.evaluate(symbol, sliced_by_tf, benchmark_slices)

        high = float(now_bar["high"])
        low = float(now_bar["low"])
        close = float(now_bar["close"])
        this_bar_return = 0.0

        if in_position:
            exit_reason = None
            exit_price = None

            if low <= stop_price:
                exit_reason = "stop_loss"
                exit_price = stop_price
            elif high >= target_price:
                exit_reason = "take_profit"
                exit_price = target_price
            elif signal is not None and signal.signal_type == "exit_warning":
                exit_reason = "exit_warning"
                exit_price = close
            elif (i - entry_idx) >= max_hold_bars:
                exit_reason = "time_stop"
                exit_price = close

            if exit_reason is not None and exit_price is not None and entry_time is not None:
                ret = (exit_price / entry_price) - 1.0
                this_bar_return = ret
                equity *= 1.0 + ret
                peak = max(peak, equity)
                if peak > 0:
                    max_drawdown = min(max_drawdown, (equity - peak) / peak)
                trades.append(
                    TradeRecord(
                        symbol=symbol,
                        entry_time=entry_time,
                        exit_time=now_ts,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        return_pct=ret * 100.0,
                        hold_bars=i - entry_idx,
                        exit_reason=exit_reason,
                        entry_signal_type=entry_signal_type,
                    )
                )
                in_position = False

        if not in_position and signal is not None and signal.signal_type in {"long_setup", "breakout_candidate"}:
            entry_price = close
            entry_time = now_ts
            stop_price = float(signal.invalidation)
            target_price = float(signal.resistance)
            if target_price <= entry_price:
                target_price = entry_price * (1.0 + settings.max_extension_pct)
            if stop_price >= entry_price:
                stop_price = entry_price * (1.0 - settings.entry_zone_pct)
            in_position = True
            entry_signal_type = signal.signal_type
            entry_idx = i

        period_returns.append(this_bar_return)

    if in_position and entry_time is not None:
        final_close = float(exec_bars.iloc[-1]["close"])
        final_ts = pd.to_datetime(exec_bars.iloc[-1]["time"]).to_pydatetime()
        ret = (final_close / entry_price) - 1.0
        period_returns.append(ret)
        equity *= 1.0 + ret
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, (equity - peak) / peak)
        trades.append(
            TradeRecord(
                symbol=symbol,
                entry_time=entry_time,
                exit_time=final_ts,
                entry_price=entry_price,
                exit_price=final_close,
                return_pct=ret * 100.0,
                hold_bars=len(exec_bars) - 1 - entry_idx,
                exit_reason="end_of_test",
                entry_signal_type=entry_signal_type,
            )
        )

    wins = [t for t in trades if t.return_pct > 0]
    avg_return = sum(t.return_pct for t in trades) / len(trades) if trades else 0.0
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    total_return = (equity - 1.0) * 100.0
    sharpe = _compute_sharpe(period_returns, settings.execution_timeframe)
    tested_start = pd.to_datetime(exec_bars.iloc[0]["time"]).to_pydatetime()
    tested_end = pd.to_datetime(exec_bars.iloc[-1]["time"]).to_pydatetime()

    return BacktestResult(
        symbol=symbol,
        start_time=tested_start,
        end_time=tested_end,
        bars_tested=len(exec_bars),
        trade_count=len(trades),
        win_rate_pct=win_rate,
        total_return_pct=total_return,
        avg_trade_return_pct=avg_return,
        max_drawdown_pct=max_drawdown * 100.0,
        sharpe_ratio=sharpe,
        trades=trades,
    )


def format_backtest_report(result: BacktestResult) -> str:
    sharpe_text = f"{result.sharpe_ratio:.3f}" if result.sharpe_ratio is not None else "N/A"
    lines = [
        f"回测结果 | {result.symbol}",
        f"区间: {result.start_time} -> {result.end_time}",
        f"样本K线: {result.bars_tested}",
        f"交易次数: {result.trade_count}",
        f"胜率: {result.win_rate_pct:.2f}%",
        f"总收益: {result.total_return_pct:.2f}%",
        f"平均单笔: {result.avg_trade_return_pct:.2f}%",
        f"最大回撤: {result.max_drawdown_pct:.2f}%",
        f"Sharpe Ratio(年化): {sharpe_text}",
    ]
    if result.trades:
        lines.append("最近5笔:")
        for item in result.trades[-5:]:
            lines.append(
                f"- {item.entry_time} -> {item.exit_time} | {item.entry_signal_type} | "
                f"{item.return_pct:.2f}% | {item.exit_reason}"
            )
    return "\n".join(lines)
