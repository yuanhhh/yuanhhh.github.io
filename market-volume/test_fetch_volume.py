import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fetch_volume import (MARKETS, SHANGHAI, amount_fen, build_payload, expected_minutes, fetch,
                          fetch_sohu_tencent, get_data, get_tencent_data, parse_daily, parse_minutes,
                          parse_sohu_daily, parse_tencent_dates, parse_tencent_minutes)


def make_daily(last_date="2026-11-03"):
    end = datetime.strptime(last_date, "%Y-%m-%d").date()
    return {
        key: {(end - timedelta(days=offset)).isoformat(): 100 + offset for offset in range(30)}
        for key in MARKETS
    }


def make_minutes(times):
    date = "2026-11-03"
    latest = date + " " + times[-1][0]
    samples = {stamp: 0 for stamp in expected_minutes(date, latest)}
    samples.update({date + " " + time: value for time, value in times})
    return {key: samples.copy() for key in MARKETS}


class VolumeTests(unittest.TestCase):
    def test_partial_hour_and_opening_auction(self):
        daily = make_daily()
        minutes = make_minutes([("09:30", 5), ("09:31", 6), ("10:30", 7), ("10:31", 8)])
        payload = build_payload(daily, minutes, datetime(2026, 11, 3, 10, 31, tzinfo=SHANGHAI))
        hours = payload["intraday"]["hourly"]
        self.assertEqual(hours[0]["values"]["sh"], 18)
        self.assertFalse(hours[0]["partial"])
        self.assertEqual(hours[1]["values"]["sh"], 8)
        self.assertTrue(hours[1]["partial"])
        self.assertIsNone(hours[2]["values"])
        self.assertEqual(payload["daily"][-1]["values"]["sh"], 26)
        self.assertTrue(payload["daily"][-1]["partial"])
        self.assertEqual(payload["metric"], "turnover")
        self.assertEqual(payload["unit"], "分")

    def test_lunch_and_last_minute_belong_to_correct_periods(self):
        times = [("09:30", 1), ("10:30", 2), ("10:31", 3), ("11:30", 4), ("13:01", 5), ("14:00", 6), ("14:01", 7), ("15:00", 8)]
        payload = build_payload(make_daily(), make_minutes(times), datetime(2026, 11, 3, 15, tzinfo=SHANGHAI))
        self.assertEqual([hour["values"]["sh"] for hour in payload["intraday"]["hourly"]], [3, 7, 11, 15])
        self.assertFalse(any(hour["partial"] for hour in payload["intraday"]["hourly"]))
        self.assertFalse(payload["daily"][-1]["partial"])

    def test_half_hour_boundaries_reconcile_with_hourly_totals(self):
        times = [("09:30", 1), ("10:00", 2), ("10:01", 3), ("10:30", 4),
                 ("10:31", 5), ("11:00", 6), ("11:01", 7), ("11:30", 8),
                 ("13:01", 9), ("13:30", 10), ("13:31", 11), ("14:00", 12),
                 ("14:01", 13), ("14:30", 14), ("14:31", 15), ("15:00", 16)]
        payload = build_payload(make_daily(), make_minutes(times), datetime(2026, 11, 3, 15, tzinfo=SHANGHAI))
        half_hours = payload["intraday"]["half_hourly"]
        self.assertEqual(len(half_hours), 8)
        self.assertEqual([row["values"]["sh"] for row in half_hours], [3, 7, 11, 15, 19, 23, 27, 31])
        for index, hour in enumerate(payload["intraday"]["hourly"]):
            for key in MARKETS:
                self.assertEqual(hour["values"][key], half_hours[index * 2]["values"][key] + half_hours[index * 2 + 1]["values"][key])
        self.assertFalse(any(row["partial"] for row in half_hours))

    def test_half_hour_in_progress_does_not_invent_future_values(self):
        times = [("09:30", 5), ("10:00", 7), ("10:01", 11)]
        payload = build_payload(make_daily(), make_minutes(times), datetime(2026, 11, 3, 10, 1, tzinfo=SHANGHAI))
        periods = payload["intraday"]["half_hourly"]
        self.assertEqual(periods[0]["values"]["sh"], 12)
        self.assertFalse(periods[0]["partial"])
        self.assertEqual(periods[1]["values"]["sh"], 11)
        self.assertTrue(periods[1]["partial"])
        self.assertTrue(all(row["values"] is None and not row["partial"] for row in periods[2:]))

    def test_non_trading_day_does_not_show_yesterday_as_today(self):
        minutes = {key: {"2026-11-02 15:00": 20} for key in MARKETS}
        payload = build_payload(make_daily("2026-11-02"), minutes, datetime(2026, 11, 3, 8, tzinfo=SHANGHAI))
        self.assertIsNone(payload["intraday"])
        self.assertEqual(payload["daily"][-1]["date"], "2026-11-02")

    def test_aligns_to_latest_common_minute(self):
        minutes = make_minutes([("09:30", 2), ("09:31", 3)])
        minutes["sh"]["2026-11-03 09:32"] = 100
        payload = build_payload(make_daily(), minutes, datetime(2026, 11, 3, 9, 32, tzinfo=SHANGHAI))
        self.assertEqual(payload["intraday"]["as_of"], "2026-11-03 09:31")
        self.assertEqual(payload["daily"][-1]["values"]["sh"], 5)

    def test_missing_market_minute_is_not_silently_zeroed(self):
        minutes = make_minutes([("09:30", 2), ("09:31", 3), ("09:32", 4)])
        del minutes["sz"]["2026-11-03 09:31"]
        with self.assertRaisesRegex(ValueError, "时间点不一致"):
            build_payload(make_daily(), minutes, datetime(2026, 11, 3, 9, 32, tzinfo=SHANGHAI))

    def test_same_missing_minute_in_all_markets_is_rejected(self):
        minutes = make_minutes([("09:30", 2), ("09:32", 4)])
        for points in minutes.values():
            del points["2026-11-03 09:31"]
        with self.assertRaisesRegex(ValueError, "缺少分钟"):
            build_payload(make_daily(), minutes, datetime(2026, 11, 3, 9, 32, tzinfo=SHANGHAI))

    def test_incomplete_today_fails_instead_of_showing_old_data(self):
        with self.assertRaisesRegex(ValueError, "分时数据暂不可用"):
            build_payload(make_daily(), {key: {} for key in MARKETS}, datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))

    def test_rejects_mismatched_trading_days(self):
        daily = make_daily()
        del daily["star"]["2026-11-03"]
        with self.assertRaisesRegex(ValueError, "交易日"):
            build_payload(daily, {key: {} for key in MARKETS}, datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))

    def test_rejects_malformed_and_duplicate_data(self):
        with self.assertRaises(ValueError):
            parse_daily({"klines": ["2026-11-03,1,1,1,1,12,-1"]})
        with self.assertRaises(ValueError):
            parse_minutes({"trends": ["2026-11-03 12:00,1,1,1,1,2,0"]})
        with self.assertRaises(ValueError):
            parse_minutes({"trends": ["2026-11-03 09:30,1,1,1,1,2,0"] * 2})

    def test_provider_daily_amounts_use_actual_amount_fields(self):
        self.assertEqual(parse_daily({"klines": ["2026-11-03,1,1,1,1,99,456.78"]}), {"2026-11-03": 45678})
        self.assertEqual(parse_sohu_daily({"hq": [["2026-11-03", "1", "1", "1", "1", "1", "1", "99", "12.34"]]}), {"2026-11-03": 12340000})
        self.assertEqual(parse_tencent_dates({"day": [["2026-11-03", "1", "1", "1", "1", "99"]]}), ["2026-11-03"])
        self.assertEqual(amount_fen("123.45"), 12345)
        with self.assertRaises(ValueError):
            amount_fen("1.001")
        with self.assertRaises(ValueError):
            amount_fen("NaN")

    def test_eastmoney_minute_uses_amount_not_volume(self):
        result = parse_minutes({"trends": ["2026-11-03 09:30,1,1,1,1,987,123.45"]})
        self.assertEqual(result, {"2026-11-03 09:30": 12345})
        with self.assertRaises(ValueError):
            parse_minutes({"trends": ["2026-11-03 09:30,1,1,1,1,987"]})

    def test_tencent_cumulative_minutes_include_auction_and_ignore_lunch_marker(self):
        data = {"data": {"date": "20261103", "data": [
            "0930 10 5 5.12", "0931 10 11 11.24", "1130 10 50 50.55",
            "1300 10 50 50.55", "1301 10 70 70.80", "1500 10 95 95.95",
        ]}}
        result = parse_tencent_minutes(data)
        self.assertEqual(list(result.values()), [512, 612, 3931, 2025, 2515])
        self.assertNotIn("2026-11-03 13:00", result)
        self.assertEqual(sum(result.values()), 9595)

    def test_tencent_rejects_invalid_lunch_and_falling_cumulative_amount(self):
        with self.assertRaisesRegex(ValueError, "午休"):
            parse_tencent_minutes({"data": {"date": "20261103", "data": ["1130 10 50 50.00", "1300 10 51 51.00"]}})
        with self.assertRaisesRegex(ValueError, "累计成交额"):
            parse_tencent_minutes({"data": {"date": "20261103", "data": ["0930 10 10 10.00", "0931 10 11 9.00"]}})

    def test_sohu_history_with_today_derived_from_tencent_minutes(self):
        now = datetime(2026, 11, 3, 9, 30, tzinfo=SHANGHAI)
        dates = [(now.date() - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]
        sohu = {"hq": [[date, "1", "1", "1", "1", "1", "1", "99", "12.34"] for date in dates[:-1]]}
        tencent_day = {"day": [[date, "1", "1", "1", "1", "99"] for date in dates]}
        tencent_minute = {"data": {"date": "20261103", "data": ["0930 10 99 5.12"]}}
        with patch("fetch_volume.get_sohu_daily", return_value=sohu), \
             patch("fetch_volume.get_tencent_data", side_effect=lambda kind, _: tencent_day if kind == "day" else tencent_minute):
            daily, minutes = fetch_sohu_tencent(now)
        self.assertEqual(len(daily["sh"]), 30)
        payload = build_payload(daily, minutes, now)
        self.assertEqual(payload["daily"][-2]["values"]["sh"], 12340000)
        self.assertEqual(payload["daily"][-1]["values"]["sh"], 512)
        self.assertEqual(payload["intraday"]["hourly"][0]["values"]["sh"], 512)

    def test_sohu_history_requires_all_past_days(self):
        now = datetime(2026, 11, 3, 9, 30, tzinfo=SHANGHAI)
        dates = [(now.date() - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]
        sohu = {"hq": [[date, "1", "1", "1", "1", "1", "1", "99", "12.34"] for date in dates[1:-1]]}
        tencent_day = {"day": [[date, "1", "1", "1", "1", "99"] for date in dates]}
        tencent_minute = {"data": {"date": "20261103", "data": ["0930 10 99 5.12"]}}
        with patch("fetch_volume.get_sohu_daily", return_value=sohu), \
             patch("fetch_volume.get_tencent_data", side_effect=lambda kind, _: tencent_day if kind == "day" else tencent_minute):
            with self.assertRaisesRegex(ValueError, "历史成交额不完整"):
                fetch_sohu_tencent(now)

    def test_bad_provider_response_is_rejected(self):
        with patch("fetch_volume.get_json", return_value=[]):
            with self.assertRaises(ValueError):
                get_data("kline", "1.000002")
        with patch("fetch_volume.get_json", return_value={"code": 0, "data": []}):
            with self.assertRaises(ValueError):
                get_tencent_data("minute", "sh000002")
        with self.assertRaises(ValueError):
            parse_tencent_minutes({"data": {"date": "20261103", "data": [None]}})

    def test_uses_sohu_tencent_before_fallback(self):
        daily = make_daily()
        minutes = {key: {} for key in MARKETS}
        with patch("fetch_volume.fetch_sohu_tencent", return_value=(daily, minutes)) as primary, \
             patch("fetch_volume.fetch_eastmoney") as fallback, \
             patch("fetch_volume.build_payload", return_value={"source": "搜狐历史、腾讯财经分时"}) as build:
            self.assertEqual(fetch(datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))["source"], "搜狐历史、腾讯财经分时")
            primary.assert_called_once()
            fallback.assert_not_called()
            self.assertIn("搜狐历史、腾讯财经分时", build.call_args.args[3])

    def test_falls_back_to_complete_eastmoney_snapshot(self):
        daily = make_daily()
        minutes = {key: {} for key in MARKETS}
        with patch("fetch_volume.fetch_sohu_tencent", side_effect=RuntimeError("connection closed")), \
             patch("fetch_volume.fetch_eastmoney", return_value=(daily, minutes)) as fallback, \
             patch("fetch_volume.build_payload", return_value={"source": "东方财富"}) as build:
            self.assertEqual(fetch(datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))["source"], "东方财富")
            fallback.assert_called_once()
            self.assertIs(build.call_args.args[0], daily)
            self.assertIs(build.call_args.args[1], minutes)
            self.assertIn("东方财富", build.call_args.args[3])

    def test_invalid_primary_data_also_triggers_full_fallback(self):
        daily = make_daily()
        minutes = {key: {} for key in MARKETS}
        with patch("fetch_volume.fetch_sohu_tencent", return_value=(daily, minutes)), \
             patch("fetch_volume.fetch_eastmoney", return_value=(daily, minutes)) as fallback, \
             patch("fetch_volume.build_payload", side_effect=[ValueError("incomplete"), {"source": "东方财富"}]):
            self.assertEqual(fetch(datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))["source"], "东方财富")
            fallback.assert_called_once()

    def test_reports_both_sources_when_unavailable(self):
        with patch("fetch_volume.fetch_sohu_tencent", side_effect=RuntimeError("腾讯故障")), \
             patch("fetch_volume.fetch_eastmoney", side_effect=RuntimeError("东方财富故障")):
            with self.assertRaisesRegex(RuntimeError, "腾讯故障.*东方财富故障"):
                fetch(datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))


if __name__ == "__main__":
    unittest.main()
