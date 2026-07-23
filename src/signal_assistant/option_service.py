from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date, datetime
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class OptionSnapshot:
    available: bool
    expiry_dates: list[str]
    call_volume: Optional[int]
    put_volume: Optional[int]
    pcr: Optional[float]
    atm_strike: Optional[float]
    atm_call_iv: Optional[float]
    atm_put_iv: Optional[float]
    nearest_expiry_chain: list[dict[str, Any]]
    error: Optional[str]


@dataclass(frozen=True)
class OptionRiskPlan:
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_days: int
    position_risk_pct: float


@dataclass(frozen=True)
class OptionContractPlan:
    side: str
    expiry_date: str
    strike: float
    contract_symbol: str
    call_iv: Optional[float]
    put_iv: Optional[float]


@dataclass(frozen=True)
class OptionExecutionPlan:
    strategy_name: str
    direction: str
    contract: OptionContractPlan
    risk: OptionRiskPlan


def _resolve_longbridge_bin() -> str:
    env_bin = os.getenv("LONGBRIDGE_CLI_PATH", "").strip()
    if env_bin:
        return env_bin
    found = shutil.which("longbridge")
    if found:
        return found
    win_default = r"C:\Users\adrian.sun\AppData\Local\Programs\longbridge\longbridge.exe"
    if os.path.exists(win_default):
        return win_default
    linux_default = "/usr/local/bin/longbridge"
    if os.path.exists(linux_default):
        return linux_default
    return ""


def _to_longbridge_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if "." not in raw:
        return raw
    left, right = raw.split(".", 1)
    if left in {"US", "HK", "CN", "SG"}:
        return f"{right}.{left}"
    return raw


def _run_longbridge_json(args: list[str]) -> Any:
    bin_path = _resolve_longbridge_bin()
    if not bin_path:
        raise RuntimeError(
            "Longbridge CLI not found. Set LONGBRIDGE_CLI_PATH or install longbridge binary in runtime image."
        )
    cmd = [bin_path, *args, "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or f"command failed: {' '.join(cmd)}")
    return json.loads(stdout)


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _pick_atm_row(chain_rows: list[dict[str, Any]], spot_price: Optional[float]) -> Optional[dict[str, Any]]:
    if not chain_rows:
        return None
    if spot_price is None:
        return chain_rows[len(chain_rows) // 2]
    ranked = sorted(
        chain_rows,
        key=lambda row: abs((_to_float(row.get("strike")) or 0.0) - spot_price),
    )
    return ranked[0] if ranked else None


def _days_to_expiry(expiry_text: str) -> Optional[int]:
    try:
        expiry = datetime.strptime(expiry_text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (expiry - date.today()).days


def _pick_expiry(expiries: list[str], horizon_hint: Optional[str]) -> Optional[str]:
    if not expiries:
        return None
    target_min = 7
    target_max = 14
    if horizon_hint == "next_week":
        target_min = 3
        target_max = 9
    elif horizon_hint == "swing":
        target_min = 14
        target_max = 35

    scored: list[tuple[int, str]] = []
    for item in expiries:
        dte = _days_to_expiry(item)
        if dte is None or dte < 0:
            continue
        if target_min <= dte <= target_max:
            score = abs(dte - ((target_min + target_max) // 2))
        else:
            score = abs(dte - target_min) + 10
        scored.append((score, item))
    if not scored:
        return expiries[0]
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


def _pick_strike_row(
    chain_rows: list[dict[str, Any]],
    spot_price: float,
    side: str,
    moneyness: str,
) -> Optional[dict[str, Any]]:
    if not chain_rows:
        return None

    if moneyness == "ATM":
        return _pick_atm_row(chain_rows, spot_price)

    if side == "CALL":
        # ITM call => strike below spot; OTM call => strike above spot.
        if moneyness == "ITM":
            candidates = [row for row in chain_rows if (_to_float(row.get("strike")) or 0.0) <= spot_price]
            if not candidates:
                return _pick_atm_row(chain_rows, spot_price)
            return sorted(candidates, key=lambda r: abs((spot_price - (_to_float(r.get("strike")) or 0.0))))[0]
        candidates = [row for row in chain_rows if (_to_float(row.get("strike")) or 0.0) >= spot_price]
        if not candidates:
            return _pick_atm_row(chain_rows, spot_price)
        return sorted(candidates, key=lambda r: abs(((_to_float(r.get("strike")) or 0.0) - spot_price)))[0]

    # PUT
    if moneyness == "ITM":
        candidates = [row for row in chain_rows if (_to_float(row.get("strike")) or 0.0) >= spot_price]
        if not candidates:
            return _pick_atm_row(chain_rows, spot_price)
        return sorted(candidates, key=lambda r: abs(((_to_float(r.get("strike")) or 0.0) - spot_price)))[0]
    candidates = [row for row in chain_rows if (_to_float(row.get("strike")) or 0.0) <= spot_price]
    if not candidates:
        return _pick_atm_row(chain_rows, spot_price)
    return sorted(candidates, key=lambda r: abs((spot_price - (_to_float(r.get("strike")) or 0.0))))[0]


def _build_occ_symbol(symbol: str, expiry_date: str, side: str, strike: float) -> str:
    # OCC: UNDERLYING(<=6, padded right) + yymmdd + C/P + strike*1000 as 8-digit.
    ticker = symbol.split(".", 1)[-1].split(".", 1)[0].upper()
    if "." in symbol:
        left, right = symbol.upper().split(".", 1)
        if left in {"US", "HK", "CN", "SG"}:
            ticker = right
        else:
            ticker = left
    root = ticker[:6].ljust(6)
    dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    ymd = dt.strftime("%y%m%d")
    cp = "C" if side.upper() == "CALL" else "P"
    strike_int = int(round(strike * 1000))
    return f"{root}{ymd}{cp}{strike_int:08d}".replace(" ", "")


def fetch_option_snapshot(symbol: str, spot_price: Optional[float] = None) -> OptionSnapshot:
    query_symbol = _to_longbridge_symbol(symbol)
    try:
        expiries_raw = _run_longbridge_json(["option", "chain", query_symbol])
        expiries = [str(item.get("expiry_date", "")) for item in expiries_raw if item.get("expiry_date")]

        volume_raw = _run_longbridge_json(["option", "volume", query_symbol])
        call_vol = _to_int(volume_raw.get("c"))
        put_vol = _to_int(volume_raw.get("p"))
        pcr = None
        if call_vol and call_vol > 0 and put_vol is not None:
            pcr = put_vol / call_vol

        atm_strike = None
        atm_call_iv = None
        atm_put_iv = None
        nearest_expiry_chain: list[dict[str, Any]] = []
        if expiries:
            chain_rows = _run_longbridge_json(["option", "chain", query_symbol, "--date", expiries[0]])
            nearest_expiry_chain = list(chain_rows)
            atm_row = _pick_atm_row(chain_rows, spot_price)
            if atm_row:
                atm_strike = _to_float(atm_row.get("strike"))
                atm_call_iv = _to_float(atm_row.get("call_iv"))
                atm_put_iv = _to_float(atm_row.get("put_iv"))

        return OptionSnapshot(
            available=True,
            expiry_dates=expiries[:6],
            call_volume=call_vol,
            put_volume=put_vol,
            pcr=pcr,
            atm_strike=atm_strike,
            atm_call_iv=atm_call_iv,
            atm_put_iv=atm_put_iv,
            nearest_expiry_chain=nearest_expiry_chain,
            error=None,
        )
    except Exception as exc:
        return OptionSnapshot(
            available=False,
            expiry_dates=[],
            call_volume=None,
            put_volume=None,
            pcr=None,
            atm_strike=None,
            atm_call_iv=None,
            atm_put_iv=None,
            nearest_expiry_chain=[],
            error=str(exc),
        )


def build_single_leg_plan(
    symbol: str,
    spot_price: float,
    option_snapshot: OptionSnapshot,
    prefer_side: str,
    horizon_hint: Optional[str] = None,
    risk_style: str = "balanced",
) -> Optional[OptionExecutionPlan]:
    if not option_snapshot.available:
        return None
    side = "CALL" if prefer_side.upper().startswith("C") else "PUT"
    expiry = _pick_expiry(option_snapshot.expiry_dates, horizon_hint)
    if not expiry:
        return None
    chain_rows = option_snapshot.nearest_expiry_chain
    if not chain_rows or expiry != (option_snapshot.expiry_dates[0] if option_snapshot.expiry_dates else ""):
        query_symbol = _to_longbridge_symbol(symbol)
        chain_rows = _run_longbridge_json(["option", "chain", query_symbol, "--date", expiry])
    moneyness = "ATM" if risk_style != "aggressive" else "OTM"
    if risk_style == "conservative":
        moneyness = "ITM"
    row = _pick_strike_row(chain_rows, spot_price, side=side, moneyness=moneyness)
    if not row:
        return None
    strike = _to_float(row.get("strike"))
    if strike is None:
        return None
    contract = OptionContractPlan(
        side=side,
        expiry_date=expiry,
        strike=strike,
        contract_symbol=_build_occ_symbol(symbol, expiry, side, strike),
        call_iv=_to_float(row.get("call_iv")),
        put_iv=_to_float(row.get("put_iv")),
    )
    if risk_style == "conservative":
        risk = OptionRiskPlan(stop_loss_pct=30.0, take_profit_pct=45.0, max_hold_days=5, position_risk_pct=0.5)
    elif risk_style == "aggressive":
        risk = OptionRiskPlan(stop_loss_pct=45.0, take_profit_pct=80.0, max_hold_days=10, position_risk_pct=1.0)
    else:
        risk = OptionRiskPlan(stop_loss_pct=35.0, take_profit_pct=60.0, max_hold_days=7, position_risk_pct=0.7)
    direction = "偏多" if side == "CALL" else "偏空"
    return OptionExecutionPlan(
        strategy_name="单腿策略",
        direction=direction,
        contract=contract,
        risk=risk,
    )
