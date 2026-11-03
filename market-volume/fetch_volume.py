#!/usr/bin/env python3
"""Fetch index volumes and group today's minute data by trading hour."""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
MARKETS = {
    "sh": ("沪市 A 股", "1.000002"),
    "sz": ("深市 A 股", "0.399107"),
    "star": ("科创板", "1.000680"),
}
PERIODS = (
    ("09:30–10:30", "09:30", "10:30"),
    ("10:30–11:30", "10:30", "11:30"),
    ("13:00–14:00", "13:00", "14:00"),
    ("14:00–15:00", "14:00", "15:00"),
)
API_ROOT = "https://push2his.eastmoney.com/api/qt/stock"
TENCENT_ROOT = "https://web.ifzq.gtimg.cn/appstock/app"
FIELDS = {"fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57", "ut": "7eea3edcaed734bea9cbfc24409ed989"}


def get_json(url, label, referer):
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except (OSError, ValueError, KeyError) as error:
            if attempt == 2:
                raise RuntimeError(f"无法获取 {label}: {error}") from error
            time.sleep(attempt + 1)


def get_data(kind, secid, **extra):
    params = {**FIELDS, "secid": secid, **extra}
    url = f"{API_ROOT}/{kind}/get?{urlencode(params)}"
    result = get_json(url, f"{secid} {kind}", "https://quote.eastmoney.com/")
    if not isinstance(result, dict) or result.get("rc") != 0 or not isinstance(result.get("data"), dict):
        raise ValueError(f"东方财富行情接口返回空数据: {secid} {kind}")
    return result["data"]


def get_tencent_data(kind, symbol):
    if kind == "day":
        path, params = "kline/kline", {"param": f"{symbol},day,,,60"}
    else:
        path, params = "minute/query", {"code": symbol}
    # Avoid a cached minute snapshot when refreshing during trading hours.
    params["_"] = int(time.time() * 1000)
    result = get_json(f"{TENCENT_ROOT}/{path}?{urlencode(params)}", f"腾讯 {symbol} {kind}", "https://gu.qq.com/")
    if not isinstance(result, dict) or result.get("code") != 0:
        raise ValueError(f"腾讯行情接口返回错误: {symbol} {kind}")
    markets = result.get("data")
    data = markets.get(symbol) if isinstance(markets, dict) else None
    if not isinstance(data, dict):
        raise ValueError(f"腾讯行情接口返回空数据: {symbol} {kind}")
    return data


def tencent_volume(value):
    value = str(value)
    if not re.fullmatch(r"\d+(?:\.0+)?", value):
        raise ValueError("腾讯成交量无效")
    return int(value.split(".")[0])  # Both index endpoints report hands.


def parse_tencent_daily(data):
    rows = data.get("day")
    if not isinstance(rows, list) or not rows:
        raise ValueError("腾讯日线数据为空")
    result = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError("腾讯日线记录无效")
        date = row[0]
        datetime.strptime(date, "%Y-%m-%d")
        if date in result:
            raise ValueError(f"重复的交易日: {date}")
        result[date] = tencent_volume(row[5])
    return result


def parse_tencent_minutes(data):
    data = data.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("腾讯分时数据为空")
    date = datetime.strptime(data.get("date", ""), "%Y%m%d").strftime("%Y-%m-%d")
    result, previous, last_stamp = {}, 0, ""
    for row in data["data"]:
        if not isinstance(row, str):
            raise ValueError("腾讯分时记录无效")
        parts = row.split()
        if len(parts) < 3 or not re.fullmatch(r"\d{4}", parts[0]):
            raise ValueError("腾讯分时记录无效")
        clock = parts[0][:2] + ":" + parts[0][2:]
        stamp = f"{date} {clock}"
        cumulative = tencent_volume(parts[2])
        if stamp <= last_stamp or cumulative < previous:
            raise ValueError("腾讯分时记录时间或累计成交量异常")
        if clock == "13:00":
            # Tencent repeats the last morning value at 13:00; it is not a new minute.
            if last_stamp != f"{date} 11:30" or cumulative != previous:
                raise ValueError("腾讯午休分时记录异常")
            last_stamp = stamp
            continue
        if not ("09:30" <= clock <= "11:30" or "13:01" <= clock <= "15:00"):
            raise ValueError(f"不支持的分时时间: {stamp}")
        result[stamp] = cumulative - previous
        previous, last_stamp = cumulative, stamp
    return result


def volume(row):
    if not isinstance(row, str):
        raise ValueError("行情记录无效")
    parts = row.split(",")
    if len(parts) < 6 or not re.fullmatch(r"\d+", parts[5]):
        raise ValueError("行情成交量无效")
    return int(parts[5])  # Eastmoney reports volume in hands (100 shares).


def parse_daily(data):
    rows = data.get("klines")
    if not isinstance(rows, list) or not rows:
        raise ValueError("日线数据为空")
    result = {}
    for row in rows:
        if not isinstance(row, str):
            raise ValueError("日线记录无效")
        date = row.split(",", 1)[0]
        datetime.strptime(date, "%Y-%m-%d")
        if date in result:
            raise ValueError(f"重复的交易日: {date}")
        result[date] = volume(row)
    return result


def parse_minutes(data):
    rows = data.get("trends")
    if not isinstance(rows, list):
        raise ValueError("分时数据为空")
    result = {}
    for row in rows:
        if not isinstance(row, str):
            raise ValueError("分时记录无效")
        stamp = row.split(",", 1)[0]
        parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
        clock = parsed.strftime("%H:%M")
        if not ("09:30" <= clock <= "11:30" or "13:01" <= clock <= "15:00"):
            raise ValueError(f"不支持的分时时间: {stamp}")
        if stamp in result:
            raise ValueError(f"重复的分时时间: {stamp}")
        result[stamp] = volume(row)
    return result


def expected_minutes(date, latest):
    result = set()
    for start, end in (("09:30", "11:30"), ("13:01", "15:00")):
        current = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M")
        stop = datetime.strptime(f"{date} {end}", "%Y-%m-%d %H:%M")
        while current <= stop:
            stamp = current.strftime("%Y-%m-%d %H:%M")
            if stamp <= latest:
                result.add(stamp)
            current += timedelta(minutes=1)
    return result


def build_payload(daily, minutes, now, source="东方财富指数行情：上证A股指数、深证A指、科创综指"):
    today = now.astimezone(SHANGHAI).date().isoformat()
    date_sets = [set(daily[key]) for key in MARKETS]
    dates = sorted(set.intersection(*date_sets))[-30:]
    if len(dates) != 30 or any(sorted(days)[-30:] != dates for days in date_sets):
        raise ValueError("三个行情源的最近 30 个交易日不一致或不完整")
    if dates[-1] > today:
        raise ValueError("行情日期晚于当前日期")

    daily_rows = [{"date": date, "values": {key: daily[key][date] for key in MARKETS}} for date in dates]
    today_points = {key: {stamp: value for stamp, value in minutes[key].items() if stamp.startswith(today + " ")} for key in MARKETS}
    intraday = None
    if any(today_points.values()):
        if not all(today_points.values()) or dates[-1] != today:
            raise ValueError("当日分时和日线数据不完整，请稍后重试")
        # Sources may update between requests; align all markets to the last common minute.
        common = set.intersection(*(set(points) for points in today_points.values()))
        if not common:
            raise ValueError("三个市场的分时数据没有共同时间点")
        latest = max(common)
        aligned = {key: {stamp: value for stamp, value in points.items() if stamp <= latest} for key, points in today_points.items()}
        if any(set(points) != set(aligned["sh"]) for points in aligned.values()):
            raise ValueError("三个市场的分时数据时间点不一致")
        if set(aligned["sh"]) != expected_minutes(today, latest):
            raise ValueError("当日分时数据缺少分钟记录，请稍后重试")
        hourly = []
        for index, (label, start, end) in enumerate(PERIODS):
            stamps = [stamp for stamp in aligned["sh"] if (stamp[11:] >= start if index == 0 else stamp[11:] > start) and stamp[11:] <= end]
            hourly.append({
                "period": label,
                "values": {key: sum(aligned[key][stamp] for stamp in stamps) for key in MARKETS} if stamps else None,
                "partial": bool(stamps) and latest[11:] < end,
            })
        totals = {key: sum(points.values()) for key, points in aligned.items()}
        daily_rows[-1]["values"] = totals
        daily_rows[-1]["partial"] = latest[11:] < "15:00"
        intraday = {"date": today, "as_of": latest, "hourly": hourly}
    elif dates[-1] == today:
        # A today's K-line without minute data must not be presented as a complete refresh.
        raise ValueError("当日日线已更新，但分时数据暂不可用，请稍后重试")

    return {
        "generated_at": now.astimezone(SHANGHAI).isoformat(timespec="seconds"),
        "unit": "手",
        "source": source,
        "daily": daily_rows,
        "intraday": intraday,
    }


def fetch_eastmoney(now):
    start = (now.astimezone(SHANGHAI).date() - timedelta(days=150)).strftime("%Y%m%d")
    daily, minutes = {}, {}
    for key, (_, secid) in MARKETS.items():
        daily[key] = parse_daily(get_data("kline", secid, klt=101, fqt=0, beg=start, end="20500101"))
        minutes[key] = parse_minutes(get_data("trends2", secid, ndays=1, iscr=0))
    return daily, minutes


def fetch_tencent(_now):
    daily, minutes = {}, {}
    for key, (_, secid) in MARKETS.items():
        market, code = secid.split(".")
        symbol = ("sh" if market == "1" else "sz") + code
        daily[key] = parse_tencent_daily(get_tencent_data("day", symbol))
        minutes[key] = parse_tencent_minutes(get_tencent_data("minute", symbol))
    return daily, minutes


def fetch(now):
    errors = []
    for name, provider in (("腾讯财经", fetch_tencent), ("东方财富", fetch_eastmoney)):
        try:
            print(f"正在获取{name}行情…", flush=True)
            daily, minutes = provider(now)
            payload = build_payload(daily, minutes, datetime.now(SHANGHAI), f"{name}指数行情：上证A股指数、深证A指、科创综指")
            print(f"行情来源：{name}", flush=True)
            return payload
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{name}: {error}")
            print(f"{name}不可用：{error}", file=sys.stderr, flush=True)
    raise RuntimeError("所有行情源均不可用；" + "；".join(errors))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"volume-[0-9]{17}-[0-9a-f]{8}", args.request_id):
        parser.error("invalid request id")
    payload = fetch(datetime.now(SHANGHAI))
    payload["request_id"] = args.request_id
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, args.output)
    print(f"已获取 {len(payload['daily'])} 个交易日；输出: {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"行情获取失败: {error}", file=sys.stderr)
        sys.exit(1)
