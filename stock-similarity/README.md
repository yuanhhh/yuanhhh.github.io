# Stock Similarity Search

This directory contains the GitHub Pages client and GitHub Actions worker for stock-pattern matching.

## One-Time GitHub Setup

1. In the repository settings, set **Actions > General > Workflow permissions** to **Read and write permissions**.
2. Open the GitHub Pages page at `/stock-similarity/`.
3. Create a short-lived fine-grained GitHub token for `yuanhhh/yuanhhh.github.io` with **Actions: Read and write** and **Contents: Read**. The browser keeps it only in memory and never writes it to the repository or local storage.

The form dispatches `.github/workflows/stock-similarity.yml`. The workflow downloads Yahoo Finance daily adjusted OHLCV data, calculates the composite score, and commits a per-request JSON result to `stock-similarity/results/`. The page polls that result and renders the table.

`recent` mode compares the latest available N-day window of each ticker. `historical` mode slides an N-trading-day window through up to eight years of history and includes forward 5/20 trading-day return validation.

The page supports two candidate scopes:

- **Custom universe**: accepts up to 50 comma-separated Yahoo Finance tickers and supports both modes.
- **沪深京 A 股全市场**: uses the versioned `a-share-universe.txt` list (5,565 symbols when last refreshed), divides it into batches of 200 symbols, and scans up to five batches in parallel. This scope supports `recent` mode only. The workflow maps codes to Yahoo Finance `.SS`, `.SZ`, or `.BJ` symbols, uses adjusted daily OHLCV data, then merges global Top 20 results.

The list is intentionally versioned so the Actions runner does not need to fetch an exchange listing before every search. Refresh `a-share-universe.txt` periodically from a trusted A-share listing source.
