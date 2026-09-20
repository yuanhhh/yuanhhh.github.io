# Stock Similarity Search

This directory contains the GitHub Pages client and GitHub Actions worker for stock-pattern matching.

## One-Time GitHub Setup

1. In the repository settings, set **Actions > General > Workflow permissions** to **Read and write permissions**.
2. Open the GitHub Pages page at `/stock-similarity/`.
3. Create a short-lived fine-grained GitHub token for `yuanhhh/yuanhhh.github.io` with **Actions: Read and write** and **Contents: Read**. The browser keeps it only in memory and never writes it to the repository or local storage.

The form dispatches `.github/workflows/stock-similarity.yml`. The workflow downloads Yahoo Finance daily adjusted OHLCV data, calculates the composite score, and commits a per-request JSON result to `stock-similarity/results/`. The page polls that result and renders the table.

`recent` mode compares the latest available N-day window of each ticker. `historical` mode slides an N-trading-day window through up to eight years of history and includes forward 5/20 trading-day return validation.
