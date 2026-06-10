from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from signal_assistant.qa_service import (  # noqa: E402
    format_advice_reply,
    format_price_reply,
    get_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal assistant Q&A helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    price_parser = subparsers.add_parser("price", help="Query latest price")
    price_parser.add_argument("symbol", help="e.g. US.NVDA or HK.07709")

    advice_parser = subparsers.add_parser("advice", help="Query strategy advice now")
    advice_parser.add_argument("symbol", help="e.g. US.NVDA or HK.07709")

    args = parser.parse_args()
    symbol = str(args.symbol).strip()
    if args.command == "price":
        print(format_price_reply(get_snapshot(symbol, include_llm=False)))
    else:
        print(format_advice_reply(get_snapshot(symbol, include_llm=False)))


if __name__ == "__main__":
    main()
