"""Administration: infrastructure, users, allocation rounds and phases, settings."""
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

from ..allocation import METHODS, available_methods
from ..allocation_service import (
    allocate_phase,
    allocate_round,
    announce_phase,
    answer_feedback,
    bookings_of_week,
    build_problem,
    current_phase,
    default_round_week,
    feedback_overview,
    get_or_create_phase,
    get_or_create_round,
    materialize_week,
    occupancy,
    phase_for_week,
    remind_phase_deadline,
    week_end,
)
from ..blueprints.auth import admin_required
from ..extensions import db
from ..models import (
    PHASE_CLOSED,
    PHASE_OPEN,
    POST_STATUS_LABELS,
    ROUND_OPEN,
    AuditLog,
    Booking,
    BookingPhase,
    BookingRequest,
    BookingRound,
    ChargingPoint,
    ChargingStation,
    EmailMessage,
    ParkingSpace,
    Post,
    RegistrationToken,
    RoundParticipation,
    Setting,
    SlotInterest,
    SwapOffer,
    User,
    Vehicle,
    WaitlistEntry,
)
from ..services import (
    KEY_ALLOCATION_METHOD,
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
    anonymize_user,
    capacity_per_slot,
    current_allocation_method,
    distribution_report,
    phase_reminder_days,
    phase_weeks,
    process_email_queue,
    slot_reminder_minutes,
    waitlist_offer_minutes,
    week_start,
    windows,
    workdays,
)
from ..slots import (
    DAY_NAMES,
    DEFAULT_WINDOWS,
    DEFAULT_WORKDAYS,
    week_dates,
    window_label,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _parse_week(raw: str | None) -> date:
    if raw:
        try:
            return week_start(date.fromisoformat(raw))
        except ValueError:
            pass
    return default_round_week()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route("/")
@admin_required
def dashboard():
    today = date.today()
    week = week_start(today)

    users = User.query.filter_by(role="user").all()
    active_users = [user for user in users if user.is_active]
    points = ChargingPoint.query.all()
    active_points = [point for point in points if point.is_active]
    stations = ChargingStation.query.count()
    spaces = ParkingSpace.query.count()

    capacity = capacity_per_slot() * len(workdays()) * len(windows())
    counts, _ = occupancy(week)
    used = sum(counts.values())

    round_obj = BookingRound.query.filter_by(week_start=default_round_week()).first()
    open_round = BookingRound.query.filter_by(status="open").first()

    report = distribution_report(weeks=12)
    pending_mail = EmailMessage.query.filter_by(status="pending").count()
    failed_mail = EmailMessage.query.filter_by(status="failed").count()

    open_offers = SwapOffer.query.filter_by(status="open").count()
    waiting = WaitlistEntry.query.filter_by(status="waiting").count()
    offered = WaitlistEntry.query.filter_by(status="offered").count()
    interests = SlotInterest.query.filter(SlotInterest.status.in_(("waiting", "notified"))).count()
    open_phase = BookingPhase.query.filter_by(status="open").first()

    recent_audit = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(12).all()

    next_allocations = (
        BookingRound.query
        .filter(BookingRound.week_start >= week)
        .order_by(BookingRound.week_start)
        .limit(6)
        .all()
    )

    return render_template(
        "admin/dashboard.html",
        today=today,
        week=week,
        counts={
            "users": len(users),
            "active_users": len(active_users),
            "inactive_users": len(users) - len(active_users),
            "stations": stations,
            "points": len(points),
            "active_points": len(active_points),
            "spaces": spaces,
            "capacity": capacity,
            "used": used,
            "utilisation": round(used / capacity * 100, 1) if capacity else 0.0,
        },
        round_obj=round_obj,
        open_round=open_round,
        report=report,
        pending_mail=pending_mail,
        failed_mail=failed_mail,
        open_offers=open_offers,
        waiting=waiting,
        offered=offered,
        interests=interests,
        open_phase=open_phase,
        recent_audit=recent_audit,
        next_allocations=next_allocations,
        method=METHODS[current_allocation_method()],
    )


# ---------------------------------------------------------------------------
# Allocation rounds
# ---------------------------------------------------------------------------

@bp.route("/runden")
@admin_required
def rounds():
    week = _parse_week(request.args.get("woche"))
    entries = (
        BookingRound.query
        .order_by(BookingRound.week_start.desc())
        .limit(30)
        .all()
    )
    phases = (
        BookingPhase.query
        .order_by(BookingPhase.start_week.desc())
        .limit(12)
        .all()
    )
    return render_template(
        "admin/rounds.html",
        entries=entries,
        phases=phases,
        current_phase=current_phase(),
        week=week,
        week_label=f"KW {week.isocalendar().week}",
        methods=available_methods(),
        active_method=current_allocation_method(),
        default_week=default_round_week(),
        phase_weeks_default=phase_weeks(),
        today=date.today(),
    )


# ---------------------------------------------------------------------------
# Allocation phases (several weeks at once)
# ---------------------------------------------------------------------------

@bp.route("/phasen/neu", methods=["POST"])
@admin_required
def create_phase():
    start = _parse_week(request.form.get("week"))
    weeks = max(1, min(12, int(request.form.get("weeks", str(phase_weeks())) or phase_weeks())))
    method = request.form.get("method") or current_allocation_method()
    closes_raw = request.form.get("closes_at", "")
    closes_at = None
    if closes_raw:
        try:
            closes_at = datetime.fromisoformat(closes_raw)
        except ValueError:
            closes_at = None

    phase = get_or_create_phase(start, weeks=weeks, method=method, closes_at=closes_at)
    if phase is None:
        flash("Die Phase konnte nicht angelegt werden.", "error")
        return redirect(url_for("admin.rounds"))

    if request.form.get("announce"):
        queued = announce_phase(phase, current_app)
        flash(f"{queued} Einladungen wurden in die E-Mail-Queue gestellt.", "success")
    audit("admin.phase_create", f"{phase.name} ({weeks} Wochen)", current_user)
    db.session.commit()
    flash(f"Vergabephase {phase.name} angelegt.", "success")
    return redirect(url_for("admin.phase_detail", phase_id=phase.id))


@bp.route("/phasen/<int:phase_id>")
@admin_required
def phase_detail(phase_id: int):
    phase = db.session.get(BookingPhase, phase_id)
    if phase is None:
        abort(404)

    rows = []
    for round_obj in phase.rounds:
        participations = RoundParticipation.query.filter_by(round_id=round_obj.id).count()
        requests = BookingRequest.query.filter_by(round_id=round_obj.id).count()
        used = len(bookings_of_week(round_obj.week_start))
        rows.append(
            {
                "round": round_obj,
                "participations": participations,
                "requests": requests,
                "used": used,
                "capacity": capacity_per_slot() * len(workdays()) * len(windows()),
            }
        )

    return render_template(
        "admin/phase_detail.html",
        phase=phase,
        rows=rows,
        methods=available_methods(),
        method=METHODS.get(phase.method, METHODS[current_allocation_method()]),
        phase_weeks_default=phase_weeks(),
        reminder_days=phase_reminder_days(),
    )


@bp.route("/phasen/<int:phase_id>/einladen", methods=["POST"])
@admin_required
def phase_announce(phase_id: int):
    phase = db.session.get(BookingPhase, phase_id)
    if phase is None:
        abort(404)
    queued = announce_phase(phase, current_app)
    flash(
        f"{queued} Einladungen eingereiht. Der Versand erfolgt durch den Worker.",
        "success",
    )
    return redirect(url_for("admin.phase_detail", phase_id=phase.id))


@bp.route("/phasen/<int:phase_id>/erinnern", methods=["POST"])
@admin_required
def phase_remind(phase_id: int):
    phase = db.session.get(BookingPhase, phase_id)
    if phase is None:
        abort(404)
    queued = remind_phase_deadline(phase, current_app)
    flash(f"{queued} Erinnerungen eingereiht.", "success" if queued else "info")
    return redirect(url_for("admin.phase_detail", phase_id=phase.id))


@bp.route("/phasen/<int:phase_id>/verteilen", methods=["POST"])
@admin_required
def phase_allocate(phase_id: int):
    phase = db.session.get(BookingPhase, phase_id)
    if phase is None:
        abort(404)
    method = request.form.get("method")
    if method in METHODS:
        phase.method = method
        db.session.commit()
    if phase.status != PHASE_OPEN:
        flash("Diese Phase ist bereits abgeschlossen.", "warning")
        return redirect(url_for("admin.phase_detail", phase_id=phase.id))

    summary = allocate_phase(phase, actor=current_user, app=current_app)
    flash(
        f"Vergabe abgeschlossen: {summary['assigned']} Ladezeiten in "
        f"{len(summary['weeks'])} Wochen.",
        "success",
    )
    return redirect(url_for("admin.phase_detail", phase_id=phase.id))


@bp.route("/phasen/<int:phase_id>/schliessen", methods=["POST"])
@admin_required
def phase_close(phase_id: int):
    phase = db.session.get(BookingPhase, phase_id)
    if phase is None:
        abort(404)
    phase.status = PHASE_CLOSED
    for round_obj in phase.rounds:
        if round_obj.status == ROUND_OPEN:
            round_obj.status = ROUND_CLOSED
    audit("admin.phase_close", phase.name, current_user)
    db.session.commit()
    flash("Phase geschlossen.", "success")
    return redirect(url_for("admin.phase_detail", phase_id=phase.id))


# ---------------------------------------------------------------------------
# Interest in specific slots
# ---------------------------------------------------------------------------

@bp.route("/interesse")
@admin_required
def interests():
    status = request.args.get("status", "active")
    query = SlotInterest.query
    if status == "active":
        query = query.filter(SlotInterest.status.in_(("waiting", "notified")))
    entries = query.order_by(SlotInterest.week_start.desc(), SlotInterest.weekday).limit(300).all()

    grouped: dict = {}
    for entry in entries:
        key = (entry.week_start, entry.weekday, entry.window_index)
        grouped.setdefault(key, []).append(entry)

    return render_template(
        "admin/interests.html",
        grouped=grouped,
        status=status,
        counts={
            "active": SlotInterest.query.filter(
                SlotInterest.status.in_(("waiting", "notified"))
            ).count(),
            "all": SlotInterest.query.count(),
        },
    )


# ---------------------------------------------------------------------------
# Feedback / improvement suggestions
# ---------------------------------------------------------------------------

@bp.route("/feedback")
@admin_required
def feedback():
    suggestions = feedback_overview()
    return render_template(
        "admin/feedback.html",
        suggestions=suggestions,
        status_labels=POST_STATUS_LABELS,
        counts={
            key: sum(1 for post in suggestions if post.status == key)
            for key in ("open", "in_progress", "done", "declined")
        },
    )


@bp.route("/feedback/<int:post_id>", methods=["POST"])
@admin_required
def feedback_answer(post_id: int):
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    answer_feedback(
        post,
        request.form.get("reply", "").strip(),
        request.form.get("status", "open"),
        current_user,
    )
    flash("Rückmeldung gespeichert. Die Person wurde per E-Mail informiert.", "success")
    return redirect(url_for("admin.feedback"))



@bp.route("/runden/neu", methods=["POST"])
@admin_required
def create_round():
    week = _parse_week(request.form.get("week"))
    method = request.form.get("method") or current_allocation_method()
    round_obj = get_or_create_round(week, method=method)
    if round_obj is None:
        flash("Die Runde konnte nicht angelegt werden.", "error")
        return redirect(url_for("admin.rounds"))

    materialize_week(week)
    audit("admin.round_create", f"Runde für {week} angelegt", current_user)
    db.session.commit()
    flash(f"Vergaberunde für KW {week.isocalendar().week} ist offen.", "success")
    return redirect(url_for("admin.round_detail", round_id=round_obj.id))


@bp.route("/verfahren", methods=["POST"])
@admin_required
def set_default_method():
    """Set the global default allocation method (admin area shortcut)."""
    method = request.form.get("method", "")
    if method not in METHODS:
        flash("Unbekanntes Vergabeverfahren.", "error")
    else:
        Setting.set(KEY_ALLOCATION_METHOD, method)
        audit("admin.method_default", f"Standardverfahren: {method}", current_user)
        db.session.commit()
        flash(f"'{METHODS[method].label}' ist jetzt das Standardverfahren.", "success")
    return redirect(request.form.get("next") or url_for("admin.rounds"))


@bp.route("/runden/<int:round_id>")
@admin_required
def round_detail(round_id: int):
    round_obj = db.session.get(BookingRound, round_id)
    if round_obj is None:
        abort(404)

    participations = (
        RoundParticipation.query.filter_by(round_id=round_obj.id).all()
    )
    requests = (
        BookingRequest.query
        .filter_by(round_id=round_obj.id)
        .order_by(BookingRequest.user_id, BookingRequest.priority)
        .all()
    )
    grouped: dict[int, list[BookingRequest]] = {}
    for item in requests:
        grouped.setdefault(item.user_id, []).append(item)

    rows = []
    for participation in participations:
        user = participation.user
        rows.append(
            {
                "user": user,
                "participation": participation,
                "requests": grouped.get(user.id, []),
                "held": len(
                    [
                        booking for booking in (user.bookings or [])
                        if booking.start.date() >= round_obj.week_start
                        and booking.start.date() <= week_end(round_obj.week_start)
                    ]
                )
                if user
                else 0,
            }
        )
    rows.sort(key=lambda row: row["user"].last_name if row["user"] else "")

    problem = build_problem(round_obj) if round_obj.status == "open" else None
    result_bookings = (
        Booking.query
        .filter_by(allocation_round_id=round_obj.id)
        .order_by(Booking.start)
        .all()
    )
    return render_template(
        "admin/round_detail.html",
        round_obj=round_obj,
        rows=rows,
        methods=available_methods(),
        problem=problem,
        result_bookings=result_bookings,
        method=METHODS.get(round_obj.method, METHODS[current_allocation_method()]),
    )


@bp.route("/runden/<int:round_id>/methode", methods=["POST"])
@admin_required
def set_round_method(round_id: int):
    round_obj = db.session.get(BookingRound, round_id)
    if round_obj is None:
        abort(404)
    method = request.form.get("method", current_allocation_method())
    if method not in METHODS:
        flash("Unbekanntes Vergabeverfahren.", "error")
        return redirect(url_for("admin.round_detail", round_id=round_obj.id))
    round_obj.method = method
    audit("admin.round_method", f"Runde {round_obj.id}: Verfahren auf {method} gesetzt", current_user)
    db.session.commit()
    if request.form.get("also_default"):
        Setting.set(KEY_ALLOCATION_METHOD, method)
        db.session.commit()
        flash(f"'{METHODS[method].label}' ist jetzt das Standardverfahren.", "success")
    else:
        flash(f"Verfahren für diese Runde: {METHODS[method].label}.", "success")
    return redirect(url_for("admin.round_detail", round_id=round_obj.id))


@bp.route("/runden/<int:round_id>/verteilen", methods=["POST"])
@admin_required
def run_allocation(round_id: int):
    round_obj = db.session.get(BookingRound, round_id)
    if round_obj is None:
        abort(404)
    method = request.form.get("method")
    if method in METHODS:
        round_obj.method = method
        db.session.commit()

    if round_obj.status != "open":
        flash("Diese Runde ist bereits abgeschlossen.", "warning")
        return redirect(url_for("admin.round_detail", round_id=round_obj.id))

    stats = allocate_round(round_obj, actor=current_user, app=current_app)
    flash(
        f"Vergabe abgeschlossen: {stats['assigned']} Ladezeiten an "
        f"{stats['supplied']} Personen ({METHODS.get(method or round_obj.method).label}).",
        "success",
    )
    return redirect(url_for("admin.round_detail", round_id=round_obj.id))


@bp.route("/runden/<int:round_id>/vergleich")
@admin_required
def compare_round(round_id: int):
    """Run every method on the current round *without* saving the result."""
    from ..allocation import run as run_method

    round_obj = db.session.get(BookingRound, round_id)
    if round_obj is None:
        abort(404)

    materialize_week(round_obj.week_start)
    problem = build_problem(round_obj)
    seed = int(round_obj.week_start.toordinal())
    rows = []
    for spec in available_methods():
        if not problem.users or not problem.slots:
            break
        assignment = run_method(
            spec.key,
            problem,
            seed=seed,
            time_limit=current_app.config["SOLVER_TIME_LIMIT"],
            workers=current_app.config["SOLVER_WORKERS"],
        )
        supplied = 0
        assigned = 0
        top_hits = 0
        weighted_rank = 0.0
        for user in problem.users:
            keys = assignment.slots_for(user.index)
            if keys:
                supplied += 1
            assigned += len(keys)
            for key in keys:
                position = user.priorities.index(key) + 1
                weighted_rank += position
                if position == 1:
                    top_hits += 1
        rows.append(
            {
                "spec": spec,
                "supplied": supplied,
                "assigned": assigned,
                "unassigned": len(assignment.unassigned),
                "top_hits": top_hits,
                "mean_rank": round(weighted_rank / assigned, 2) if assigned else 0.0,
                "wall_time": round(assignment.info.get("wall_time", 0.0), 2),
                "util": round(assigned / max(1, problem.total_capacity) * 100, 1),
            }
        )
    return render_template(
        "admin/round_compare.html",
        round_obj=round_obj,
        rows=rows,
        problem=problem if (problem.users and problem.slots) else None,
    )


@bp.route("/runden/<int:round_id>/status", methods=["POST"])
@admin_required
def set_round_status(round_id: int):
    round_obj = db.session.get(BookingRound, round_id)
    if round_obj is None:
        abort(404)
    status = request.form.get("status")
    if status in ("open", "closed"):
        round_obj.status = status
        audit("admin.round_status", f"Runde {round_obj.id} -> {status}", current_user)
        db.session.commit()
        flash("Status aktualisiert.", "success")
    return redirect(url_for("admin.round_detail", round_id=round_obj.id))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@bp.route("/benutzer")
@admin_required
def users():
    query = User.query
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                User.email.ilike(like),
                User.first_name.ilike(like),
                User.last_name.ilike(like),
                User.department.ilike(like),
            )
        )
    role = request.args.get("role")
    if role in ("user", "admin"):
        query = query.filter(User.role == role)
    state = request.args.get("state")
    if state == "active":
        query = query.filter(User.is_active.is_(True))
    elif state == "inactive":
        query = query.filter(User.is_active.is_(False))

    entries = query.order_by(User.last_name, User.first_name).all()
    counts = {
        "all": User.query.count(),
        "active": User.query.filter_by(is_active=True).count(),
        "inactive": User.query.filter_by(is_active=False).count(),
    }
    return render_template(
        "admin/users.html",
        entries=entries,
        search=search,
        role=role or "",
        state=state or "",
        counts=counts,
    )


@bp.route("/benutzer/<int:user_id>", methods=["GET", "POST"])
@admin_required
def user_detail(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "toggle_active":
            user.is_active = not user.is_active
            audit("admin.user_toggle", f"{user.email} -> {'aktiv' if user.is_active else 'inaktiv'}",
                  current_user)
            flash("Status geändert.", "success")
        elif action == "verify":
            user.email_verified = True
            audit("admin.user_verify", f"{user.email} manuell bestätigt", current_user)
            flash("E-Mail-Adresse als bestätigt markiert.", "success")
        elif action == "make_admin":
            user.role = "admin"
            audit("admin.user_role", f"{user.email} ist jetzt Administrator", current_user)
            flash("Rolle geändert.", "success")
        elif action == "make_user":
            user.role = "user"
            audit("admin.user_role", f"{user.email} ist jetzt Mitarbeiter", current_user)
            flash("Rolle geändert.", "success")
        elif action == "anonymize":
            anonymize_user(user, keep_statistics=True)
            audit("admin.user_anonymize", f"Konto #{user.id} anonymisiert", current_user)
            flash("Konto anonymisiert und deaktiviert.", "info")
        elif action == "update":
            user.first_name = request.form.get("first_name", user.first_name)
            user.last_name = request.form.get("last_name", user.last_name)
            user.department = request.form.get("department") or None
            user.phone = request.form.get("phone") or None
            limit = request.form.get("max_weekly_slots", "")
            user.max_weekly_slots = int(limit) if limit.isdigit() and int(limit) > 0 else None
            audit("admin.user_update", f"Profil von {user.email} geändert", current_user)
            flash("Nutzerdaten gespeichert.", "success")
        db.session.commit()
        return redirect(url_for("admin.user_detail", user_id=user.id))

    bookings = (
        Booking.query
        .filter_by(user_id=user.id)
        .order_by(Booking.start.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "admin/user_detail.html",
        user=user,
        bookings=bookings,
        interests=(
            SlotInterest.query
            .filter_by(user_id=user.id)
            .order_by(SlotInterest.week_start.desc())
            .limit(10)
            .all()
        ),
    )


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

@bp.route("/infrastruktur", methods=["GET", "POST"])
@admin_required
def infrastructure():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_station":
            station = ChargingStation(
                name=request.form.get("name", "Neue Ladesäule"),
                location=request.form.get("location") or None,
            )
            db.session.add(station)
            db.session.flush()
            for index in range(int(request.form.get("points", "2") or 2)):
                db.session.add(
                    ChargingPoint(
                        station_id=station.id,
                        name=f"{station.id}{chr(65 + index)}",
                        power_kw=float(request.form.get("power_kw", "11") or 11),
                    )
                )
            audit("admin.station_add", f"Ladesäule {station.name} angelegt", current_user)
            flash("Ladesäule angelegt. Alle Ladepunkte stehen BEV und PHEV zur Verfügung.", "success")
        elif action == "toggle_point":
            point = db.session.get(ChargingPoint, int(request.form.get("point_id", "0")))
            if point:
                point.is_active = not point.is_active
                audit("admin.point_toggle",
                      f"{point.name} -> {'aktiv' if point.is_active else 'inaktiv'}", current_user)
                flash("Ladepunkt aktualisiert.", "success")
        elif action == "add_space":
            db.session.add(
                ParkingSpace(
                    name=request.form.get("name", "P"),
                    station_id=None,
                )
            )
            flash("Stellplatz angelegt.", "success")
        elif action == "toggle_space":
            space = db.session.get(ParkingSpace, int(request.form.get("space_id", "0")))
            if space:
                space.is_active = not space.is_active
                flash("Stellplatz aktualisiert.", "success")
        db.session.commit()
        return redirect(url_for("admin.infrastructure"))

    stations = ChargingStation.query.order_by(ChargingStation.id).all()
    spaces = ParkingSpace.query.order_by(ParkingSpace.name).all()
    return render_template(
        "admin/infrastructure.html",
        stations=stations,
        spaces=spaces,
        capacity=capacity_per_slot(),
        windows=[window_label(windows(), index) for index in range(len(windows()))],
        days=[DAY_NAMES[day] for day in workdays()],
        weekly_capacity=capacity_per_slot() * len(workdays()) * len(windows()),
    )


# ---------------------------------------------------------------------------
# Distribution statistics
# ---------------------------------------------------------------------------

@bp.route("/verteilung")
@admin_required
def distribution():
    weeks = int(request.args.get("wochen", "12") or 12)
    report = distribution_report(weeks=weeks)
    users = User.query.filter_by(role="user", is_active=True).all()
    rows = sorted(
        (
            {"user": user, "count": report["counts"].get(user.id, 0)}
            for user in users
        ),
        key=lambda row: (-row["count"], row["user"].last_name),
    )
    maximum = max((row["count"] for row in rows), default=1) or 1
    return render_template("admin/distribution.html", report=report, rows=rows,
                           maximum=maximum, weeks=weeks)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@bp.route("/einstellungen", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        section = request.form.get("section", "allocation")

        if section == "allocation":
            method = request.form.get("method")
            if method in METHODS:
                Setting.set(KEY_ALLOCATION_METHOD, method)
            Setting.set(KEY_MAX_SLOTS_PER_USER, max(1, int(request.form.get("max_slots", "2") or 2)))
            Setting.set(
                KEY_MAX_REGULAR_PER_WEEK, max(1, int(request.form.get("max_regular", "1") or 1))
            )
            Setting.set(
                KEY_WAITLIST_OFFER_MINUTES,
                max(5, int(request.form.get("offer_minutes", "30") or 30)),
            )
            Setting.set(
                KEY_DISTRIBUTION_WINDOW_WEEKS,
                max(1, int(request.form.get("distribution_weeks", "12") or 12)),
            )
            Setting.set("reminder.enabled", bool(request.form.get("reminders")))
            audit("admin.settings", "Vergabeeinstellungen geändert", current_user)
            flash("Einstellungen gespeichert.", "success")

        elif section == "grid":
            raw_windows = []
            for index in range(len(DEFAULT_WINDOWS)):
                raw = request.form.get(f"window_{index}", "")
                if "-" in raw:
                    start_raw, end_raw = raw.split("-", 1)
                    try:
                        start = _to_minutes(start_raw)
                        end = _to_minutes(end_raw)
                    except ValueError:
                        continue
                    if start < end:
                        raw_windows.append([start, end])
            if raw_windows:
                Setting.set(KEY_WINDOWS, raw_windows)
            days = [
                int(value) for value in request.form.getlist("workdays") if value.isdigit()
            ]
            if days:
                Setting.set(KEY_WORKDAYS, sorted(days))
            audit("admin.settings", "Zeitraster geändert", current_user)
            flash(
                "Zeitraster gespeichert. Das Raster gilt für alle Fahrzeuge – "
                "BEV und PHEV nutzen dieselben Ladezeitfenster.",
                "success",
            )

        db.session.commit()
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        methods=available_methods(),
        active_method=current_allocation_method(),
        windows=[window_label(windows(), index) for index in range(len(windows()))],
        raw_windows=windows(),
        workdays_list=workdays(),
        max_slots=Setting.get_int(KEY_MAX_SLOTS_PER_USER, 3),
        max_regular=Setting.get_int(KEY_MAX_REGULAR_PER_WEEK, 1),
        offer_minutes=Setting.get_int(KEY_WAITLIST_OFFER_MINUTES, 30),
        distribution_weeks=Setting.get_int(KEY_DISTRIBUTION_WINDOW_WEEKS, 12),
        reminders=Setting.get_bool("reminder.enabled", True),
        phase_weeks=phase_weeks(),
        phase_reminder_days=phase_reminder_days(),
        slot_reminder_minutes=slot_reminder_minutes(),
    )


def _to_minutes(raw: str) -> int:
    raw = raw.strip().replace(".", ":")
    if ":" in raw:
        hours, minutes = raw.split(":", 1)
        return int(hours) * 60 + int(minutes)
    return int(raw) * 60


# ---------------------------------------------------------------------------
# Registration codes
# ---------------------------------------------------------------------------

@bp.route("/codes", methods=["GET", "POST"])
@admin_required
def codes():
    if request.method == "POST":
        if request.form.get("action") == "create":
            token = RegistrationToken(
                token=RegistrationToken.generate_token(),
                label=request.form.get("label") or None,
                max_uses=max(1, int(request.form.get("max_uses", "1") or 1)),
                created_by_id=current_user.id,
            )
            db.session.add(token)
            audit("admin.token_create", f"Registrierungscode {token.token}", current_user)
            db.session.commit()
            flash(f"Registrierungscode {token.token} erzeugt.", "success")
        elif request.form.get("action") == "toggle":
            token = db.session.get(RegistrationToken, int(request.form.get("token_id", "0")))
            if token:
                token.is_active = not token.is_active
                db.session.commit()
                flash("Code aktualisiert.", "success")
        return redirect(url_for("admin.codes"))

    tokens = RegistrationToken.query.order_by(RegistrationToken.created_at.desc()).all()
    return render_template("admin/codes.html", tokens=tokens)


# ---------------------------------------------------------------------------
# Email queue
# ---------------------------------------------------------------------------

@bp.route("/email")
@admin_required
def email_queue():
    status = request.args.get("status", "")
    query = EmailMessage.query
    if status in ("pending", "sent", "failed"):
        query = query.filter(EmailMessage.status == status)
    entries = query.order_by(EmailMessage.id.desc()).limit(200).all()
    counts = {
        "pending": EmailMessage.query.filter_by(status="pending").count(),
        "sent": EmailMessage.query.filter_by(status="sent").count(),
        "failed": EmailMessage.query.filter_by(status="failed").count(),
    }
    return render_template(
        "admin/email.html",
        entries=entries,
        counts=counts,
        status=status,
        dry_run=current_app.config["MAIL_DRY_RUN"],
        smtp_host=current_app.config["SMTP_HOST"] or "nicht konfiguriert",
    )


@bp.route("/email/senden", methods=["POST"])
@admin_required
def email_dispatch():
    stats = process_email_queue(current_app, limit=200)
    flash(
        f"{stats['sent']} E-Mail(s) versendet, {stats['failed']} Fehler. "
        f"(Testmodus: {current_app.config['MAIL_DRY_RUN']})",
        "success" if not stats["failed"] else "warning",
    )
    return redirect(url_for("admin.email_queue"))


@bp.route("/email/<int:message_id>/erneut", methods=["POST"])
@admin_required
def email_retry(message_id: int):
    message = db.session.get(EmailMessage, message_id)
    if message is None:
        abort(404)
    message.status = "pending"
    message.attempts = 0
    message.next_attempt = datetime.now()
    message.last_error = None
    db.session.commit()
    flash("Nachricht erneut eingeordnet.", "success")
    return redirect(url_for("admin.email_queue"))


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@bp.route("/audit")
@admin_required
def audit_log():
    entries = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(300).all()
    return render_template("admin/audit.html", entries=entries)


# ---------------------------------------------------------------------------
# Demo helpers
# ---------------------------------------------------------------------------

@bp.route("/demo/historie", methods=["POST"])
@admin_required
def demo_history():
    from ..seed import seed_demo_history, seed_demo_round

    seed_demo_history(weeks=int(request.form.get("weeks", "8") or 8))
    seed_demo_round()
    flash("Demo-Historie und eine offene Vergaberunde wurden erzeugt.", "success")
    return redirect(url_for("admin.dashboard"))


_ = (
    Vehicle,
    week_dates,
    DEFAULT_WINDOWS,
    DEFAULT_WORKDAYS,
    waitlist_offer_minutes,
    dtime,
    timedelta,
    login_required,
    phase_for_week,
)
