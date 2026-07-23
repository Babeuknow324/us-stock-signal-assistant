from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from dataclasses import replace

import pandas as pd

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from signal_assistant.backtest import format_backtest_report, run_backtest_for_symbol
from signal_assistant.config import load_settings
from signal_assistant.feishu_bitable import FeishuBitableClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Run signal strategy backtest for one symbol.")
    parser.add_argument("symbol", help="Symbol like US.NVDA / NVDA.US / SPY")
    parser.add_argument("--bars", type=int, default=800, help="Max historical bars per timeframe")
    parser.add_argument("--max-hold", type=int, default=24, help="Max hold bars before time stop")
    parser.add_argument("--exec-tf", default="", help="Override execution timeframe, e.g. 15m/1h")
    parser.add_argument("--trend-tf", default="", help="Override trend timeframe, e.g. 1h/4h")
    parser.add_argument(
        "--confirm-tfs",
        default="",
        help="Override confirm timeframes, comma-separated, e.g. 5m,1m",
    )
    parser.add_argument(
        "--save-trades",
        default="",
        help="Optional CSV path to save trade records, e.g. data/backtest_trades_nvda_15m.csv",
    )
    parser.add_argument(
        "--sync-feishu",
        action="store_true",
        help="Append each trade to Feishu Bitable table",
    )
    parser.add_argument(
        "--feishu-app-token",
        default="",
        help="Feishu Bitable app token (bascn...)",
    )
    parser.add_argument(
        "--feishu-table-name",
        default="TradeRecords",
        help="Feishu Bitable table name to write records",
    )
    parser.add_argument(
        "--feishu-table-id",
        default="",
        help="Feishu Bitable table id (tbl...) to write directly",
    )
    args = parser.parse_args()

    settings = load_settings()
    exec_tf = (args.exec_tf or "").strip()
    trend_tf = (args.trend_tf or "").strip()
    confirm_tfs = [x.strip() for x in (args.confirm_tfs or "").split(",") if x.strip()]
    if exec_tf or trend_tf or confirm_tfs:
        settings = replace(
            settings,
            execution_timeframe=exec_tf or settings.execution_timeframe,
            trend_timeframe=trend_tf or settings.trend_timeframe,
            confirm_timeframes=confirm_tfs or settings.confirm_timeframes,
        )

    result = run_backtest_for_symbol(
        symbol=args.symbol,
        settings=settings,
        max_count=max(120, args.bars),
        max_hold_bars=max(1, args.max_hold),
    )
    print(format_backtest_report(result))

    if args.save_trades:
        path = Path(args.save_trades)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time.isoformat(sep=" "),
                "exit_time": t.exit_time.isoformat(sep=" "),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "return_pct": t.return_pct,
                "hold_bars": t.hold_bars,
                "exit_reason": t.exit_reason,
                "entry_signal_type": t.entry_signal_type,
                "execution_timeframe": settings.execution_timeframe,
                "trend_timeframe": settings.trend_timeframe,
                "confirm_timeframes": ",".join(settings.confirm_timeframes),
            }
            for t in result.trades
        ]
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"Trade records saved: {path}")

    if args.sync_feishu:
        app_id = (os.getenv("FEISHU_QA_APP_ID", "") or os.getenv("FEISHU_APP_ID", "")).strip()
        app_secret = (os.getenv("FEISHU_QA_APP_SECRET", "") or os.getenv("FEISHU_APP_SECRET", "")).strip()
        app_token = (args.feishu_app_token or os.getenv("FEISHU_BITABLE_APP_TOKEN", "")).strip()
        table_id = (args.feishu_table_id or os.getenv("FEISHU_BITABLE_TABLE_ID", "")).strip()
        if not app_id or not app_secret or not app_token:
            raise RuntimeError(
                "Missing Feishu config for sync. Need app_id/app_secret/app_token "
                "(env: FEISHU_QA_APP_ID, FEISHU_QA_APP_SECRET, FEISHU_BITABLE_APP_TOKEN)."
            )
        if app_token.startswith("tbl"):
            raise RuntimeError(
                "FEISHU_BITABLE_APP_TOKEN should be bascn... (Base token), not tbl... (table id)."
            )
        client = FeishuBitableClient(app_id=app_id, app_secret=app_secret, app_token=app_token)
        if table_id:
            sync = client.append_backtest_to_table_id(
                table_id=table_id,
                result=result,
                exec_tf=settings.execution_timeframe,
                trend_tf=settings.trend_timeframe,
                confirm_tfs=",".join(settings.confirm_timeframes),
            )
        else:
            sync = client.append_backtest_result(
                table_name=args.feishu_table_name,
                result=result,
                exec_tf=settings.execution_timeframe,
                trend_tf=settings.trend_timeframe,
                confirm_tfs=",".join(settings.confirm_timeframes),
            )
        created = "yes" if sync.created_table else "no"
        print(
            "Feishu Bitable synced: "
            f"table_id={sync.table_id}, created_table={created}, rows_written={sync.written_count}, "
            f"rows_skipped={sync.skipped_count}"
        )


if __name__ == "__main__":
    main()
