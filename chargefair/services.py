"""Cross-cutting services: settings, audit log, distribution stats, email queue.

Everything in here works on the application database and is free of request
context, so it can also be used from the mail worker process.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import threading
from datetime import date, datetime, timedelta
from email.message import EmailMessage as SMTPMessage
from email.utils import formataddr
from pathlib import Path

from .extensions import db
from .models import (
    BOOKING_ACTIVE,
    BOOKING_COMPLETED,
    AuditLog,
    Booking,
    EmailMessage,
    Setting,
    User,
    utcnow,
)
from .slots import (
    DEFAULT_WINDOWS,
    DEFAULT_WORKDAYS,
    calendar_week,
    parse_windows,
    slot_label,
    week_start,
    window_label,
)

# ---------------------------------------------------------------------------
# Settings (editable through the admin UI)
# ---------------------------------------------------------------------------

KEY_WINDOWS = "slots.windows"
KEY_WORKDAYS = "slots.workdays"
KEY_CHARGING_POINTS = "infra.charging_points"
KEY_ALLOCATION_METHOD = "allocation.method"
KEY_MAX_SLOTS_PER_USER = "allocation.max_slots_per_user_new_user"
KEY_MAX_REGULAR_PER_WEEK = "allocation.max_regular_slots_per_week"
KEY_ROUND_OPEN_WEEKDAY = "allocation.round_opens_weekday"
KEY_ROUND_CLOSES_WEEKDAY = "allocation.round_closes_weekday"
KEY_WAITLIST_OFFER_MINUTES = "waitlist.offer_minutes"
KEY_DISTRIBUTION_WINDOW_WEEKS = "distribution.window_weeks"
KEY_BOOKING_HORIZON_WEEKS = "booking.horizon_weeks"
KEY_PHASE_WEEKS = "phase.weeks"
KEY_PHASE_REMINDER_DAYS = "phase.reminder_days"
KEY_SLOT_REMINDER_MINUTES = "reminder.minutes_before_start"


def _int_list(raw, fallback: list[int]) -> list[int]:
    if raw is None:
        return list(fallback)
    if isinstance(raw, (list, tuple)):
        return [int(value) for value in raw]
    values = []
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit():
            values.append(int(part))
    return values or list(fallback)


def windows() -> tuple[tuple[int, int], ...]:
    """Active charging windows as ``(start_minute, end_minute)`` tuples."""
    raw = Setting.get_json(KEY_WINDOWS)
    if not raw:
        raw = Setting.get(KEY_WINDOWS)
    return parse_windows(raw) if raw else DEFAULT_WINDOWS


def workdays() -> tuple[int, ...]:
    """Weekdays that offer charging slots (0 = Monday)."""
    raw = Setting.get_json(KEY_WORKDAYS)
    if not raw:
        raw = Setting.get(KEY_WORKDAYS)
    return tuple(_int_list(raw, list(DEFAULT_WORKDAYS)))


def slot_keys() -> list[tuple[int, int]]:
    """All ``(weekday, window_index)`` combinations of the weekly grid."""
    return [(day, index) for day in workdays() for index in range(len(windows()))]


def slot_text(weekday: int, window_index: int, short: bool = False) -> str:
    return slot_label(windows(), weekday, window_index, short=short)


def current_allocation_method() -> str:
    from .allocation import DEFAULT_METHOD, METHODS

    key = Setting.get(KEY_ALLOCATION_METHOD, DEFAULT_METHOD)
    return key if key in METHODS else DEFAULT_METHOD


def max_regular_slots_per_week() -> int:
    """Slots every applicant is entitled to before extras are handed out.

    This is the guarantee described to the employees: one charging slot per
    week, provided the infrastructure has the capacity.
    """
    return max(1, Setting.get_int(KEY_MAX_REGULAR_PER_WEEK, 1))


def max_slots_per_user() -> int:
    """Upper bound of charging slots per person and week (guarantee + extras)."""
    return max(1, Setting.get_int(KEY_MAX_SLOTS_PER_USER, 3))


def guaranteed_slots_per_week() -> int:
    """Effective guarantee, never above the total weekly bound."""
    return max(1, min(max_regular_slots_per_week(), max_slots_per_user()))


def booking_horizon_weeks() -> int:
    return max(1, Setting.get_int(KEY_BOOKING_HORIZON_WEEKS, 10))


def phase_weeks() -> int:
    """Length of an allocation phase in weeks (default and recommended: 4)."""
    return max(1, min(12, Setting.get_int(KEY_PHASE_WEEKS, 4)))


def distribution_window_weeks() -> int:
    """Period the distribution statistics look at."""
    return max(1, Setting.get_int(KEY_DISTRIBUTION_WINDOW_WEEKS, 12))


def phase_reminder_days() -> int:
    """Days before the deadline at which a reminder is sent (0 = disabled)."""
    return max(0, Setting.get_int(KEY_PHASE_REMINDER_DAYS, 2))


def slot_reminder_minutes() -> int:
    """How long before a charging slot the reminder email goes out."""
    return max(5, Setting.get_int(KEY_SLOT_REMINDER_MINUTES, 45))


def waitlist_offer_minutes() -> int:
    return max(5, Setting.get_int(KEY_WAITLIST_OFFER_MINUTES, 30))


def capacity_per_slot() -> int:
    """Charging points available in parallel -- identical for every window.

    BEV and PHEV share the full infrastructure, so this single number is the
    capacity of each slot.
    """
    from .models import ChargingPoint

    active = db.session.query(db.func.count(ChargingPoint.id)).filter(
        ChargingPoint.is_active.is_(True)
    ).scalar()
    return int(active or Setting.get_int(KEY_CHARGING_POINTS, 8))


def set_setting(key: str, value, description: str | None = None) -> None:
    Setting.set(key, value, description)
    db.session.commit()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit(action: str, detail: str = "", actor: User | None = None,
          ip: str | None = None) -> AuditLog:
    entry = AuditLog(
        action=action,
        detail=detail[:500] if detail else None,
        actor_id=getattr(actor, "id", None),
        actor_label=getattr(actor, "email", None) or "system",
        ip=ip,
    )
    db.session.add(entry)
    return entry


# ---------------------------------------------------------------------------
# Email queue
# ---------------------------------------------------------------------------

def queue_mail(recipient: str, subject: str, body: str, kind: str = "info",
               user: User | None = None, booking: Booking | None = None) -> EmailMessage:
    """Add a message to the outbox. The web request never waits for SMTP."""
    message = EmailMessage(
        recipient=recipient,
        subject=subject,
        body=body,
        kind=kind,
        user_id=getattr(user, "id", None),
        booking_id=getattr(booking, "id", None),
        status="pending",
        next_attempt=utcnow(),
    )
    db.session.add(message)
    return message


def process_email_queue(app, limit: int = 50) -> dict:
    """Send due messages from the outbox.

    Follows the pattern proven in the Lebenshof project: the whole batch is
    delivered over **one** SMTP connection instead of dialling in per message.
    During an allocation phase several hundred notifications can pile up, and a
    connection per mail would be both slow and easy to rate-limit.

    Failures are retried with exponential backoff; after
    ``MAIL_MAX_ATTEMPTS`` the message is marked as failed and stays visible in
    the admin area.
    """
    sent = failed = 0
    with app.app_context():
        messages = (
            EmailMessage.query
            .filter(EmailMessage.status == "pending")
            .filter((EmailMessage.next_attempt.is_(None)) | (EmailMessage.next_attempt <= utcnow()))
            .order_by(EmailMessage.id)
            .limit(limit)
            .all()
        )
        if not messages:
            return {"sent": 0, "failed": 0, "pending": 0}

        if _use_remote_transport(app):
            _deliver_batch(app, messages)
            sent = sum(1 for message in messages if message.status == "sent")
            failed = sum(1 for message in messages if message.status == "failed")
        else:
            for message in messages:
                _store_in_outbox(app, message)
                _mark_sent(message)
                sent += 1

        db.session.commit()
        pending = EmailMessage.query.filter_by(status="pending").count()
    return {"sent": sent, "failed": failed, "pending": pending}


def _use_remote_transport(app) -> bool:
    """True when a real SMTP server should be used."""
    return bool(app.config["SMTP_HOST"]) and not app.config["MAIL_DRY_RUN"]


def _mark_sent(message: EmailMessage) -> None:
    message.status = "sent"
    message.sent_at = utcnow()
    message.last_error = None


def _mark_failed(app, message: EmailMessage, error: Exception) -> None:
    message.attempts += 1
    message.last_error = str(error)[:400]
    if message.attempts >= app.config["MAIL_MAX_ATTEMPTS"]:
        message.status = "failed"
    else:
        message.next_attempt = utcnow() + timedelta(minutes=min(60, 2 ** message.attempts))


def _deliver_batch(app, messages: list) -> None:
    """Open one connection and push the whole batch through it."""
    context = ssl.create_default_context()
    timeout = app.config.get("SMTP_TIMEOUT", 30)
    host = app.config["SMTP_HOST"]
    port = app.config["SMTP_PORT"]

    if app.config["SMTP_TLS"]:
        connection = smtplib.SMTP(host, port, timeout=timeout)
    else:
        connection = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)

    try:
        if app.config["SMTP_TLS"]:
            connection.ehlo()
            connection.starttls(context=context)
            connection.ehlo()
        if app.config["SMTP_USER"]:
            connection.login(app.config["SMTP_USER"], app.config["SMTP_PASSWORD"])

        for message in messages:
            try:
                connection.send_message(_build_message(app, message))
                _mark_sent(message)
            except Exception as exc:  # pragma: no cover - depends on SMTP server
                _mark_failed(app, message, exc)
                if _connection_is_broken(exc):
                    break
    finally:
        try:
            connection.quit()
        except Exception:  # pragma: no cover - best effort
            pass


def _connection_is_broken(error: Exception) -> bool:
    """A dropped connection must not burn every remaining attempt."""
    return isinstance(
        error,
        (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError),
    )


def _build_message(app, message: EmailMessage) -> SMTPMessage:
    mail = SMTPMessage()
    mail["Subject"] = message.subject
    mail["From"] = formataddr((app.config["MAIL_FROM_NAME"], app.config["MAIL_FROM"]))
    mail["To"] = message.recipient
    mail["Auto-Submitted"] = "auto-generated"
    mail.set_content(message.body)
    return mail


def _store_in_outbox(app, message: EmailMessage) -> None:
    """Dry-run mode: write the message to disk so it can be inspected."""
    directory = Path(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")).parent / "mail_outbox"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in message.recipient)
    path = directory / f"{stamp}-{message.id}-{safe}.txt"
    path.write_text(
        f"To: {message.recipient}\nSubject: {message.subject}\n\n{message.body}\n",
        encoding="utf-8",
    )


_worker_lock = threading.Lock()


def process_email_queue_start(app) -> dict:
    """Dispatch due mails inline, but never twice at the same time.

    The web process uses this so that a freshly queued message goes out right
    away in development. In production the worker process handles it.
    """
    if not _worker_lock.acquire(blocking=False):
        return {"sent": 0, "failed": 0, "skipped": True}
    try:
        return process_email_queue(app)
    finally:
        _worker_lock.release()


def start_mail_worker(app) -> threading.Thread:
    """Start the background thread that drains the outbox."""
    interval = max(3, app.config["MAIL_WORKER_INTERVAL"])
    batch = app.config["MAIL_BATCH_SIZE"]

    def loop() -> None:
        while True:
            try:
                if _worker_lock.acquire(blocking=False):
                    try:
                        process_email_queue(app, limit=batch)
                    finally:
                        _worker_lock.release()
            except Exception:  # pragma: no cover - keep the worker alive
                app.logger.exception("mail worker iteration failed")
            threading.Event().wait(interval)

    thread = threading.Thread(target=loop, name="chargefair-mail", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Distribution statistics
# ---------------------------------------------------------------------------
#
# There is deliberately no scoring or point system behind the allocation:
# nobody is preferred for having waited longer. The statistics below only
# describe how the granted charging slots are spread, so the administration
# can spot structural imbalance and react (more capacity, different grid).


def distribution_report(weeks: int = 12, reference: date | None = None) -> dict:
    """Aggregate statistics for the admin dashboard.

    Answers the question "how evenly are the charging slots distributed?"
    without ranking people or awarding points.
    """
    reference = reference or date.today()
    start = datetime.combine(reference - timedelta(weeks=weeks), datetime.min.time())
    rows = (
        db.session.query(Booking.user_id, db.func.count(Booking.id))
        .filter(Booking.start >= start)
        .filter(Booking.status.in_((BOOKING_ACTIVE, BOOKING_COMPLETED)))
        .group_by(Booking.user_id)
        .all()
    )
    counts = {user_id: count for user_id, count in rows}
    users = User.query.filter(User.role == "user").all()
    values = [counts.get(user.id, 0) for user in users]
    total = sum(values)
    mean = total / len(values) if values else 0.0
    variance = (
        sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
    )
    buckets = {
        "none": sum(1 for value in values if value == 0),
        "low": sum(1 for value in values if 1 <= value <= 5),
        "mid": sum(1 for value in values if 6 <= value <= 10),
        "high": sum(1 for value in values if value > 10),
    }
    ranked = sorted(users, key=lambda user: (-counts.get(user.id, 0), user.last_name))
    return {
        "weeks": weeks,
        "users": len(users),
        "total": total,
        "mean": round(mean, 2),
        "stddev": round(variance ** 0.5, 2),
        "buckets": buckets,
        "max": max(values) if values else 0,
        "min": min(values) if values else 0,
        "chart": [
            {"name": user.full_name, "count": counts.get(user.id, 0)}
            for user in ranked[:25]
        ],
        "counts": counts,
    }


def week_of(value: date) -> date:
    return week_start(value)


def describe_week(monday: date) -> str:
    return f"KW {calendar_week(monday)} ({monday.strftime('%d.%m.')}–{(monday + timedelta(days=6)).strftime('%d.%m.%Y')})"


def anonymize_user(user: User, keep_statistics: bool = True) -> None:
    """GDPR deletion: strip personal data, keep anonymous statistics."""
    user.first_name = "Ehemalige/r"
    user.last_name = f"Mitarbeiter/in #{user.id}"
    user.email = f"deleted+{user.id}@invalid.local"
    user.phone = None
    user.department = None
    user.is_active = False
    user.password_hash = "!"
    user.verification_token = None
    user.reset_token = None
    user.anonymized_at = utcnow()
    for vehicle in list(user.vehicles):
        db.session.delete(vehicle)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}
