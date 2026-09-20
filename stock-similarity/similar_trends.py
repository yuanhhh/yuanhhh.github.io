#!/usr/bin/env python3
"""GitHub Actions worker for the static historical stock-similarity page."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


EPSILON = 1e-12
WEIGHTS = {"price": 0.35, "ma": 0.25, "volume": 0.15, "indicator": 0.25}
REQUEST_ID_PATTERN = re.compile(r"^[a-z0-9-]{16,80}$")
TICKER_PATTERN = re.compile(r"^[A-Z0-9.^=-]{1,24}$")


@dataclass(frozen=True)
class Match:
    ticker: str
    window_start: str
    window_end: str
    similarity_score: float
    price_shape_score: float
    ma_shape_score: float
    volume_score: float
    indicator_score: float
    future_return_5d_pct: float | None
    future_return_20d_pct: float | None


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--threshold", required=True, type=float)
    parser.add_argument("--mode", required=True, choices=("recent", "historical"))
    parser.add_argument("--provider", choices=("yahoo", "a-share"), default="yahoo")
    parser.add_argument("--partial", action="store_true", help="Mark output as a batch result for workflow merging")
    parser.add_argument("--top", required=True, type=int)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.ticker = args.ticker.upper()
    if args.start > args.end:
        parser.error("Start date must not be after end date")
    if not 0 <= args.threshold <= 100 or not 1 <= args.top <= 100:
        parser.error("Threshold must be 0-100 and top must be 1-100")
    if not REQUEST_ID_PATTERN.fullmatch(args.request_id):
        parser.error("Request ID must contain only lowercase letters, digits, and hyphens")
    if not TICKER_PATTERN.fullmatch(args.ticker):
        parser.error("Target ticker contains unsupported characters")
    if args.provider == "a-share" and (args.mode != "recent" or not re.fullmatch(r"\d{6}(?:\.(?:SS|SZ|BJ))?", args.ticker)):
        parser.error("A-share provider supports six-digit symbols in recent mode only")
    return args


def history(ticker: str, start: date, end: date, provider: str) -> pd.DataFrame:
    if provider == "a-share":
        ticker = yahoo_a_share_ticker(ticker)

    data = yf.Ticker(ticker).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=True,
        actions=False,
        timeout=30,
        raise_errors=False,
    )
    if data.empty:
        raise ValueError("No daily OHLCV data returned")
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    return data[["Open", "High", "Low", "Close", "Volume"]].dropna()


def yahoo_a_share_ticker(ticker: str) -> str:
    if "." in ticker:
        return ticker
    if ticker.startswith(("6", "9")):
        return f"{ticker}.SS"
    if ticker.startswith(("0", "2", "3")):
        return f"{ticker}.SZ"
    if ticker.startswith(("4", "8")):
        return f"{ticker}.BJ"
    raise ValueError("Unable to determine the A-share exchange suffix")


def indicators(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    close = result["Close"]
    for period in (5, 10, 20, 60):
        result[f"MA{period}"] = close.rolling(period).mean()
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    result["MACD"] = fast - slow
    result["MACD_SIGNAL"] = result["MACD"].ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = (-delta.clip(upper=0)).rolling(14).mean()
    result["RSI"] = 100 - 100 / (1 + gains / (losses + EPSILON))
    previous_close = close.shift(1)
    ranges = pd.concat(
        [result["High"] - result["Low"], (result["High"] - previous_close).abs(), (result["Low"] - previous_close).abs()],
        axis=1,
    )
    result["ATR"] = ranges.max(axis=1).rolling(14).mean()
    result["VOLUME_MA20"] = result["Volume"].rolling(20).mean()
    return result


def normalized(frame: pd.DataFrame) -> np.ndarray:
    values = frame.to_numpy(dtype=float)
    deviations = values.std(axis=0, keepdims=True)
    return (values - values.mean(axis=0, keepdims=True)) / np.where(deviations < EPSILON, 1, deviations)


def features(window: pd.DataFrame) -> dict[str, np.ndarray]:
    close = window["Close"]
    price = pd.DataFrame({
        "open_return": window["Open"].pct_change().fillna(0),
        "high_return": window["High"].pct_change().fillna(0),
        "low_return": window["Low"].pct_change().fillna(0),
        "close_return": close.pct_change().fillna(0),
        "body": (window["Close"] - window["Open"]) / close,
        "upper_shadow": (window["High"] - window[["Open", "Close"]].max(axis=1)) / close,
        "lower_shadow": (window[["Open", "Close"]].min(axis=1) - window["Low"]) / close,
        "range": (window["High"] - window["Low"]) / close,
    })
    return {
        "price": normalized(price),
        "ma": normalized(pd.DataFrame({f"MA{period}": window[f"MA{period}"] / close - 1 for period in (5, 10, 20, 60)})),
        "volume": normalized(pd.DataFrame({
            "log_volume_change": np.log1p(window["Volume"]).diff().fillna(0),
            "volume_ratio": window["Volume"] / (window["VOLUME_MA20"] + EPSILON),
        })),
        "indicator": normalized(pd.DataFrame({
            "macd_relative": window["MACD"] / close,
            "macd_signal_relative": window["MACD_SIGNAL"] / close,
            "rsi": window["RSI"] / 100,
            "atr_relative": window["ATR"] / close,
        })),
    }


def similarity(left: np.ndarray, right: np.ndarray) -> float:
    left = left.reshape(-1)
    right = right.reshape(-1)
    norm = np.linalg.norm(left) * np.linalg.norm(right)
    cosine = 0.0 if norm < EPSILON else (np.dot(left, right) / norm + 1) / 2
    correlation = np.corrcoef(left, right)[0, 1]
    correlation = 0.0 if not np.isfinite(correlation) else (correlation + 1) / 2
    rmse_score = 1 / (1 + np.sqrt(np.mean((left - right) ** 2)))
    return 0.4 * cosine + 0.4 * correlation + 0.2 * rmse_score


def score(target: dict[str, np.ndarray], candidate: dict[str, np.ndarray]) -> tuple[float, dict[str, float]]:
    components = {name: similarity(target[name], candidate[name]) * 100 for name in WEIGHTS}
    return sum(WEIGHTS[name] * components[name] for name in WEIGHTS), components


def forward_return(data: pd.DataFrame, end_index: int, days: int) -> float | None:
    if end_index + days >= len(data):
        return None
    return round((data["Close"].iloc[end_index + days] / data["Close"].iloc[end_index] - 1) * 100, 2)


def match(ticker: str, data: pd.DataFrame, target: dict[str, np.ndarray], window_size: int, end_index: int) -> Match:
    window = data.iloc[end_index - window_size + 1 : end_index + 1]
    total, components = score(target, features(window))
    return Match(
        ticker=ticker,
        window_start=window.index[0].date().isoformat(),
        window_end=window.index[-1].date().isoformat(),
        similarity_score=round(total, 2),
        price_shape_score=round(components["price"], 2),
        ma_shape_score=round(components["ma"], 2),
        volume_score=round(components["volume"], 2),
        indicator_score=round(components["indicator"], 2),
        future_return_5d_pct=forward_return(data, end_index, 5),
        future_return_20d_pct=forward_return(data, end_index, 20),
    )


def main() -> int:
    args = arguments()
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        tickers = list(dict.fromkeys(item.strip().upper() for item in args.universe.split(",") if item.strip()))
        if not tickers:
            raise ValueError("Ticker universe is empty")
        invalid_tickers = [ticker for ticker in tickers if not TICKER_PATTERN.fullmatch(ticker)]
        if invalid_tickers:
            raise ValueError(f"Unsupported ticker format: {', '.join(invalid_tickers)}")
        if args.provider == "yahoo" and len(tickers) > 50:
            raise ValueError("Ticker universe is limited to 50 symbols")
        target_data = indicators(history(args.ticker, args.start - timedelta(days=120), args.end, args.provider))
        target_window = target_data.loc[args.start.isoformat() : args.end.isoformat()].dropna()
        if target_window.empty:
            raise ValueError("The target dates do not contain sufficient complete trading-day indicator data")
        target_features = features(target_window)
        window_size = len(target_window)
        target_start = target_window.index[0].date().isoformat()
        target_end = target_window.index[-1].date().isoformat()
        today = pd.Timestamp.today().normalize().date()
        search_start = (
            (pd.Timestamp(today) - pd.Timedelta(days=180)).date()
            if args.mode == "recent"
            else (target_window.index[-1] - pd.DateOffset(years=8) - pd.Timedelta(days=120)).date()
        )
        matches: list[Match] = []
        skipped: list[str] = []

        for ticker in tickers:
            try:
                data = indicators(history(ticker, search_start, today, args.provider)).dropna()
                if len(data) < window_size:
                    skipped.append(f"{ticker}: insufficient history")
                    continue
                end_indices = [len(data) - 1] if args.mode == "recent" else range(window_size - 1, len(data))
                for end_index in end_indices:
                    candidate = match(ticker, data, target_features, window_size, end_index)
                    if (
                        ticker == args.ticker.upper()
                        and candidate.window_start == target_start
                        and candidate.window_end == target_end
                    ):
                        continue
                    if candidate.similarity_score >= args.threshold:
                        matches.append(candidate)
            except Exception as error:
                skipped.append(f"{ticker}: {error}")

        matches.sort(key=lambda item: item.similarity_score, reverse=True)
        result = {
            "status": "partial" if args.partial else "completed",
            "request_id": args.request_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "query": {
                "ticker": args.ticker.upper(),
                "start": target_window.index[0].date().isoformat(),
                "end": target_window.index[-1].date().isoformat(),
                "trading_days": window_size,
                "threshold": args.threshold,
                "mode": args.mode,
                "provider": args.provider,
                "top": args.top,
                "universe_size": len(tickers),
            },
            "matches": [asdict(item) for item in matches[: args.top]],
            "total_matches": len(matches),
            "skipped": skipped,
        }
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception as error:
        output.write_text(json.dumps({
            "status": "error",
            "request_id": args.request_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "message": str(error),
            "matches": [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
