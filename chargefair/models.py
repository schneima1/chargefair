"""SQLAlchemy data model.

Layout:
  User -- Vehicle
  ChargingStation -- ChargingPoint / ParkingSpace
  BookingSeries -- Booking (concrete occurrences, including releases/takeovers)
  BookingRound -- RoundParticipation / BookingRequest  (allocation rounds)
  SlotInterest, WaitlistEntry, SwapOffer, Post/Comment, EmailMessage, AuditLog
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .slots import DAY_SHORT, RECURRENCE_LABELS, recurrence_label


def utcnow() -> datetime:
    """Naive local time -- the application serves a single site."""
    return datetime.now()

class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Users & vehicles
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model, TimestampMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40))
    department = db.Column(db.String(80))
    role = db.Column(db.String(16), default="user", nullable=False)  # user | admin
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    verification_token = db.Column(db.String(64), index=True)
    verification_expires = db.Column(db.DateTime)
    reset_token = db.Column(db.String(64), index=True)
    reset_expires = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    anonymized_at = db.Column(db.DateTime)

    # Allocation preferences
    preferred_days = db.Column(db.String(32), default="0,1,2,3,4")
    preferred_windows = db.Column(db.String(32), default="1,2,3,0")
    shift_model = db.Column(db.String(24), default="flex")
    max_weekly_slots = db.Column(db.Integer)

    # Notification preferences
    reminders_enabled = db.Column(db.Boolean, default=True, nullable=False)
    phase_mails_enabled = db.Column(db.Boolean, default=True, nullable=False)

    vehicles = db.relationship(
        "Vehicle", backref="user", cascade="all, delete-orphan", lazy="selectin"
    )
    bookings = db.relationship(
        "Booking", backref="user", foreign_keys="Booking.user_id",
        cascade="all, delete-orphan", lazy="dynamic",
    )

    # --- Password -------------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # --- Roles ----------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def short_name(self) -> str:
        return f"{self.first_name} {self.last_name[:1]}." if self.last_name else self.first_name

    @property
    def primary_vehicle(self) -> Optional["Vehicle"]:
        return self.vehicles[0] if self.vehicles else None

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > utcnow())

    def weekly_limit(self, default: int = 2) -> int:
        return self.max_weekly_slots or default

    def day_list(self) -> list[int]:
        return _parse_int_list(self.preferred_days, [0, 1, 2, 3, 4])

    def window_order(self) -> list[int]:
        return _parse_int_list(self.preferred_windows, [1, 2, 3, 0])

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


class Vehicle(db.Model, TimestampMixin):
    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    type = db.Column(db.String(8), default="BEV", nullable=False)  # BEV or PHEV
    manufacturer = db.Column(db.String(80))
    model = db.Column(db.String(80))
    license_plate = db.Column(db.String(24))
    power_kw = db.Column(db.Float, default=11.0)
    battery_kwh = db.Column(db.Float)

    @property
    def display(self) -> str:
        parts = [part for part in (self.manufacturer, self.model) if part]
        return " ".join(parts) or "Fahrzeug"


# ---------------------------------------------------------------------------
# Charging infrastructure
# ---------------------------------------------------------------------------

class ChargingStation(db.Model, TimestampMixin):
    __tablename__ = "charging_stations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    location = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    points = db.relationship("ChargingPoint", backref="station", cascade="all, delete-orphan")
    parking_spaces = db.relationship("ParkingSpace", backref="station", cascade="all, delete-orphan")


class ChargingPoint(db.Model, TimestampMixin):
    __tablename__ = "charging_points"

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("charging_stations.id"), nullable=False)
    name = db.Column(db.String(40), nullable=False)
    connector_type = db.Column(db.String(24), default="Typ 2")
    power_kw = db.Column(db.Float, default=11.0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.station.name})" if self.station else self.name


class ParkingSpace(db.Model, TimestampMixin):
    __tablename__ = "parking_spaces"

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("charging_stations.id"))
    charging_point_id = db.Column(db.Integer, db.ForeignKey("charging_points.id"))
    name = db.Column(db.String(40), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    charging_point = db.relationship("ChargingPoint", backref="parking_spaces")


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

BOOKING_ACTIVE = "active"
BOOKING_RELEASED = "released"
BOOKING_CANCELLED = "cancelled"
BOOKING_COMPLETED = "completed"
BOOKING_NO_SHOW = "no_show"

BOOKING_STATUS_LABELS = {
    BOOKING_ACTIVE: "Bestätigt",
    BOOKING_RELEASED: "Freigegeben",
    BOOKING_CANCELLED: "Storniert",
    BOOKING_COMPLETED: "Wahrgenommen",
    BOOKING_NO_SHOW: "Nicht wahrgenommen",
}

BOOKING_ORIGIN_LABELS = {
    "allocation": "Vergaberunde",
    "self": "Eigene Buchung",
    "takeover": "Übernommen",
    "swap": "Getauscht",
}


class BookingSeries(db.Model, TimestampMixin):
    """Recurring charging slot (e.g. every Monday 09:30, odd calendar weeks)."""

    __tablename__ = "booking_series"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    charging_point_id = db.Column(db.Integer, db.ForeignKey("charging_points.id"), nullable=False)
    parking_space_id = db.Column(db.Integer, db.ForeignKey("parking_spaces.id"))
    weekday = db.Column(db.Integer, nullable=False)
    window_index = db.Column(db.Integer, nullable=False)
    recurrence = db.Column(db.String(16), default="weekly", nullable=False)
    valid_from = db.Column(db.Date, nullable=False)
    valid_until = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    user = db.relationship("User", backref="series")
    charging_point = db.relationship("ChargingPoint")
    parking_space = db.relationship("ParkingSpace")
    bookings = db.relationship("Booking", backref="series", cascade="all, delete-orphan")

    @property
    def recurrence_label(self) -> str:
        return recurrence_label(self.recurrence)


class Booking(db.Model, TimestampMixin):
    """A single concrete charging appointment."""

    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    booking_series_id = db.Column(db.Integer, db.ForeignKey("booking_series.id"))
    charging_point_id = db.Column(db.Integer, db.ForeignKey("charging_points.id"), nullable=False)
    parking_space_id = db.Column(db.Integer, db.ForeignKey("parking_spaces.id"))
    start = db.Column(db.DateTime, nullable=False, index=True)
    end = db.Column(db.DateTime, nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    window_index = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), default=BOOKING_ACTIVE, nullable=False, index=True)
    origin = db.Column(db.String(16), default="self", nullable=False)
    allocation_round_id = db.Column(db.Integer, db.ForeignKey("booking_rounds.id"))
    previous_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    released_at = db.Column(db.DateTime)
    release_note = db.Column(db.String(255))
    checked_in_at = db.Column(db.DateTime)
    no_show_reported = db.Column(db.Boolean, default=False, nullable=False)

    charging_point = db.relationship("ChargingPoint")
    parking_space = db.relationship("ParkingSpace")
    previous_user = db.relationship("User", foreign_keys=[previous_user_id])

    @property
    def status_label(self) -> str:
        return BOOKING_STATUS_LABELS.get(self.status, self.status)

    @property
    def origin_label(self) -> str:
        return BOOKING_ORIGIN_LABELS.get(self.origin, self.origin)

    @property
    def is_free(self) -> bool:
        return self.status == BOOKING_RELEASED

    @property
    def is_open(self) -> bool:
        return self.status in (BOOKING_ACTIVE, BOOKING_RELEASED)

    @property
    def window_label(self) -> str:
        from .services import windows

        from .slots import window_label

        return window_label(windows(), self.window_index)

    @property
    def slot_label(self) -> str:
        from .services import windows

        from .slots import window_label

        return f"{DAY_SHORT[self.weekday]} {window_label(windows(), self.window_index)}"

    @property
    def date_label(self) -> str:
        return self.start.strftime("%d.%m.%Y")

    @property
    def is_past(self) -> bool:
        return self.end < utcnow()


# ---------------------------------------------------------------------------
# Allocation rounds
# ---------------------------------------------------------------------------

ROUND_OPEN = "open"
ROUND_CLOSED = "closed"
ROUND_ALLOCATED = "allocated"

PHASE_OPEN = "open"
PHASE_ALLOCATED = "allocated"
PHASE_CLOSED = "closed"


class BookingPhase(db.Model, TimestampMixin):
    """An allocation phase covering several weeks at once (default: four).

    Employees file their wishes once for the whole phase; the allocation then
    runs for every week inside it. This keeps the administrative effort low and
    gives employees a predictable rhythm ("every four weeks").
    """

    __tablename__ = "booking_phases"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    start_week = db.Column(db.Date, nullable=False, index=True)
    weeks = db.Column(db.Integer, default=4, nullable=False)
    method = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), default=PHASE_OPEN, nullable=False)
    opens_at = db.Column(db.DateTime)
    closes_at = db.Column(db.DateTime)
    allocated_at = db.Column(db.DateTime)
    note = db.Column(db.String(255))
    stats = db.Column(db.JSON)
    notified_at = db.Column(db.DateTime)

    rounds = db.relationship(
        "BookingRound",
        backref="phase",
        cascade="all, delete-orphan",
        order_by="BookingRound.week_start",
    )

    @property
    def end_week(self) -> date:
        """Monday of the last week inside the phase."""
        return self.start_week + timedelta(days=7 * (max(1, self.weeks) - 1))

    @property
    def last_day(self) -> date:
        return self.end_week + timedelta(days=6)

    @property
    def status_label(self) -> str:
        return {
            PHASE_OPEN: "Offen",
            PHASE_ALLOCATED: "Verteilt",
            PHASE_CLOSED: "Geschlossen",
        }.get(self.status, self.status)

    @property
    def is_open(self) -> bool:
        return self.status == PHASE_OPEN

    @property
    def week_list(self) -> list:
        return [self.start_week + timedelta(days=7 * index) for index in range(max(1, self.weeks))]

    @property
    def period_label(self) -> str:
        return (
            f"KW {self.start_week.isocalendar().week}–{self.end_week.isocalendar().week} "
            f"({self.start_week.strftime('%d.%m.')}–{self.last_day.strftime('%d.%m.%Y')})"
        )


class BookingRound(db.Model, TimestampMixin):
    """One allocation round covering a single calendar week.

    Rounds are normally created as part of a :class:`BookingPhase` (four weeks
    at a time) but an administrator may also run a single week on its own.
    """

    __tablename__ = "booking_rounds"

    id = db.Column(db.Integer, primary_key=True)
    week_start = db.Column(db.Date, nullable=False, index=True)
    phase_id = db.Column(db.Integer, db.ForeignKey("booking_phases.id"))
    method = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), default=ROUND_OPEN, nullable=False)
    opens_at = db.Column(db.DateTime)
    closes_at = db.Column(db.DateTime)
    allocated_at = db.Column(db.DateTime)
    note = db.Column(db.String(255))
    stats = db.Column(db.JSON)

    participations = db.relationship(
        "RoundParticipation", backref="round", cascade="all, delete-orphan"
    )
    requests = db.relationship("BookingRequest", backref="round", cascade="all, delete-orphan")

    @property
    def status_label(self) -> str:
        return {"open": "Offen", "closed": "Geschlossen", "allocated": "Verteilt"}.get(
            self.status, self.status
        )


class RoundParticipation(db.Model, TimestampMixin):
    """Participation of a user in a round (timestamp + requested amount)."""

    __tablename__ = "round_participations"

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("booking_rounds.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    desired_count = db.Column(db.Integer, default=1, nullable=False)
    note = db.Column(db.String(255))

    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("round_id", "user_id", name="uq_round_user"),)


REQUEST_PENDING = "pending"
REQUEST_GRANTED = "granted"
REQUEST_REJECTED = "rejected"


class BookingRequest(db.Model, TimestampMixin):
    """One wish inside an allocation round (priority 1 = most wanted slot)."""

    __tablename__ = "booking_requests"

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("booking_rounds.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    priority = db.Column(db.Integer, default=1, nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    window_index = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), default=REQUEST_PENDING, nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))

    user = db.relationship("User")
    booking = db.relationship("Booking")

    @property
    def status_label(self) -> str:
        return {
            REQUEST_PENDING: "offen",
            REQUEST_GRANTED: "erfüllt",
            REQUEST_REJECTED: "nicht erfüllt",
        }.get(self.status, self.status)


# ---------------------------------------------------------------------------
# Interest in a specific slot
# ---------------------------------------------------------------------------

INTEREST_WAITING = "waiting"
INTEREST_NOTIFIED = "notified"
INTEREST_ACCEPTED = "accepted"
INTEREST_EXPIRED = "expired"
INTEREST_CANCELLED = "cancelled"

INTEREST_STATUS_LABELS = {
    INTEREST_WAITING: "vorgemerkt",
    INTEREST_NOTIFIED: "benachrichtigt",
    INTEREST_ACCEPTED: "übernommen",
    INTEREST_EXPIRED: "abgelaufen",
    INTEREST_CANCELLED: "zurückgezogen",
}


class SlotInterest(db.Model, TimestampMixin):
    """Interest in one concrete slot that is currently taken by somebody else.

    Different from the waitlist: the waitlist covers slots without any free
    capacity, while an interest targets a *specific, occupied* slot. As soon as
    its owner releases or cancels it, everybody who registered an interest is
    notified by email and can take it over with one click.
    """

    __tablename__ = "slot_interests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    week_start = db.Column(db.Date, nullable=False, index=True)
    weekday = db.Column(db.Integer, nullable=False)
    window_index = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), default=INTEREST_WAITING, nullable=False)
    notified_at = db.Column(db.DateTime)
    taken_booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))
    note = db.Column(db.String(255))

    user = db.relationship("User")
    taken_booking = db.relationship("Booking", foreign_keys=[taken_booking_id])

    __table_args__ = (
        db.UniqueConstraint("user_id", "week_start", "weekday", "window_index",
                            name="uq_interest_slot"),
    )

    @property
    def status_label(self) -> str:
        return INTEREST_STATUS_LABELS.get(self.status, self.status)

    @property
    def is_active(self) -> bool:
        return self.status in (INTEREST_WAITING, INTEREST_NOTIFIED)


# ---------------------------------------------------------------------------
# Waitlist
# ---------------------------------------------------------------------------

WAITLIST_WAITING = "waiting"
WAITLIST_OFFERED = "offered"
WAITLIST_ACCEPTED = "accepted"
WAITLIST_EXPIRED = "expired"
WAITLIST_CANCELLED = "cancelled"


class WaitlistEntry(db.Model, TimestampMixin):
    """Waitlist entry for one slot (week + weekday + time window)."""

    __tablename__ = "waitlist"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    week_start = db.Column(db.Date, nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    window_index = db.Column(db.Integer, nullable=False)
    position = db.Column(db.Integer, default=1, nullable=False)
    status = db.Column(db.String(16), default=WAITLIST_WAITING, nullable=False)
    offered_booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))
    offer_expires_at = db.Column(db.DateTime)
    note = db.Column(db.String(255))

    user = db.relationship("User")
    offered_booking = db.relationship("Booking")

    @property
    def status_label(self) -> str:
        return {
            WAITLIST_WAITING: "wartet",
            WAITLIST_OFFERED: "Angebot offen",
            WAITLIST_ACCEPTED: "angenommen",
            WAITLIST_EXPIRED: "abgelaufen",
            WAITLIST_CANCELLED: "zurückgezogen",
        }.get(self.status, self.status)

    @property
    def is_offer_expired(self) -> bool:
        return bool(self.offer_expires_at and self.offer_expires_at < utcnow())


# ---------------------------------------------------------------------------
# Swaps
# ---------------------------------------------------------------------------

SWAP_OPEN = "open"
SWAP_ACCEPTED = "accepted"
SWAP_CANCELLED = "cancelled"


class SwapOffer(db.Model, TimestampMixin):
    """Swap offer: give away your own slot and receive someone else's in return."""

    __tablename__ = "swap_offers"

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    wanted_weekday = db.Column(db.Integer)
    wanted_window_index = db.Column(db.Integer)
    message = db.Column(db.String(400))
    status = db.Column(db.String(16), default=SWAP_OPEN, nullable=False)
    accepted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    accepted_booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))

    booking = db.relationship("Booking", foreign_keys=[booking_id])
    accepted_booking = db.relationship("Booking", foreign_keys=[accepted_booking_id])
    user = db.relationship("User", foreign_keys=[user_id])
    accepted_by = db.relationship("User", foreign_keys=[accepted_by_id])


# ---------------------------------------------------------------------------
# Board (forum)
# ---------------------------------------------------------------------------

class Post(db.Model, TimestampMixin):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    content = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(16), default="info", nullable=False)  # info | offer | request
    topic = db.Column(db.String(24), default="ladeplatz", nullable=False, index=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))
    is_closed = db.Column(db.Boolean, default=False, nullable=False)
    is_pinned = db.Column(db.Boolean, default=False, nullable=False)
    # Feedback only.
    anonymous = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(16), default="open", nullable=False)
    admin_reply = db.Column(db.Text)
    replied_at = db.Column(db.DateTime)

    user = db.relationship("User")
    booking = db.relationship("Booking")
    comments = db.relationship(
        "Comment", backref="post", cascade="all, delete-orphan",
        order_by="Comment.created_at",
    )

    @property
    def kind_label(self) -> str:
        return POST_KIND_LABELS.get(self.kind, self.kind)

    @property
    def topic_label(self) -> str:
        return POST_TOPIC_LABELS.get(self.topic, self.topic)

    @property
    def status_label(self) -> str:
        return POST_STATUS_LABELS.get(self.status, self.status)

    @property
    def author_label(self) -> str:
        return "Anonym" if self.anonymous else self.user.short_name

    @property
    def is_feedback(self) -> bool:
        return self.topic == TOPIC_FEEDBACK


POST_TOPIC_LADE = "ladeplatz"
TOPIC_FEEDBACK = "verbesserung"

POST_TOPIC_LABELS = {
    "ladeplatz": "Ladeplatz",
    "allgemein": "Allgemein",
    "verbesserung": "Verbesserungsvorschlag",
    "frage": "Frage",
}
POST_KIND_LABELS = {
    "info": "Information",
    "offer": "Angebot",
    "request": "Gesuch",
    "feedback": "Feedback",
}

POST_STATUS_LABELS = {
    "open": "offen",
    "in_progress": "in Prüfung",
    "done": "umgesetzt",
    "declined": "abgelehnt",
}


def topic_choices() -> list:
    """Topics a regular employee may pick when writing a post."""
    return [
        ("ladeplatz", POST_TOPIC_LABELS["ladeplatz"]),
        ("allgemein", POST_TOPIC_LABELS["allgemein"]),
        ("frage", POST_TOPIC_LABELS["frage"]),
    ]


class Comment(db.Model, TimestampMixin):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)

    user = db.relationship("User")


# ---------------------------------------------------------------------------
# Email queue & audit log
# ---------------------------------------------------------------------------

MAIL_PENDING = "pending"
MAIL_SENT = "sent"
MAIL_FAILED = "failed"


class EmailMessage(db.Model, TimestampMixin):
    __tablename__ = "email_queue"

    id = db.Column(db.Integer, primary_key=True)
    recipient = db.Column(db.String(255), nullable=False, index=True)
    subject = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(40))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))
    status = db.Column(db.String(16), default=MAIL_PENDING, nullable=False, index=True)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    last_error = db.Column(db.String(400))
    next_attempt = db.Column(db.DateTime, default=utcnow)
    sent_at = db.Column(db.DateTime)


class AuditLog(db.Model, TimestampMixin):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    actor_label = db.Column(db.String(120))
    action = db.Column(db.String(80), nullable=False)
    detail = db.Column(db.String(500))
    ip = db.Column(db.String(60))

    actor = db.relationship("User")


# ---------------------------------------------------------------------------
# Settings & registration tokens
# ---------------------------------------------------------------------------

class Setting(db.Model, TimestampMixin):
    __tablename__ = "settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)
    description = db.Column(db.String(255))

    @staticmethod
    def get(key: str, default=None):
        row = db.session.get(Setting, key)
        if row is None or row.value is None:
            return default
        return row.value

    @staticmethod
    def get_int(key: str, default: int) -> int:
        try:
            return int(Setting.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def get_float(key: str, default: float) -> float:
        try:
            return float(Setting.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def get_bool(key: str, default: bool = False) -> bool:
        raw = Setting.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "ja"}

    @staticmethod
    def get_json(key: str, default=None):
        import json

        raw = Setting.get(key)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def set(key: str, value, description: str | None = None) -> "Setting":
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key, description=description)
            db.session.add(row)
        row.value = None if value is None else (
            value if isinstance(value, str) else _to_json(value)
        )
        return row


def _to_json(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


class RegistrationToken(db.Model, TimestampMixin):
    """Registration code created and revocable by an administrator."""

    __tablename__ = "registration_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(40), unique=True, nullable=False, index=True)
    label = db.Column(db.String(120))
    max_uses = db.Column(db.Integer, default=1, nullable=False)
    uses = db.Column(db.Integer, default=0, nullable=False)
    expires_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    created_by = db.relationship("User")

    @staticmethod
    def generate_token() -> str:
        raw = secrets.token_hex(6).upper()
        return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"

    @property
    def is_valid(self) -> bool:
        if not self.is_active or self.uses >= self.max_uses:
            return False
        if self.expires_at and self.expires_at < utcnow():
            return False
        return True


def _parse_int_list(raw: str | None, default: list[int]) -> list[int]:
    if not raw:
        return list(default)
    values = []
    for part in str(raw).split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            values.append(int(part))
    return values or list(default)
