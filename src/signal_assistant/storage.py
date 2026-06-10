from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .signal_engine import SignalResult


class SignalStorage:
    def __init__(self, sqlite_path: str) -> None:
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path.as_posix())
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                price REAL NOT NULL,
                risk_level TEXT NOT NULL,
                explanation TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT
            )
            """
        )
        self.conn.commit()

    def record_signal(self, signal: SignalResult) -> None:
        payload = signal.to_dict()
        self.conn.execute(
            """
            INSERT INTO signal_history (
                timestamp, symbol, timeframe, signal_type, price, risk_level, explanation, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["timestamp"],
                payload["symbol"],
                payload["timeframe"],
                payload["signal_type"],
                payload["price"],
                payload["risk_level"],
                payload["explanation"],
                json.dumps(payload, ensure_ascii=True),
            ),
        )
        self.conn.commit()

    def record_notification(
        self, signal: SignalResult, status: str, error_message: Optional[str] = None
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO notification_logs (
                timestamp, symbol, timeframe, signal_type, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                signal.symbol,
                signal.timeframe,
                signal.signal_type,
                status,
                error_message,
            ),
        )
        self.conn.commit()

    def should_suppress(
        self,
        symbol: str,
        signal_type: str,
        latest_price: float,
        cooldown_minutes: int,
        duplicate_price_tolerance_pct: float,
    ) -> bool:
        row = self.conn.execute(
            """
            SELECT timestamp, price
            FROM signal_history
            WHERE symbol = ? AND signal_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol, signal_type),
        ).fetchone()

        if row is None:
            return False

        last_ts = datetime.fromisoformat(row["timestamp"])
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)

        if datetime.now(timezone.utc) - last_ts < timedelta(minutes=cooldown_minutes):
            return True

        previous_price = float(row["price"])
        if previous_price <= 0:
            return False

        delta = abs(latest_price - previous_price) / previous_price
        return delta < duplicate_price_tolerance_pct

    def get_daily_signal_summary(
        self, start_utc_iso: str, end_utc_iso: str, top_n: int
    ) -> Dict[str, object]:
        total_count = self.conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (start_utc_iso, end_utc_iso),
        ).fetchone()["cnt"]

        avg_quality_row = self.conn.execute(
            """
            SELECT AVG(CAST(json_extract(payload_json, '$.quality_score') AS REAL)) AS avg_quality
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (start_utc_iso, end_utc_iso),
        ).fetchone()
        avg_quality = float(avg_quality_row["avg_quality"]) if avg_quality_row["avg_quality"] is not None else 0.0

        by_type_rows = self.conn.execute(
            """
            SELECT signal_type, COUNT(*) AS cnt
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY signal_type
            ORDER BY cnt DESC
            """,
            (start_utc_iso, end_utc_iso),
        ).fetchall()
        by_type: List[Dict[str, object]] = [
            {"signal_type": row["signal_type"], "count": int(row["cnt"])} for row in by_type_rows
        ]

        top_symbols_rows = self.conn.execute(
            """
            SELECT
                symbol,
                COUNT(*) AS cnt,
                AVG(CAST(json_extract(payload_json, '$.quality_score') AS REAL)) AS avg_quality
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY symbol
            ORDER BY cnt DESC, avg_quality DESC
            LIMIT ?
            """,
            (start_utc_iso, end_utc_iso, top_n),
        ).fetchall()
        top_symbols: List[Dict[str, object]] = [
            {
                "symbol": row["symbol"],
                "count": int(row["cnt"]),
                "avg_quality": round(float(row["avg_quality"] or 0.0), 1),
            }
            for row in top_symbols_rows
        ]

        return {
            "total_count": int(total_count),
            "avg_quality": round(avg_quality, 1),
            "by_type": by_type,
            "top_symbols": top_symbols,
        }

    def get_recent_symbol_signal(self, symbol: str, lookback_minutes: int) -> Optional[Dict[str, object]]:
        row = self.conn.execute(
            """
            SELECT timestamp, signal_type, price, payload_json
            FROM signal_history
            WHERE symbol = ? AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                symbol,
                (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).isoformat(),
            ),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return {
            "timestamp": row["timestamp"],
            "signal_type": row["signal_type"],
            "price": float(row["price"]),
            "quality_score": int(payload.get("quality_score", 0)),
            "priority_tier": str(payload.get("priority_tier", "C")),
        }

    def get_weekly_signal_summary(
        self, start_utc_iso: str, end_utc_iso: str, top_n: int
    ) -> Dict[str, object]:
        total_count = self.conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (start_utc_iso, end_utc_iso),
        ).fetchone()["cnt"]

        avg_quality_row = self.conn.execute(
            """
            SELECT AVG(CAST(json_extract(payload_json, '$.quality_score') AS REAL)) AS avg_quality
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (start_utc_iso, end_utc_iso),
        ).fetchone()
        avg_quality = float(avg_quality_row["avg_quality"]) if avg_quality_row["avg_quality"] is not None else 0.0

        by_tier_rows = self.conn.execute(
            """
            SELECT json_extract(payload_json, '$.priority_tier') AS tier, COUNT(*) AS cnt
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY tier
            ORDER BY cnt DESC
            """,
            (start_utc_iso, end_utc_iso),
        ).fetchall()
        by_tier = [{"tier": str(r["tier"]), "count": int(r["cnt"])} for r in by_tier_rows]

        top_symbols_rows = self.conn.execute(
            """
            SELECT
                symbol,
                COUNT(*) AS cnt,
                AVG(CAST(json_extract(payload_json, '$.quality_score') AS REAL)) AS avg_quality
            FROM signal_history
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY symbol
            ORDER BY cnt DESC, avg_quality DESC
            LIMIT ?
            """,
            (start_utc_iso, end_utc_iso, top_n),
        ).fetchall()
        top_symbols = [
            {
                "symbol": row["symbol"],
                "count": int(row["cnt"]),
                "avg_quality": round(float(row["avg_quality"] or 0.0), 1),
            }
            for row in top_symbols_rows
        ]
        return {
            "total_count": int(total_count),
            "avg_quality": round(avg_quality, 1),
            "by_tier": by_tier,
            "top_symbols": top_symbols,
        }

    def close(self) -> None:
        self.conn.close()
