"""Database bootstrap: schema, infrastructure, demo users and history.

Charging infrastructure is deliberately *vehicle agnostic*: all eight charging
points are Type 2 AC points and are available to BEV and PHEV alike. The vehicle
type is stored for information purposes (and for the overview table) but never
restricts access to a charging point or a time window. There is exactly one slot
grid for everybody.
"""
from __future__ import annotations

import os
import random
from datetime import date, datetime, time as dtime, timedelta

from .allocation import DEFAULT_METHOD, METHODS
from .extensions import db
from .models import (
    BOOKING_ACTIVE,
    BOOKING_COMPLETED,
    BOOKING_RELEASED,
    Booking,
    BookingRequest,
    BookingRound,
    ChargingPoint,
    ChargingStation,
    ParkingSpace,
    RegistrationToken,
    RoundParticipation,
    Setting,
    User,
    Vehicle,
    utcnow,
)
from .services import (
    KEY_ALLOCATION_METHOD,
    KEY_CHARGING_POINTS,
    KEY_DISTRIBUTION_WINDOW_WEEKS,
    KEY_MAX_REGULAR_PER_WEEK,
    KEY_MAX_SLOTS_PER_USER,
    KEY_PHASE_REMINDER_DAYS,
    KEY_PHASE_WEEKS,
    KEY_SLOT_REMINDER_MINUTES,
    KEY_WAITLIST_OFFER_MINUTES,
    KEY_WINDOWS,
    KEY_WORKDAYS,
    audit,
)
from .slots import DEFAULT_WINDOWS, DEFAULT_WORKDAYS, week_dates

DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChargeFair!2026")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Demo!2026")

FIRST_NAMES = (
    "Anna", "Lukas", "Sophie", "Maximilian", "Marie", "Felix", "Laura", "Jonas",
    "Julia", "David", "Lea", "Philipp", "Sarah", "Moritz", "Nina", "Tobias",
    "Katharina", "Sebastian", "Hannah", "Alexander", "Mia", "Paul", "Emma", "Leon",
)
LAST_NAMES = (
    "Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker",
    "Schulz", "Hoffmann", "Schäfer", "Koch", "Bauer", "Richter", "Klein", "Wolf",
    "Schröder", "Neumann", "Schwarz", "Zimmermann", "Braun", "Krüger", "Hofmann", "Hartmann",
)
DEPARTMENTS = ("Entwicklung", "Vertrieb", "Produktion", "Qualität", "Verwaltung", "IT")

VEHICLES = (
    # (type, manufacturer, model, plate suffix, power_kw, battery_kwh)
    ("BEV", "VW", "ID.3 Pro", "E", 11.0, 58.0),
    ("BEV", "Tesla", "Model 3", "E", 11.0, 60.0),
    ("BEV", "Skoda", "Enyaq 85", "E", 11.0, 77.0),
    ("BEV", "Cupra", "Born 58", "E", 11.0, 58.0),
    ("BEV", "BMW", "i4 eDrive40", "E", 11.0, 81.0),
    ("BEV", "Hyundai", "Ioniq 5", "E", 11.0, 72.6),
    ("BEV", "Renault", "Zoe", "E", 22.0, 52.0),
    ("BEV", "Opel", "Corsa Electric", "E", 11.0, 50.0),
    ("PHEV", "VW", "Golf GTE", "E", 3.6, 13.0),
    ("PHEV", "Mercedes", "C300 e", "E", 11.0, 25.6),
    ("PHEV", "BMW", "330e", "E", 3.7, 12.0),
    ("PHEV", "Kia", "Niro PHEV", "E", 3.3, 11.1),
    ("BEV", "Audi", "Q4 45 e-tron", "E", 11.0, 77.0),
    ("BEV", "Peugeot", "e-208", "E", 11.0, 50.0),
    ("BEV", "Kia", "EV6", "E", 11.0, 77.4),
    ("BEV", "Fiat", "500e", "E", 11.0, 42.0),
    ("PHEV", "Volvo", "XC60 T8", "E", 3.6, 18.8),
    ("BEV", "Nissan", "Leaf e+", "E", 6.6, 59.0),
    ("BEV", "Polestar", "2", "E", 11.0, 82.0),
    ("BEV", "Renault", "Megane E-Tech", "E", 22.0, 60.0),
)

SHIFT_MODELS = ("frueh", "spaet", "flex")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Columns that were removed from the models. SQLite cannot drop a NOT NULL
# column implicitly, and inserts would fail as soon as a retired column has no
# database-level default left. Listing them explicitly keeps the sync safe:
# only these known columns are ever dropped.
RETIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("fairness_points",),
    "fairness_history": ("points",),
}

# Tables whose models no longer exist and that can therefore be dropped.
RETIRED_TABLES: tuple[str, ...] = ("fairness_history",)


def init_db(app) -> None:
    with app.app_context():
        db.create_all()
        sync_schema()
        _seed_settings()
        _seed_infrastructure()
        _seed_admin()
        if os.environ.get("SEED_DEMO", "1") not in {"0", "false", "no"}:
            _seed_demo_users()


def sync_schema() -> None:
    """Bring an existing SQLite database in line with the models.

    ``db.create_all()`` only creates *missing* tables - it never touches the
    columns of an existing one. Because the application ships as a single
    container without a migration tool, new columns are added here on startup.
    Only additive (plus a short, explicit list of retired) changes are applied,
    so no data is lost.
    """
    from sqlalchemy import inspect, text

    engine = db.engine
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for name in RETIRED_TABLES:
        if name in existing_tables:
            db.session.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
            existing_tables.discard(name)

    changes: list[str] = []
    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # freshly created by create_all()
        present = {column["name"] for column in inspector.get_columns(table.name)}

        for column in table.columns:
            if column.name in present:
                continue
            ddl = _add_column_ddl(table.name, column)
            db.session.execute(text(ddl))
            changes.append(f"{table.name}.{column.name}")

        for retired in RETIRED_COLUMNS.get(table.name, ()):
            if retired in present:
                db.session.execute(
                    text(f'ALTER TABLE "{table.name}" DROP COLUMN "{retired}"')
                )
                changes.append(f"{table.name}.{retired} (entfernt)")

    db.session.commit()
    if changes:
        print(f"[schema] angepasst: {', '.join(changes)}")


def _add_column_ddl(table_name: str, column) -> str:
    """Build an ``ALTER TABLE ... ADD COLUMN`` statement with a safe default.

    SQLite requires a default for NOT NULL columns when the table already holds
    rows, so one is derived from the model default or a neutral fallback.
    """
    from sqlalchemy import Boolean, Date, DateTime, Float, Integer

    if isinstance(column.type, Boolean):
        sql_type = "BOOLEAN"
    elif isinstance(column.type, Integer):
        sql_type = "INTEGER"
    elif isinstance(column.type, Float):
        sql_type = "FLOAT"
    elif isinstance(column.type, DateTime):
        sql_type = "DATETIME"
    elif isinstance(column.type, Date):
        sql_type = "DATE"
    else:
        sql_type = "VARCHAR"

    clause = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {sql_type}'

    default = column.default.arg if column.default is not None else None
    if callable(default):
        default = None
    if default is None and not column.nullable:
        if isinstance(column.type, Boolean):
            default = True
        elif isinstance(column.type, (Integer, Float)):
            default = 0
        else:
            default = ""

    if default is not None:
        rendered = "1" if default is True else "0" if default is False else default
        if isinstance(rendered, str):
            rendered = "'" + rendered.replace("'", "''") + "'"
        clause += f" DEFAULT {rendered}"

    return clause


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _seed_settings() -> None:
    """Create the default settings on a fresh database.

    The environment variables ``ALLOCATION_METHOD``, ``MAX_SLOTS_PER_WEEK`` and
    ``GUARANTEED_SLOTS_PER_WEEK`` only apply on the first start; afterwards the
    administration owns these values.
    """
    from flask import current_app

    method = current_app.config.get("ALLOCATION_METHOD_DEFAULT") or DEFAULT_METHOD
    if method not in METHODS:
        method = DEFAULT_METHOD
    maximum = max(1, int(current_app.config.get("MAX_SLOTS_PER_WEEK_DEFAULT", 3)))
    guarantee = max(1, int(current_app.config.get("GUARANTEED_SLOTS_PER_WEEK_DEFAULT", 1)))
    phase_length = max(1, min(12, int(current_app.config.get("PHASE_WEEKS_DEFAULT", 4))))
    phase_reminder = max(0, int(current_app.config.get("PHASE_REMINDER_DAYS_DEFAULT", 2)))
    slot_reminder = max(5, int(current_app.config.get("SLOT_REMINDER_MINUTES_DEFAULT", 45)))

    defaults = [
        (KEY_WINDOWS, list(DEFAULT_WINDOWS), "Ladezeitfenster in Minuten seit Mitternacht"),
        (KEY_WORKDAYS, list(DEFAULT_WORKDAYS), "Werktage mit Ladebetrieb (0 = Montag)"),
        (KEY_ALLOCATION_METHOD, method, "Aktives Vergabeverfahren"),
        (KEY_MAX_SLOTS_PER_USER, maximum, "Maximale Ladezeiten je Person und Woche"),
        (KEY_MAX_REGULAR_PER_WEEK, min(guarantee, maximum),
         "Garantierte Ladezeit je Person und Woche"),
        (KEY_WAITLIST_OFFER_MINUTES, 30, "Reaktionszeit für Wartelisten-Angebote (Minuten)"),
        (KEY_DISTRIBUTION_WINDOW_WEEKS, 12, "Zeitraum der Verteilungsstatistik (Wochen)"),
        (KEY_PHASE_WEEKS, phase_length, "Wochen je Vergabephase"),
        (KEY_PHASE_REMINDER_DAYS, phase_reminder,
         "Tage vor Fristende für die Erinnerung (0 = aus)"),
        (KEY_SLOT_REMINDER_MINUTES, slot_reminder,
         "Minuten vor Ladebeginn für die Erinnerung"),
        ("reminder.enabled", True, "Erinnerungen vor Ladebeginn/-ende versenden"),
    ]
    changed = False
    for key, value, description in defaults:
        if db.session.get(Setting, key) is None:
            Setting.set(key, value, description)
            changed = True
    if changed:
        db.session.commit()


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

def _seed_infrastructure() -> None:
    """Create four stations with two identical charging points each."""
    if ChargingStation.query.count():
        return

    stations = []
    for index in range(1, 5):
        station = ChargingStation(
            name=f"Ladesäule {index}",
            location="Mitarbeiterparkplatz P1" if index <= 2 else "Mitarbeiterparkplatz P2",
        )
        db.session.add(station)
        db.session.flush()
        stations.append(station)

        for letter in ("A", "B"):
            db.session.add(
                ChargingPoint(
                    station_id=station.id,
                    name=f"{index}{letter}",
                    connector_type="Typ 2",
                    power_kw=11.0,
                )
            )
        db.session.flush()

    # 14 parking spaces; the first eight belong to a charging point.
    points = ChargingPoint.query.order_by(ChargingPoint.id).all()
    for index in range(1, 15):
        point = points[index - 1] if index <= len(points) else None
        db.session.add(
            ParkingSpace(
                station_id=point.station_id if point else stations[-1].id,
                charging_point_id=point.id if point else None,
                name=f"P{index:02d}",
            )
        )
    db.session.commit()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _seed_admin() -> None:
    if User.query.filter_by(role="admin").count():
        return
    admin = User(
        email=DEFAULT_ADMIN_EMAIL,
        first_name="Administrator",
        last_name="ChargeFair",
        role="admin",
        is_active=True,
        email_verified=True,
        department="IT",
    )
    admin.set_password(DEFAULT_ADMIN_PASSWORD)
    db.session.add(admin)
    db.session.commit()

    db.session.add(
        RegistrationToken(
            token=RegistrationToken.generate_token(),
            label="Standardcode für neue Mitarbeiter",
            max_uses=50,
            created_by_id=admin.id,
        )
    )
    db.session.commit()
    audit("seed.admin", f"Administrator {admin.email} angelegt", admin)


def _seed_demo_users(count: int | None = None) -> list[User]:
    count = count or int(os.environ.get("SEED_USERS", "24"))
    if User.query.filter_by(role="user").count():
        return User.query.filter_by(role="user").all()

    rng = random.Random(20260928)
    names = set()
    created: list[User] = []
    for index in range(count):
        first = FIRST_NAMES[index % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 7) % len(LAST_NAMES)]
        while f"{first} {last}" in names:
            last = LAST_NAMES[(LAST_NAMES.index(last) + 1) % len(LAST_NAMES)]
        names.add(f"{first} {last}")

        user = User(
            email=f"{first.lower()}.{last.lower().replace('ü', 'ue').replace('ö', 'oe').replace('ä', 'ae')}"
                  f"@example.com",
            first_name=first,
            last_name=last,
            phone=f"+49 761 {rng.randint(100000, 999999)}",
            department=DEPARTMENTS[index % len(DEPARTMENTS)],
            role="user",
            is_active=True,
            email_verified=True,
            preferred_days="0,1,2,3,4",
            preferred_windows="1,2,3,0",
            shift_model=SHIFT_MODELS[index % len(SHIFT_MODELS)],
            max_weekly_slots=2,
        )
        user.set_password(DEMO_PASSWORD)
        db.session.add(user)
        db.session.flush()

        vehicle_type, manufacturer, model, suffix, power, battery = VEHICLES[index % len(VEHICLES)]
        db.session.add(
            Vehicle(
                user_id=user.id,
                type=vehicle_type,
                manufacturer=manufacturer,
                model=model,
                license_plate=f"KA-{manufacturer[:2].upper()}{rng.randint(100, 999)}{suffix}",
                power_kw=power,
                battery_kwh=battery,
            )
        )
        created.append(user)

    db.session.commit()
    audit("seed.users", f"{len(created)} Demo-Mitarbeiter angelegt")
    db.session.commit()
    return created


# ---------------------------------------------------------------------------
# Demo history (so the distribution dashboard shows something meaningful)
# ---------------------------------------------------------------------------

def seed_demo_history(weeks: int = 8) -> None:
    """Create past bookings with an intentionally uneven distribution.

    The uneven pattern mirrors the situation this project wants to fix: a few
    colleagues grabbed a slot nearly every week while others rarely got one.
    """
    users = User.query.filter_by(role="user", is_active=True).all()
    if not users or Booking.query.count():
        return

    rng = random.Random(4711)
    points = ChargingPoint.query.order_by(ChargingPoint.id).all()
    spaces = ParkingSpace.query.order_by(ParkingSpace.id).all()
    today = date.today()
    current_monday = today - timedelta(days=today.weekday())

    # A small group is "always there", the majority gets a slot occasionally.
    weights = {user.id: (6 if index % 4 == 0 else 1) for index, user in enumerate(users)}

    for week_offset in range(weeks, 0, -1):
        monday = current_monday - timedelta(weeks=week_offset)
        round_obj = BookingRound(
            week_start=monday,
            method=DEFAULT_METHOD,
            status="allocated",
            opens_at=datetime.combine(monday - timedelta(days=7), dtime(8, 0)),
            closes_at=datetime.combine(monday - timedelta(days=3), dtime(18, 0)),
            allocated_at=datetime.combine(monday - timedelta(days=2), dtime(9, 0)),
            stats={"seeded": True},
        )
        db.session.add(round_obj)
        db.session.flush()

        used: dict[tuple[int, int], set[int]] = {}
        granted = 0
        for user in users:
            if rng.random() > min(0.95, weights[user.id] / 6 * 0.8 + 0.25):
                continue
            slots_this_week = 1 if rng.random() > 0.15 else 2
            participation = RoundParticipation(
                round_id=round_obj.id, user_id=user.id, desired_count=slots_this_week
            )
            db.session.add(participation)

            for _ in range(slots_this_week):
                weekday = rng.choice(list(DEFAULT_WORKDAYS))
                window_index = rng.choices(
                    range(len(DEFAULT_WINDOWS)), weights=(2, 5, 4, 2)
                )[0]
                key = (weekday, window_index)
                taken = used.setdefault(key, set())
                if len(taken) >= len(points):
                    continue
                point = next(point for point in points if point.id not in taken)
                taken.add(point.id)

                start_minute, end_minute = DEFAULT_WINDOWS[window_index]
                day = monday + timedelta(days=weekday)
                rolled = rng.random()
                if rolled < 0.65:
                    status = BOOKING_COMPLETED
                elif rolled < 0.8:
                    status = BOOKING_RELEASED
                else:
                    status = BOOKING_ACTIVE
                booking = Booking(
                    user_id=user.id,
                    charging_point_id=point.id,
                    parking_space_id=spaces[(len(taken) - 1) % len(spaces)].id if spaces else None,
                    start=datetime.combine(day, dtime(start_minute // 60, start_minute % 60)),
                    end=datetime.combine(day, dtime(end_minute // 60, end_minute % 60)),
                    weekday=weekday,
                    window_index=window_index,
                    status=status,
                    origin="allocation",
                    allocation_round_id=round_obj.id,
                    no_show_reported=status == BOOKING_COMPLETED and rng.random() < 0.08,
                    checked_in_at=datetime.combine(day, dtime(start_minute // 60, start_minute % 60))
                    if status == BOOKING_COMPLETED
                    else None,
                )
                db.session.add(booking)
                granted += 1

        round_obj.stats = {
            "seeded": True,
            "method": DEFAULT_METHOD,
            "assigned": granted,
            "capacity": len(points) * len(DEFAULT_WORKDAYS) * len(DEFAULT_WINDOWS),
        }
        db.session.commit()


def seed_demo_round(week: date | None = None) -> BookingRound | None:
    """Open an allocation round for next week including a few wishes."""
    users = User.query.filter_by(role="user", is_active=True).all()
    if not users:
        return None
    today = date.today()
    monday = week or (today - timedelta(days=today.weekday()) + timedelta(days=7))
    if BookingRound.query.filter_by(week_start=monday).first():
        return BookingRound.query.filter_by(week_start=monday).first()

    round_obj = BookingRound(
        week_start=monday,
        method=DEFAULT_METHOD,
        status="open",
        opens_at=utcnow(),
        closes_at=datetime.combine(monday - timedelta(days=1), dtime(18, 0)),
        note="Automatisch angelegte Demo-Runde",
    )
    db.session.add(round_obj)
    db.session.flush()

    rng = random.Random(99)
    for user in users:
        if rng.random() > 0.85:
            continue
        db.session.add(
            RoundParticipation(round_id=round_obj.id, user_id=user.id, desired_count=1)
        )
        windows_count = len(DEFAULT_WINDOWS)
        for priority, weekday in enumerate(rng.sample(list(DEFAULT_WORKDAYS), 3), start=1):
            db.session.add(
                BookingRequest(
                    round_id=round_obj.id,
                    user_id=user.id,
                    priority=priority,
                    weekday=weekday,
                    window_index=rng.choices(range(windows_count), weights=(2, 5, 4, 2))[0],
                )
            )
    db.session.commit()
    return round_obj


def main() -> None:  # pragma: no cover - CLI helper
    from .app import create_app

    app = create_app()
    init_db(app)
    with app.app_context():
        if os.environ.get("SEED_HISTORY", "1") not in {"0", "false", "no"}:
            seed_demo_history()
        seed_demo_round()
        users = User.query.count()
        points = ChargingPoint.query.count()
        print(f"Benutzer: {users}, Ladepunkte: {points}")
        print(f"Admin:    {DEFAULT_ADMIN_EMAIL} / {DEFAULT_ADMIN_PASSWORD}")
        print(f"Demo:     <vorname.nachname>@example.com / {DEMO_PASSWORD}")
        token = RegistrationToken.query.filter_by(is_active=True).first()
        if token:
            print(f"Registrierungscode: {token.token}")


_ = (week_dates, BOOKING_RELEASED, Vehicle)
