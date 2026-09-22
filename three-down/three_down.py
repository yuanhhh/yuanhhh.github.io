#!/usr/bin/env python3
"""A-share three consecutive down-days with declining volume screener."""
import argparse
import gzip
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

WORK = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(WORK, "three_down_cache.jsonl.gz")
SINA = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData?symbol={}&scale=240&ma=no&datalen={}"
TENCENT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={},day,,{},qfq"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def symbol(code):
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def sina(code, count):
    try:
        text = requests.get(SINA.format(symbol(code), count), headers=HEADERS, timeout=12).text
        data = json.loads(text[text.find("(") + 1:text.rfind(")")])
        return [{"day": x["day"], "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]), "close": float(x["close"]), "volume": float(x["volume"])} for x in data]
    except Exception:
        return None


def tencent(code, count):
    try:
        key = symbol(code)
        data = requests.get(TENCENT.format(key, count), headers=HEADERS, timeout=12).json()["data"][key]
        bars = data.get("qfqday") or data.get("day") or []
        return [{"day": x[0], "open": float(x[1]), "close": float(x[2]), "high": float(x[3]), "low": float(x[4]), "volume": float(x[5]) * 100} for x in bars if len(x) >= 6]
    except Exception:
        return None


def fetch(code, count, source):
    order = [source] if source != "auto" else ["sina", "tencent"]
    for name in order + [x for x in ("sina", "tencent") if x not in order]:
        for _ in range(2):
            rows = (sina if name == "sina" else tencent)(code, count)
            if rows:
                return rows
            time.sleep(.3 + random.random() * .2)
    return None


def cache_load():
    if not os.path.exists(CACHE):
        return {}
    try:
        with gzip.open(CACHE, "rt", encoding="utf-8") as file:
            return {row["code"]: row for line in file if (row := json.loads(line))}
    except Exception:
        return {}


def cache_save(cache):
    with gzip.open(CACHE + ".tmp", "wt", encoding="utf-8") as file:
        for row in cache.values():
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(CACHE + ".tmp", CACHE)


def universe():
    import akshare as ak
    result = []
    for code, name in zip(ak.stock_info_a_code_name()["code"], ak.stock_info_a_code_name()["name"]):
        code, name = str(code), str(name).replace(" ", "")
        if len(code) == 6 and code.startswith(("60", "68", "00", "30")) and "ST" not in name and "退" not in name:
            result.append((code, name))
    return result


def gather(items, args):
    cache, data, today = cache_load(), {}, time.strftime("%Y-%m-%d")
    pending = []
    for code, name in items:
        row = cache.get(code)
        if not args.refresh and row and row.get("date") == today and row.get("count", 0) >= args.count:
            data[code] = (name, row["rows"])
        else:
            pending.append((code, name))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, code, args.count, args.source): (code, name) for code, name in pending}
        for future in as_completed(futures):
            code, name = futures[future]
            rows = future.result()
            if rows:
                data[code] = (name, rows)
                cache[code] = {"code": code, "date": today, "count": args.count, "rows": rows}
    if pending:
        cache_save(cache)
    return data


def window(rows, index, minimum, maximum):
    if index < 1 or index + 2 >= len(rows):
        return None
    base, first, second, last = rows[index - 1:index + 3]
    if not (base["close"] > first["close"] > second["close"] > last["close"]):
        return None
    if not (first["volume"] > second["volume"] > last["volume"]):
        return None
    decline = (last["close"] / base["close"] - 1) * 100
    if not minimum <= decline <= maximum:
        return None
    shrink = (1 - last["volume"] / first["volume"]) * 100
    return {"start": first["day"], "end": last["day"], "base_close": round(base["close"], 3), "close": round(last["close"], 3), "pct1": round((first["close"] / base["close"] - 1) * 100, 2), "pct2": round((second["close"] / first["close"] - 1) * 100, 2), "pct3": round((last["close"] / second["close"] - 1) * 100, 2), "ret3": round(decline, 2), "vol_shrink": round(shrink, 1)}


def score(item, args):
    decline = (item["ret3"] - args.min_decline) / ((args.max_decline - args.min_decline) or 1)
    return round(args.w_shrink * max(0, min(1, item["vol_shrink"] / 100)) + args.w_decline * max(0, min(1, decline)), 4)


def forward(rows, index):
    entry = rows[index + 2]["close"]
    result = {"gap": None, "f1": None, "f3": None, "f5": None}
    if index + 3 < len(rows):
        result["gap"] = round((rows[index + 3]["open"] / entry - 1) * 100, 2)
    for days in (1, 3, 5):
        if index + 2 + days < len(rows):
            result[f"f{days}"] = round((rows[index + 2 + days]["close"] / entry - 1) * 100, 2)
    return result


def trade(rows, entry_index, args):
    entry, peak, active = rows[entry_index]["close"], rows[entry_index]["close"], False
    for day in range(1, args.hold + 1):
        if entry_index + day >= len(rows):
            return None
        row = rows[entry_index + day]
        active |= row["high"] >= entry * (1 + args.take / 100)
        threshold = peak * (1 - args.trail / 100) if active else entry * (1 - args.stop / 100)
        if row["low"] <= threshold and (active or args.stop > 0):
            exit_price = min(row["open"], threshold)
            return {"exit_day": row["day"], "exit_px": round(exit_price, 3), "hold": day, "reason": "回落止盈" if active else "回落止损", "ret": round((exit_price / entry - 1) * 100 - args.fee, 2)}
        peak = max(peak, row["high"])
    row = rows[entry_index + args.hold]
    return {"exit_day": row["day"], "exit_px": round(row["close"], 3), "hold": args.hold, "reason": "到期", "ret": round((row["close"] / entry - 1) * 100 - args.fee, 2)}


def run(args):
    items = universe()[:args.limit or None]
    print(f"股票池: {len(items)} 只")
    data, hits = gather(items, args), []
    for code, (name, rows) in data.items():
        indexes = [len(rows) - 3] if args.cmd == "recent" else range(1, len(rows) - 2)
        for index in indexes:
            if args.cmd != "recent" and not (rows[index]["day"] >= args.start and (not args.end or rows[index]["day"] <= args.end)):
                continue
            item = window(rows, index, args.min_decline, args.max_decline)
            if not item:
                continue
            item.update({"code": code, "name": name, "score": score(item, args)})
            if args.cmd == "recent":
                item.update({"date": rows[-1]["day"], "price": round(rows[-1]["close"], 3)})
            elif args.cmd == "hist":
                item.update(forward(rows, index))
            else:
                simulation = trade(rows, index + 2, args)
                if not simulation:
                    continue
                item.update({"signal": rows[index]["day"], "entry": item["close"], **simulation})
            hits.append(item)
    if not hits:
        print("无满足条件的结果。")
        return
    frame = pd.DataFrame(hits).sort_values(["start" if args.cmd != "sim" else "signal", "score"], ascending=[True, False])
    if args.per_day:
        key = "signal" if args.cmd == "sim" else "start"
        frame = frame.groupby(key, as_index=False).head(args.per_day)
    output = os.path.join(WORK, args.out or {"recent": "recent_hits.csv", "hist": "hist_hits.csv", "sim": "sim_trades.csv"}[args.cmd])
    frame.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"已保存 {len(frame)} 条结果: {output}")


def main():
    parser = argparse.ArgumentParser(description="三连阴缩量选股")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("recent", "hist", "sim"):
        item = sub.add_parser(name)
        item.add_argument("--start")
        item.add_argument("--end")
        item.add_argument("--top", type=int)
        item.add_argument("--workers", type=int, default=8)
        item.add_argument("--source", choices=["auto", "sina", "tencent"], default="auto")
        item.add_argument("--limit", type=int, default=0)
        item.add_argument("--refresh", action="store_true")
        item.add_argument("--out")
        item.add_argument("--min-decline", type=float, default=-6)
        item.add_argument("--max-decline", type=float, default=0)
        item.add_argument("--w-shrink", type=float, default=.5)
        item.add_argument("--w-decline", type=float, default=.4)
        item.add_argument("--per-day", type=int, default=3 if name == "sim" else 20)
        item.add_argument("--count", type=int, default=40 if name == "recent" else 700)
        if name == "sim":
            item.add_argument("--take", type=float, default=5)
            item.add_argument("--trail", type=float, default=2)
            item.add_argument("--stop", type=float, default=2)
            item.add_argument("--hold", type=int, default=5)
            item.add_argument("--fee", type=float, default=0)
    args = parser.parse_args()
    if args.cmd in ("hist", "sim") and not args.start:
        parser.error(f"{args.cmd} 模式需要 --start YYYY-MM-DD")
    if args.cmd == "sim" and not args.end:
        parser.error("sim 模式需要 --end YYYY-MM-DD")
    run(args)


if __name__ == "__main__":
    main()
