from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    error: Optional[str]


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
        if expiries:
            chain_rows = _run_longbridge_json(["option", "chain", query_symbol, "--date", expiries[0]])
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
            error=str(exc),
        )
