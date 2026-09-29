"""Employee area: landing page, dashboard, calendar, bookings, waitlist, swaps, board."""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..allocation_service import (
    accept_swap_offer,
    accept_waitlist_offer,
    active_interests,
    board_posts,
    bookings_of_week,
    cancel_booking,
    cancel_swap_offer,
    check_in,
    create_booking,
    create_swap_offer,
    default_round_week,
    free_capacity,
    get_or_create_round,
    has_booking_at,
    interest_map,
    join_waitlist,
    notify_admins_about_feedback,
    occupancy,
    register_interest,
    release_booking,
    take_over_booking,
    user_bookings_in_week,
    waitlist_for_slot,
    week_end,
    week_overview,
    withdraw_interest,
)
from ..extensions import db
from ..models import (
    BOOKING_ACTIVE,
    BOOKING_RELEASED,
    TOPIC_FEEDBACK,
    POST_TOPIC_LABELS,
    Booking,
    BookingRequest,
    BookingRound,
    BookingSeries,
    ChargingPoint,
    Comment,
    ParkingSpace,
    Post,
    RoundParticipation,
    Setting,
    SlotInterest,
    SwapOffer,
    User,
    WaitlistEntry,
    topic_choices,
    utcnow,
)
from ..method_docs import all_methods_math, method_with_spec
from ..services import (
    audit,
    capacity_per_slot,
    current_allocation_method,
    guaranteed_slots_per_week,
    max_slots_per_user,
    week_of,
    windows,
    workdays,
    describe_week,
)
from ..slots import (
    DAY_NAMES,
    DAY_SHORT,
    RECURRENCE_LABELS,
    recurrence_matches,
    week_dates,
    window_label,
)
bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_week(raw: str | None) -> date:
    if raw:
        try:
            return week_of(date.fromisoformat(raw))
        except ValueError:
            pass
    return week_of(date.today())


def _round_for(week: date) -> BookingRound | None:
    return BookingRound.query.filter_by(week_start=week).first()


def _direct_booking_allowed(week: date, round_obj: BookingRound | None) -> tuple[bool, str]:
    """Short-term or post-round capacity may be booked directly."""
    today = week_of(date.today())
    if week < today:
        return False, "Für vergangene Wochen können keine Ladezeiten gebucht werden."
    if week == today:
        return True, ""
    if round_obj is None:
        return True, ""
    if round_obj.status == "open":
        return (
            False,
            "Für diese Woche läuft eine Vergaberunde. Bitte gib dort deine Wunschzeiten an – "
            "die Vergabe erfolgt nicht nach der Reihenfolge des Eingangs.",
        )
    return True, ""


def _my_limit_left(week: date) -> int:
    """How many more charging slots this person may book this week."""
    return max(0, _my_limit_total() - len(user_bookings_in_week(current_user, week)))


def _my_limit_total() -> int:
    """Total weekly allowance: personal preference capped by the global maximum."""
    maximum = max_slots_per_user()
    return max(1, min(current_user.max_weekly_slots or maximum, maximum))


def _weekly_rules() -> dict:
    """Guarantee and maximum as described to the employees."""
    return {
        "guarantee": guaranteed_slots_per_week(),
        "maximum": max_slots_per_user(),
        "personal_max": _my_limit_total(),
    }


def _own_booking_or_404(booking_id: int) -> Booking:
    booking = db.session.get(Booking, booking_id)
    if booking is None:
        abort(404)
    if booking.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return booking


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@bp.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    points = ChargingPoint.query.filter_by(is_active=True).count()
    users = User.query.filter_by(role="user", is_active=True).count()
    days = len(workdays())
    per_day = len(windows())
    capacity = points * days * per_day
    return render_template(
        "landing.html",
        points=points,
        users=users,
        capacity=capacity,
        per_week=round(capacity / users, 2) if users else 0,
        windows=[window_label(windows(), index) for index in range(len(windows()))],
    )


@bp.route("/healthz")
def healthz():
    """Liveness probe used by Docker and orchestration tooling."""
    from flask import jsonify

    from ..models import ChargingPoint

    try:
        points = ChargingPoint.query.filter(ChargingPoint.is_active.is_(True)).count()
    except Exception:  # pragma: no cover - database not ready yet
        return jsonify(status="starting"), 503
    return jsonify(status="ok", charging_points=points), 200


@bp.route("/hilfe")
def help_page():
    from ..allocation import available_methods

    return render_template(
        "help.html",
        methods=available_methods(),
        active_method=current_allocation_method(),
        points=capacity_per_slot(),
        windows=[window_label(windows(), index) for index in range(len(windows()))],
        days=[DAY_NAMES[day] for day in workdays()],
        max_regular=guaranteed_slots_per_week(),
        max_total=max_slots_per_user(),
    )


@bp.route("/verfahren")
def methods_overview():
    """Overview of all allocation methods with links to the formal description."""
    from ..allocation import available_methods

    return render_template(
        "methods.html",
        methods=available_methods(),
        active_method=current_allocation_method(),
    )


@bp.route("/verfahren/<key>")
def method_detail(key: str):
    """Mathematical description of one allocation method."""
    spec, math_doc = method_with_spec(key)
    if spec is None or math_doc is None:
        abort(404)
    others = [item for item in all_methods_math() if item.key != key]
    return render_template(
        "method_detail.html",
        spec=spec,
        doc=math_doc,
        active_method=current_allocation_method(),
        others=others,
        points=capacity_per_slot(),
    )


# ---------------------------------------------------------------------------
# One-click links from emails (no login required, signed and expiring)
# ---------------------------------------------------------------------------

@bp.route("/freigeben/<token>")
def one_click_release(token: str):
    """Release a booking through the signed link in a reminder email."""
    from ..tokens import read_release_token

    payload = read_release_token(current_app.config["SECRET_KEY"], token)
    booking = db.session.get(Booking, payload["booking"]) if payload else None
    user = db.session.get(User, payload["user"]) if payload else None

    if not payload or booking is None or user is None:
        return render_template(
            "link_result.html",
            title="Link ungültig oder abgelaufen",
            message=(
                "Dieser Freigabe-Link ist nicht mehr gültig. Ladezeiten können "
                "jederzeit angemeldet im Bereich „Meine Ladezeiten“ freigegeben werden."
            ),
            ok=False,
        ), 410

    if booking.user_id != user.id:
        return render_template(
            "link_result.html",
            title="Termin nicht mehr vorhanden",
            message="Diese Ladezeit gehört inzwischen zu einer anderen Person.",
            ok=False,
        ), 409

    if booking.status != BOOKING_ACTIVE:
        return render_template(
            "link_result.html",
            title="Bereits erledigt",
            message=f"Für diese Ladezeit ist der Status „{booking.status_label}“ gespeichert.",
            ok=True,
            booking=booking,
        )

    if booking.is_past:
        return render_template(
            "link_result.html",
            title="Termin liegt in der Vergangenheit",
            message="Vergangene Ladezeiten können nicht mehr freigegeben werden.",
            ok=False,
            booking=booking,
        )

    release_booking(booking, user, note="Freigabe über E-Mail-Link")
    return render_template(
        "link_result.html",
        title="Ladezeit freigegeben",
        message=(
            "Vielen Dank! Alle Personen, die Interesse an diesem Termin hatten, "
            "wurden sofort per E-Mail informiert."
        ),
        ok=True,
        booking=booking,
    )


@bp.route("/slot/<token>")
def one_click_takeover(token: str):
    """Take over a freed slot through the signed link in an email."""
    from ..tokens import read_takeover_token

    payload = read_takeover_token(current_app.config["SECRET_KEY"], token)
    booking = db.session.get(Booking, payload["booking"]) if payload else None
    user = db.session.get(User, payload["user"]) if payload else None

    if not payload or booking is None or user is None:
        return render_template(
            "link_result.html",
            title="Link ungültig oder abgelaufen",
            message=(
                "Dieser Link ist nicht mehr gültig. Freie Ladezeiten findest du "
                "jederzeit im Wochenplan."
            ),
            ok=False,
        ), 410

    if booking.status == BOOKING_ACTIVE and booking.user_id == user.id:
        return render_template(
            "link_result.html",
            title="Du hast den Platz bereits",
            message="Diese Ladezeit ist schon dir zugeordnet.",
            ok=True,
            booking=booking,
        )

    if booking.status != BOOKING_RELEASED:
        return render_template(
            "link_result.html",
            title="Platz ist nicht mehr frei",
            message=(
                "Der Termin wurde inzwischen anderweitig vergeben oder ist "
                "zurückgezogen worden. Im Wochenplan siehst du alle freien Zeiten."
            ),
            ok=False,
            booking=booking,
        )

    if take_over_booking(booking, user):
        return render_template(
            "link_result.html",
            title="Ladeplatz übernommen",
            message="Der Termin ist jetzt dir zugeordnet. Viel Erfolg beim Laden!",
            ok=True,
            booking=booking,
        )

    return render_template(
        "link_result.html",
        title="Übernahme nicht möglich",
        message=(
            "Du hast zu diesem Zeitpunkt bereits eine Ladezeit gebucht oder der "
            "Platz wurde gerade vergeben."
        ),
        ok=False,
        booking=booking,
    )


# ---------------------------------------------------------------------------
# Contact between colleagues
# ---------------------------------------------------------------------------

@bp.route("/kontakt/<int:user_id>", methods=["GET", "POST"])
@login_required
def contact(user_id: int):
    """Write to a colleague through the system instead of exposing addresses."""
    recipient = db.session.get(User, user_id)
    if recipient is None or not recipient.is_active:
        abort(404)
    if recipient.id == current_user.id:
        flash("Du kannst dir nicht selbst schreiben.", "info")
        return redirect(url_for("main.overview"))

    booking = None
    raw_booking = request.values.get("booking", "")
    if raw_booking.isdigit():
        candidate = db.session.get(Booking, int(raw_booking))
        if candidate is not None and candidate.user_id == recipient.id:
            booking = candidate

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()[:120]
        message = request.form.get("message", "").strip()[:2000]
        if len(subject) < 3 or len(message) < 5:
            flash("Bitte gib einen Betreff und eine kurze Nachricht an.", "error")
            return render_template(
                "contact.html", recipient=recipient, booking=booking,
                subject=subject, message=message,
            )
        from .. import notifications as notify

        notify.send_contact_message(
            recipient=recipient,
            sender=current_user,
            subject=subject,
            message=message,
            booking=booking,
        )
        audit("contact.send", f"Nachricht an Nutzer #{recipient.id}", current_user, request.remote_addr)
        db.session.commit()
        flash(
            f"Nachricht an {recipient.short_name} gesendet. Die Antwort läuft "
            f"direkt zwischen euch – die E-Mail-Adressen werden nicht ausgetauscht.",
            "success",
        )
        return redirect(url_for("main.overview"))

    return render_template(
        "contact.html", recipient=recipient, booking=booking,
        subject="", message="",
    )


# ---------------------------------------------------------------------------
# Overview of all bookings and vehicles
# ---------------------------------------------------------------------------

@bp.route("/uebersicht")
@login_required
def overview():
    """Who charges when, with vehicle and contact option."""
    week = _parse_week(request.args.get("woche"))
    bookings = bookings_of_week(week)
    interests = interest_map(week, current_user)

    rows = []
    for index in range(len(windows())):
        cells = []
        for day in workdays():
            entries = [
                booking for booking in bookings
                if booking.weekday == day and booking.window_index == index
            ]
            entries = _visible_order(entries, current_user)
            cells.append(
                {
                    "weekday": day,
                    "window": index,
                    "entries": entries,
                    "interest": interests.get((day, index)),
                    "waiting": len(waitlist_for_slot(week, day, index)),
                }
            )
        rows.append({"label": window_label(windows(), index), "cells": cells})

    return render_template(
        "overview.html",
        week=week,
        week_label=describe_week(week),
        prev_week=week - timedelta(weeks=1),
        next_week=week + timedelta(weeks=1),
        rows=rows,
        days=[
            {
                "weekday": day,
                "short": DAY_SHORT[day],
                "name": DAY_NAMES[day],
                "date": week + timedelta(days=day),
            }
            for day in workdays()
        ],
        capacity=capacity_per_slot(),
        my_interests=active_interests(current_user),
        free=sum(free_capacity(week).values()),
        total_capacity=capacity_per_slot() * len(workdays()) * len(windows()),
    )


def _visible_order(entries: list, viewer: User) -> list:
    """Own bookings first, then free slots, then everything else."""
    def key(booking: Booking):
        if booking.user_id == viewer.id:
            return (0, booking.start)
        if booking.status == BOOKING_RELEASED:
            return (1, booking.start)
        return (2, booking.start)

    return sorted(entries, key=key)


@bp.route("/interesse", methods=["POST"])
@login_required
def set_interest():
    """Register or withdraw interest in a concrete slot."""
    action = request.form.get("action", "add")
    booking_id = request.form.get("booking", "")
    week = _parse_week(request.form.get("week"))
    try:
        weekday = int(request.form.get("weekday", "-1"))
        window_index = int(request.form.get("window", "-1"))
    except ValueError:
        abort(400)

    if action == "remove":
        interest_id = request.form.get("interest", "")
        interest = db.session.get(SlotInterest, int(interest_id)) if interest_id.isdigit() else None
        if interest is None or not withdraw_interest(interest, current_user):
            flash("Der Eintrag konnte nicht entfernt werden.", "error")
        else:
            flash("Dein Interesse wurde zurückgezogen.", "info")
        return redirect(request.form.get("next") or url_for("main.overview", woche=week.isoformat()))

    if weekday not in workdays() or not 0 <= window_index < len(windows()):
        abort(400)

    if not bookings_of_week(week) or not any(
        booking.weekday == weekday and booking.window_index == window_index
        for booking in bookings_of_week(week)
    ):
        flash("Für dieses Zeitfenster ist derzeit keine Ladezeit gebucht.", "info")
        return redirect(request.form.get("next") or url_for("main.overview", woche=week.isoformat()))

    register_interest(current_user, week, weekday, window_index,
                      request.form.get("note", ""))
    from .. import notifications as notify

    notify.send_interest_confirmation(current_user)
    audit("interest.add", f"{week} {DAY_NAMES[weekday]} {window_label(windows(), window_index)}", current_user)
    db.session.commit()
    flash(
        "Interesse vorgemerkt. Sobald die Person den Termin freigibt, bekommst du "
        "automatisch eine E-Mail mit einem Ein-Klick-Link.",
        "success",
    )
    return redirect(request.form.get("next") or url_for("main.overview", woche=week.isoformat()))


# ---------------------------------------------------------------------------
# Feedback: improvement suggestions and topics beyond charging
# ---------------------------------------------------------------------------

@bp.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    """Form for improvement suggestions, plus the list of submitted ones."""
    from ..models import POST_STATUS_LABELS

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        if len(title) < 4 or len(content) < 10:
            flash("Bitte gib einen Titel und eine etwas ausführlichere Beschreibung an.", "error")
            return redirect(url_for("main.feedback"))

        post = Post(
            user_id=current_user.id,
            title=title[:160],
            content=content[:4000],
            kind="feedback",
            topic=TOPIC_FEEDBACK,
            anonymous=bool(request.form.get("anonymous")),
            status="open",
        )
        db.session.add(post)
        db.session.flush()
        audit("feedback.create", f"Vorschlag #{post.id}: {post.title}", current_user)
        db.session.commit()

        notify_admins_about_feedback(post)
        flash(
            "Danke! Dein Verbesserungsvorschlag ist eingegangen. Die Administration "
            "prüft ihn und antwortet hier im Board.",
            "success",
        )
        return redirect(url_for("main.feedback"))

    suggestions = (
        Post.query
        .filter(Post.topic == TOPIC_FEEDBACK)
        .order_by(Post.created_at.desc())
        .limit(60)
        .all()
    )
    counts = {
        key: sum(1 for post in suggestions if post.status == key)
        for key in ("open", "in_progress", "done", "declined")
    }
    return render_template(
        "feedback.html",
        suggestions=[post for post in suggestions if not post.is_closed],
        closed=[post for post in suggestions if post.is_closed],
        counts=counts,
        status_labels=POST_STATUS_LABELS,
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route("/dashboard")
@login_required
def dashboard():
    today = date.today()
    this_week = week_of(today)
    next_week = this_week + timedelta(weeks=1)

    upcoming = (
        Booking.query
        .filter(Booking.user_id == current_user.id)
        .filter(Booking.end >= utcnow())
        .filter(Booking.status.in_((BOOKING_ACTIVE, BOOKING_RELEASED)))
        .order_by(Booking.start)
        .limit(6)
        .all()
    )
    next_booking = upcoming[0] if upcoming else None

    overview = week_overview(this_week, current_user)
    round_obj = _round_for(next_week)
    my_requests = []
    participation = None
    if round_obj:
        participation = RoundParticipation.query.filter_by(
            round_id=round_obj.id, user_id=current_user.id
        ).first()
        my_requests = (
            BookingRequest.query
            .filter_by(round_id=round_obj.id, user_id=current_user.id)
            .order_by(BookingRequest.priority)
            .all()
        )

    waitlist = (
        WaitlistEntry.query
        .filter(WaitlistEntry.user_id == current_user.id)
        .filter(WaitlistEntry.status.in_(("waiting", "offered")))
        .order_by(WaitlistEntry.created_at)
        .all()
    )
    open_offers = [entry for entry in waitlist if entry.status == "offered" and not entry.is_offer_expired]
    released = (
        Booking.query
        .filter(Booking.status == BOOKING_RELEASED)
        .filter(Booking.end >= utcnow())
        .filter(Booking.user_id != current_user.id)
        .order_by(Booking.start)
        .limit(5)
        .all()
    )

    stats_recent = (
        Booking.query
        .filter(Booking.user_id == current_user.id)
        .filter(Booking.start >= datetime.combine(today - timedelta(weeks=4), dtime.min))
        .filter(Booking.status.in_((BOOKING_ACTIVE, "completed")))
        .count()
    )
    average = _average_bookings()

    return render_template(
        "dashboard.html",
        today=today,
        this_week=this_week,
        next_week=next_week,
        upcoming=upcoming,
        next_booking=next_booking,
        overview=overview,
        round_obj=round_obj,
        participation=participation,
        my_requests=my_requests,
        waitlist=waitlist,
        open_offers=open_offers,
        released=released,
        recent_count=stats_recent,
        average=average,
        limit_left=_my_limit_left(this_week),
        limit_total=_my_limit_total(),
        rules=_weekly_rules(),
        granted_this_week=len(overview["my_bookings"]),
        free_this_week=sum(value for value in free_capacity(this_week).values()),
        capacity_this_week=overview["totals"]["capacity"],
    )


def _average_bookings(weeks: int = 4) -> float:
    since = datetime.combine(date.today() - timedelta(weeks=weeks), dtime.min)
    total = (
        Booking.query
        .filter(Booking.start >= since)
        .filter(Booking.status.in_((BOOKING_ACTIVE, "completed")))
        .count()
    )
    users = User.query.filter_by(role="user", is_active=True).count()
    return round(total / users, 2) if users else 0.0


# ---------------------------------------------------------------------------
# Weekly plan / calendar
# ---------------------------------------------------------------------------

@bp.route("/plan")
@login_required
def plan():
    week = _parse_week(request.args.get("woche"))
    round_obj = _round_for(week)
    overview = week_overview(week, current_user)
    allowed, reason = _direct_booking_allowed(week, round_obj)

    my_requests = []
    if round_obj:
        my_requests = (
            BookingRequest.query
            .filter_by(round_id=round_obj.id, user_id=current_user.id)
            .order_by(BookingRequest.priority)
            .all()
        )
    requested_keys = {(item.weekday, item.window_index) for item in my_requests}
    priority_map = {(item.weekday, item.window_index): item.priority for item in my_requests}
    free = free_capacity(week)
    participation = None
    if round_obj:
        participation = RoundParticipation.query.filter_by(
            round_id=round_obj.id, user_id=current_user.id
        ).first()

    # Wishes may be filed until the round is closed. A missing round is not a
    # blocker: it is created on submit (for the current or a future week).
    wish_open = (
        round_obj.status == "open" if round_obj else week >= week_of(date.today())
    )

    days = []
    for day in workdays():
        cells = []
        for index in range(len(windows())):
            key = (day, index)
            cells.append(
                {
                    "weekday": day,
                    "window": index,
                    "key": key,
                    "free": free.get(key, 0),
                    "capacity": capacity_per_slot(),
                    "requested": key in requested_keys,
                }
            )
        days.append({"weekday": day, "name": DAY_NAMES[day], "short": DAY_SHORT[day], "cells": cells})

    return render_template(
        "plan.html",
        week=week,
        week_label=describe_week(week),
        prev_week=week - timedelta(weeks=1),
        next_week=week + timedelta(weeks=1),
        overview=overview,
        round_obj=round_obj,
        days=days,
        windows=[window_label(windows(), index) for index in range(len(windows()))],
        direct_allowed=allowed,
        direct_reason=reason,
        wish_open=wish_open,
        my_requests=my_requests,
        priority_map=priority_map,
        participation=participation,
        limit_left=_my_limit_left(week),
        limit_total=_my_limit_total(),
        rules=_weekly_rules(),
        granted_this_week=len(user_bookings_in_week(current_user, week)),
        free_slots=sum(free.values()),
        capacity_total=sum(free.values()) + len(bookings_of_week(week)),
        is_current_week=week == week_of(date.today()),
    )


@bp.route("/plan/wunsch", methods=["POST"])
@login_required
def submit_wish():
    week = _parse_week(request.form.get("week"))
    round_obj = _round_for(week)
    if round_obj is None:
        round_obj = get_or_create_round(week, create=True)
    if round_obj is None or round_obj.status != "open":
        flash("Für diese Woche können keine Wünsche mehr abgegeben werden.", "error")
        return redirect(url_for("main.plan", woche=week.isoformat()))

    keys: list[tuple[int, int]] = []
    for raw in request.form.getlist("slot"):
        try:
            day_raw, window_raw = raw.split(":")
            keys.append((int(day_raw), int(window_raw)))
        except (ValueError, AttributeError):
            continue
    if not keys:
        flash("Bitte wähle mindestens einen Wunschtermin aus.", "error")
        return redirect(url_for("main.plan", woche=week.isoformat()))

    participation = RoundParticipation.query.filter_by(
        round_id=round_obj.id, user_id=current_user.id
    ).first()
    if participation is None:
        participation = RoundParticipation(
            round_id=round_obj.id, user_id=current_user.id, desired_count=1
        )
        db.session.add(participation)
        db.session.flush()

    desired_raw = request.form.get("desired_count", "1")
    desired = int(desired_raw) if desired_raw.isdigit() else 1
    personal_max = min(current_user.max_weekly_slots or max_slots_per_user(), max_slots_per_user())
    participation.desired_count = max(1, min(desired, personal_max))
    participation.note = (request.form.get("note") or "")[:255] or None

    BookingRequest.query.filter_by(
        round_id=round_obj.id, user_id=current_user.id
    ).delete()

    seen = set()
    priority = 1
    ordered = []
    for position, key in enumerate(keys, start=1):
        ranking = request.form.get(f"rank_{key[0]}_{key[1]}")
        ordered.append((int(ranking) if ranking and ranking.isdigit() else position, key))
    ordered.sort()

    for _, key in ordered:
        if key in seen:
            continue
        seen.add(key)
        db.session.add(
            BookingRequest(
                round_id=round_obj.id,
                user_id=current_user.id,
                priority=priority,
                weekday=key[0],
                window_index=key[1],
            )
        )
        priority += 1

    audit("wish.submit", f"{priority - 1} Wünsche für {week} abgegeben", current_user)
    db.session.commit()
    flash(
        f"Deine Wünsche für {describe_week(week)} sind gespeichert. "
        f"Die Vergabe erfolgt nicht nach dem Eingangszeitpunkt.",
        "success",
    )
    return redirect(url_for("main.plan", woche=week.isoformat()))


@bp.route("/plan/wunsch/loeschen", methods=["POST"])
@login_required
def delete_wishes():
    week = _parse_week(request.form.get("week"))
    round_obj = _round_for(week)
    if round_obj is not None and round_obj.status != "open":
        flash("Die Wünsche können nicht mehr geändert werden.", "error")
        return redirect(url_for("main.plan", woche=week.isoformat()))
    if round_obj is not None:
        BookingRequest.query.filter_by(
            round_id=round_obj.id, user_id=current_user.id
        ).delete()
        RoundParticipation.query.filter_by(
            round_id=round_obj.id, user_id=current_user.id
        ).delete()
        db.session.commit()
    flash("Deine Wünsche wurden zurückgezogen.", "info")
    return redirect(url_for("main.plan", woche=week.isoformat()))


@bp.route("/plan/buchen", methods=["POST"])
@login_required
def direct_booking():
    week = _parse_week(request.form.get("week"))
    round_obj = _round_for(week)
    allowed, reason = _direct_booking_allowed(week, round_obj)
    if not allowed:
        flash(reason, "error")
        return redirect(url_for("main.plan", woche=week.isoformat()))

    try:
        weekday = int(request.form.get("weekday", "-1"))
        window_index = int(request.form.get("window", "-1"))
    except ValueError:
        abort(400)
    if weekday not in workdays() or not 0 <= window_index < len(windows()):
        abort(400)

    if _my_limit_left(week) <= 0:
        flash(
            "Du hast dein wöchentliches Kontingent ausgeschöpft. "
            "Weitere Ladezeiten sind erst wieder nächste Woche möglich.",
            "error",
        )
        return redirect(url_for("main.plan", woche=week.isoformat()))

    if has_booking_at(current_user, week, weekday, window_index):
        flash("Für diesen Termin hast du bereits eine Buchung.", "info")
        return redirect(url_for("main.plan", woche=week.isoformat()))

    if free_capacity(week).get((weekday, window_index), 0) <= 0:
        flash(
            "Dieser Termin ist leider belegt. Du kannst dich auf die Warteliste setzen "
            "lassen – wir melden uns, sobald ein Platz frei wird.",
            "warning",
        )
        return redirect(url_for("main.plan", woche=week.isoformat()))

    booking = create_booking(current_user, week, weekday, window_index, origin="self")
    if booking is None:
        flash("Der Termin ist inzwischen vergeben.", "error")
        return redirect(url_for("main.plan", woche=week.isoformat()))

    audit("booking.create", f"Buchung {booking.slot_label} am {booking.date_label}", current_user)
    db.session.commit()
    flash(
        f"Ladezeit gebucht: {DAY_NAMES[weekday]}, {booking.date_label}, "
        f"{window_label(windows(), window_index)} Uhr.",
        "success",
    )
    return redirect(url_for("main.plan", woche=week.isoformat()))


@bp.route("/plan/belegung")
@login_required
def occupancy_view():
    week = _parse_week(request.args.get("woche"))
    bookings = bookings_of_week(week)
    rows = []
    for index in range(len(windows())):
        cells = []
        for day in workdays():
            entries = [
                booking for booking in bookings
                if booking.weekday == day and booking.window_index == index
            ]
            cells.append({"weekday": day, "window": index, "entries": entries})
        rows.append({"label": window_label(windows(), index), "cells": cells})
    return render_template(
        "occupancy.html",
        week=week,
        week_label=describe_week(week),
        prev_week=week - timedelta(weeks=1),
        next_week=week + timedelta(weeks=1),
        rows=rows,
        days=[
            {
                "weekday": day,
                "short": DAY_SHORT[day],
                "name": DAY_NAMES[day],
                "date": week + timedelta(days=day),
            }
            for day in workdays()
        ],
        capacity=capacity_per_slot(),
    )


# ---------------------------------------------------------------------------
# My bookings
# ---------------------------------------------------------------------------

@bp.route("/meine-buchungen")
@login_required
def my_bookings():
    today = date.today()
    upcoming = (
        Booking.query
        .filter(Booking.user_id == current_user.id)
        .filter(Booking.end >= utcnow())
        .filter(Booking.status.in_((BOOKING_ACTIVE, BOOKING_RELEASED)))
        .order_by(Booking.start)
        .all()
    )
    past = (
        Booking.query
        .filter(Booking.user_id == current_user.id)
        .filter(Booking.end < utcnow())
        .order_by(Booking.start.desc())
        .limit(30)
        .all()
    )
    series_list = BookingSeries.query.filter_by(user_id=current_user.id).order_by(
        BookingSeries.weekday
    ).all()
    return render_template(
        "bookings.html",
        upcoming=upcoming,
        past=past,
        series_list=series_list,
        recurrences=RECURRENCE_LABELS,
        today=today,
        max_date=today + timedelta(weeks=26),
    )


@bp.route("/buchung/<int:booking_id>/freigeben", methods=["POST"])
@login_required
def release(booking_id: int):
    booking = _own_booking_or_404(booking_id)
    if release_booking(booking, current_user, request.form.get("note", "")):
        flash(
            "Ladezeit freigegeben. Wir informieren alle, die Interesse an diesem "
            "Termin hinterlegt haben, per E-Mail.",
            "success",
        )
    else:
        flash("Diese Ladezeit kann nicht freigegeben werden.", "error")
    return redirect(request.form.get("next") or url_for("main.my_bookings"))


@bp.route("/buchung/<int:booking_id>/stornieren", methods=["POST"])
@login_required
def cancel(booking_id: int):
    booking = _own_booking_or_404(booking_id)
    if cancel_booking(booking, current_user):
        flash("Ladezeit storniert. Andere Kolleginnen und Kollegen können den Platz nutzen.", "info")
    else:
        flash("Diese Ladezeit kann nicht storniert werden.", "error")
    return redirect(request.form.get("next") or url_for("main.my_bookings"))


@bp.route("/buchung/<int:booking_id>/uebernehmen", methods=["POST"])
@login_required
def takeover(booking_id: int):
    booking = db.session.get(Booking, booking_id)
    if booking is None:
        abort(404)
    if take_over_booking(booking, current_user):
        flash(
            f"Ladeplatz übernommen: {booking.date_label}, {booking.window_label} Uhr, "
            f"{booking.charging_point.name}.",
            "success",
        )
    else:
        flash("Der Platz konnte nicht übernommen werden – möglicherweise ist er schon vergeben.", "error")
    return redirect(request.form.get("next") or url_for("main.plan"))


@bp.route("/buchung/<int:booking_id>/checkin", methods=["POST"])
@login_required
def do_check_in(booking_id: int):
    booking = _own_booking_or_404(booking_id)
    check_in(booking)
    flash("Danke! Deine Ladezeit ist als wahrgenommen vermerkt.", "success")
    return redirect(url_for("main.my_bookings"))


@bp.route("/buchung/<int:booking_id>/serie", methods=["POST"])
@login_required
def make_series(booking_id: int):
    booking = _own_booking_or_404(booking_id)
    recurrence = request.form.get("recurrence", "weekly")
    if recurrence not in RECURRENCE_LABELS:
        recurrence = "weekly"
    raw_until = request.form.get("valid_until", "")
    try:
        valid_until = date.fromisoformat(raw_until)
    except ValueError:
        valid_until = booking.start.date() + timedelta(weeks=12)
    valid_until = min(valid_until, booking.start.date() + timedelta(weeks=26))

    if BookingSeries.query.filter_by(
        user_id=current_user.id,
        weekday=booking.weekday,
        window_index=booking.window_index,
        recurrence=recurrence,
    ).first():
        flash("Für diesen Termin existiert bereits eine regelmäßige Buchung.", "info")
        return redirect(url_for("main.my_bookings"))

    series = BookingSeries(
        user_id=current_user.id,
        charging_point_id=booking.charging_point_id,
        parking_space_id=booking.parking_space_id,
        weekday=booking.weekday,
        window_index=booking.window_index,
        recurrence=recurrence,
        valid_from=booking.start.date(),
        valid_until=valid_until,
        is_active=True,
    )
    db.session.add(series)
    db.session.flush()
    booking.booking_series_id = series.id

    audit("series.create", f"Regelmäßige Buchung ab {booking.date_label}", current_user)
    db.session.commit()
    flash(
        "Regelmäßige Ladezeit gespeichert. Du kannst einzelne Termine jederzeit "
        "freigeben – zum Beispiel im Urlaub.",
        "success",
    )
    return redirect(url_for("main.my_bookings"))


@bp.route("/serie/<int:series_id>/beenden", methods=["POST"])
@login_required
def end_series(series_id: int):
    series = db.session.get(BookingSeries, series_id)
    if series is None or (series.user_id != current_user.id and not current_user.is_admin):
        abort(403)
    series.is_active = False
    for booking in Booking.query.filter(
        Booking.booking_series_id == series.id,
        Booking.start > utcnow(),
        Booking.status.in_((BOOKING_ACTIVE, BOOKING_RELEASED)),
    ).all():
        booking.status = "cancelled"
    db.session.commit()
    flash("Die regelmäßige Ladezeit wurde beendet.", "info")
    return redirect(url_for("main.my_bookings"))


# ---------------------------------------------------------------------------
# Waitlist
# ---------------------------------------------------------------------------

@bp.route("/warteliste", methods=["GET", "POST"])
@login_required
def waitlist():
    if request.method == "POST":
        week = _parse_week(request.form.get("week"))
        weekday = int(request.form.get("weekday", "-1"))
        window_index = int(request.form.get("window", "-1"))
        if weekday not in workdays() or not 0 <= window_index < len(windows()):
            abort(400)
        join_waitlist(current_user, week, weekday, window_index, request.form.get("note", ""))
        audit("waitlist.join", f"{week} {DAY_NAMES[weekday]} {window_label(windows(), window_index)}", current_user)
        db.session.commit()
        flash(
            "Du stehst auf der Warteliste. Wir benachrichtigen dich per E-Mail, "
            "sobald ein Platz frei wird.",
            "success",
        )
        return redirect(url_for("main.waitlist"))

    entries = (
        WaitlistEntry.query
        .filter(WaitlistEntry.user_id == current_user.id)
        .filter(WaitlistEntry.status.in_(("waiting", "offered")))
        .order_by(WaitlistEntry.week_start, WaitlistEntry.weekday, WaitlistEntry.window_index)
        .all()
    )

    week = _parse_week(request.args.get("woche"))
    slot_rows = []
    for day in workdays():
        for index in range(len(windows())):
            free = free_capacity(week).get((day, index), 0)
            if free > 0:
                continue
            queue = waitlist_for_slot(week, day, index)
            slot_rows.append(
                {
                    "weekday": day,
                    "window": index,
                    "label": f"{DAY_NAMES[day]}, {window_label(windows(), index)}",
                    "queue": queue,
                    "joined": any(entry.user_id == current_user.id for entry in queue),
                }
            )

    return render_template(
        "waitlist.html",
        entries=entries,
        week=week,
        week_label=describe_week(week),
        prev_week=week - timedelta(weeks=1),
        next_week=week + timedelta(weeks=1),
        slot_rows=slot_rows,
    )


@bp.route("/warteliste/<int:entry_id>/annehmen", methods=["POST"])
@login_required
def accept_offer(entry_id: int):
    entry = db.session.get(WaitlistEntry, entry_id)
    if entry is None or entry.user_id != current_user.id:
        abort(403)
    booking = accept_waitlist_offer(entry, current_user)
    if booking is None:
        flash("Das Angebot ist leider abgelaufen.", "warning")
    else:
        flash(
            f"Ladeplatz übernommen: {booking.date_label}, {booking.window_label} Uhr, "
            f"{booking.charging_point.name}.",
            "success",
        )
    return redirect(url_for("main.waitlist"))


@bp.route("/warteliste/<int:entry_id>/zurueckziehen", methods=["POST"])
@login_required
def withdraw_waitlist(entry_id: int):
    entry = db.session.get(WaitlistEntry, entry_id)
    if entry is None or entry.user_id != current_user.id:
        abort(403)
    entry.status = "cancelled"
    db.session.commit()
    flash("Du hast dich von der Warteliste abgemeldet.", "info")
    return redirect(url_for("main.waitlist"))


# ---------------------------------------------------------------------------
# Swaps
# ---------------------------------------------------------------------------

@bp.route("/tausch", methods=["GET", "POST"])
@login_required
def swaps():
    if request.method == "POST":
        booking = _own_booking_or_404(int(request.form.get("booking_id", "0")))
        wanted_day = request.form.get("wanted_weekday", "")
        wanted_window = request.form.get("wanted_window", "")
        offer = create_swap_offer(
            booking,
            current_user,
            int(wanted_day) if wanted_day.isdigit() else None,
            int(wanted_window) if wanted_window.isdigit() else None,
            request.form.get("message", ""),
        )
        db.session.commit()
        flash(
            "Tauschangebot veröffentlicht. Passende Kolleginnen und Kollegen wurden "
            "per E-Mail informiert.",
            "success",
        )
        return redirect(url_for("main.swaps"))

    own = (
        Booking.query
        .filter(Booking.user_id == current_user.id)
        .filter(Booking.end >= utcnow())
        .filter(Booking.status == BOOKING_ACTIVE)
        .order_by(Booking.start)
        .all()
    )
    open_offers = (
        SwapOffer.query
        .filter(SwapOffer.status == "open")
        .filter(SwapOffer.user_id != current_user.id)
        .order_by(SwapOffer.created_at.desc())
        .all()
    )
    matches = []
    for offer in open_offers:
        candidates = [
            booking for booking in own
            if week_of(booking.start.date()) == week_of(offer.booking.start.date())
        ]
        matches.append({"offer": offer, "candidates": candidates})

    my_offers = (
        SwapOffer.query
        .filter(SwapOffer.user_id == current_user.id)
        .order_by(SwapOffer.created_at.desc())
        .all()
    )
    return render_template(
        "swaps.html",
        own=own,
        matches=matches,
        my_offers=my_offers,
        days=[{"index": day, "name": DAY_NAMES[day]} for day in workdays()],
        windows=[{"index": index, "label": window_label(windows(), index)} for index in range(len(windows()))],
    )


@bp.route("/tausch/<int:offer_id>/annehmen", methods=["POST"])
@login_required
def accept_swap(offer_id: int):
    offer = db.session.get(SwapOffer, offer_id)
    if offer is None:
        abort(404)
    booking = _own_booking_or_404(int(request.form.get("booking_id", "0")))
    if accept_swap_offer(offer, current_user, booking):
        flash("Tausch durchgeführt. Beide Termine sind jetzt umgebucht.", "success")
    else:
        flash("Der Tausch konnte nicht durchgeführt werden.", "error")
    return redirect(url_for("main.swaps"))


@bp.route("/tausch/<int:offer_id>/zurueckziehen", methods=["POST"])
@login_required
def withdraw_swap(offer_id: int):
    offer = db.session.get(SwapOffer, offer_id)
    if offer is None:
        abort(404)
    if cancel_swap_offer(offer, current_user):
        flash("Tauschangebot zurückgezogen.", "info")
    else:
        flash("Das Angebot kann nicht zurückgezogen werden.", "error")
    return redirect(url_for("main.swaps"))


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

@bp.route("/board", methods=["GET", "POST"])
@login_required
def board():
    """Board for short-term arrangements plus topics beyond charging."""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        if len(title) < 4 or len(content) < 4:
            flash("Bitte gib einen Titel und einen kurzen Text an.", "error")
            return redirect(url_for("main.board"))
        kind = request.form.get("kind", "info")
        if kind not in ("info", "offer", "request"):
            kind = "info"
        topic = request.form.get("topic", "ladeplatz")
        if topic not in dict(topic_choices()):
            topic = "ladeplatz"
        booking_id = request.form.get("booking_id", "")
        post = Post(
            user_id=current_user.id,
            title=title[:160],
            content=content[:4000],
            kind=kind,
            topic=topic,
            booking_id=int(booking_id) if booking_id.isdigit() else None,
        )
        db.session.add(post)
        audit("board.create", f"Beitrag „{post.title}“ ({post.topic_label})", current_user)
        db.session.commit()
        flash("Beitrag veröffentlicht.", "success")
        return redirect(url_for("main.board", thema=topic if topic != "ladeplatz" else None))

    selected = request.args.get("thema") or ""
    if selected and selected not in dict(topic_choices()):
        selected = ""
    posts = board_posts(topic=selected or None)
    my_bookings = (
        Booking.query
        .filter(Booking.user_id == current_user.id)
        .filter(Booking.end >= utcnow())
        .order_by(Booking.start)
        .all()
    )
    return render_template(
        "board.html",
        posts=posts,
        my_bookings=my_bookings,
        topic_choices=topic_choices(),
        topic_labels=POST_TOPIC_LABELS,
        selected_topic=selected,
    )


@bp.route("/board/<int:post_id>/kommentar", methods=["POST"])
@login_required
def comment(post_id: int):
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    content = request.form.get("content", "").strip()
    if len(content) < 2:
        flash("Bitte gib einen Kommentar ein.", "error")
        return redirect(url_for("main.board"))
    db.session.add(Comment(post_id=post.id, user_id=current_user.id, content=content[:2000]))
    db.session.commit()
    return redirect(url_for("main.board"))


@bp.route("/board/<int:post_id>/schliessen", methods=["POST"])
@login_required
def close_post(post_id: int):
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    if post.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    post.is_closed = True
    db.session.commit()
    flash("Beitrag geschlossen.", "info")
    return redirect(url_for("main.board"))


@bp.route("/board/<int:post_id>/loeschen", methods=["POST"])
@login_required
def delete_post(post_id: int):
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    if post.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(post)
    db.session.commit()
    flash("Beitrag gelöscht.", "info")
    return redirect(url_for("main.board"))


_ = (
    BOOKING_RELEASED,
    Setting,
    recurrence_matches,
    week_dates,
    ParkingSpace,
    occupancy,
    week_end,
    default_round_week,
)
