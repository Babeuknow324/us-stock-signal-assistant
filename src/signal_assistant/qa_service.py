from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Tuple

from .config import Settings, load_settings
from .fundamental_service import fetch_fundamental_snapshot
from .llm_analyzer import LLMAnalyzer
from .labels import label_evidence_strength, label_risk_level, label_signal_type
from .longbridge_client import LongbridgeMarketDataClient
from .option_service import fetch_option_snapshot
from .review_bot import format_review_reply
from .signal_engine import SignalEngine, SignalResult


@dataclass(frozen=True)
class QuerySnapshot:
    symbol: str
    price: float
    signal: Optional[SignalResult]
    llm_text: Optional[str]
    history_bars: int
    data_note: Optional[str]


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
        history_bars = min(len(df) for df in bars_by_timeframe.values()) if bars_by_timeframe else 0
        signal = None
        data_note = None
        try:
            signal = signal_engine.evaluate(normalized, bars_by_timeframe, benchmark_bars_by_timeframe)
        except ValueError as exc:
            # New listings or sparse bars can fail indicator bootstrap; keep Q&A alive.
            data_note = f"历史K线数量不足，当前按观察模式处理（{exc}）。"
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
        return QuerySnapshot(
            symbol=normalized,
            price=latest_price,
            signal=signal,
            llm_text=llm_text,
            history_bars=history_bars,
            data_note=data_note,
        )
    finally:
        market_data_client.close()


def format_price_reply(snapshot: QuerySnapshot) -> str:
    return f"我刚看了下，{snapshot.symbol} 现在大概在 {snapshot.price:.2f}。"


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
            f"我看了下 {snapshot.symbol}，当前价格 {snapshot.price:.2f}。\n"
            "暂时没有明确买卖点，先观察会更稳一点。"
        )

    bias = _signal_trade_bias(signal.signal_type)
    hint = _action_hint(signal.signal_type)
    return (
        f"我看了下 {snapshot.symbol}（{signal.timeframe}），当前更偏 **{bias}**。\n"
        f"信号类型是「{label_signal_type(signal.signal_type)}」，现价 {snapshot.price:.2f}。\n"
        f"- 买点区间：{signal.entry_min}-{signal.entry_max}\n"
        f"- 卖点/止损参考：{signal.invalidation}\n"
        f"- 目标/压力参考：{signal.resistance}\n"
        f"- 执行建议：{hint}\n"
        f"- 信号质量：{signal.quality_score}/100（{signal.priority_tier}级）\n"
        f"- 风险标签：{label_risk_level(signal.risk_level)}，ATR {signal.atr_pct}%"
    )


def format_advice_reply(snapshot: QuerySnapshot) -> str:
    message = _format_signal_card(snapshot, title="盘中决策卡")
    if snapshot.data_note:
        message += f"\n补充: {snapshot.data_note}"
    return message


def format_buypoint_reply(snapshot: QuerySnapshot) -> str:
    signal = snapshot.signal
    if signal is None:
        message = _format_signal_card(snapshot, title="买点卡")
        if snapshot.data_note:
            message += f"\n补充: {snapshot.data_note}"
        return message

    if signal.signal_type not in {"breakout_candidate", "long_setup"}:
        return (
            _format_signal_card(snapshot, title="买点卡")
            + "\n补一句：现在不是标准偏多触发窗口，先别急着追会更划算。"
        )
    message = _format_signal_card(snapshot, title="买点卡")
    if snapshot.data_note:
        message += f"\n补充: {snapshot.data_note}"
    return message


def format_sellpoint_reply(snapshot: QuerySnapshot) -> str:
    signal = snapshot.signal
    if signal is None:
        message = _format_signal_card(snapshot, title="卖点卡")
        if snapshot.data_note:
            message += f"\n补充: {snapshot.data_note}"
        return message
    if signal.signal_type != "exit_warning":
        return (
            _format_signal_card(snapshot, title="卖点卡")
            + "\n目前还没到强卖点，先按失效位做防守就好。"
        )
    message = _format_signal_card(snapshot, title="卖点卡")
    if snapshot.data_note:
        message += f"\n补充: {snapshot.data_note}"
    return message


def format_counter_question_reply(snapshot: QuerySnapshot) -> str:
    signal = snapshot.signal
    if signal is None:
        return (
            f"{snapshot.symbol} 现在没有特别清晰的触发。\n"
            "你可以先想清楚这三件事：\n"
            "1) 是想找买点还是卖点？\n"
            "2) 可承受的止损幅度是多少？\n"
            "3) 你更看重突破还是回踩？"
        )
    return (
        f"{snapshot.symbol} 当前信号是「{label_signal_type(signal.signal_type)}」。\n"
        "如果你准备下单，先确认这 4 点：\n"
        f"1) 触发条件是否满足（价格在 {signal.entry_min}-{signal.entry_max} 区间）？\n"
        f"2) 失效位 {signal.invalidation} 触发后是否愿意执行止损？\n"
        f"3) 目标/压力位 {signal.resistance} 附近是否有减仓计划？\n"
        f"4) 当前信号质量 {signal.quality_score}/100 是否符合你的阈值？"
    )


def format_risk_reply(snapshot: QuerySnapshot) -> str:
    if snapshot.signal is None:
        return f"{snapshot.symbol} 当前没有明显风险触发，先中性观察就行。"
    signal = snapshot.signal
    return (
        f"{snapshot.symbol} 的风险等级目前是 {label_risk_level(signal.risk_level)}。\n"
        f"当前价 {snapshot.price:.2f}，关键失效位在 {signal.invalidation}。\n"
        f"一句话：{signal.explanation}"
    )


def format_explain_reply(snapshot: QuerySnapshot) -> str:
    if snapshot.signal is None:
        return f"{snapshot.symbol} 现在还没触发明确结构，所以暂时没有可解释的信号逻辑。"
    if snapshot.llm_text:
        return f"我按当前信号帮你拆解一下 {snapshot.symbol}：\n{snapshot.llm_text}"
    return (
        f"{snapshot.symbol} 当前信号是「{label_signal_type(snapshot.signal.signal_type)}」。\n"
        f"规则侧的解释是：{snapshot.signal.explanation}\n"
        "这次没有拿到 LLM 解读，我先给你规则版本。"
    )


def format_fundamental_reply(symbol: str) -> str:
    normalized = normalize_user_symbol(symbol)
    data = fetch_fundamental_snapshot(normalized)
    if not data.available:
        return f"{normalized} 的基本面数据暂时拉取失败（{data.error or 'unknown error'}），你可以稍后再试。"

    lines = [f"{normalized} 基本面速览（Longbridge）"]
    if data.revenue_yoy is not None:
        lines.append(f"- 营收同比: {data.revenue_yoy * 100:.1f}%")
    if data.net_profit_yoy is not None:
        lines.append(f"- 净利润同比: {data.net_profit_yoy * 100:.1f}%")
    if data.eps_yoy is not None:
        lines.append(f"- EPS同比: {data.eps_yoy * 100:.1f}%")
    if data.pe is not None:
        pe_txt = f"{data.pe:.2f}x"
        if data.pe_industry_median is not None:
            pe_txt += f"（行业中位 {data.pe_industry_median:.2f}x）"
        lines.append(f"- PE估值: {pe_txt}")
    if data.analyst_recommend:
        lines.append(f"- 机构一致评级: {data.analyst_recommend}")
    if data.target_upside_pct is not None:
        lines.append(f"- 机构目标隐含空间: {data.target_upside_pct:.1f}%")
    if len(lines) == 1:
        lines.append("- 暂无可用关键指标")
    return "\n".join(lines)


def format_tech_fund_reply(symbol: str, default_symbol: Optional[str] = None) -> tuple[str, str]:
    symbol_guess = symbol or default_symbol or ""
    snap = get_snapshot(symbol_guess, include_llm=False)
    tech = format_advice_reply(snap)
    fund = format_fundamental_reply(snap.symbol)
    reply = (
        f"{snap.symbol} 我给你合并看一遍（技术面 + 基本面）：\n\n"
        f"{tech}\n\n"
        f"{fund}"
    )
    return reply, snap.symbol


def _allowed_option_underlyings(settings: Settings) -> set[str]:
    allowed = {normalize_user_symbol(item) for item in settings.watchlist}
    # Keep a practical fallback universe for US options if watchlist changes.
    allowed.update(
        {
            "US.SPY",
            "US.NVDA",
            "US.MSFT",
            "US.AAPL",
            "US.AMZN",
            "US.META",
            "US.TSLA",
            "US.GOOGL",
            "US.MU",
            "US.SNDK",
        }
    )
    return allowed


def format_option_reply(
    symbol: str,
    default_symbol: Optional[str] = None,
    horizon_hint: Optional[str] = None,
    preference_text: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> tuple[str, str]:
    cfg = settings or load_settings()
    symbol_guess = normalize_user_symbol(symbol or default_symbol or "")
    if not symbol_guess:
        return "你想看期权没问题，给我一个标的就行（例如 SPY / NVDA / TSLA）。", ""

    allowed = _allowed_option_underlyings(cfg)
    if symbol_guess not in allowed:
        universe = ", ".join(sorted(allowed))
        return f"{symbol_guess} 目前不在你这版期权范围里。\n当前支持: {universe}", symbol_guess

    snap = get_snapshot(symbol_guess, settings=cfg, include_llm=False)
    option_snap = fetch_option_snapshot(snap.symbol, spot_price=snap.price)
    if not option_snap.available:
        return (
            f"{snap.symbol} 期权数据暂时拉取失败（{option_snap.error or 'unknown error'}），你可以稍后再试。",
            snap.symbol,
        )

    pref = (preference_text or "").lower()
    prefer_single_leg = any(k in pref for k in {"单腿", "裸买", "single leg", "single-leg"})
    prefer_bull = any(k in pref for k in {"做多", "偏多", "看涨", "bull", "call", "单腿多"})
    prefer_bear = any(k in pref for k in {"做空", "偏空", "看跌", "bear", "put", "单腿空"})

    if prefer_bull:
        direction = "偏多（按你的偏好）"
        if prefer_single_leg:
            structure = "按你偏好：单腿买入 call（优先 ATM 或轻度 ITM）"
        else:
            structure = "优先考虑 call debit spread（比裸买 call 更稳）"
    elif prefer_bear:
        direction = "偏空（按你的偏好）"
        if prefer_single_leg:
            structure = "按你偏好：单腿买入 put（优先 ATM 或轻度 ITM）"
        else:
            structure = "可考虑 put debit spread，避免裸买 put 的时间损耗压力"
    elif snap.signal and snap.signal.signal_type in {"breakout_candidate", "long_setup"}:
        direction = "偏多"
        structure = "优先考虑 call debit spread（比裸买 call 更稳）"
    elif snap.signal and snap.signal.signal_type == "exit_warning":
        direction = "偏防守/偏空"
        structure = "可考虑 put debit spread，避免裸买 put 的时间损耗压力"
    else:
        direction = "中性观察"
        if prefer_single_leg:
            structure = "按你偏好先用单腿，但建议等方向明确后再开仓"
        else:
            structure = "先等方向明确，再选期权结构"

    if option_snap.pcr is not None:
        if option_snap.pcr >= 1.2:
            flow_note = f"Put/Call 量比约 {option_snap.pcr:.2f}，资金更偏防守。"
        elif option_snap.pcr <= 0.8:
            flow_note = f"Put/Call 量比约 {option_snap.pcr:.2f}，资金偏风险偏好。"
        else:
            flow_note = f"Put/Call 量比约 {option_snap.pcr:.2f}，情绪中性。"
    else:
        flow_note = "暂未拿到 Put/Call 量比。"

    dte_note = "建议先看 7-14 天到期，平衡胜率与时间损耗。"
    if horizon_hint == "next_week":
        dte_note = "你提到下周到期，建议优先 next week 的 ATM 附近价差单，控制 Theta 风险。"

    expiry_preview = ", ".join(option_snap.expiry_dates[:4]) if option_snap.expiry_dates else "暂无到期日数据"
    atm_iv = "N/A"
    if option_snap.atm_call_iv is not None and option_snap.atm_put_iv is not None:
        atm_iv = f"Call IV {option_snap.atm_call_iv:.3f} / Put IV {option_snap.atm_put_iv:.3f}"

    reply = (
        f"{snap.symbol} 期权融合卡（技术面 + 期权数据）\n"
        f"- 当前技术面倾向: {direction}\n"
        f"- 现价: {snap.price:.2f}\n"
        f"- 结构建议: {structure}\n"
        f"- 到期建议: {dte_note}\n"
        f"- 近期到期日: {expiry_preview}\n"
        f"- 量能: Call {option_snap.call_volume or 0} / Put {option_snap.put_volume or 0}\n"
        f"- 情绪: {flow_note}\n"
        f"- ATM参考: strike {option_snap.atm_strike or 'N/A'}, {atm_iv}\n"
        "- 仓位建议: 单笔风险预算尽量 <= 账户 0.5%-1.0%"
    )
    return reply, snap.symbol


def _help_text() -> str:
    return (
        "你直接像平时聊天那样问我就行，我会自动识别意图。\n"
        "例如你可以这样问：\n"
        "- NVDA 现在能买吗？\n"
        "- TSLA 要不要减仓？\n"
        "- 帮我审一下 MU 这单\n"
        "- MU 基本面怎么样？\n"
        "- NVDA 基本面和技术面一起看下\n"
        "- SPY 期权怎么做？\n"
        "- 那如果改成下周到期呢？"
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
    if cmd in {"review", "审单", "复核", "crosscheck"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_review_reply(snap), snap.symbol
    if cmd in {"counter", "反问", "复盘"}:
        snap = get_snapshot(symbol, include_llm=False)
        return True, format_counter_question_reply(snap), snap.symbol
    if cmd in {"explain", "解释", "分析"}:
        snap = get_snapshot(symbol, include_llm=True)
        return True, format_explain_reply(snap), snap.symbol
    if cmd in {"fund", "fundamental", "基本面", "财报", "估值", "机构评级"}:
        normalized = normalize_user_symbol(symbol)
        return True, format_fundamental_reply(normalized), normalized
    if cmd in {"combo", "all", "综合", "一起看", "基本面技术面"}:
        reply, used_symbol = format_tech_fund_reply(symbol)
        return True, reply, used_symbol
    if cmd in {"option", "期权", "op"}:
        reply, used_symbol = format_option_reply(symbol, preference_text=cmd)
        return True, reply, used_symbol
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
    stop_tokens = {
        "HELP",
        "BUY",
        "SELL",
        "RISK",
        "PRICE",
        "CALL",
        "PUT",
        "IV",
        "PCR",
        "ETF",
    }
    for token in candidates:
        if token not in stop_tokens:
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
    if any(
        k in text
        for k in {
            "审单",
            "审一下",
            "复核",
            "校验",
            "过一遍",
            "检查这单",
            "cross check",
            "crosscheck",
        }
    ):
        return "review"
    if any(k in text for k in {"风控", "风险", "止损", "失效"}):
        return "risk"
    if any(
        k in text
        for k in {
            "期权",
            "option",
            "call",
            "put",
            "iv",
            "pcr",
            "到期",
            "行权价",
            "delta",
            "gamma",
            "单腿",
            "看涨",
            "看跌",
        }
    ):
        return "option"
    if any(k in text for k in {"解释", "逻辑", "为什么", "分析"}):
        return "explain"
    if any(
        k in text
        for k in {
            "基本面和技术面",
            "技术面和基本面",
            "一起看",
            "综合看",
            "合并看",
            "全看",
        }
    ):
        return "combo"
    if any(k in text for k in {"基本面", "财报", "估值", "机构评级", "目标价", "roe", "eps"}):
        return "fund"
    if any(k in text for k in {"技术面", "趋势", "形态", "均线", "支撑", "压力位", "走势"}):
        return "advice"
    if any(k in text for k in {"反问", "我该怎么做", "怎么操作", "执行计划"}):
        return "counter"
    if any(k in text for k in {"建议", "怎么看", "怎么样"}):
        return "advice"
    return None


def _needs_combo_reply(raw: str) -> bool:
    text = raw.lower()
    has_fund = any(k in text for k in {"基本面", "财报", "估值", "机构评级", "目标价", "roe", "eps"})
    has_tech = any(k in text for k in {"技术面", "走势", "趋势", "形态", "均线", "支撑", "压力位", "信号"})
    return has_fund and has_tech


def _smalltalk_reply(raw: str) -> Optional[str]:
    text = raw.strip().lower()
    if any(k in text for k in {"在吗", "在不在", "你好", "hi", "hello"}):
        return "我在，随时可以问我盘中问题。比如：NVDA 现在能买吗？"
    if any(k in text for k in {"谢谢", "thank", "thx"}):
        return "不客气，我们继续盯盘就好。"
    return None


def route_text(text: str, default_symbol: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    raw = text.strip()
    if not raw:
        return False, "", None

    if raw in {"帮助", "help", "/help", "指令"}:
        return True, _help_text(), None

    # Backward-compatible explicit mode; natural language remains first-class.
    normalized = raw.replace("：", ":")
    match = re.match(r"^([^\s:]+)\s*(?::|\s)\s*([A-Za-z0-9.]+)$", normalized)
    if match:
        cmd = match.group(1).lower()
        symbol = match.group(2)
        handled, reply, used_symbol = _dispatch_command(cmd, symbol)
        return handled, reply, used_symbol if handled else None

    # Natural language mode
    if _needs_combo_reply(raw):
        symbol_guess = _extract_symbol_from_text(raw) or default_symbol
        if not symbol_guess:
            return True, "你要我合并看基本面+技术面没问题，给我一个标的就行（例如 NVDA/TSLA/SPY）。", None
        reply, used_symbol = format_tech_fund_reply(symbol_guess, default_symbol=default_symbol)
        return True, reply, used_symbol

    intent = _infer_intent(raw)
    if intent == "help":
        return True, _help_text(), None
    if intent is None:
        talk = _smalltalk_reply(raw)
        if talk:
            return True, talk, default_symbol

        symbol_guess = _extract_symbol_from_text(raw) or default_symbol
        if symbol_guess:
            snap = get_snapshot(symbol_guess, include_llm=False)
            return True, format_advice_reply(snap), snap.symbol
        return (
            True,
            (
                "没问题，我们就按自然语言来聊。\n"
                "你可以直接这样问：\n"
                "- NVDA 现在能买吗？\n"
                "- TSLA 要不要减仓？\n"
                "- MU 基本面怎么样？\n"
                "- SPY 期权怎么做？"
            ),
            None,
        )

    symbol = _extract_symbol_from_text(raw) or default_symbol
    if not symbol:
        return (
            True,
            "我理解你的意思了，但还缺一个标的。比如你可以说：`NVDA`、`TSLA`、`SPY`。",
            None,
        )

    if intent == "option":
        horizon_hint = "next_week" if any(k in raw for k in {"下周", "next week", "nextweek"}) else None
        reply, used_symbol = format_option_reply(
            symbol,
            default_symbol=default_symbol,
            horizon_hint=horizon_hint,
            preference_text=raw,
        )
        return True, reply, used_symbol or normalize_user_symbol(symbol)

    handled, reply, used_symbol = _dispatch_command(intent, symbol)
    return handled, reply, used_symbol if handled else None


def route_review_text(text: str, default_symbol: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    raw = text.strip()
    if not raw:
        return False, "", None

    normalized = raw.replace("：", ":")
    match = re.match(r"^([^\s:]+)\s*(?::|\s)\s*([A-Za-z0-9.]+)$", normalized)
    if match:
        cmd = match.group(1).lower()
        symbol = match.group(2)
        if cmd in {"review", "审单", "复核", "crosscheck", "cross-check"}:
            snap = get_snapshot(symbol, include_llm=False)
            return True, format_review_reply(snap), snap.symbol
        return False, "", None

    intent = _infer_intent(raw)
    if intent != "review":
        return False, "", None

    symbol = _extract_symbol_from_text(raw) or default_symbol
    if not symbol:
        return True, "请告诉我要审哪只标的，例如：审单 NVDA。", None

    snap = get_snapshot(symbol, include_llm=False)
    return True, format_review_reply(snap), snap.symbol


def route_command(text: str) -> Tuple[bool, str]:
    handled, reply, _ = route_text(text, default_symbol=None)
    return handled, reply
