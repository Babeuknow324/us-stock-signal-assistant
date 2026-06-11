from __future__ import annotations

import logging
from typing import Optional, Tuple

import requests

from .config import Settings
from .labels import label_evidence_strength, label_risk_level, label_signal_type
from .signal_engine import SignalResult

LOGGER = logging.getLogger(__name__)
FEISHU_KEYWORD_PREFIX = "[signal-assistant]"


def _format_signal_message(signal: SignalResult) -> str:
    signal_label = label_signal_type(signal.signal_type)
    risk_label = label_risk_level(signal.risk_level)
    evidence_label = label_evidence_strength(signal.rule_evidence_strength)
    base = (
        f"{signal.symbol} | {signal_label} | {signal.timeframe}\n"
        f"当前价格: {signal.price}\n"
        f"参考入场: {signal.entry_min} - {signal.entry_max}\n"
        f"失效位: {signal.invalidation}\n"
        f"最近阻力: {signal.resistance}\n"
        f"风险等级: {risk_label}\n"
        f"ATR%: {signal.atr_pct}\n"
        f"优先级: {signal.priority_tier}级\n"
        f"规则质量: {signal.quality_score}/100（证据强度{evidence_label}）\n"
        f"规则说明: {signal.explanation}\n"
    )
    llm = signal.llm_analysis or {}
    if llm:
        key_levels = ", ".join(llm.get("key_levels", []))
        ai_block = (
            "\nAI 解读:\n"
            f"- 总结: {llm.get('summary', '')}\n"
            f"- 多头逻辑: {llm.get('bull_case', '')}\n"
            f"- 空头逻辑: {llm.get('bear_case', '')}\n"
            f"- 关键位: {key_levels}\n"
            f"- 证据强度: {label_evidence_strength(llm.get('evidence_strength', 'medium'))}\n"
            f"- 置信度: {llm.get('confidence', 50)}/100\n"
            f"- 失效条件: {llm.get('failure_condition', '')}\n"
            f"- 下一步观察: {llm.get('next_check', '')}\n"
            f"- 风险提示: {llm.get('risk_note', '')}\n"
        )
        base += ai_block
    return base + "仅供参考，不构成投资建议。"


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.enable_telegram
        self._bot_token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id

    def send(self, signal: SignalResult) -> Tuple[bool, Optional[str]]:
        if not self._enabled:
            LOGGER.info("Telegram disabled. Signal not pushed: %s %s", signal.symbol, signal.signal_type)
            return True, None

        if not self._bot_token or not self._chat_id:
            return False, "Telegram credentials are missing"

        message = _format_signal_message(signal)
        return self.send_text(message)

    def send_text(self, message: str) -> Tuple[bool, Optional[str]]:
        if not self._enabled:
            LOGGER.info("Telegram disabled. Text not pushed.")
            return True, None

        if not self._bot_token or not self._chat_id:
            return False, "Telegram credentials are missing"

        endpoint = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        try:
            response = requests.post(
                endpoint,
                json={"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            response.raise_for_status()
            return True, None
        except requests.RequestException as exc:
            return False, str(exc)


class FeishuNotifier:
    def __init__(self, settings: Settings) -> None:
        self._webhook_url = settings.feishu_webhook_url

    def send(self, signal: SignalResult) -> Tuple[bool, Optional[str]]:
        return self.send_text(_format_signal_message(signal))

    def send_text(self, message: str) -> Tuple[bool, Optional[str]]:
        if not self._webhook_url:
            return False, "FEISHU_WEBHOOK_URL is missing"
        # Add a stable keyword prefix to satisfy Feishu keyword-based bot protection.
        message_with_keyword = message
        if FEISHU_KEYWORD_PREFIX.lower() not in message.lower():
            message_with_keyword = f"{FEISHU_KEYWORD_PREFIX} {message}"
        try:
            response = requests.post(
                self._webhook_url,
                json={"msg_type": "text", "content": {"text": message_with_keyword}},
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code", 0) != 0:
                return False, str(body)
            return True, None
        except requests.RequestException as exc:
            return False, str(exc)
        except ValueError as exc:
            return False, f"Invalid Feishu response: {exc}"


class SignalNotifier:
    def __init__(self, settings: Settings) -> None:
        channel = settings.notifier_channel
        if channel == "feishu":
            self._delegate = FeishuNotifier(settings)
            self._channel = "feishu"
        else:
            self._delegate = TelegramNotifier(settings)
            self._channel = "telegram"
        LOGGER.info("Notifier channel: %s", self._channel)

    def send(self, signal: SignalResult) -> Tuple[bool, Optional[str]]:
        return self._delegate.send(signal)

    def send_text(self, message: str) -> Tuple[bool, Optional[str]]:
        return self._delegate.send_text(message)
