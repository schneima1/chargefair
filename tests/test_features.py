"""Tests for the email-driven features: phases, interests, one-click links,
contact, overview and feedback."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

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
os.environ.setdefault("BASE_URL", "http://localhost:8000")

from chargefair.allocation_service import (  # noqa: E402
    allocate_phase,
    announce_phase,
    current_phase,
    default_round_week,
    get_or_create_phase,
    get_or_create_round,
    materialize_week,
    register_interest,
    release_booking,
)
from chargefair.app import create_app  # noqa: E402
from chargefair.config import Config  # noqa: E402
from chargefair.extensions import db  # noqa: E402
from chargefair.models import (  # noqa: E402
    INTEREST_NOTIFIED,
    INTEREST_WAITING,
    Booking,
    BookingPhase,
    BookingRequest,
    BookingRound,
    Comment,
    EmailMessage,
    Post,
    RegistrationToken,
    RoundParticipation,
    Setting,
    SlotInterest,
    TOPIC_FEEDBACK,
    User,
)
from chargefair.services import (  # noqa: E402
    KEY_PHASE_WEEKS,
    KEY_PHASE_REMINDER_DAYS,
    KEY_SLOT_REMINDER_MINUTES,
)

TARGET_WEEK = "2026-10-05"


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    REGISTRATION_REQUIRES_TOKEN = True
    REQUIRE_EMAIL_VERIFICATION = False
    MAIL_DRY_RUN = True
    BASE_URL = "http://localhost:8000"
    SECRET_KEY = "test-secret-key-for-signed-links"


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


def make_user(app, email: str = "anna@example.com") -> int:
    """Create an employee directly and return the id."""
    with app.app_context():
        user = User(
            email=email,
            first_name="Anna",
            last_name="Muster",
            role="user",
            is_active=True,
            email_verified=True,
        )
        user.set_password("Sicher!2026x")
        db.session.add(user)
        db.session.commit()
        return user.id


def queue_count(app, kind: str | None = None) -> int:
    with app.app_context():
        query = EmailMessage.query
        if kind:
            query = query.filter_by(kind=kind)
        return query.count()


# ---------------------------------------------------------------------------
# Allocation phases (four weeks at once)
# ---------------------------------------------------------------------------

def test_phase_creates_one_round_per_week(app):
    with app.app_context():
        phase = get_or_create_phase(date(2026, 10, 5), weeks=4)
        assert phase.weeks == 4
        assert len(phase.rounds) == 4
        weeks = sorted(round_obj.week_start for round_obj in phase.rounds)
        assert weeks == [date(2026, 10, 5), date(2026, 10, 12),
                         date(2026, 10, 19), date(2026, 10, 26)]
        assert all(round_obj.phase_id == phase.id for round_obj in phase.rounds)


def test_phase_is_the_default_length_from_the_settings(app):
    with app.app_context():
        Setting.set(KEY_PHASE_WEEKS, 4)
        db.session.commit()
        phase = get_or_create_phase(date(2026, 11, 2))
        assert phase.weeks == 4


def test_get_or_create_phase_is_idempotent(app):
    with app.app_context():
        first = get_or_create_phase(date(2026, 10, 5), weeks=4)
        second = get_or_create_phase(date(2026, 10, 5), weeks=4)
        assert first.id == second.id
        assert BookingPhase.query.count() == 1
        assert BookingRound.query.count() == 4


def test_phase_reports_its_period(app):
    with app.app_context():
        phase = get_or_create_phase(date(2026, 10, 5), weeks=4)
        assert phase.end_week == date(2026, 10, 26)
        assert phase.last_day == date(2026, 11, 1)
        assert "KW 41" in phase.period_label
        assert phase.status_label == "Offen"


def test_announce_phase_queues_one_mail_per_employee(app):
    make_user(app, "anna@example.com")
    make_user(app, "ben@example.com")
    make_user(app, "carla@example.com")
    with app.app_context():
        phase = get_or_create_phase(date(2026, 10, 5), weeks=4)
        queued = announce_phase(phase, app)
        assert queued == 3
        assert EmailMessage.query.filter_by(kind="phase_opened").count() == 3
        assert phase.notified_at is not None

        message = EmailMessage.query.filter_by(kind="phase_opened").first()
        body = message.body
        # The announcement explains the phase, the deadline and the guarantee.
        assert "KW 41" in message.subject
        assert "KW 44" in body  # last week of the phase
        assert "Frist" in body
        assert "garantierte Ladezeit" in body
        assert "/plan?woche=2026-10-05" in body


def test_phase_announcement_respects_the_opt_out(app):
    make_user(app, "anna@example.com")
    with app.app_context():
        user = User.query.filter_by(email="anna@example.com").first()
        user.phase_mails_enabled = False
        db.session.commit()
        phase = get_or_create_phase(date(2026, 10, 5), weeks=4)
        assert announce_phase(phase, app) == 0


def test_phase_reminder_is_sent_once_per_person(app):
    make_user(app, "anna@example.com")
    with app.app_context():
        Setting.set(KEY_PHASE_REMINDER_DAYS, 2)
        db.session.commit()
        phase = get_or_create_phase(date(2026, 10, 5), weeks=4)
        phase.closes_at = datetime.now() + timedelta(hours=6)  # inside the window
        db.session.commit()

        from chargefair.allocation_service import remind_phase_deadline

        assert remind_phase_deadline(phase, app) == 1
        assert remind_phase_deadline(phase, app) == 0, "second run must not duplicate"


def test_allocating_a_phase_covers_every_week(app):
    user_id = make_user(app, "anna@example.com")
    with app.app_context():
        phase = get_or_create_phase(date(2026, 10, 5), weeks=4)
        for round_obj in phase.rounds:
            materialize_week(round_obj.week_start)
            db.session.add(
                RoundParticipation(round_id=round_obj.id, user_id=user_id, desired_count=1)
            )
            db.session.add(
                BookingRequest(
                    round_id=round_obj.id, user_id=user_id, priority=1,
                    weekday=0, window_index=1,
                )
            )
        db.session.commit()

        summary = allocate_phase(phase, app=app)
        assert len(summary["weeks"]) == 4
        assert summary["assigned"] == 4

        bookings = Booking.query.filter_by(user_id=user_id).all()
        assert len(bookings) == 4
        assert len({booking.week_start for booking in bookings} if False else
                   {booking.start.date() for booking in bookings}) == 4
        assert phase.status == "allocated"
        assert phase.allocated_at is not None

        # Every week gets its own result mail.
        assert EmailMessage.query.filter_by(kind="round_result").count() == 4


def test_current_phase_picks_the_next_open_one(app):
    with app.app_context():
        get_or_create_phase(date(2026, 10, 5), weeks=4)
        phase = current_phase(date(2026, 9, 29))
        assert phase is not None
        assert phase.start_week == date(2026, 10, 5)


# ---------------------------------------------------------------------------
# Interest in a specific slot
# ---------------------------------------------------------------------------

def make_booking(app, user_id: int, weekday: int = 0, window: int = 1) -> int:
    with app.app_context():
        week = date(2026, 10, 5)
        materialize_week(week)
        from chargefair.allocation_service import create_booking

        user = db.session.get(User, user_id)
        booking = create_booking(user, week, weekday, window, origin="self")
        db.session.commit()
        return booking.id


def test_register_interest_is_idempotent(app):
    user_id = make_user(app)
    with app.app_context():
        user = db.session.get(User, user_id)
        first = register_interest(user, date(2026, 10, 5), 0, 1)
        second = register_interest(user, date(2026, 10, 5), 0, 1)
        assert first.id == second.id
        assert SlotInterest.query.count() == 1
        assert first.status == INTEREST_WAITING


def test_releasing_a_slot_notifies_interested_people_with_a_link(app):
    owner_id = make_user(app, "owner@example.com")
    fan_id = make_user(app, "fan@example.com")
    booking_id = make_booking(app, owner_id)

    with app.app_context():
        fan = db.session.get(User, fan_id)
        register_interest(fan, date(2026, 10, 5), 0, 1)
        db.session.commit()

        booking = db.session.get(Booking, booking_id)
        owner = db.session.get(User, owner_id)
        assert release_booking(booking, owner) is True

        interest = SlotInterest.query.filter_by(user_id=fan_id).first()
        assert interest.status == INTEREST_NOTIFIED
        assert interest.notified_at is not None

        mail = EmailMessage.query.filter_by(kind="interest_available").one()
        assert mail.recipient == "fan@example.com"
        assert "/slot/" in mail.body
        # The owner is told how many people were informed.
        confirmation = EmailMessage.query.filter_by(kind="release").one()
        assert "1 Person(en)" in confirmation.body


def test_slot_owner_is_not_notified_about_their_own_slot(app):
    owner_id = make_user(app, "owner@example.com")
    booking_id = make_booking(app, owner_id)
    with app.app_context():
        owner = db.session.get(User, owner_id)
        register_interest(owner, date(2026, 10, 5), 0, 1)
        db.session.commit()
        booking = db.session.get(Booking, booking_id)
        release_booking(booking, owner)
        assert EmailMessage.query.filter_by(kind="interest_available").count() == 0


# ---------------------------------------------------------------------------
# One-click links
# ---------------------------------------------------------------------------

def test_one_click_release_works_without_login(app, client):
    owner_id = make_user(app, "owner@example.com")
    booking_id = make_booking(app, owner_id)

    with app.app_context():
        from chargefair.tokens import release_token

        token = release_token(TestConfig.SECRET_KEY, booking_id, owner_id)

    response = client.get(f"/freigeben/{token}")
    assert response.status_code == 200
    assert "Ladezeit freigegeben".encode() in response.data

    with app.app_context():
        assert db.session.get(Booking, booking_id).status == "released"


def test_one_click_release_rejects_a_tampered_token(app, client):
    owner_id = make_user(app, "owner@example.com")
    booking_id = make_booking(app, owner_id)
    with app.app_context():
        from chargefair.tokens import release_token

        token = release_token(TestConfig.SECRET_KEY, booking_id, owner_id)

    response = client.get(f"/freigeben/{token[:-3]}xyz")
    assert response.status_code == 410
    with app.app_context():
        assert db.session.get(Booking, booking_id).status == "active"


def test_one_click_takeover_assigns_the_slot(app, client):
    owner_id = make_user(app, "owner@example.com")
    fan_id = make_user(app, "fan@example.com")
    booking_id = make_booking(app, owner_id)

    with app.app_context():
        from chargefair.tokens import takeover_token

        booking = db.session.get(Booking, booking_id)
        owner = db.session.get(User, owner_id)
        release_booking(booking, owner)
        token = takeover_token(TestConfig.SECRET_KEY, booking_id, fan_id)

    response = client.get(f"/slot/{token}")
    assert response.status_code == 200
    assert "Ladeplatz übernommen".encode() in response.data

    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.user_id == fan_id
        assert booking.status == "active"
        assert booking.origin == "takeover"
        assert booking.previous_user_id == owner_id


def test_one_click_takeover_reports_a_slot_that_is_gone(app, client):
    owner_id = make_user(app, "owner@example.com")
    fan_id = make_user(app, "fan@example.com")
    booking_id = make_booking(app, owner_id)

    with app.app_context():
        from chargefair.tokens import takeover_token

        # Slot is still active - somebody else was faster.
        token = takeover_token(TestConfig.SECRET_KEY, booking_id, fan_id)

    response = client.get(f"/slot/{token}")
    assert response.status_code == 200
    assert "nicht mehr frei".encode() in response.data


def test_reminder_contains_a_one_click_release_link(app):
    user_id = make_user(app, "anna@example.com")
    booking_id = make_booking(app, user_id)

    with app.app_context():
        Setting.set(KEY_SLOT_REMINDER_MINUTES, 45)
        booking = db.session.get(Booking, booking_id)
        booking.start = datetime.now() + timedelta(minutes=20)
        booking.end = booking.start + timedelta(hours=3)
        db.session.commit()

        from chargefair.allocation_service import send_due_reminders

        assert send_due_reminders(app=app) >= 1

        mail = EmailMessage.query.filter_by(kind="reminder_start").one()
        assert "/freigeben/" in mail.body
        assert "gib ihn mit einem Klick frei" in mail.body


def test_reminders_respect_the_opt_out(app):
    user_id = make_user(app, "anna@example.com")
    booking_id = make_booking(app, user_id)
    with app.app_context():
        user = db.session.get(User, user_id)
        user.reminders_enabled = False
        booking = db.session.get(Booking, booking_id)
        booking.start = datetime.now() + timedelta(minutes=20)
        booking.end = booking.start + timedelta(hours=3)
        db.session.commit()

        from chargefair.allocation_service import send_due_reminders

        assert send_due_reminders(app=app) == 0


# ---------------------------------------------------------------------------
# Overview and contact
# ---------------------------------------------------------------------------

def test_overview_shows_vehicles_but_no_license_plates(app, client):
    from chargefair.models import Vehicle

    user_id = make_user(app, "anna@example.com")
    with app.app_context():
        db.session.add(
            Vehicle(user_id=user_id, type="BEV", manufacturer="VW", model="ID.3 Pro",
                    license_plate="KA-XX 9999", power_kw=11)
        )
        db.session.commit()
    make_booking(app, user_id)

    login(client, "anna@example.com", "Sicher!2026x")
    body = client.get(f"/uebersicht?woche={TARGET_WEEK}").data.decode()
    assert "VW ID.3 Pro" in body
    assert "BEV" in body
    assert "KA-XX 9999" not in body, "license plates must not be exposed"
    assert "Belegung" in body


def test_overview_offers_contact_and_interest(app, client):
    owner_id = make_user(app, "owner@example.com")
    make_user(app, "fan@example.com")
    booking_id = make_booking(app, owner_id)

    login(client, "fan@example.com", "Sicher!2026x")
    body = client.get(f"/uebersicht?woche={TARGET_WEEK}").data.decode()
    assert "Interesse" in body
    assert "Nachricht" in body

    client.post(
        "/interesse",
        data={"action": "add", "week": TARGET_WEEK, "weekday": "0", "window": "1"},
        follow_redirects=True,
    )
    with app.app_context():
        assert SlotInterest.query.count() == 1
        assert EmailMessage.query.filter_by(kind="interest_confirmation").count() == 1

    client.post(
        "/interesse",
        data={
            "action": "remove",
            "interest": str(SlotInterest.query.first().id),
            "week": TARGET_WEEK,
        },
        follow_redirects=True,
    )
    with app.app_context():
        assert SlotInterest.query.first().status == "cancelled"


def test_contact_form_sends_a_message_without_exposing_addresses(app, client):
    owner_id = make_user(app, "owner@example.com")
    make_user(app, "fan@example.com")
    booking_id = make_booking(app, owner_id)

    login(client, "fan@example.com", "Sicher!2026x")
    response = client.post(
        f"/kontakt/{owner_id}",
        data={"subject": "Frage zum Termin", "message": "Könntest du tauschen?",
              "booking": str(booking_id)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Nachricht an".encode() in response.data

    with app.app_context():
        mail = EmailMessage.query.filter_by(kind="contact").one()
        assert mail.recipient == "owner@example.com"
        assert "Frage zum Termin" in mail.body
        assert "fan@example.com" not in mail.body, "sender address must not leak"
        # The related slot is quoted with date, time and charging point.
        assert "Ladepunkt" in mail.body
        assert "Montag" in mail.body


def test_contact_requires_a_real_message(app, client):
    owner_id = make_user(app, "owner@example.com")
    make_user(app, "fan@example.com")
    login(client, "fan@example.com", "Sicher!2026x")
    response = client.post(
        f"/kontakt/{owner_id}", data={"subject": "x", "message": "y"},
        follow_redirects=True,
    )
    assert "Bitte gib einen Betreff".encode() in response.data
    assert queue_count(app, "contact") == 0


def test_you_cannot_write_to_yourself(app, client):
    make_user(app, "anna@example.com")
    with app.app_context():
        user_id = User.query.filter_by(email="anna@example.com").first().id
    login(client, "anna@example.com", "Sicher!2026x")
    response = client.get(f"/kontakt/{user_id}", follow_redirects=True)
    assert "nicht selbst schreiben".encode() in response.data


# ---------------------------------------------------------------------------
# Feedback and board
# ---------------------------------------------------------------------------

def test_feedback_form_creates_a_suggestion_and_notifies_admins(app, client):
    make_user(app, "anna@example.com")
    login(client, "anna@example.com", "Sicher!2026x")

    response = client.post(
        "/feedback",
        data={"title": "Erinnerung früher senden", "content": "Bitte 60 Minuten vorher."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Danke".encode() in response.data

    with app.app_context():
        post = Post.query.one()
        assert post.topic == TOPIC_FEEDBACK
        assert post.status == "open"
        assert post.anonymous is False
        # The administration is notified.
        mail = EmailMessage.query.filter_by(kind="feedback").one()
        assert mail.recipient == "admin@example.com"
        assert "Erinnerung früher senden" in mail.body


def test_feedback_can_be_submitted_anonymously(app, client):
    make_user(app, "anna@example.com")
    login(client, "anna@example.com", "Sicher!2026x")
    client.post(
        "/feedback",
        data={"title": "Anonymer Hinweis", "content": "Bitte prüfen.", "anonymous": "1"},
        follow_redirects=True,
    )
    with app.app_context():
        post = Post.query.one()
        assert post.anonymous is True
        assert post.author_label == "Anonym"
        # The suggestion itself carries no name, not even in the admin notice.
        mail = EmailMessage.query.filter_by(kind="feedback").one()
        assert "anonym" in mail.body
        assert "Anna Muster" not in mail.body

    body = client.get("/feedback").data.decode()
    card = body.split("Anonymer Hinweis")[-1]
    assert "anonym" in card
    assert "Anna M." not in card.split("Erledigte Vorschläge")[0]


def test_feedback_shows_and_updates_the_status(app, client):
    make_user(app, "anna@example.com")
    login(client, "anna@example.com", "Sicher!2026x")
    client.post(
        "/feedback",
        data={"title": "Statusbitte", "content": "Bitte Status pflegen."},
        follow_redirects=True,
    )
    client.post("/abmelden")

    login(client, "admin@example.com", "ChargeFair!2026")
    with app.app_context():
        post_id = Post.query.one().id
    response = client.post(
        f"/admin/feedback/{post_id}",
        data={"status": "done", "reply": "Wird in Version 1.1 umgesetzt."},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        post = db.session.get(Post, post_id)
        assert post.status == "done"
        assert post.admin_reply.startswith("Wird in Version")
        assert post.replied_at is not None
        # Answer is visible as a comment and sent by email.
        assert Comment.query.filter(Comment.content.like("%Rückmeldung%")).count() == 1
        mail = EmailMessage.query.filter_by(kind="feedback_response").one()
        assert "Wird in Version 1.1 umgesetzt." in mail.body


def test_board_offers_topics_beyond_charging(app, client):
    make_user(app, "anna@example.com")
    login(client, "anna@example.com", "Sicher!2026x")

    body = client.get("/board").data.decode()
    assert "Allgemein" in body
    assert "Ladeplatz" in body

    client.post(
        "/board",
        data={
            "title": "Teeküche im 2. OG",
            "content": "Bitte Kaffeemaschine entkalken.",
            "kind": "info",
            "topic": "allgemein",
        },
        follow_redirects=True,
    )
    with app.app_context():
        post = Post.query.one()
        assert post.topic == "allgemein"
        assert post.topic_label == "Allgemein"

    # Feedback suggestions are kept out of the general board list.
    client.post(
        "/feedback",
        data={"title": "Vorschlag", "content": "Bitte etwas ändern."},
        follow_redirects=True,
    )
    body = client.get("/board").data.decode()
    assert "Vorschlag" not in body
    body = client.get("/feedback").data.decode()
    assert "Vorschlag" in body


def test_board_topic_filter(app, client):
    make_user(app, "anna@example.com")
    login(client, "anna@example.com", "Sicher!2026x")
    client.post(
        "/board",
        data={"title": "Allgemeines Thema", "content": "Inhalt dazu.", "topic": "allgemein"},
        follow_redirects=True,
    )
    client.post(
        "/board",
        data={"title": "Ladeplatz-Thema", "content": "Inhalt dazu.", "topic": "ladeplatz"},
        follow_redirects=True,
    )
    body = client.get("/board?thema=allgemein").data.decode()
    assert "Allgemeines Thema" in body
    assert "Ladeplatz-Thema" not in body


# ---------------------------------------------------------------------------
# Mathematical documentation
# ---------------------------------------------------------------------------

def test_methods_overview_links_to_the_math_pages(client):
    response = client.get("/verfahren")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Mathematische Beschreibung" in body


@pytest.mark.parametrize("key", ["lexicographic", "rank_based", "guaranteed", "lottery", "fcfs"])
def test_every_method_has_a_math_page(client, key: str):
    response = client.get(f"/verfahren/{key}")
    assert response.status_code == 200
    body = response.data.decode()
    for section in ("Eingangsgrößen", "Entscheidungsvariablen", "Zielfunktionen",
                    "Nebenbedingungen", "Ablauf", "Eigenschaften"):
        assert section in body, f"{key}: {section} missing"
    # The formal core is present.
    assert "x_{u,s}" in body
    assert "\\begin{align*}" in body, "LaTeX block expected"


def test_math_page_states_the_physical_constraint(client):
    body = client.get("/verfahren/lexicographic").data.decode()
    assert "≤ c_s" in body or "&le; c_s" in body
    assert "Ladepunkte frei" in body


def test_unknown_method_returns_404(client):
    assert client.get("/verfahren/gibtsnicht").status_code == 404


def test_help_page_links_to_the_math_pages(client):
    body = client.get("/hilfe").data.decode()
    assert "/verfahren/" in body
    assert "Mathematik" in body or "mathematische" in body
