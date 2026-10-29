#!/usr/bin/env python3
"""Build and send a daily romantic email through SMTP."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from lunardate import LunarDate


REQUIRED_SETTINGS = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO")
HEADER_BREAK = re.compile(r"[\r\n]")
TAG = re.compile(r"<[^>]+>")
TEMPLATE_DIR = Path(__file__).with_name("templates")
WEDDING_DATE = date(2025, 10, 6)  # 2025 年中秋节（农历八月十五）
WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
TEMPLATES = ("monday.html", "tuesday.html", "wednesday.html", "thursday.html", "friday.html", "saturday.html", "sunday.html")
WMO_WEATHER = {
    0: ("晴朗", "☀️"), 1: ("晴间多云", "🌤️"), 2: ("多云", "⛅"), 3: ("阴天", "☁️"),
    45: ("有雾", "🌫️"), 48: ("雾凇", "🌫️"), 51: ("小毛雨", "🌦️"), 53: ("毛毛雨", "🌦️"),
    55: ("强毛毛雨", "🌧️"), 56: ("冻雨", "🌧️"), 57: ("强冻雨", "🌧️"), 61: ("小雨", "🌦️"),
    63: ("中雨", "🌧️"), 65: ("大雨", "🌧️"), 66: ("冻雨", "🌧️"), 67: ("强冻雨", "🌧️"),
    71: ("小雪", "🌨️"), 73: ("中雪", "🌨️"), 75: ("大雪", "❄️"), 77: ("米雪", "❄️"),
    80: ("阵雨", "🌦️"), 81: ("中等阵雨", "🌧️"), 82: ("强阵雨", "⛈️"),
    85: ("阵雪", "🌨️"), 86: ("强阵雪", "❄️"), 95: ("雷暴", "⛈️"), 96: ("冰雹雷暴", "⛈️"),
    97: ("强雷暴", "⛈️"), 99: ("强冰雹雷暴", "⛈️"),
}


def get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def split_recipients(value: str) -> list[str]:
    recipients = [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]
    if not recipients:
        raise ValueError("MAIL_TO must contain at least one recipient")
    if any(HEADER_BREAK.search(item) for item in recipients):
        raise ValueError("MAIL_TO contains an invalid line break")
    return recipients


def plain_text_from_html(content: str) -> str:
    text = re.sub(r"<(br|/p|/div|/li|/h[1-6])\b[^>]*>", "\n", content, flags=re.IGNORECASE)
    text = TAG.sub("", text)
    return html.unescape(re.sub(r"\n{3,}", "\n\n", text)).strip() or "Please view this message in an HTML-capable email client."


def get_wuhan_weather() -> dict[str, str]:
    """Fetch today's Wuhan forecast without making weather availability fatal."""
    query = urlencode({
        "latitude": "30.5928",
        "longitude": "114.3055",
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
        "timezone": "Asia/Shanghai",
        "forecast_days": "1",
    })
    try:
        with urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=15) as response:
            payload = json.load(response)
        current = payload["current"]
        daily = payload["daily"]
        temperature = round(float(current["temperature_2m"]))
        feels_like = round(float(current["apparent_temperature"]))
        high = round(float(daily["temperature_2m_max"][0]))
        low = round(float(daily["temperature_2m_min"][0]))
        code = int(daily["weather_code"][0])
        precipitation = int(round(float(daily["precipitation_probability_max"][0])))
        condition, icon = WMO_WEATHER.get(code, ("天气多变", "🌤️"))
        reminders: list[str] = []
        if code >= 51 or precipitation >= 50:
            reminders.append("可能有雨，出门别忘了带伞。")
        if high >= 32 or temperature >= 30:
            reminders.append("天气热了，注意防暑降温，多喝水。")
        elif low <= 12 or temperature <= 14:
            reminders.append("天气凉了，记得加衣服，别着凉。")
        elif low <= 20:
            reminders.append("早晚有点凉，带一件薄外套更安心。")
        else:
            reminders.append("气温正舒服，愿你今天轻松又明亮。")
        return {
            "weather_icon": icon,
            "weather_condition": condition,
            "temperature": f"{temperature}°C",
            "feels_like": f"体感 {feels_like}°C",
            "temperature_range": f"{low}°C — {high}°C",
            "weather_advice": " ".join(reminders),
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "weather_icon": "🌤️",
            "weather_condition": "天气数据暂不可用",
            "temperature": "--",
            "feels_like": "武汉",
            "temperature_range": "出门前查看天气",
            "weather_advice": "愿你无论晴雨，都照顾好自己。",
        }


def lunar_text(today: date) -> tuple[str, bool]:
    lunar = LunarDate.fromSolarDate(today.year, today.month, today.day)
    months = ("正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊")
    days = ("初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十", "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十", "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十")
    prefix = "闰" if lunar.isLeapMonth else ""
    text = f"农历{prefix}{months[lunar.month - 1]}月{days[lunar.day - 1]}"
    is_special = lunar.month == 8 and lunar.day == 15 and not lunar.isLeapMonth
    return text, is_special


def replace_tokens(template: str, values: dict[str, str]) -> str:
    """Replace explicit {{token}} markers while HTML-escaping dynamic values."""
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", html.escape(str(value)))
    return template


def render_daily_email(today: date) -> str:
    lunar_date, is_special_day = lunar_text(today)
    days_married = max(1, (today - WEDDING_DATE).days + 1)
    special_day = (
        "今天是农历八月十五——你的生日，也是我们的结婚纪念日。生日快乐，纪念日快乐！"
        if is_special_day else "今天没有预设纪念日，但和你一起度过的每一天都很特别。"
    )
    values = {
        "today": today.strftime("%Y 年 %m 月 %d 日"),
        "weekday": WEEKDAYS[today.weekday()],
        "lunar_date": lunar_date,
        "wedding_days": str(days_married),
        "special_day": special_day,
        **get_wuhan_weather(),
    }
    template = (TEMPLATE_DIR / TEMPLATES[today.weekday()]).read_text(encoding="utf-8")
    return replace_tokens(template, values)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str]
    subject: str
    use_ssl: bool
    starttls: bool

    @classmethod
    def from_environment(cls) -> "Settings":
        missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
        if missing:
            raise ValueError("Missing required settings: " + ", ".join(missing))

        sender = os.environ["MAIL_FROM"].strip()
        subject = os.getenv("MAIL_SUBJECT", "Scheduled email").strip()
        if HEADER_BREAK.search(sender) or HEADER_BREAK.search(subject):
            raise ValueError("MAIL_FROM and MAIL_SUBJECT cannot contain line breaks")

        try:
            port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError as error:
            raise ValueError("SMTP_PORT must be an integer") from error
        if not 1 <= port <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")

        use_ssl = get_bool("SMTP_USE_SSL")
        starttls = get_bool("SMTP_STARTTLS", default=not use_ssl)
        if use_ssl and starttls:
            raise ValueError("SMTP_USE_SSL and SMTP_STARTTLS cannot both be true")

        return cls(
            host=os.environ["SMTP_HOST"].strip(),
            port=port,
            username=os.environ["SMTP_USERNAME"],
            password=os.environ["SMTP_PASSWORD"],
            sender=sender,
            recipients=split_recipients(os.environ["MAIL_TO"]),
            subject=subject or "Scheduled email",
            use_ssl=use_ssl,
            starttls=starttls,
        )


def build_message(settings: Settings, html_content: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message["Subject"] = settings.subject
    message.set_content(plain_text_from_html(html_content))
    message.add_alternative(html_content, subtype="html")
    return message


def send(settings: Settings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    smtp_class = smtplib.SMTP_SSL if settings.use_ssl else smtplib.SMTP
    with smtp_class(settings.host, settings.port, timeout=30, context=context) if settings.use_ssl else smtp_class(settings.host, settings.port, timeout=30) as client:
        client.ehlo()
        if settings.starttls:
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.username, settings.password)
        client.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the scheduled HTML email through SMTP.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and render the message without sending it")
    parser.add_argument("--date", type=date.fromisoformat, help="Render a specific date (YYYY-MM-DD), useful for testing")
    args = parser.parse_args()

    try:
        settings = Settings.from_environment()
        today = args.date or datetime.now(WUHAN_TIMEZONE).date()
        html_content = render_daily_email(today)
        message = build_message(settings, html_content)
        if args.dry_run:
            print(f"Dry run succeeded: rendered {TEMPLATES[today.weekday()]} and would send '{settings.subject}' to {len(settings.recipients)} recipient(s).")
            return 0
        send(settings, message)
        print(f"Email sent to {len(settings.recipients)} recipient(s).")
        return 0
    except (OSError, ValueError, smtplib.SMTPException) as error:
        print(f"Email delivery failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
