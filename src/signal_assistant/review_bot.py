from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from .labels import label_risk_level, label_signal_type

if TYPE_CHECKING:
    from .qa_service import QuerySnapshot


@dataclass(frozen=True)
class ReviewResult:
    status: str
    score: int
    reasons: list[str]
    position_hint: str
    reward_risk: Optional[float]


def _score_to_status(score: int) -> str:
    if score >= 75:
        return "PASS"
    if score >= 55:
        return "WARN"
    return "BLOCK"


def _position_hint(score: int) -> str:
    if score >= 85:
        return "可考虑标准仓位（例如 1.0x 风险预算）"
    if score >= 70:
        return "建议中等仓位（例如 0.6x-0.8x）"
    if score >= 55:
        return "建议轻仓试错（例如 0.3x-0.5x）"
    return "建议暂不入场，等待下一次触发"


def review_snapshot(snapshot: QuerySnapshot) -> ReviewResult:
    signal = snapshot.signal
    if signal is None:
        return ReviewResult(
            status="BLOCK",
            score=35,
            reasons=["当前没有明确触发信号，审单不通过。"],
            position_hint="暂不交易",
            reward_risk=None,
        )

    reasons: list[str] = []
    score = 50

    is_buy_setup = signal.signal_type in {"long_setup", "breakout_candidate"}
    is_sell_setup = signal.signal_type == "exit_warning"

    if is_buy_setup:
        score += 12
        reasons.append(f"方向一致：当前信号为 {label_signal_type(signal.signal_type)}。")
    elif is_sell_setup:
        score += 4
        reasons.append("当前更偏卖点/减仓结构，不建议新开多头。")
    else:
        score -= 8
        reasons.append("当前仅观察结构，交易确定性一般。")

    # Reward/Risk from current signal levels.
    entry_mid = (signal.entry_min + signal.entry_max) / 2.0
    risk = abs(entry_mid - signal.invalidation)
    reward = abs(signal.resistance - entry_mid)
    rr = (reward / risk) if risk > 0 else 0.0
    if rr >= 1.5:
        score += 12
        reasons.append(f"收益风险比较好（约 {rr:.2f}）。")
    elif rr >= 1.2:
        score += 6
        reasons.append(f"收益风险比尚可（约 {rr:.2f}）。")
    else:
        score -= 12
        reasons.append(f"收益风险比偏低（约 {rr:.2f}），性价比一般。")

    if signal.quality_score >= 80:
        score += 12
        reasons.append(f"信号质量高（{signal.quality_score}/100）。")
    elif signal.quality_score >= 65:
        score += 6
        reasons.append(f"信号质量中等偏上（{signal.quality_score}/100）。")
    else:
        score -= 10
        reasons.append(f"信号质量一般（{signal.quality_score}/100）。")

    if signal.risk_level == "low":
        score += 8
        reasons.append("风险标签较低。")
    elif signal.risk_level == "medium":
        score += 2
        reasons.append("风险标签中等。")
    else:
        score -= 10
        reasons.append("风险标签偏高。")

    # Very high volatility means reduce confidence for directional entries.
    if signal.atr_pct >= 4.0:
        score -= 8
        reasons.append(f"ATR 较高（{signal.atr_pct}%），短线波动风险偏大。")
    elif signal.atr_pct <= 1.0:
        score -= 4
        reasons.append(f"ATR 偏低（{signal.atr_pct}%），动能可能不足。")
    else:
        score += 3
        reasons.append(f"ATR 适中（{signal.atr_pct}%）。")

    score = max(0, min(100, score))
    status = _score_to_status(score)
    return ReviewResult(
        status=status,
        score=score,
        reasons=reasons,
        position_hint=_position_hint(score),
        reward_risk=rr,
    )


def format_review_reply(snapshot: QuerySnapshot) -> str:
    result = review_snapshot(snapshot)
    signal = snapshot.signal

    header = f"【审单结果】{result.status}（{result.score}/100）"
    if signal is None:
        return f"{header}\n标的: {snapshot.symbol}\n结论: 当前无有效触发信号，建议继续观察。"

    top_reasons = result.reasons[:3]
    reason_lines = "\n".join(f"- {item}" for item in top_reasons)
    rr_text = f"{result.reward_risk:.2f}" if result.reward_risk is not None else "N/A"
    return (
        f"{header}\n"
        f"标的: {snapshot.symbol} ({signal.timeframe})\n"
        f"当前信号: {label_signal_type(signal.signal_type)}\n"
        f"风险标签: {label_risk_level(signal.risk_level)}\n"
        f"收益风险比(估算): {rr_text}\n"
        f"核心理由:\n{reason_lines}\n"
        f"仓位建议: {result.position_hint}"
    )
