"""Send e-mail for StudyGenie (password reset codes and username reminders).

Uses SMTP. For a Gmail sender: turn on 2-step verification, create an "App password"
(Google Account > Security > App passwords) and put these in .streamlit/secrets.toml:

    SMTP_USER = "yourname@gmail.com"
    SMTP_PASSWORD = "the 16-character app password"

or set the same names as environment variables. Optional: SMTP_HOST (default smtp.gmail.com),
SMTP_PORT (default 465, SSL) and SMTP_FROM (default SMTP_USER).
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


class MailError(Exception):
    """The e-mail could not be sent."""


def _setting(name: str, st=None, default: str = "") -> str:
    value = os.environ.get(name)
    if not value and st is not None:
        try:
            value = str(st.secrets.get(name, "") or "")
        except Exception:  # noqa: BLE001 - no secrets file is normal
            value = ""
    return value or default


def is_configured(st=None) -> bool:
    return bool(_setting("SMTP_USER", st) and _setting("SMTP_PASSWORD", st))


def send(to: str, subject: str, body: str, st=None) -> None:
    """Send a plain-text e-mail. Raises MailError with a short reason if it fails."""
    user, password = _setting("SMTP_USER", st), _setting("SMTP_PASSWORD", st)
    if not (user and password):
        raise MailError("e-mail is not set up on this server")
    host = _setting("SMTP_HOST", st, "smtp.gmail.com")
    port = int(_setting("SMTP_PORT", st, "465"))
    msg = EmailMessage()
    msg["From"] = _setting("SMTP_FROM", st, user)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(user, password)
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as err:
        raise MailError("the e-mail account login was refused (check the Gmail app password)") from err
    except (smtplib.SMTPException, OSError) as err:
        raise MailError("the e-mail could not be sent right now") from err