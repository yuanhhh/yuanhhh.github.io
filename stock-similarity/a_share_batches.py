#!/usr/bin/env python3
"""Create bounded A-share ticker batches for a GitHub Actions matrix."""

from __future__ import annotations

import json

import akshare as ak


BATCH_SIZE = 200


def main() -> None:
    stocks = ak.stock_info_a_code_name()
    codes = sorted({str(code).zfill(6) for code in stocks["code"] if str(code).isdigit() and len(str(code)) <= 6})
    batches = [codes[index : index + BATCH_SIZE] for index in range(0, len(codes), BATCH_SIZE)]
    print(json.dumps({"include": [{"index": index, "tickers": ",".join(batch)} for index, batch in enumerate(batches)]}))


if __name__ == "__main__":
    main()
