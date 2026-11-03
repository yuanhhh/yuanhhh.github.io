import unittest
from datetime import datetime, timedelta

from fetch_volume import MARKETS, SHANGHAI, build_payload, expected_minutes, parse_daily, parse_minutes


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


if __name__ == "__main__":
    unittest.main()
