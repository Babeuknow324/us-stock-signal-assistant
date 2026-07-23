from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class FundamentalSnapshot:
    available: bool
    revenue_yoy: Optional[float]
    net_profit_yoy: Optional[float]
    eps_yoy: Optional[float]
    pe: Optional[float]
    pe_industry_median: Optional[float]
    analyst_recommend: Optional[str]
    target_upside_pct: Optional[float]
    notes: list[str]
    error: Optional[str]


def _resolve_longbridge_bin() -> str:
    env_bin = os.getenv("LONGBRIDGE_CLI_PATH", "").strip()
    if env_bin:
        return env_bin
    win_default = r"C:\Users\adrian.sun\AppData\Local\Programs\longbridge\longbridge.exe"
    if os.path.exists(win_default):
        return win_default
    return "longbridge"


def _run_longbridge_json(args: list[str]) -> dict[str, Any]:
    cmd = [_resolve_longbridge_bin(), *args, "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or f"command failed: {' '.join(cmd)}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from Longbridge CLI: {exc}") from exc


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_longbridge_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if "." not in raw:
        return raw
    left, right = raw.split(".", 1)
    if left in {"US", "HK", "CN", "SG"}:
        return f"{right}.{left}"
    return raw


def fetch_fundamental_snapshot(symbol: str) -> FundamentalSnapshot:
    notes: list[str] = []
    query_symbol = _to_longbridge_symbol(symbol)
    try:
        val = _run_longbridge_json(["valuation", query_symbol])
        fr = _run_longbridge_json(["financial-report", query_symbol, "--latest"])
        ir = _run_longbridge_json(["institution-rating", query_symbol])
    except Exception as exc:
        return FundamentalSnapshot(
            available=False,
            revenue_yoy=None,
            net_profit_yoy=None,
            eps_yoy=None,
            pe=None,
            pe_industry_median=None,
            analyst_recommend=None,
            target_upside_pct=None,
            notes=[],
            error=str(exc),
        )

    pe = _to_float(val.get("overview", {}).get("metrics", {}).get("pe", {}).get("metric", "").replace("x", ""))
    pe_median = _to_float(val.get("overview", {}).get("metrics", {}).get("pe", {}).get("industry_median"))

    indicators = fr.get("indicators", [])
    rev_yoy = None
    np_yoy = None
    eps_yoy = None
    for item in indicators:
        key = str(item.get("field_name", ""))
        yoy = _to_float(item.get("yoy"))
        if key == "operating_revenue":
            rev_yoy = yoy
        elif key == "net_profit":
            np_yoy = yoy
        elif key == "eps":
            eps_yoy = yoy

    instratings = ir.get("instratings") or {}
    analyst = ir.get("analyst") or {}
    recommend = (
        instratings.get("recommend")
        or (analyst.get("evaluate") or {}).get("recommend")
    )
    target = _to_float(instratings.get("target"))
    prev_close = _to_float((analyst.get("target") or {}).get("prev_close"))
    target_upside = None
    if target is not None and prev_close and prev_close > 0:
        target_upside = (target / prev_close - 1.0) * 100.0

    if rev_yoy is not None:
        notes.append(f"营收同比 {rev_yoy * 100:.1f}%")
    if np_yoy is not None:
        notes.append(f"净利润同比 {np_yoy * 100:.1f}%")
    if pe is not None and pe_median is not None:
        notes.append(f"PE {pe:.1f} vs 行业中位 {pe_median:.1f}")
    if target_upside is not None:
        notes.append(f"机构目标隐含空间 {target_upside:.1f}%")

    return FundamentalSnapshot(
        available=True,
        revenue_yoy=rev_yoy,
        net_profit_yoy=np_yoy,
        eps_yoy=eps_yoy,
        pe=pe,
        pe_industry_median=pe_median,
        analyst_recommend=str(recommend).lower() if recommend else None,
        target_upside_pct=target_upside,
        notes=notes,
        error=None,
    )
