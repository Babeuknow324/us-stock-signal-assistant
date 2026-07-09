from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Tuple

from .config import Settings, load_settings
from .llm_analyzer import LLMAnalyzer
from .labels import label_evidence_strength, label_risk_level, label_signal_type
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
                    f"证据强度: {label_evidence_strength(llm_result.get('evidence_strength', 'medium'))}\n"
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


def _signal_trade_bias(signal_type: str) -> str:
    if signal_type in {"breakout_candidate", "long_setup"}:
        return "买点关注"
    if signal_type == "exit_warning":
        return "卖点/减仓关注"
    return "继续观察"


def _action_hint(signal_type: str) -> str:
    hints = {
        "breakout_candidate": "回踩不破可考虑跟随，避免直接追高。",
        "long_setup": "可小仓分批试错，失效位下方不恋战。",
        "exit_warning": "已有仓位优先减仓或收紧止损。",
        "observation_only": "暂不交易，等待确认后再行动。",
    }
    return hints.get(signal_type, "等待下一次明确信号。")


def _format_signal_card(snapshot: QuerySnapshot, title: str) -> str:
    signal = snapshot.signal
    if signal is None:
        return (
            f"【{title}】\n"
            f"标的: {snapshot.symbol}\n"
            f"当前价: {snapshot.price:.2f}\n"
            "当前无有效买卖点，建议继续观察。"
        )

    return (
        f"【{title}】\n"
        f"标的: {snapshot.symbol} ({signal.timeframe})\n"
        f"方向: {_signal_trade_bias(signal.signal_type)}\n"
        f"信号: {label_signal_type(signal.signal_type)}\n"
        f"当前价: {snapshot.price:.2f}\n"
        f"买点区间: {signal.entry_min}-{signal.entry_max}\n"
        f"卖点/止损参考: {signal.invalidation}\n"
        f"目标/压力参考: {signal.resistance}\n"
        f"执行建议: {_action_hint(signal.signal_type)}\n"
        f"信号质量: {signal.quality_score}/100 ({signal.priority_tier}级)\n"
        f"风险标签: {label_risk_level(signal.risk_level)} | ATR {signal.atr_pct}%"
    )


def format_advice_reply(snapshot: QuerySnapshot) -> str:
    return _format_signal_card(snapshot, title="盘中决策卡")


def format_buypoint_reply(snapshot: QuerySnapshot) -> str:
    signal = snapshot.signal
    if signal is None:
        return _format_signal_card(snapshot, title="买点卡")

    if signal.signal_type not in {"breakout_candidate", "long_setup"}:
        return (
            _format_signal_card(snapshot, title="买点卡")
            + "\n提示: 当前不是偏多触发窗口，买点性价比一般。"
        )
    return _format_signal_card(snapshot, title="买点卡")


def format_sellpoint_reply(snapshot: QuerySnapshot) -> str:
    signal = snapshot.signal
    if signal is None:
        return _format_signal_card(snapshot, title="卖点卡")
    if signal.signal_type != "exit_warning":
        return (
            _format_signal_card(snapshot, title="卖点卡")
            + "\n提示: 当前未出现强卖点，可按失效位做防守。"
        )
    return _format_signal_card(snapshot, title="卖点卡")


def format_counter_question_reply(snapshot: QuerySnapshot) -> str:
    signal = snapshot.signal
    if signal is None:
        return (
            f"【反问清单】\n"
            f"{snapshot.symbol} 当前无明确信号。\n"
            "你可以先回答这三问：\n"
            "1) 是想找买点还是卖点？\n"
            "2) 可承受的止损幅度是多少？\n"
            "3) 你更看重突破还是回踩？"
        )
    return (
        f"【反问清单】\n"
        f"标的: {snapshot.symbol}\n"
        f"当前信号: {label_signal_type(signal.signal_type)}\n"
        "请先确认这 4 点再下单：\n"
        f"1) 触发条件是否满足（价格在 {signal.entry_min}-{signal.entry_max} 区间）？\n"
        f"2) 失效位 {signal.invalidation} 触发后是否愿意执行止损？\n"
        f"3) 目标/压力位 {signal.resistance} 附近是否有减仓计划？\n"
        f"4) 当前信号质量 {signal.quality_score}/100 是否符合你的阈值？"
    )


def format_risk_reply(snapshot: QuerySnapshot) -> str:
    if snapshot.signal is None:
        return f"{snapshot.symbol}\n当前无触发信号，风险中性偏观望。"
    signal = snapshot.signal
    return (
        f"{snapshot.symbol}\n"
        f"风险等级: {label_risk_level(signal.risk_level)}\n"
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
        f"当前信号: {label_signal_type(snapshot.signal.signal_type)}\n"
        f"规则解释: {snapshot.signal.explanation}\n"
        "LLM 未启用或本次未返回分析。"
    )


def _help_text() -> str:
    return (
        "可用命令示例:\n"
        "- 价格 NVDA\n"
        "- 建议 NVDA\n"
        "- 买点 NVDA\n"
        "- 卖点 TSLA\n"
        "- 反问 QQQ\n"
        "- 解释 HK.07709\n"
        "- 风控 TSLA\n"
        "也支持自然语言:\n"
        "- NVDA 现在能买吗？\n"
        "- TSLA 该不该减仓？\n"
        "- 7709 怎么看？"
    )


def _dispatch_command(cmd: str, symbol: str) -> Tuple[bool, str, str]:
    if cmd in {"price", "价格", "报价"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_price_reply(snap), snap.symbol
    if cmd in {"advice", "建议", "信号"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_advice_reply(snap), snap.symbol
    if cmd in {"buy", "买点", "买入"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_buypoint_reply(snap), snap.symbol
    if cmd in {"sell", "卖点", "卖出"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_sellpoint_reply(snap), snap.symbol
    if cmd in {"counter", "反问", "复盘"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_counter_question_reply(snap), snap.symbol
    if cmd in {"explain", "解释", "分析"}:
        snap = get_snapshot(symbol, include_llm=True)
        return True, format_explain_reply(snap), snap.symbol
    if cmd in {"risk", "风控", "风险"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_risk_reply(snap), snap.symbol
    return False, "", ""


def _extract_symbol_from_text(raw: str) -> Optional[str]:
    text = raw.upper()
    explicit = re.search(r"(?:US|HK)\.[A-Z0-9]{1,8}", text)
    if explicit:
        return explicit.group(0)

    # Bare 4-5 digits: assume HK code
    hk_num = re.search(r"(?<!\d)\d{4,5}(?!\d)", text)
    if hk_num:
        return hk_num.group(0)

    # Ticker-like token possibly glued with Chinese words, e.g. "NVDA现在能买吗"
    candidates = re.findall(r"[A-Z]{1,5}", text)
    for token in candidates:
        if token not in {"HELP", "BUY", "SELL", "RISK", "PRICE"}:
            return token
    return None


def _infer_intent(raw: str) -> Optional[str]:
    text = raw.lower()
    if any(k in text for k in {"帮助", "help", "/help", "指令"}):
        return "help"
    if any(k in text for k in {"价格", "报价", "现价", "最新价", "price", "quote"}):
        return "price"
    if any(k in text for k in {"买点", "买吗", "能买吗", "能不能买", "可不可以买", "买入", "开仓", "加仓", "做多"}):
        return "buy"
    if any(k in text for k in {"卖点", "卖出", "减仓", "止盈", "离场", "平仓"}):
        return "sell"
    if any(k in text for k in {"风控", "风险", "止损", "失效"}):
        return "risk"
    if any(k in text for k in {"解释", "逻辑", "为什么", "分析"}):
        return "explain"
    if any(k in text for k in {"反问", "我该怎么做", "怎么操作", "执行计划"}):
        return "counter"
    if any(k in text for k in {"建议", "怎么看", "怎么样"}):
        return "advice"
    return None


def route_text(text: str, default_symbol: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    raw = text.strip()
    if not raw:
        return False, "", None

    if raw in {"帮助", "help", "/help", "指令"}:
        return True, _help_text(), None

    # Explicit command mode: "建议 NVDA" / "建议:NVDA"
    normalized = raw.replace("：", ":")
    match = re.match(r"^([^\s:]+)\s*(?::|\s)\s*([A-Za-z0-9.]+)$", normalized)
    if match:
        cmd = match.group(1).lower()
        symbol = match.group(2)
        handled, reply, used_symbol = _dispatch_command(cmd, symbol)
        return handled, reply, used_symbol if handled else None

    # Natural language mode
    intent = _infer_intent(raw)
    if intent == "help":
        return True, _help_text(), None
    if intent is None:
        return (
            False,
            (
                "我收到了你的消息，但还不确定你想查什么。\n"
                "你可以直接说：\n"
                "- NVDA 现在能买吗？\n"
                "- TSLA 该不该减仓？\n"
                "- QQQ 怎么看？"
            ),
            None,
        )

    symbol = _extract_symbol_from_text(raw) or default_symbol
    if not symbol:
        return (
            True,
            "我理解你的意图了，但还缺少标的代码。请补一个，如：NVDA、TSLA、7709。",
            None,
        )

    handled, reply, used_symbol = _dispatch_command(intent, symbol)
    return handled, reply, used_symbol if handled else None


def route_command(text: str) -> Tuple[bool, str]:
    handled, reply, _ = route_text(text, default_symbol=None)
    return handled, reply
