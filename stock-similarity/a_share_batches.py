#!/usr/bin/env python3
"""Create bounded A-share ticker batches from the versioned repository universe."""

from __future__ import annotations

import json

from pathlib import Path


BATCH_SIZE = 200


def main() -> None:
    universe = Path(__file__).with_name("a-share-universe.txt")
    codes = [line.strip() for line in universe.read_text(encoding="utf-8").splitlines() if line.strip()]
    batches = [codes[index : index + BATCH_SIZE] for index in range(0, len(codes), BATCH_SIZE)]
    print(json.dumps({"include": [{"index": index, "tickers": ",".join(batch)} for index, batch in enumerate(batches)]}))


if __name__ == "__main__":
    main()
