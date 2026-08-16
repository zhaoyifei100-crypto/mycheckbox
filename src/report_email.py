"""Send the previous week's MyCheckBox logs by QQ SMTP on Sundays."""

from __future__ import annotations

import json
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_RECIPIENT = "zhaoyifei100@gmail.com"
DEFAULT_TIME_ZONE = "Asia/Shanghai"
DEFAULT_SMTP_HOST = "smtp.qq.com"
DEFAULT_SMTP_PORT = 465


def _read_mail_secret() -> str:
    """Read the mail credential bundle from an explicit local file or Secret Manager."""

    local_file = os.environ.get("MYCHECKBOX_QQ_MAIL_FILE")
    if local_file:
        try:
            return Path(local_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("读取 QQ 邮件授权文件失败") from exc

    from .checkin import read_secret

    return read_secret(os.environ.get("MYCHECKBOX_QQ_MAIL_SECRET_ID", "mycheckbox-qq-mail"))


def _parse_mail_secret(value: str) -> tuple[str, str]:
    """Parse a two-line email/auth-code file without exposing either value."""

    lines = [
        line.strip()
        for line in value.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    sender: str | None = None
    auth_code: str | None = None

    for line in lines:
        key, separator, candidate = line.partition("=")
        key_name = key.strip().lower()
        if key_name in {"email", "mail", "username", "user", "qq_email", "sender"} and separator:
            sender = candidate.strip()
            continue
        if key_name in {"auth_code", "authorization_code", "authcode", "password", "pwd", "key", "code"} and separator:
            auth_code = candidate.strip()
            continue

        if sender is None and "@" in line:
            # Also accept a bare address with an accidental trailing '='.
            sender = line.rstrip("=").strip()
        elif auth_code is None:
            auth_code = line

    if not sender or not re.fullmatch(r"[^\s@]+@[^\s@]+", sender):
        raise RuntimeError("QQ 邮件授权文件缺少有效发件邮箱")
    if not auth_code:
        raise RuntimeError("QQ 邮件授权文件缺少授权码")
    return sender, auth_code


def _current_week_to_date(now: datetime) -> tuple[datetime, datetime] | None:
    """Return the current Monday-to-now interval only on Sunday."""

    if now.weekday() != 6:
        return None
    current_week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=now.weekday()
    )
    return current_week_start, now


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return str(payload)


def _fetch_log_file(start: datetime, end: datetime, report_zone: ZoneInfo) -> str:
    """Read stdout entries from the dedicated Cloud Logging view."""

    from google.cloud import logging_v2

    project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("缺少 GCP_PROJECT_ID")

    bucket = os.environ.get("MYCHECKBOX_LOG_BUCKET", "mycheckbox-logs")
    location = os.environ.get("MYCHECKBOX_LOG_LOCATION", "global")
    view = os.environ.get("MYCHECKBOX_LOG_VIEW", "_AllLogs")
    job_name = os.environ.get("MYCHECKBOX_JOB_NAME", "mycheckbox")
    view_name = f"projects/{project_id}/locations/{location}/buckets/{bucket}/views/{view}"
    filter_ = (
        f'resource.type="cloud_run_job" AND resource.labels.job_name="{job_name}" '
        'AND logName:"run.googleapis.com%2Fstdout" '
        f'AND timestamp >= "{_rfc3339(start)}" AND timestamp < "{_rfc3339(end)}"'
    )

    client = logging_v2.Client(project=project_id)
    entries = client.list_entries(
        resource_names=[view_name],
        filter_=filter_,
        order_by="timestamp asc",
        page_size=100,
        max_results=5000,
    )

    lines = [
        "MyCheckBox 本周签到日志",
        f"时间范围: {start.strftime('%Y-%m-%d %H:%M:%S %Z')} 至 {end.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
    ]
    count = 0
    for entry in entries:
        timestamp = entry.timestamp or start
        local_timestamp = timestamp.astimezone(report_zone)
        execution = (entry.labels or {}).get("run.googleapis.com/execution_name", "unknown")
        severity = getattr(entry.severity, "name", None) or str(entry.severity or "DEFAULT")
        lines.append(
            f"[{local_timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}] "
            f"[{execution}] [{severity}] {_payload_text(entry.payload)}"
        )
        count += 1

    if count == 0:
        lines.append("本周没有找到签到 stdout 日志。")
    return "\n".join(lines) + "\n"


def maybe_send_weekly_report() -> str | None:
    """Send the report on Sunday; return None on all other days."""

    if os.environ.get("MYCHECKBOX_REPORT_ENABLED", "1").lower() in {"0", "false", "no"}:
        return None

    try:
        report_zone = ZoneInfo(os.environ.get("MYCHECKBOX_REPORT_TIME_ZONE", DEFAULT_TIME_ZONE))
    except Exception as exc:
        raise RuntimeError("周报时区配置无效") from exc
    now = datetime.now(report_zone)
    interval = _current_week_to_date(now)
    if interval is None and os.environ.get("MYCHECKBOX_FORCE_WEEKLY_REPORT") != "1":
        return None
    if interval is None:
        current_week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=now.weekday()
        )
        interval = (current_week_start, now)

    start, end = interval
    log_text = _fetch_log_file(start, end, report_zone)
    sender, auth_code = _parse_mail_secret(_read_mail_secret())
    recipient = os.environ.get("MYCHECKBOX_REPORT_RECIPIENT", DEFAULT_RECIPIENT)
    subject = (
        f"MyCheckBox 本周签到日志 "
        f"{start.strftime('%Y-%m-%d')} 至 {end.strftime('%Y-%m-%d')}"
    )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(log_text)

    smtp_host = os.environ.get("MYCHECKBOX_SMTP_HOST", DEFAULT_SMTP_HOST)
    smtp_port = int(os.environ.get("MYCHECKBOX_SMTP_PORT", str(DEFAULT_SMTP_PORT)))
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.login(sender, auth_code)
            smtp.send_message(message)
    except Exception as exc:
        raise RuntimeError("QQ 邮件发送失败") from exc

    return f"已发送本周签到日志邮件：{start:%Y-%m-%d} 至 {end:%Y-%m-%d}"
