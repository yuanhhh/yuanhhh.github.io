import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fetch_volume import (MARKETS, SHANGHAI, build_payload, expected_minutes, fetch, get_data,
                          get_tencent_data, parse_daily, parse_minutes, parse_tencent_daily, parse_tencent_minutes)


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

    def test_lunch_and_last_minute_belong_to_correct_periods(self):
        times = [("09:30", 1), ("10:30", 2), ("10:31", 3), ("11:30", 4), ("13:01", 5), ("14:00", 6), ("14:01", 7), ("15:00", 8)]
        payload = build_payload(make_daily(), make_minutes(times), datetime(2026, 11, 3, 15, tzinfo=SHANGHAI))
        self.assertEqual([hour["values"]["sh"] for hour in payload["intraday"]["hourly"]], [3, 7, 11, 15])
        self.assertFalse(any(hour["partial"] for hour in payload["intraday"]["hourly"]))
        self.assertFalse(payload["daily"][-1]["partial"])

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
            parse_daily({"klines": ["2026-11-03,1,1,1,1,-1,0"]})
        with self.assertRaises(ValueError):
            parse_minutes({"trends": ["2026-11-03 12:00,1,1,1,1,2,0"]})
        with self.assertRaises(ValueError):
            parse_minutes({"trends": ["2026-11-03 09:30,1,1,1,1,2,0"] * 2})

    def test_tencent_daily_uses_hands_and_rejects_fractional_volume(self):
        self.assertEqual(parse_tencent_daily({"day": [["2026-11-03", "1", "1", "1", "1", "49600012.000"]]}), {"2026-11-03": 49600012})
        with self.assertRaises(ValueError):
            parse_tencent_daily({"day": [["2026-11-03", "1", "1", "1", "1", "1.5"]]})

    def test_tencent_cumulative_minutes_include_auction_and_ignore_lunch_marker(self):
        data = {"data": {"date": "20261103", "data": [
            "0930 10 5 0", "0931 10 11 0", "1130 10 50 0",
            "1300 10 50 0", "1301 10 70 0", "1500 10 95 0",
        ]}}
        result = parse_tencent_minutes(data)
        self.assertEqual(list(result.values()), [5, 6, 39, 20, 25])
        self.assertNotIn("2026-11-03 13:00", result)
        self.assertEqual(sum(result.values()), 95)

    def test_tencent_rejects_invalid_lunch_and_falling_cumulative_volume(self):
        with self.assertRaisesRegex(ValueError, "午休"):
            parse_tencent_minutes({"data": {"date": "20261103", "data": ["1130 10 50 0", "1300 10 51 0"]}})
        with self.assertRaisesRegex(ValueError, "累计成交量"):
            parse_tencent_minutes({"data": {"date": "20261103", "data": ["0930 10 10 0", "0931 10 9 0"]}})

    def test_bad_provider_response_is_rejected(self):
        with patch("fetch_volume.get_json", return_value=[]):
            with self.assertRaises(ValueError):
                get_data("kline", "1.000002")
        with patch("fetch_volume.get_json", return_value={"code": 0, "data": []}):
            with self.assertRaises(ValueError):
                get_tencent_data("minute", "sh000002")
        with self.assertRaises(ValueError):
            parse_tencent_minutes({"data": {"date": "20261103", "data": [None]}})

    def test_uses_tencent_before_fallback(self):
        daily = make_daily()
        minutes = {key: {} for key in MARKETS}
        with patch("fetch_volume.fetch_tencent", return_value=(daily, minutes)) as primary, \
             patch("fetch_volume.fetch_eastmoney") as fallback, \
             patch("fetch_volume.build_payload", return_value={"source": "腾讯财经"}) as build:
            self.assertEqual(fetch(datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))["source"], "腾讯财经")
            primary.assert_called_once()
            fallback.assert_not_called()
            self.assertIn("腾讯财经", build.call_args.args[3])

    def test_falls_back_to_complete_eastmoney_snapshot(self):
        daily = make_daily()
        minutes = {key: {} for key in MARKETS}
        with patch("fetch_volume.fetch_tencent", side_effect=RuntimeError("connection closed")), \
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
        with patch("fetch_volume.fetch_tencent", return_value=(daily, minutes)), \
             patch("fetch_volume.fetch_eastmoney", return_value=(daily, minutes)) as fallback, \
             patch("fetch_volume.build_payload", side_effect=[ValueError("incomplete"), {"source": "东方财富"}]):
            self.assertEqual(fetch(datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))["source"], "东方财富")
            fallback.assert_called_once()

    def test_reports_both_sources_when_unavailable(self):
        with patch("fetch_volume.fetch_tencent", side_effect=RuntimeError("腾讯故障")), \
             patch("fetch_volume.fetch_eastmoney", side_effect=RuntimeError("东方财富故障")):
            with self.assertRaisesRegex(RuntimeError, "腾讯故障.*东方财富故障"):
                fetch(datetime(2026, 11, 3, 10, tzinfo=SHANGHAI))


if __name__ == "__main__":
    unittest.main()
