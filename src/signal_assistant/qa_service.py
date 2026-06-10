from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .config import Settings, load_settings
from .llm_analyzer import LLMAnalyzer
from .longbridge_client import LongbridgeMarketDataClient
from .signal_engine import SignalEngine, SignalResult


@dataclass(frozen=True)
class QuerySnapshot:
    symbol: str
    price: float
    signal: Optional[SignalResult]
    llm_text: Optional[str]


def normalize_user_symbol(raw: str) -> str:
    token = raw.strip().upper()
    if "." in token:
        return token
    if token.isdigit():
        return f"HK.{int(token):05d}"
    return f"US.{token}"


def get_snapshot(
    symbol: str,
    settings: Optional[Settings] = None,
    include_llm: bool = False,
) -> QuerySnapshot:
    cfg = settings or load_settings()
    normalized = normalize_user_symbol(symbol)
    market_data_client = LongbridgeMarketDataClient(cfg)
    signal_engine = SignalEngine(cfg)
    llm_analyzer = LLMAnalyzer(cfg)

    try:
        latest_price = market_data_client.get_latest_price(normalized)
        bars_by_timeframe = {
            timeframe: market_data_client.get_kline(normalized, timeframe, max_count=300)
            for timeframe in cfg.all_timeframes
        }
        benchmark_bars_by_timeframe = None
        if cfg.enable_relative_strength_filter:
            benchmark_bars_by_timeframe = {
                timeframe: market_data_client.get_kline(
                    cfg.relative_strength_benchmark, timeframe, max_count=300
                )
                for timeframe in cfg.all_timeframes
            }
        signal = signal_engine.evaluate(normalized, bars_by_timeframe, benchmark_bars_by_timeframe)
        llm_text = None
        if include_llm and signal is not None and llm_analyzer.enabled:
            llm_result, llm_error = llm_analyzer.analyze(signal)
            if llm_result:
                llm_text = (
                    f"总结: {llm_result.get('summary', '')}\n"
                    f"多头逻辑: {llm_result.get('bull_case', '')}\n"
                    f"空头逻辑: {llm_result.get('bear_case', '')}\n"
                    f"证据强度: {llm_result.get('evidence_strength', 'medium')}\n"
                    f"失效条件: {llm_result.get('failure_condition', '')}\n"
                    f"下一步观察: {llm_result.get('next_check', '')}\n"
                    f"置信度: {llm_result.get('confidence', 50)}/100"
                )
            elif llm_error:
                llm_text = f"LLM 暂不可用: {llm_error}"
        return QuerySnapshot(symbol=normalized, price=latest_price, signal=signal, llm_text=llm_text)
    finally:
        market_data_client.close()


def format_price_reply(snapshot: QuerySnapshot) -> str:
    return f"{snapshot.symbol} 最新价: {snapshot.price:.2f}"


def format_advice_reply(snapshot: QuerySnapshot) -> str:
    if snapshot.signal is None:
        return (
            f"{snapshot.symbol}\n"
            f"当前价格: {snapshot.price:.2f}\n"
            "当前信号: 无\n"
            "建议: 先观望，等待明确触发。"
        )
    signal = snapshot.signal
    action = {
        "long_setup": "偏多候选，可等回踩确认后分批关注。",
        "breakout_candidate": "突破候选，关注量能与回踩站稳。",
        "exit_warning": "偏风控，优先控制仓位风险。",
        "observation_only": "仅观察，暂不急于操作。",
    }.get(signal.signal_type, "先观望。")
    return (
        f"{snapshot.symbol}\n"
        f"当前价格: {snapshot.price:.2f}\n"
        f"当前信号: {signal.signal_type} (Tier {signal.priority_tier}, 质量 {signal.quality_score}/100)\n"
        f"建议: {action}\n"
        f"关键位: 入场 {signal.entry_min}-{signal.entry_max} | 失效 {signal.invalidation} | 阻力 {signal.resistance}\n"
        f"风控: {signal.risk_level} | ATR {signal.atr_pct}%"
    )


def format_risk_reply(snapshot: QuerySnapshot) -> str:
    if snapshot.signal is None:
        return f"{snapshot.symbol}\n当前无触发信号，风险中性偏观望。"
    signal = snapshot.signal
    return (
        f"{snapshot.symbol}\n"
        f"风险等级: {signal.risk_level}\n"
        f"失效位: {signal.invalidation}\n"
        f"当前价: {snapshot.price:.2f}\n"
        f"说明: {signal.explanation}"
    )


def format_explain_reply(snapshot: QuerySnapshot) -> str:
    if snapshot.signal is None:
        return f"{snapshot.symbol}\n当前无触发信号，暂无结构化解释。"
    if snapshot.llm_text:
        return f"{snapshot.symbol}\n{snapshot.llm_text}"
    return (
        f"{snapshot.symbol}\n"
        f"当前信号: {snapshot.signal.signal_type}\n"
        f"规则解释: {snapshot.signal.explanation}\n"
        "LLM 未启用或本次未返回分析。"
    )


def route_command(text: str) -> Tuple[bool, str]:
    raw = text.strip()
    if not raw:
        return False, ""

    parts = raw.replace("：", ":").split()
    if len(parts) < 2:
        if raw in {"帮助", "help", "/help", "指令"}:
            return True, (
                "命令格式示例:\n"
                "- 价格 7709\n"
                "- 建议 NVDA\n"
                "- 解释 HK.07709\n"
                "- 风控 TSLA"
            )
        return False, ""

    cmd = parts[0].lower()
    symbol = parts[1]
    if cmd in {"price", "价格", "报价"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_price_reply(snap)
    if cmd in {"advice", "建议", "信号"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_advice_reply(snap)
    if cmd in {"explain", "解释", "分析"}:
        snap = get_snapshot(symbol, include_llm=True)
        return True, format_explain_reply(snap)
    if cmd in {"risk", "风控", "风险"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_risk_reply(snap)

    return False, ""
