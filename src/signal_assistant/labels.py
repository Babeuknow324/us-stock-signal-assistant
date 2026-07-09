from __future__ import annotations

SIGNAL_TYPE_LABELS = {
    "long_setup": "偏多候选",
    "breakout_candidate": "突破候选",
    "exit_warning": "卖点提醒",
    "observation_only": "观察",
}

RISK_LEVEL_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
}

EVIDENCE_STRENGTH_LABELS = {
    "strong": "强",
    "medium": "中",
    "weak": "弱",
}


def label_signal_type(signal_type: str) -> str:
    return SIGNAL_TYPE_LABELS.get(signal_type, signal_type)


def label_risk_level(risk_level: str) -> str:
    return RISK_LEVEL_LABELS.get(risk_level, risk_level)


def label_evidence_strength(strength: str) -> str:
    return EVIDENCE_STRENGTH_LABELS.get(strength, strength)
