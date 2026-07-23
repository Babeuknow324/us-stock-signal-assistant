from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from .backtest import BacktestResult, TradeRecord


@dataclass(frozen=True)
class BitableSyncResult:
    table_id: str
    created_table: bool
    written_count: int
    skipped_count: int


class FeishuBitableClient:
    def __init__(self, app_id: str, app_secret: str, app_token: str) -> None:
        self._app_id = app_id.strip()
        self._app_secret = app_secret.strip()
        self._app_token = app_token.strip()
        self._tenant_token = ""
        self._token_expire_at = 0.0
        if not self._app_id or not self._app_secret or not self._app_token:
            raise ValueError("Missing app_id/app_secret/app_token for Feishu Bitable client")

    def _get_tenant_token(self) -> str:
        now = time.time()
        if self._tenant_token and now < self._token_expire_at:
            return self._tenant_token
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self._app_id, "app_secret": self._app_secret}
        response = requests.post(url, json=payload, timeout=12)
        response.raise_for_status()
        body = response.json()
        if body.get("code", -1) != 0:
            raise RuntimeError(f"Feishu token request failed: {body}")
        self._tenant_token = str(body["tenant_access_token"])
        expire_sec = int(body.get("expire", 7200))
        self._token_expire_at = now + max(60, expire_sec - 120)
        return self._tenant_token

    def _request(self, method: str, url: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        token = self._get_tenant_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        response = requests.request(method, url, headers=headers, json=payload, timeout=15)
        body_text = response.text
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            raise RuntimeError(f"Feishu API HTTP {response.status_code}: {body or body_text}")
        if body.get("code", -1) != 0:
            raise RuntimeError(f"Feishu API failed: {body}")
        return body

    def _list_tables(self) -> list[dict[str, Any]]:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self._app_token}/tables?page_size=200"
        body = self._request("GET", url)
        return list(body.get("data", {}).get("items", []))

    def _list_records(self, table_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            token_q = f"&page_token={page_token}" if page_token else ""
            url = (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self._app_token}/tables/"
                f"{table_id}/records?page_size=500{token_q}"
            )
            body = self._request("GET", url)
            data = body.get("data", {})
            items.extend(list(data.get("items", [])))
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token", "")).strip()
            if not page_token:
                break
        return items

    def _existing_trade_ids(self, table_id: str) -> set[str]:
        trade_ids: set[str] = set()
        for row in self._list_records(table_id):
            fields = row.get("fields", {}) or {}
            tid = str(fields.get("TradeID", "")).strip()
            if tid:
                trade_ids.add(tid)
        return trade_ids

    def _create_table(self, table_name: str) -> str:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self._app_token}/tables"
        payload = {
            "table": {
                "name": table_name,
                "default_view_name": "交易记录",
                "fields": [
                    {"field_name": "TradeID", "type": 1},
                    {"field_name": "Symbol", "type": 1},
                    {"field_name": "EntryTime", "type": 1},
                    {"field_name": "ExitTime", "type": 1},
                    {"field_name": "EntryPrice", "type": 1},
                    {"field_name": "ExitPrice", "type": 1},
                    {"field_name": "ReturnPct", "type": 1},
                    {"field_name": "HoldBars", "type": 1},
                    {"field_name": "ExitReason", "type": 1},
                    {"field_name": "EntrySignal", "type": 1},
                    {"field_name": "ExecTF", "type": 1},
                    {"field_name": "TrendTF", "type": 1},
                    {"field_name": "ConfirmTFs", "type": 1},
                ],
            }
        }
        body = self._request("POST", url, payload)
        return str(body.get("data", {}).get("table_id", ""))

    def ensure_table(self, table_name: str) -> tuple[str, bool]:
        for item in self._list_tables():
            if str(item.get("name", "")).strip() == table_name:
                return str(item.get("table_id", "")), False
        table_id = self._create_table(table_name)
        if not table_id:
            raise RuntimeError("Failed to create Feishu Bitable table: empty table_id")
        return table_id, True

    def append_trade(self, table_id: str, trade: TradeRecord, exec_tf: str, trend_tf: str, confirm_tfs: str) -> None:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self._app_token}/tables/{table_id}/records"
        trade_id = f"{trade.symbol}-{trade.entry_time.isoformat()}-{trade.exit_time.isoformat()}"
        payload = {
            "fields": {
                "TradeID": trade_id,
                "Symbol": trade.symbol,
                "EntryTime": trade.entry_time.isoformat(sep=" "),
                "ExitTime": trade.exit_time.isoformat(sep=" "),
                "EntryPrice": f"{trade.entry_price:.4f}",
                "ExitPrice": f"{trade.exit_price:.4f}",
                "ReturnPct": f"{trade.return_pct:.4f}",
                "HoldBars": str(trade.hold_bars),
                "ExitReason": trade.exit_reason,
                "EntrySignal": trade.entry_signal_type,
                "ExecTF": exec_tf,
                "TrendTF": trend_tf,
                "ConfirmTFs": confirm_tfs,
            }
        }
        self._request("POST", url, payload)

    def append_backtest_to_table_id(
        self,
        table_id: str,
        result: BacktestResult,
        exec_tf: str,
        trend_tf: str,
        confirm_tfs: str,
    ) -> BitableSyncResult:
        table_id = table_id.strip()
        if not table_id:
            raise ValueError("table_id is empty")
        existing_ids = self._existing_trade_ids(table_id)
        written = 0
        skipped = 0
        for trade in result.trades:
            trade_id = f"{trade.symbol}-{trade.entry_time.isoformat()}-{trade.exit_time.isoformat()}"
            if trade_id in existing_ids:
                skipped += 1
                continue
            self.append_trade(table_id, trade, exec_tf=exec_tf, trend_tf=trend_tf, confirm_tfs=confirm_tfs)
            written += 1
            existing_ids.add(trade_id)
        return BitableSyncResult(
            table_id=table_id,
            created_table=False,
            written_count=written,
            skipped_count=skipped,
        )

    def append_backtest_result(
        self,
        table_name: str,
        result: BacktestResult,
        exec_tf: str,
        trend_tf: str,
        confirm_tfs: str,
    ) -> BitableSyncResult:
        table_id, created = self.ensure_table(table_name)
        existing_ids = self._existing_trade_ids(table_id)
        written = 0
        skipped = 0
        for trade in result.trades:
            trade_id = f"{trade.symbol}-{trade.entry_time.isoformat()}-{trade.exit_time.isoformat()}"
            if trade_id in existing_ids:
                skipped += 1
                continue
            self.append_trade(table_id, trade, exec_tf=exec_tf, trend_tf=trend_tf, confirm_tfs=confirm_tfs)
            written += 1
            existing_ids.add(trade_id)
        return BitableSyncResult(
            table_id=table_id,
            created_table=created,
            written_count=written,
            skipped_count=skipped,
        )

