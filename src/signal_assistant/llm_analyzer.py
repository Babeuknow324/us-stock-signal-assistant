from __future__ import annotations

import json
import logging
from typing import Dict, Optional, Tuple

import requests

from .config import Settings
from .signal_engine import SignalResult

LOGGER = logging.getLogger(__name__)


class LLMAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.enable_llm_analysis
        self._provider = settings.llm_provider.strip().lower()
        self._api_key = settings.llm_api_key
        self._base_url = settings.llm_base_url.rstrip("/")
        self._model = settings.llm_model
        self._timeout = settings.llm_timeout_seconds

    @property
    def enabled(self) -> bool:
        return self._enabled

    def analyze(self, signal: SignalResult) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
        if not self._enabled:
            return None, None

        if self._provider != "openai":
            return None, f"Unsupported LLM provider: {self._provider}"

        if not self._api_key:
            return None, "LLM_API_KEY is missing"

        prompt = self._build_prompt(signal)
        endpoint = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self._model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是辅助 discretionary 交易的技术分析助手。"
                        "不要给出直接的买入/卖出指令。语言简洁实用，全部用中文回复。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=self._timeout)
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            normalized = self._normalize(parsed)
            return normalized, None
        except requests.RequestException as exc:
            LOGGER.warning("LLM request failed: %s", exc)
            return None, str(exc)
        except (KeyError, ValueError, TypeError) as exc:
            LOGGER.warning("LLM response parse failed: %s", exc)
            return None, f"Invalid LLM response: {exc}"

    @staticmethod
    def _normalize(raw: Dict[str, object]) -> Dict[str, object]:
        confidence = raw.get("confidence", 50)
        try:
            confidence = max(0, min(100, int(confidence)))
        except (ValueError, TypeError):
            confidence = 50

        key_levels = raw.get("key_levels", [])
        if not isinstance(key_levels, list):
            key_levels = []

        return {
            "summary": str(raw.get("summary", "")).strip(),
            "bull_case": str(raw.get("bull_case", "")).strip(),
            "bear_case": str(raw.get("bear_case", "")).strip(),
            "key_levels": [str(x).strip() for x in key_levels if str(x).strip()],
            "risk_note": str(raw.get("risk_note", "")).strip(),
            "evidence_strength": str(raw.get("evidence_strength", "medium")).strip().lower(),
            "failure_condition": str(raw.get("failure_condition", "")).strip(),
            "next_check": str(raw.get("next_check", "")).strip(),
            "confidence": confidence,
        }

    @staticmethod
    def _build_prompt(signal: SignalResult) -> str:
        payload = signal.to_dict()
        payload_text = json.dumps(payload, ensure_ascii=True)
        return (
            "分析以下技术信号，仅返回简洁 JSON，字段包括: "
            "summary, bull_case, bear_case, key_levels (数组), risk_note, evidence_strength, "
            "failure_condition, next_check, confidence (0-100)。"
            "所有文本字段用中文，每条不超过 220 字，面向不太会看图的普通用户。\n"
            "evidence_strength 只能是: strong, medium, weak 之一。\n"
            "failure_condition 需明确说明何种情况会使该结构失效。\n"
            "next_check 应是接下来 1-3 根 K 线内值得观察的一件事。\n"
            f"信号 JSON:\n{payload_text}"
        )
