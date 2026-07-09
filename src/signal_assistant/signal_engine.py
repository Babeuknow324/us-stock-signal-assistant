from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from .config import Settings
from .indicator_engine import IndicatorSnapshot, build_indicator_snapshot


@dataclass(frozen=True)
class SignalResult:
    source: str
    symbol: str
    timeframe: str
    signal_type: str
    price: float
    entry_min: float
    entry_max: float
    invalidation: float
    resistance: float
    rsi: float
    ma20: float
    ma50: float
    atr_pct: float
    volume_ratio: float
    risk_level: str
    explanation: str
    timestamp: str
    quality_score: int
    rule_evidence_strength: str
    priority_tier: str
    llm_analysis: Optional[Dict[str, object]] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class SignalEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self,
        symbol: str,
        bars_by_timeframe: Dict[str, pd.DataFrame],
        benchmark_bars_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Optional[SignalResult]:
        snapshots: Dict[str, IndicatorSnapshot] = {}
        for timeframe, bars in bars_by_timeframe.items():
            snapshots[timeframe] = build_indicator_snapshot(
                bars, self.settings.swing_lookback_bars, self.settings.atr_period
            )

        exec_tf = self.settings.execution_timeframe
        trend_tf = self.settings.trend_timeframe
        main = snapshots[exec_tf]
        trend = snapshots[trend_tf]
        confirms = [snapshots[tf] for tf in self.settings.confirm_timeframes if tf in snapshots]
        exec_bars = bars_by_timeframe[exec_tf]

        benchmark_exec_bars = None
        if benchmark_bars_by_timeframe is not None:
            benchmark_exec_bars = benchmark_bars_by_timeframe.get(exec_tf)

        atr_ok = self._passes_atr_filter(main)
        rs_ok = self._passes_relative_strength_filter(symbol, exec_bars, benchmark_exec_bars)

        long_signal = self._build_long_setup(symbol, main, trend, confirms, atr_ok, rs_ok)
        breakout_signal = self._build_breakout_candidate(
            symbol, main, trend, confirms, exec_bars, atr_ok, rs_ok
        )
        exit_signal = self._build_exit_warning(symbol, main)

        # Buy-point capture is prioritized in V2. Exit warnings are still produced,
        # but only when no buy setup is active in the same evaluation window.
        if breakout_signal:
            return breakout_signal
        if long_signal:
            return long_signal
        if exit_signal:
            return exit_signal
        return self._build_observation_only(symbol, main, trend, confirms, atr_ok, rs_ok)

    def _passes_atr_filter(self, main: IndicatorSnapshot) -> bool:
        if not self.settings.enable_atr_filter:
            return True
        return self.settings.atr_min_pct <= main.atr_pct <= self.settings.atr_max_pct

    def _passes_relative_strength_filter(
        self,
        symbol: str,
        exec_bars: pd.DataFrame,
        benchmark_exec_bars: Optional[pd.DataFrame],
    ) -> bool:
        if not self.settings.enable_relative_strength_filter:
            return True
        if symbol == self.settings.relative_strength_benchmark:
            return True
        if benchmark_exec_bars is None:
            return False

        lookback = self.settings.relative_strength_lookback_bars
        if len(exec_bars) <= lookback or len(benchmark_exec_bars) <= lookback:
            return False

        sym_return = float(exec_bars["close"].iloc[-1] / exec_bars["close"].iloc[-1 - lookback] - 1.0)
        bench_return = float(
            benchmark_exec_bars["close"].iloc[-1] / benchmark_exec_bars["close"].iloc[-1 - lookback] - 1.0
        )
        return sym_return >= bench_return + self.settings.relative_strength_min_excess_return

    @staticmethod
    def _quality_to_strength(score: int) -> str:
        if score >= 80:
            return "strong"
        if score >= 60:
            return "medium"
        return "weak"

    @staticmethod
    def _quality_to_tier(score: int) -> str:
        if score >= 80:
            return "A"
        if score >= 60:
            return "B"
        return "C"

    def _build_long_setup(
        self,
        symbol: str,
        main: IndicatorSnapshot,
        trend: IndicatorSnapshot,
        confirms: List[IndicatorSnapshot],
        atr_ok: bool,
        rs_ok: bool,
    ) -> Optional[SignalResult]:
        above_mas = main.price > main.ma20 and main.price > main.ma50
        ma_structure = main.ma20 > main.ma50 or main.ma20 > main.prev_ma20
        rsi_recovering = main.prev_rsi14 < self.settings.rsi_recovery_threshold <= main.rsi14
        trend_ok = trend.price > trend.ma20 and trend.ma20 >= trend.ma50
        trend_gap_pct = (trend.ma20 - trend.ma50) / trend.price if trend.price else 0.0
        trend_strength_ok = trend_gap_pct >= self.settings.min_trend_ma_gap_pct
        not_overheated = main.rsi14 <= self.settings.buy_rsi_ceiling

        resistance_distance = (main.swing_high - main.price) / main.price if main.price else 0.0
        not_too_close_resistance = resistance_distance > self.settings.resistance_buffer_pct
        ma20_distance_pct = abs(main.price - main.ma20) / main.price if main.price else 0.0
        not_too_extended_from_ma20 = ma20_distance_pct <= self.settings.long_max_ma20_distance_pct

        confirm_votes = 0
        for confirm in confirms:
            if confirm.price >= confirm.ma20 and confirm.rsi14 >= 50:
                confirm_votes += 1
        confirm_ok = confirm_votes >= 1
        invalidation = round(min(main.ma20, main.swing_low), 2)
        risk = main.price - invalidation
        reward = max(main.swing_high - main.price, 0.0)
        reward_risk = reward / risk if risk > 0 else 0.0
        rr_ok = reward_risk >= self.settings.min_buy_reward_risk

        if not all(
            [
                above_mas,
                ma_structure,
                rsi_recovering,
                trend_ok,
                trend_strength_ok,
                not_overheated,
                not_too_close_resistance,
                not_too_extended_from_ma20,
                rr_ok,
                confirm_ok,
                atr_ok,
                rs_ok,
            ]
        ):
            return None

        risk_level = "low" if confirm_votes >= 2 and main.volume_ratio >= self.settings.volume_ratio_min else "medium"
        quality_score = 55
        quality_score += 10 if trend_ok and trend_strength_ok else 0
        quality_score += 10 if confirm_votes >= 2 else 5 if confirm_votes == 1 else 0
        quality_score += 10 if main.volume_ratio >= self.settings.volume_ratio_min else 0
        quality_score += 5 if rr_ok else 0
        quality_score += 5 if atr_ok else 0
        quality_score += 5 if rs_ok else 0
        quality_score += 3 if not_overheated else 0
        quality_score += 2 if not_too_extended_from_ma20 else 0
        quality_score += 5 if resistance_distance > (self.settings.resistance_buffer_pct * 2) else 0
        quality_score = max(0, min(100, quality_score))
        entry_min = round(main.price * (1 - self.settings.entry_zone_pct), 2)
        entry_max = round(main.price * (1 + self.settings.entry_zone_pct), 2)

        return SignalResult(
            source="futu",
            symbol=symbol,
            timeframe=self.settings.execution_timeframe,
            signal_type="long_setup",
            price=round(main.price, 2),
            entry_min=entry_min,
            entry_max=entry_max,
            invalidation=invalidation,
            resistance=round(main.swing_high, 2),
            rsi=round(main.rsi14, 2),
            ma20=round(main.ma20, 2),
            ma50=round(main.ma50, 2),
            atr_pct=round(main.atr_pct * 100, 2),
            volume_ratio=round(main.volume_ratio, 2),
            risk_level=risk_level,
            explanation=(
                "15m 价格站上 MA20/MA50，1h 趋势配合，RSI 回升突破阈值，"
                "低周期有确认，且通过了不过热、收益风险比与波动率/相对强弱过滤。"
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
            quality_score=quality_score,
            rule_evidence_strength=self._quality_to_strength(quality_score),
            priority_tier=self._quality_to_tier(quality_score),
        )

    def _build_breakout_candidate(
        self,
        symbol: str,
        main: IndicatorSnapshot,
        trend: IndicatorSnapshot,
        confirms: List[IndicatorSnapshot],
        exec_bars: pd.DataFrame,
        atr_ok: bool,
        rs_ok: bool,
    ) -> Optional[SignalResult]:
        breakout_level = main.swing_high
        broke_out = main.price > breakout_level
        volume_ok = main.volume_ratio >= self.settings.volume_ratio_min
        trend_ok = trend.price >= trend.ma20
        extended_pct = (main.price - breakout_level) / breakout_level if breakout_level else 1.0
        not_extended = extended_pct <= self.settings.max_extension_pct
        confirm_ok = any(item.price >= item.ma20 for item in confirms) if confirms else True
        prev_close = float(exec_bars["close"].iloc[-2])
        last_low = float(exec_bars["low"].iloc[-1])
        last_high = float(exec_bars["high"].iloc[-1])
        fresh_break = prev_close <= breakout_level < main.price
        retest_hold = last_low <= breakout_level * (1 + self.settings.breakout_retest_tolerance_pct) and main.price > breakout_level
        breakout_structure_ok = (
            (fresh_break or retest_hold) if self.settings.enable_breakout_retest_filter else True
        )
        not_overheated = main.rsi14 <= self.settings.breakout_rsi_ceiling
        candle_range = max(last_high - last_low, 1e-6)
        close_strength = (main.price - last_low) / candle_range
        close_strength_ok = close_strength >= self.settings.min_breakout_close_strength
        target_price = main.price * (1 + self.settings.max_extension_pct)
        risk = main.price - main.ma20
        reward = max(target_price - main.price, 0.0)
        reward_risk = reward / risk if risk > 0 else 0.0
        rr_ok = reward_risk >= self.settings.min_buy_reward_risk

        if not all(
            [
                broke_out,
                volume_ok,
                trend_ok,
                not_extended,
                confirm_ok,
                atr_ok,
                rs_ok,
                breakout_structure_ok,
                not_overheated,
                close_strength_ok,
                rr_ok,
            ]
        ):
            return None
        quality_score = 50
        quality_score += 15 if volume_ok else 0
        quality_score += 10 if trend_ok else 0
        quality_score += 10 if confirm_ok else 0
        quality_score += 10 if main.rsi14 >= 55 else 0
        quality_score += 5 if breakout_structure_ok else 0
        quality_score += 5 if rr_ok else 0
        quality_score += 3 if close_strength_ok else 0
        quality_score += 2 if not_overheated else 0
        quality_score += 5 if atr_ok else 0
        quality_score += 5 if rs_ok else 0
        quality_score = max(0, min(100, quality_score))

        return SignalResult(
            source="futu",
            symbol=symbol,
            timeframe=self.settings.execution_timeframe,
            signal_type="breakout_candidate",
            price=round(main.price, 2),
            entry_min=round(breakout_level, 2),
            entry_max=round(main.price, 2),
            invalidation=round(main.ma20, 2),
            resistance=round(target_price, 2),
            rsi=round(main.rsi14, 2),
            ma20=round(main.ma20, 2),
            ma50=round(main.ma50, 2),
            atr_pct=round(main.atr_pct * 100, 2),
            volume_ratio=round(main.volume_ratio, 2),
            risk_level="medium",
            explanation=(
                "价格突破近期摆动高点且量能放大，1h 趋势与低周期结构仍配合，"
                "突破结构（新鲜突破或回踩站稳）已确认，并通过收盘强度与收益风险比过滤。"
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
            quality_score=quality_score,
            rule_evidence_strength=self._quality_to_strength(quality_score),
            priority_tier=self._quality_to_tier(quality_score),
        )

    def _build_exit_warning(self, symbol: str, main: IndicatorSnapshot) -> Optional[SignalResult]:
        lose_ma20 = main.price < main.ma20 and main.rsi14 < main.prev_rsi14
        weak_rsi = main.rsi14 < self.settings.exit_rsi_threshold
        break_support = main.price < main.swing_low
        # Reduce noisy defensive alerts: require a clear structure break or
        # at least two weakness conditions to flag a sell/risk reminder.
        weakness_votes = int(lose_ma20) + int(weak_rsi) + int(break_support)
        if not (break_support or weakness_votes >= 2):
            return None
        quality_score = 70 if break_support else 60 if lose_ma20 and weak_rsi else 52

        return SignalResult(
            source="futu",
            symbol=symbol,
            timeframe=self.settings.execution_timeframe,
            signal_type="exit_warning",
            price=round(main.price, 2),
            entry_min=round(main.price, 2),
            entry_max=round(main.price, 2),
            invalidation=round(main.ma20, 2),
            resistance=round(main.swing_high, 2),
            rsi=round(main.rsi14, 2),
            ma20=round(main.ma20, 2),
            ma50=round(main.ma50, 2),
            atr_pct=round(main.atr_pct * 100, 2),
            volume_ratio=round(main.volume_ratio, 2),
            risk_level="high",
            explanation=(
                "走弱信号增强：价格失守 MA20 和/或 RSI 继续走弱，并可能出现支撑失守。"
                "建议检查持仓风险并收紧计划。"
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
            quality_score=quality_score,
            rule_evidence_strength=self._quality_to_strength(quality_score),
            priority_tier=self._quality_to_tier(quality_score),
        )

    def _build_observation_only(
        self,
        symbol: str,
        main: IndicatorSnapshot,
        trend: IndicatorSnapshot,
        confirms: List[IndicatorSnapshot],
        atr_ok: bool,
        rs_ok: bool,
    ) -> Optional[SignalResult]:
        improving = (main.price > main.ma20) or (main.rsi14 > main.prev_rsi14)
        trend_not_bad = trend.price >= trend.ma50
        if not (improving and trend_not_bad):
            return None
        quality_score = 40 if improving and trend_not_bad else 30
        quality_score += 3 if atr_ok else 0
        quality_score += 2 if rs_ok else 0

        return SignalResult(
            source="futu",
            symbol=symbol,
            timeframe=self.settings.execution_timeframe,
            signal_type="observation_only",
            price=round(main.price, 2),
            entry_min=round(main.price * (1 - self.settings.entry_zone_pct), 2),
            entry_max=round(main.price * (1 + self.settings.entry_zone_pct), 2),
            invalidation=round(main.swing_low, 2),
            resistance=round(main.swing_high, 2),
            rsi=round(main.rsi14, 2),
            ma20=round(main.ma20, 2),
            ma50=round(main.ma50, 2),
            atr_pct=round(main.atr_pct * 100, 2),
            volume_ratio=round(main.volume_ratio, 2),
            risk_level="high",
            explanation=(
                "结构有改善，但趋势或低周期确认仍不完整。"
                "仅观察，不宜急于入场。"
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
            quality_score=quality_score,
            rule_evidence_strength=self._quality_to_strength(quality_score),
            priority_tier=self._quality_to_tier(quality_score),
        )
