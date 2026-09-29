"""Smoke tests for the Flask application (registration, login, pages, admin)."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("START_MAIL_WORKER", "0")
os.environ.setdefault("MAIL_WORKER_IN_PROCESS", "0")
os.environ.setdefault("DISPATCH_MAIL_INLINE", "0")
os.environ.setdefault("DISPATCH_HOUSEKEEPING", "0")
os.environ.setdefault("REQUIRE_EMAIL_VERIFICATION", "0")
os.environ.setdefault("REGISTRATION_REQUIRES_TOKEN", "1")
os.environ.setdefault("SEED_DEMO", "0")
os.environ.setdefault("SEED_HISTORY", "0")

from chargefair.allocation import METHODS  # noqa: E402
from chargefair.app import create_app  # noqa: E402
from chargefair.config import Config  # noqa: E402
from chargefair.extensions import db  # noqa: E402
from chargefair.models import (  # noqa: E402
    Booking,
    BookingRequest,
    BookingRound,
    ChargingPoint,
    RegistrationToken,
    RoundParticipation,
    Setting,
    User,
)
from chargefair.seed import init_db  # noqa: E402
from chargefair.services import (  # noqa: E402
    KEY_ALLOCATION_METHOD,
    KEY_MAX_REGULAR_PER_WEEK,
    KEY_MAX_SLOTS_PER_USER,
    KEY_WINDOWS,
    guaranteed_slots_per_week,
    max_slots_per_user,
)
METHOD_KEYS = list(METHODS)
METHOD_LABELS = [spec.label for spec in METHODS.values()]

# The wish week used throughout the tests.
TARGET_WEEK = "2026-10-05"  # a Monday


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    REGISTRATION_REQUIRES_TOKEN = True
    REQUIRE_EMAIL_VERIFICATION = False
    MAIL_DRY_RUN = True


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        from chargefair.seed import _seed_admin, _seed_infrastructure, _seed_settings

        _seed_settings()
        _seed_infrastructure()
        _seed_admin()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email: str, password: str):
    return client.post(
        "/anmelden", data={"email": email, "password": password}, follow_redirects=True
    )


def register(client, email: str, token: str):
    return client.post(
        "/registrieren",
        data={
            "token": token,
            "first_name": "Test",
            "last_name": "Person",
            "email": email,
            "password": "Sicher!2026x",
            "password_repeat": "Sicher!2026x",
            "vehicle_type": "BEV",
            "manufacturer": "VW",
            "model": "ID.3",
            "power_kw": "11",
        },
        follow_redirects=True,
    )


def make_employee(client, app, email: str = "anna@example.com",
                  desired_count: int | None = None, slots: list[str] | None = None):
    """Register an employee and file wishes for the target week."""
    with app.app_context():
        token = RegistrationToken.query.first().token
    register(client, email, token)
    client.post("/abmelden")
    login(client, email, "Sicher!2026x")
    if desired_count is not None:
        chosen = slots if slots is not None else ["0:1", "1:1"]
        data = {
            "week": TARGET_WEEK,
            "slot": chosen,
            "desired_count": str(desired_count),
        }
        for position, raw in enumerate(chosen, start=1):
            data[f"rank_{raw.replace(':', '_')}"] = str(position)
        client.post("/plan/wunsch", data=data, follow_redirects=True)
    client.post("/abmelden")
    return email


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def test_landing_page_is_public(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Faire Ladeplatzvergabe".encode() in response.data


def test_landing_states_shared_use_of_all_points(client):
    body = client.get("/").data.decode()
    assert "für BEV und PHEV" in body
    assert "Eine Ladezeit pro Woche garantiert" in body


def test_help_page_explains_all_methods_and_the_guarantee(client):
    response = client.get("/hilfe")
    assert response.status_code == 200
    body = response.data.decode()
    for label in METHOD_LABELS:
        assert label in body, label
    assert "keine Trennung nach Fahrzeugtyp" in body
    assert "Wie oft kann ich laden?" in body
    assert "Grundversorgung" in body
    assert "Zusatzwünsche" in body


def test_health_endpoint_reports_charging_points(app, client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["charging_points"] == 8


def test_infrastructure_has_eight_shared_charging_points(app):
    with app.app_context():
        points = ChargingPoint.query.all()
        assert len(points) == 8
        # Every point is a plain Type 2 AC point -- nothing vehicle specific.
        assert {point.connector_type for point in points} == {"Typ 2"}
        assert all(point.is_active for point in points)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def test_registration_requires_a_valid_token(app, client):
    response = register(client, "neu@example.com", "FALSCH-FALSCH")
    assert "ungültig".encode() in response.data


def test_registration_with_token_creates_user(app, client):
    with app.app_context():
        token = RegistrationToken.query.first().token
    response = register(client, "neu@example.com", token)
    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="neu@example.com").first()
        assert user is not None
        assert user.primary_vehicle.type == "BEV"
        assert RegistrationToken.query.first().uses == 1


def test_admin_login_and_dashboard(app, client):
    login(client, "admin@example.com", "ChargeFair!2026")
    for path in ("/admin/", "/admin/runden", "/admin/benutzer", "/admin/infrastruktur",
                 "/admin/verteilung", "/admin/einstellungen", "/admin/codes",
                 "/admin/email", "/admin/audit"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_employee_cannot_reach_admin_area(app, client):
    with app.app_context():
        token = RegistrationToken.query.first().token
    register(client, "ben@example.com", token)
    assert client.get("/admin/").status_code == 403


def test_password_reset_flow_does_not_leak_accounts(client):
    response = client.post(
        "/passwort-vergessen", data={"email": "gibtesnicht@example.com"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Wenn ein Konto mit dieser Adresse existiert".encode() in response.data


def test_login_is_rate_limited(app, client):
    for _ in range(10):
        login(client, "admin@example.com", "falsch")
    with app.app_context():
        admin = User.query.filter_by(email="admin@example.com").first()
        assert admin.is_locked
    response = login(client, "admin@example.com", "ChargeFair!2026")
    assert "gesperrt".encode() in response.data


# ---------------------------------------------------------------------------
# Allocation method selection (admin)
# ---------------------------------------------------------------------------

def test_admin_can_change_allocation_method(app, client):
    login(client, "admin@example.com", "ChargeFair!2026")
    response = client.post(
        "/admin/einstellungen",
        data={
            "section": "allocation",
            "method": "rank_based",
            "max_slots": "2",
            "max_regular": "1",
            "offer_minutes": "30",
            "distribution_weeks": "12",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert Setting.get(KEY_ALLOCATION_METHOD) == "rank_based"


def test_all_registered_methods_can_be_selected(app, client):
    login(client, "admin@example.com", "ChargeFair!2026")
    for key in METHOD_KEYS:
        client.post(
            "/admin/einstellungen",
            data={"section": "allocation", "method": key, "max_slots": "2",
                  "max_regular": "1", "offer_minutes": "30", "distribution_weeks": "12"},
            follow_redirects=True,
        )
        with app.app_context():
            assert Setting.get(KEY_ALLOCATION_METHOD) == key


def test_method_can_be_changed_from_the_rounds_page(app, client):
    login(client, "admin@example.com", "ChargeFair!2026")
    client.post("/admin/verfahren", data={"method": "guaranteed"}, follow_redirects=True)
    with app.app_context():
        assert Setting.get(KEY_ALLOCATION_METHOD) == "guaranteed"


def test_default_method_is_lexicographic(app, client):
    with app.app_context():
        assert Setting.get(KEY_ALLOCATION_METHOD) == "lexicographic"


def test_grid_settings_apply_to_every_vehicle_type(app, client):
    """The slot grid is global: no separate BEV/PHEV windows exist."""
    login(client, "admin@example.com", "ChargeFair!2026")
    client.post(
        "/admin/einstellungen",
        data={
            "section": "grid",
            "window_0": "06:00-09:00",
            "window_1": "09:30-12:30",
            "window_2": "13:00-16:00",
            "window_3": "16:30-20:00",
            "workdays": ["0", "1", "2", "3", "4"],
        },
        follow_redirects=True,
    )
    with app.app_context():
        from chargefair.services import slot_keys

        assert len(slot_keys()) == 20
        assert Setting.get_json(KEY_WINDOWS) == [
            [360, 540], [570, 750], [780, 960], [990, 1200],
        ]


# ---------------------------------------------------------------------------
# "How often do I want to charge?" -- wishes, guarantee and extras
# ---------------------------------------------------------------------------

def test_wish_form_offers_the_requested_number_of_charges(app, client):
    with app.app_context():
        token = RegistrationToken.query.first().token
    register(client, "dora@example.com", token)
    body = client.get(f"/plan?woche={TARGET_WEEK}").data.decode()
    assert "Wie oft möchtest du in dieser Woche laden?" in body
    assert "garantiert" in body
    assert "Zusatzwunsch" in body


def test_employee_can_request_three_charges(app, client):
    with app.app_context():
        token = RegistrationToken.query.first().token
    register(client, "erik@example.com", token)
    # The wish form is available for future weeks even before a round exists.
    body = client.get(f"/plan?woche={TARGET_WEEK}").data.decode()
    assert "Wunschzeiten für" in body
    client.post(
        "/plan/wunsch",
        data={
            "week": TARGET_WEEK,
            "slot": ["0:1", "1:1", "2:2"],
            "rank_0_1": "1", "rank_1_1": "2", "rank_2_2": "3",
            "desired_count": "3",
            "note": "Dienstreise am Freitag",
        },
        follow_redirects=True,
    )
    with app.app_context():
        round_obj = BookingRound.query.filter_by(week_start=TARGET_WEEK).first()
        assert round_obj is not None
        participation = RoundParticipation.query.filter_by(
            round_id=round_obj.id
        ).first()
        assert participation.desired_count == 3
        assert BookingRequest.query.filter_by(round_id=round_obj.id).count() == 3


def test_requested_number_is_capped_by_the_weekly_maximum(app, client):
    with app.app_context():
        Setting.set(KEY_MAX_SLOTS_PER_USER, 2)
        db.session.commit()
        token = RegistrationToken.query.first().token
    register(client, "frida@example.com", token)
    client.post(
        "/plan/wunsch",
        data={"week": TARGET_WEEK, "slot": ["0:1"], "rank_0_1": "1", "desired_count": "7"},
        follow_redirects=True,
    )
    with app.app_context():
        assert max_slots_per_user() == 2
        participation = RoundParticipation.query.first()
        assert participation.desired_count == 2  # capped by the setting at the time


def test_guarantee_helpers_default_to_one_guaranteed_slot(app):
    with app.app_context():
        assert guaranteed_slots_per_week() == 1
        assert max_slots_per_user() == 3, "three charges per week must be requestable"


def test_guarantee_is_kept_when_demand_exceeds_capacity(app, client):
    """Core rule: one slot each, and no extras while capacity is scarce.

    The grid is reduced to a single window on a single day and only two
    charging points stay active, so there are just two slots for five
    applicants who each want three.
    """
    from chargefair.services import KEY_WORKDAYS, KEY_WINDOWS

    with app.app_context():
        Setting.set(KEY_WINDOWS, [[360, 540]])  # one window: 06:00-09:00
        Setting.set(KEY_WORKDAYS, [0])  # Monday only
        for point in ChargingPoint.query.order_by(ChargingPoint.id).offset(2).all():
            point.is_active = False
        db.session.commit()
        assert ChargingPoint.query.filter_by(is_active=True).count() == 2

    for index in range(5):
        make_employee(client, app, email=f"person{index}@example.com",
                      desired_count=3, slots=["0:0"])

    with app.app_context():
        round_id = BookingRound.query.filter_by(week_start=TARGET_WEEK).first().id

    login(client, "admin@example.com", "ChargeFair!2026")
    client.post(f"/admin/runden/{round_id}/verteilen", data={"method": "lexicographic"},
                follow_redirects=True)

    with app.app_context():
        round_obj = db.session.get(BookingRound, round_id)
        bookings = Booking.query.filter_by(allocation_round_id=round_id).all()
        per_user: dict[int, int] = {}
        for booking in bookings:
            per_user[booking.user_id] = per_user.get(booking.user_id, 0) + 1

        assert round_obj.status == "allocated"
        assert len(bookings) == 2, "only two slots exist"
        assert len(per_user) == 2, "two different people must be served"
        assert max(per_user.values()) == 1, "no extras while capacity is scarce"
        assert round_obj.stats["guarantee"] == 1
        assert round_obj.stats["extras_granted"] == 0
        assert round_obj.stats["guarantee_fulfilled"] is False
        assert round_obj.stats["guarantee_met"] == 2


def test_extras_are_granted_when_capacity_allows_it(app, client):
    """With plenty of capacity a "3 times" wish is served in full."""
    for index in range(3):
        make_employee(client, app, email=f"viel{index}@example.com",
                      desired_count=3, slots=["0:1", "1:1", "2:2"])

    with app.app_context():
        round_id = BookingRound.query.filter_by(week_start=TARGET_WEEK).first().id

    login(client, "admin@example.com", "ChargeFair!2026")
    client.post(f"/admin/runden/{round_id}/verteilen", data={"method": "lexicographic"},
                follow_redirects=True)

    with app.app_context():
        round_obj = db.session.get(BookingRound, round_id)
        bookings = Booking.query.filter_by(allocation_round_id=round_id).all()
        per_user: dict[int, int] = {}
        for booking in bookings:
            per_user[booking.user_id] = per_user.get(booking.user_id, 0) + 1
        assert len(per_user) == 3
        assert all(count == 3 for count in per_user.values()), per_user
        assert round_obj.stats["guarantee_fulfilled"] is True
        assert round_obj.stats["extras_granted"] == 6  # two extra slots each


# ---------------------------------------------------------------------------
# Full round
# ---------------------------------------------------------------------------

def test_full_round_from_wish_to_booking(app, client):
    make_employee(client, app, desired_count=1, slots=["0:1", "1:1"])

    with app.app_context():
        round_obj = BookingRound.query.filter_by(week_start=TARGET_WEEK).first()
        assert round_obj is not None
        assert BookingRequest.query.filter_by(round_id=round_obj.id).count() == 2
        assert RoundParticipation.query.filter_by(round_id=round_obj.id).count() == 1
        round_id = round_obj.id

    login(client, "admin@example.com", "ChargeFair!2026")
    response = client.post(
        f"/admin/runden/{round_id}/verteilen",
        data={"method": "lexicographic"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        round_obj = db.session.get(BookingRound, round_id)
        bookings = Booking.query.filter_by(allocation_round_id=round_id).all()
        assert round_obj.status == "allocated"
        assert bookings
        assert round_obj.stats["assigned"] == len(bookings)
        assert booking_uses_a_shared_point(bookings)


def booking_uses_a_shared_point(bookings) -> bool:
    return all(booking.charging_point_id is not None for booking in bookings)


def test_method_comparison_runs_all_methods(app, client):
    make_employee(client, app, email="carla@example.com", desired_count=2)
    with app.app_context():
        round_id = BookingRound.query.filter_by(week_start=TARGET_WEEK).first().id
    login(client, "admin@example.com", "ChargeFair!2026")
    response = client.get(f"/admin/runden/{round_id}/vergleich")
    assert response.status_code == 200
    body = response.data.decode()
    for label in METHOD_LABELS:
        assert label in body, label
    assert "Grundversorgung möglich" in body


def test_round_detail_shows_guarantee_and_extras(app, client):
    make_employee(client, app, email="nils@example.com", desired_count=3)
    with app.app_context():
        round_id = BookingRound.query.filter_by(week_start=TARGET_WEEK).first().id
    login(client, "admin@example.com", "ChargeFair!2026")
    client.post(f"/admin/runden/{round_id}/verteilen", data={"method": "guaranteed"},
                follow_redirects=True)
    body = client.get(f"/admin/runden/{round_id}").data.decode()
    assert "Grundversorgung und Zusatzwünsche" in body
    assert "Zusatzwunsch" in body


def test_employee_dashboard_shows_the_weekly_allowance(app, client):
    with app.app_context():
        token = RegistrationToken.query.first().token
    register(client, "greta@example.com", token)
    body = client.get("/dashboard").data.decode()
    assert "Dein Wochenkontingent" in body
    assert "Ladezeit pro Woche ist dir garantiert" in body


def test_profile_lets_employees_set_their_own_allowance(app, client):
    with app.app_context():
        token = RegistrationToken.query.first().token
    register(client, "hanna@example.com", token)
    client.post(
        "/profil",
        data={
            "first_name": "Hanna", "last_name": "Test", "vehicle_type": "BEV",
            "manufacturer": "VW", "model": "ID.4", "power_kw": "11",
            "max_weekly_slots": "3",
            "window_rank_0": "1", "window_rank_1": "2",
            "window_rank_2": "3", "window_rank_3": "4",
        },
        follow_redirects=True,
    )
    with app.app_context():
        user = User.query.filter_by(email="hanna@example.com").first()
        assert user.max_weekly_slots == 3


def test_temporary_database_helpers_are_used():
    """Guard: the test suite must never touch the real data directory."""
    assert "memory" in TestConfig.SQLALCHEMY_DATABASE_URI
    assert Path(tempfile.gettempdir()).exists()


_ = (init_db, date, timedelta, KEY_MAX_REGULAR_PER_WEEK)
