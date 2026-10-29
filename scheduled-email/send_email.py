#!/usr/bin/env python3
"""Send a configurable HTML email through SMTP.

All delivery configuration comes from environment variables so credentials are
never stored in the repository. See README.md for the required variables.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


REQUIRED_SETTINGS = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO")
HEADER_BREAK = re.compile(r"[\r\n]")
TAG = re.compile(r"<[^>]+>")


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
    parser.add_argument("--html-file", default=Path(__file__).with_name("message.html"), type=Path, help="HTML message file")
    parser.add_argument("--dry-run", action="store_true", help="Validate and render the message without sending it")
    args = parser.parse_args()

    try:
        settings = Settings.from_environment()
        html_content = args.html_file.read_text(encoding="utf-8")
        message = build_message(settings, html_content)
        if args.dry_run:
            print(f"Dry run succeeded: would send '{settings.subject}' to {len(settings.recipients)} recipient(s).")
            return 0
        send(settings, message)
        print(f"Email sent to {len(settings.recipients)} recipient(s).")
        return 0
    except (OSError, ValueError, smtplib.SMTPException) as error:
        print(f"Email delivery failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
