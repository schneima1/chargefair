"""Business logic for bookings, allocation rounds, waitlist, swaps and reminders.

Flow of an allocation round
---------------------------
1. Recurring series are materialised into concrete bookings for the target week.
   They consume capacity first, because a regular slot belongs to its owner.
2. Employees file wishes (``BookingRequest``) with a priority per wish.
3. The admin closes the round and triggers the allocation. The selected method
   distributes the remaining capacity: every applicant gets their guaranteed
   slot first, extra wishes only afterwards.
4. Results are written back as bookings, the wishes are marked and everybody
   gets an email.

Nobody is scored, ranked or awarded points: a scarce slot is decided by the
rules of the chosen method and, at equal standing, by a seeded random draw.
"""
from __future__ import annotations

import random
import time
from datetime import date, datetime, time as dtime, timedelta

from . import notifications as notify
from .allocation import AllocationProblem, AllocationUser, run as run_method
from .extensions import db
from .models import (
    BOOKING_ACTIVE,
    BOOKING_COMPLETED,
    BOOKING_RELEASED,
    BOOKING_CANCELLED,
    INTEREST_CANCELLED,
    INTEREST_NOTIFIED,
    INTEREST_WAITING,
    PHASE_ALLOCATED,
    PHASE_OPEN,
    ROUND_OPEN,
    Booking,
    BookingPhase,
    BookingRequest,
    BookingRound,
    BookingSeries,
    ChargingPoint,
    Comment,
    EmailMessage,
    ParkingSpace,
    Post,
    RoundParticipation,
    Setting,
    SlotInterest,
    SwapOffer,
    TOPIC_FEEDBACK,
    User,
    WaitlistEntry,
    utcnow,
)
from .services import (
    audit,
    capacity_per_slot,
    current_allocation_method,
    guaranteed_slots_per_week,
    max_regular_slots_per_week,
    max_slots_per_user,
    phase_reminder_days,
    phase_weeks,
    waitlist_offer_minutes,
    windows,
    workdays,
)
from .slots import combine, recurrence_matches, week_dates, week_start, window_label

# Statuses that still block a charging point.
OCCUPYING = (BOOKING_ACTIVE, BOOKING_RELEASED, BOOKING_COMPLETED)


# ---------------------------------------------------------------------------
# Helpers around the weekly grid
# ---------------------------------------------------------------------------

def week_end(week_start: date) -> date:
    return week_start + timedelta(days=6)


def bookings_of_week(week_start: date) -> list[Booking]:
    start = datetime.combine(week_start, dtime.min)
    end = start + timedelta(days=7)
    return (
        Booking.query
        .filter(Booking.start >= start, Booking.start < end)
        .filter(Booking.status.in_(OCCUPYING))
        .order_by(Booking.start)
        .all()
    )


def occupancy(week_start: date) -> tuple[dict, dict]:
    """Return ``(counts, point_ids)`` occupied per ``(weekday, window)``."""
    counts: dict[tuple[int, int], int] = {}
    points: dict[tuple[int, int], set[int]] = {}
    for booking in bookings_of_week(week_start):
        key = (booking.weekday, booking.window_index)
        counts[key] = counts.get(key, 0) + 1
        points.setdefault(key, set()).add(booking.charging_point_id)
    return counts, points


def free_capacity(week_start: date) -> dict[tuple[int, int], int]:
    counts, _ = occupancy(week_start)
    capacity = capacity_per_slot()
    return {
        (day, index): max(0, capacity - counts.get((day, index), 0))
        for day in workdays()
        for index in range(len(windows()))
    }


def pick_free_point(week_start: date, weekday: int, window_index: int,
                    exclude: set[int] | None = None) -> ChargingPoint | None:
    _, used = occupancy(week_start)
    taken = set(used.get((weekday, window_index), set()))
    if exclude:
        taken |= exclude
    candidates = (
        ChargingPoint.query
        .filter(ChargingPoint.is_active.is_(True))
        .order_by(ChargingPoint.id)
        .all()
    )
    for point in candidates:
        if point.id not in taken:
            return point
    return None


def pick_parking_space(point: ChargingPoint, week_start: date, weekday: int,
                       window_index: int) -> ParkingSpace | None:
    """Prefer the parking space that belongs to the charging point."""
    key = (weekday, window_index)
    taken = {
        booking.parking_space_id
        for booking in bookings_of_week(week_start)
        if (booking.weekday, booking.window_index) == key and booking.parking_space_id
    }
    own = [space for space in point.parking_spaces if space.id not in taken and space.is_active]
    if own:
        return own[0]
    spare = (
        ParkingSpace.query
        .filter(ParkingSpace.is_active.is_(True))
        .order_by(ParkingSpace.id)
        .all()
    )
    for space in spare:
        if space.id not in taken:
            return space
    return None


def user_bookings_in_week(user: User, week_start: date) -> list[Booking]:
    start = datetime.combine(week_start, dtime.min)
    end = start + timedelta(days=7)
    return (
        Booking.query
        .filter(Booking.user_id == user.id)
        .filter(Booking.start >= start, Booking.start < end)
        .filter(Booking.status.in_((BOOKING_ACTIVE, BOOKING_RELEASED)))
        .order_by(Booking.start)
        .all()
    )


def has_booking_at(user: User, week_start: date, weekday: int, window_index: int) -> bool:
    return any(
        booking.weekday == weekday and booking.window_index == window_index
        for booking in user_bookings_in_week(user, week_start)
    )


# ---------------------------------------------------------------------------
# Creating bookings
# ---------------------------------------------------------------------------

def create_booking(user: User, week_start: date, weekday: int, window_index: int,
                   origin: str = "self", series: BookingSeries | None = None,
                   round_obj: BookingRound | None = None,
                   commit: bool = True) -> Booking | None:
    """Create one booking if a charging point is still available."""
    point = pick_free_point(week_start, weekday, window_index)
    if point is None:
        return None
    day = week_start + timedelta(days=weekday)
    start_minute, end_minute = windows()[window_index]
    space = pick_parking_space(point, week_start, weekday, window_index)
    booking = Booking(
        user_id=user.id,
        booking_series_id=getattr(series, "id", None),
        charging_point_id=point.id,
        parking_space_id=getattr(space, "id", None),
        start=combine(day, start_minute),
        end=combine(day, end_minute),
        weekday=weekday,
        window_index=window_index,
        status=BOOKING_ACTIVE,
        origin=origin,
        allocation_round_id=getattr(round_obj, "id", None),
    )
    db.session.add(booking)
    if commit:
        db.session.flush()
    return booking


def materialize_week(week_start: date) -> list[Booking]:
    """Create bookings for all recurring series that apply to ``week_start``."""
    created: list[Booking] = []
    series_list = BookingSeries.query.filter(BookingSeries.is_active.is_(True)).all()
    for series in series_list:
        if series.valid_until < week_start or series.valid_from > week_end(week_start):
            continue
        for day in week_dates(week_start, workdays()):
            if day < series.valid_from or day > series.valid_until:
                continue
            if series.weekday != day.weekday():
                continue
            if not recurrence_matches(series.recurrence, day):
                continue
            existing = Booking.query.filter_by(
                booking_series_id=series.id, start=combine(day, windows()[series.window_index][0])
            ).first()
            if existing:
                continue
            booking = create_booking(
                series.user, week_start, series.weekday, series.window_index,
                origin="self", series=series, commit=False,
            )
            if booking is not None:
                booking.charging_point_id = series.charging_point_id or booking.charging_point_id
                booking.parking_space_id = series.parking_space_id or booking.parking_space_id
                db.session.flush()
                created.append(booking)
    if created:
        db.session.commit()
    return created


# ---------------------------------------------------------------------------
# Allocation rounds
# ---------------------------------------------------------------------------

def get_or_create_round(week_start: date, method: str | None = None,
                        create: bool = True) -> BookingRound | None:
    round_obj = BookingRound.query.filter_by(week_start=week_start).first()
    if round_obj or not create:
        return round_obj
    round_obj = BookingRound(
        week_start=week_start,
        method=method or current_allocation_method(),
        status="open",
        opens_at=utcnow(),
    )
    db.session.add(round_obj)
    db.session.commit()
    return round_obj


def default_round_week(today: date | None = None) -> date:
    """The week the employees should currently be planning for (next week)."""
    today = today or date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=7)


# ---------------------------------------------------------------------------
# Allocation phases (several weeks at once, default: four)
# ---------------------------------------------------------------------------

def _phase_name(start_week: date, weeks: int) -> str:
    end_week = start_week + timedelta(days=7 * (weeks - 1))
    return (
        f"KW {start_week.isocalendar().week}–{end_week.isocalendar().week} "
        f"({start_week.strftime('%d.%m.')}–"
        f"{(end_week + timedelta(days=6)).strftime('%d.%m.%Y')})"
    )


def get_or_create_phase(start_week: date | None = None, weeks: int | None = None,
                        method: str | None = None, create: bool = True,
                        closes_at: datetime | None = None) -> BookingPhase | None:
    """Return the phase starting in ``start_week``, creating it if necessary.

    Creating a phase also creates one :class:`BookingRound` per week inside it,
    so employees file wishes for all four weeks at once and the administrator
    only has to start the allocation once.
    """
    start = week_start(start_week or default_round_week())
    phase = BookingPhase.query.filter_by(start_week=start).first()
    if phase or not create:
        return phase

    weeks = weeks or phase_weeks()
    method_key = method or current_allocation_method()
    phase = BookingPhase(
        name=_phase_name(start, weeks),
        start_week=start,
        weeks=weeks,
        method=method_key,
        status=PHASE_OPEN,
        opens_at=utcnow(),
        closes_at=closes_at or datetime.combine(start - timedelta(days=1), dtime(18, 0)),
    )
    db.session.add(phase)
    db.session.flush()

    for offset in range(weeks):
        monday = start + timedelta(weeks=offset)
        round_obj = BookingRound.query.filter_by(week_start=monday).first()
        if round_obj is None:
            db.session.add(
                BookingRound(
                    week_start=monday,
                    phase_id=phase.id,
                    method=method_key,
                    status=ROUND_OPEN,
                    opens_at=phase.opens_at,
                    closes_at=phase.closes_at,
                )
            )
        else:
            round_obj.phase_id = phase.id
            if not round_obj.method:
                round_obj.method = method_key
    db.session.commit()
    return phase


def current_phase(today: date | None = None) -> BookingPhase | None:
    """The phase employees should currently be filing wishes for."""
    today = today or date.today()
    monday = week_start(today)
    phase = (
        BookingPhase.query
        .filter(BookingPhase.status == PHASE_OPEN)
        .filter(BookingPhase.start_week >= monday)
        .order_by(BookingPhase.start_week)
        .first()
    )
    if phase:
        return phase
    return (
        BookingPhase.query
        .filter(BookingPhase.start_week >= monday)
        .order_by(BookingPhase.start_week)
        .first()
    )


def phase_for_week(week: date) -> BookingPhase | None:
    """The phase a given week belongs to, if any."""
    monday = week_start(week)
    for phase in BookingPhase.query.all():
        if phase.start_week <= monday <= phase.end_week:
            return phase
    return None


def announce_phase(phase: BookingPhase, app=None) -> int:
    """Tell every active employee that wishes can now be filed.

    Returns the number of queued emails. The ``notified_at`` marker keeps a
    second call from spamming everybody.
    """
    from flask import current_app

    app = app or current_app
    base_url = (app.config.get("BASE_URL") or "").rstrip("/")
    url = f"{base_url}/plan?woche={phase.start_week.isoformat()}" if base_url else "/plan"

    queued = 0
    for user in User.query.filter_by(role="user", is_active=True).all():
        if not user.phase_mails_enabled:
            continue
        notify.send_phase_opened(
            user,
            phase.start_week,
            phase.weeks,
            phase.end_week,
            phase.closes_at,
            url,
            guarantee=guaranteed_slots_per_week(),
            maximum=min(user.max_weekly_slots or max_slots_per_user(), max_slots_per_user()),
        )
        queued += 1

    phase.notified_at = utcnow()
    audit("phase.announce", f"Phase {phase.name}: {queued} Einladungen eingereiht")
    db.session.commit()
    return queued


def remind_phase_deadline(phase: BookingPhase | None = None, app=None) -> int:
    """Send the "wishes close soon" reminder for phases near their deadline.

    Also reminds everybody who has not filed a wish yet. A mail is only queued
    once per phase and person.
    """
    from flask import current_app

    app = app or current_app
    days = phase_reminder_days()
    if days <= 0:
        return 0

    candidates = [phase] if phase else BookingPhase.query.filter_by(status=PHASE_OPEN).all()
    base_url = (app.config.get("BASE_URL") or "").rstrip("/")
    now = utcnow()
    queued = 0

    for item in candidates:
        if not item.is_open or item.closes_at is None:
            continue
        remaining = item.closes_at - now
        if remaining.total_seconds() <= 0 or remaining.days >= days:
            continue

        url = f"{base_url}/plan?woche={item.start_week.isoformat()}" if base_url else "/plan"
        marker = f"KW {item.start_week.isocalendar().week}"
        for user in User.query.filter_by(role="user", is_active=True).all():
            if not user.phase_mails_enabled:
                continue
            already = (
                EmailMessage.query
                .filter_by(kind="phase_reminder", user_id=user.id)
                .filter(EmailMessage.subject.like(f"%{marker}%"))
                .count()
            )
            if already:
                continue
            filed = (
                BookingRequest.query
                .join(BookingRound, BookingRequest.round_id == BookingRound.id)
                .filter(BookingRound.phase_id == item.id, BookingRequest.user_id == user.id)
                .count()
            )
            notify.send_phase_reminder(
                user, item.start_week, item.closes_at, url, has_wishes=bool(filed)
            )
            queued += 1

    if queued:
        db.session.commit()
    return queued


def allocate_phase(phase: BookingPhase, actor: User | None = None, app=None) -> dict:
    """Run the allocation for every week of the phase.

    Each week is allocated on its own: capacity and wishes differ per week, so
    one global run would be wrong. The guarantee therefore applies per week,
    exactly as documented to the employees.
    """
    from flask import current_app

    app = app or current_app
    summary: dict = {"weeks": [], "assigned": 0, "supplied": 0}
    for round_obj in phase.rounds:
        if round_obj.status != ROUND_OPEN:
            continue
        round_obj.method = phase.method
        db.session.commit()
        stats = allocate_round(round_obj, actor=actor, app=app)
        summary["weeks"].append(
            {
                "week": round_obj.week_start.isoformat(),
                "assigned": stats.get("assigned", 0),
                "supplied": stats.get("supplied", 0),
                "guarantee_fulfilled": stats.get("guarantee_fulfilled"),
            }
        )
        summary["assigned"] += stats.get("assigned", 0)
        summary["supplied"] += stats.get("supplied", 0)

    phase.status = PHASE_ALLOCATED
    phase.allocated_at = utcnow()
    phase.stats = summary
    audit(
        "phase.allocate",
        f"{phase.name}: {summary['assigned']} Slots in {len(summary['weeks'])} Wochen",
        actor,
    )
    db.session.commit()
    return summary


# ---------------------------------------------------------------------------
# Interest in a specific slot
# ---------------------------------------------------------------------------

def register_interest(user: User, week_start_date: date, weekday: int,
                      window_index: int, note: str = "") -> SlotInterest:
    """Remember that ``user`` would like to have this particular slot."""
    monday = week_start(week_start_date)
    existing = SlotInterest.query.filter_by(
        user_id=user.id, week_start=monday, weekday=weekday, window_index=window_index
    ).first()
    if existing:
        existing.status = INTEREST_WAITING
        existing.note = note or existing.note
        db.session.commit()
        return existing

    interest = SlotInterest(
        user_id=user.id,
        week_start=monday,
        weekday=weekday,
        window_index=window_index,
        status=INTEREST_WAITING,
        note=note[:255] if note else None,
    )
    db.session.add(interest)
    db.session.commit()
    return interest


def withdraw_interest(interest: SlotInterest, user: User) -> bool:
    if interest.user_id != user.id:
        return False
    interest.status = INTEREST_CANCELLED
    db.session.commit()
    return True


def interests_for_slot(week_start_date: date, weekday: int, window_index: int,
                       active_only: bool = True) -> list[SlotInterest]:
    query = SlotInterest.query.filter_by(
        week_start=week_start(week_start_date), weekday=weekday, window_index=window_index
    )
    if active_only:
        query = query.filter(SlotInterest.status.in_((INTEREST_WAITING, INTEREST_NOTIFIED)))
    return query.order_by(SlotInterest.created_at).all()


def active_interests(user: User) -> list[SlotInterest]:
    return (
        SlotInterest.query
        .filter_by(user_id=user.id)
        .filter(SlotInterest.status.in_((INTEREST_WAITING, INTEREST_NOTIFIED)))
        .order_by(SlotInterest.week_start, SlotInterest.weekday)
        .all()
    )


def interest_map(week: date, user: User) -> dict:
    """``{(weekday, window_index): SlotInterest}`` for one week and user."""
    monday = week_start(week)
    rows = SlotInterest.query.filter_by(user_id=user.id, week_start=monday).all()
    return {(row.weekday, row.window_index): row for row in rows}


def interested_users_for_slot(week: date, weekday: int, window_index: int) -> list[User]:
    """Other people waiting for exactly this slot (for the overview page)."""
    return [interest.user for interest in interests_for_slot(week, weekday, window_index)]


def notify_interested_users(booking: Booking, app=None) -> int:
    """Inform everybody who registered interest that this slot became free.

    Called whenever a booking is released. The mail carries a signed
    one-click link, so taking over a slot needs neither login nor navigation.
    """
    from flask import current_app
    from .tokens import takeover_token

    app = app or current_app
    base_url = (app.config.get("BASE_URL") or "").rstrip("/")
    queued = 0
    for interest in interests_for_slot(
        booking.start.date(), booking.weekday, booking.window_index
    ):
        if interest.user_id == booking.user_id:
            continue
        token = takeover_token(app.config["SECRET_KEY"], booking.id, interest.user_id)
        url = f"{base_url}/slot/{token}" if base_url else f"/slot/{token}"
        notify.send_interest_available(interest.user, booking, url)
        interest.status = INTEREST_NOTIFIED
        interest.notified_at = utcnow()
        queued += 1
    if queued:
        db.session.commit()
    return queued



def build_problem(round_obj: BookingRound) -> AllocationProblem:
    """Translate the database state of a round into an allocation problem.

    Two different limits are in play:

    * **guarantee** (``max_regular_slots_per_week``, default 1) - every
      applicant is entitled to this many slots.
    * **maximum** (``max_slots_per_user`` or the personal setting) - the upper
      bound including additional slots from spare capacity.

    The allocation only hands out extras once everybody has their guaranteed
    slot, so a "3 times per week" wish never takes a slot away from somebody
    who has none.
    """
    week = round_obj.week_start
    capacity = free_capacity(week)
    guaranteed = guaranteed_slots_per_week()
    maximum = max_slots_per_user()

    users: list[AllocationUser] = []
    participations = RoundParticipation.query.filter_by(round_id=round_obj.id).all()
    for participation in participations:
        user = participation.user
        if user is None or not user.is_active:
            continue
        held = len(user_bookings_in_week(user, week))
        personal_max = min(user.max_weekly_slots or maximum, maximum)
        allowance = max(0, personal_max - held)
        if allowance <= 0:
            continue

        requests = (
            BookingRequest.query
            .filter_by(round_id=round_obj.id, user_id=user.id)
            .order_by(BookingRequest.priority)
            .all()
        )
        seen: set[tuple[int, int]] = set()
        priorities: list[tuple[int, int]] = []
        for request in requests:
            key = (request.weekday, request.window_index)
            if key in seen:
                continue
            if capacity.get(key, 0) <= 0:
                continue
            seen.add(key)
            priorities.append(key)
        if not priorities:
            continue

        users.append(
            AllocationUser(
                index=user.id,
                name=user.full_name,
                priorities=priorities,
                desired_count=min(participation.desired_count or 1, allowance),
                request_time=participation.created_at.timestamp() if participation.created_at else 0.0,
            )
        )

    slots = {key: value for key, value in capacity.items() if value > 0}
    return AllocationProblem(
        slots=slots,
        users=users,
        max_slots_per_user=maximum,
        guaranteed_per_user=guaranteed,
    )


def allocate_round(round_obj: BookingRound, actor: User | None = None,
                   seed: int | None = None, app=None) -> dict:
    """Run the configured method and persist the outcome."""
    from flask import current_app

    app = app or current_app
    week = round_obj.week_start
    method_key = round_obj.method or current_allocation_method()

    materialize_week(week)
    problem = build_problem(round_obj)

    started = time.perf_counter()
    if problem.users and problem.slots:
        assignment = run_method(
            method_key,
            problem,
            seed=seed if seed is not None else int(week.toordinal()),
            time_limit=app.config["SOLVER_TIME_LIMIT"],
            workers=app.config["SOLVER_WORKERS"],
        )
    else:
        from .allocation import Assignment

        assignment = Assignment(name=method_key, info={"status": "no applicants"})
    elapsed = time.perf_counter() - started

    # Reset previously granted requests of this round (re-run support).
    for request in BookingRequest.query.filter_by(round_id=round_obj.id).all():
        request.status = "pending"
        request.booking_id = None
    for booking in Booking.query.filter_by(allocation_round_id=round_obj.id).all():
        db.session.delete(booking)
    db.session.flush()

    granted = 0
    rejected_labels: dict[int, list[str]] = {}
    granted_bookings: dict[int, list[Booking]] = {}

    for user in problem.users:
        keys = assignment.slots_for(user.index)
        if not keys:
            continue
        employee = db.session.get(User, user.index)
        if employee is None:
            continue
        granted_bookings[user.index] = []
        for weekday, window_index in keys:
            booking = create_booking(
                employee, week, weekday, window_index,
                origin="allocation", round_obj=round_obj, commit=False,
            )
            if booking is None:
                continue
            db.session.flush()
            granted_bookings[user.index].append(booking)
            granted += 1
            request = BookingRequest.query.filter_by(
                round_id=round_obj.id, user_id=employee.id,
                weekday=weekday, window_index=window_index,
            ).first()
            if request:
                request.status = "granted"
                request.booking_id = booking.id
        if granted_bookings[user.index]:
            notify.send_booking_confirmed(employee, granted_bookings[user.index][0])

    # Collect the wishes that were not fulfilled.
    for request in BookingRequest.query.filter_by(round_id=round_obj.id).all():
        if request.status == "granted":
            continue
        request.status = "rejected"
        rejected_labels.setdefault(request.user_id, []).append(
            f"Priorität {request.priority}: "
            f"{window_label(windows(), request.window_index)} "
            f"am {['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag'][request.weekday]}"
        )

    total_capacity = capacity_per_slot() * len(workdays()) * len(windows())
    used = len(bookings_of_week(week))
    all_capacity = total_capacity
    solver_info = assignment.info or {}
    granted_guarantee = 0
    requested_more = 0
    got_extra = 0
    for participant in problem.users:
        count = len(granted_bookings.get(participant.index, []))
        guarantee = max(1, problem.guarantee_for(participant))
        if count >= guarantee:
            granted_guarantee += 1
        if problem.limit_for(participant) > guarantee:
            requested_more += 1
            if count > guarantee:
                got_extra += 1

    stats = {
        "method": method_key,
        "method_label": assignment.info.get("method_label"),
        "supplied": len([u for u in problem.users if assignment.slots_for(u.index)]),
        "assigned": granted,
        "applicants": len(problem.users),
        "unassigned": len([u for u in problem.users if not assignment.slots_for(u.index)]),
        "capacity": all_capacity,
        "free_capacity": problem.total_capacity,
        "used": used,
        "utilisation": round(used / all_capacity, 4) if all_capacity else 0.0,
        "wall_time": round(elapsed, 2),
        # Guarantee vs. extras -- the numbers the admin dashboard highlights.
        "guarantee": problem.guaranteed_per_user,
        "guarantee_fulfilled": granted_guarantee == len(problem.users),
        "guarantee_met": granted_guarantee,
        "extras_requested_by": requested_more,
        "extras_granted_to": got_extra,
        "extras_granted": max(0, granted - granted_guarantee),
        "wish_shortfall": max(0, problem.requested - granted),
        "solver": solver_info,
    }

    round_obj.status = "allocated"
    round_obj.allocated_at = utcnow()
    round_obj.stats = stats
    db.session.flush()

    # Tell everybody what they got.
    base_url = app.config.get("BASE_URL") or ""
    overview = f"{base_url}/meine-buchungen" if base_url else "/meine-buchungen"
    guarantee_setting = problem.guaranteed_per_user or guaranteed_slots_per_week()
    for participation in round_obj.participations:
        notify.send_round_result(
            participation.user,
            week,
            granted_bookings.get(participation.user_id, []),
            rejected_labels.get(participation.user_id, []),
            overview,
            desired=participation.desired_count,
            guarantee=guarantee_setting,
        )

    for admin in User.query.filter_by(role="admin", is_active=True).all():
        notify.send_admin_digest(admin, round_obj, stats, overview)

    audit(
        "allocation.run",
        f"KW {week.isocalendar().week}: {stats['assigned']} Slots an "
        f"{stats['supplied']} Nutzer ({method_key})",
        actor,
    )
    db.session.commit()
    return stats


# ---------------------------------------------------------------------------
# Waitlist
# ---------------------------------------------------------------------------

def join_waitlist(user: User, week_start: date, weekday: int, window_index: int,
                  note: str = "") -> WaitlistEntry:
    entry = WaitlistEntry.query.filter_by(
        user_id=user.id, week_start=week_start, weekday=weekday, window_index=window_index
    ).first()
    if entry:
        entry.status = "waiting"
        entry.note = note or entry.note
        db.session.commit()
        return entry
    highest = (
        db.session.query(db.func.max(WaitlistEntry.position))
        .filter_by(week_start=week_start, weekday=weekday, window_index=window_index)
        .scalar()
    )
    entry = WaitlistEntry(
        user_id=user.id,
        week_start=week_start,
        weekday=weekday,
        window_index=window_index,
        position=(highest or 0) + 1,
        status="waiting",
        note=note or None,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def waitlist_for_slot(week_start: date, weekday: int, window_index: int,
                      active_only: bool = True) -> list[WaitlistEntry]:
    query = WaitlistEntry.query.filter_by(
        week_start=week_start, weekday=weekday, window_index=window_index
    )
    if active_only:
        query = query.filter(WaitlistEntry.status.in_(("waiting", "offered")))
    return query.order_by(WaitlistEntry.position).all()


def offer_next_from_waitlist(booking: Booking, app=None) -> WaitlistEntry | None:
    """Offer a released booking to the first waiting person."""
    from flask import current_app

    app = app or current_app
    entries = waitlist_for_slot(
        _monday(booking.start), booking.weekday, booking.window_index, active_only=False
    )
    minutes = waitlist_offer_minutes()
    for entry in entries:
        if entry.status not in ("waiting",):
            continue
        if entry.user_id == booking.user_id:
            continue
        entry.status = "offered"
        entry.offered_booking_id = booking.id
        entry.offer_expires_at = utcnow() + timedelta(minutes=minutes)
        base_url = app.config.get("BASE_URL") or ""
        notify.send_offer_to_waitlist(
            entry.user,
            entry,
            booking,
            minutes,
            f"{base_url}/warteliste" if base_url else "/warteliste",
        )
        db.session.commit()
        return entry
    return None


def _monday(moment: datetime) -> date:
    return moment.date() - timedelta(days=moment.weekday())


def expire_waitlist_offers(app=None) -> int:
    """Hand offers that were not accepted in time to the next person."""
    from flask import current_app

    app = app or current_app
    expired = (
        WaitlistEntry.query
        .filter(WaitlistEntry.status == "offered")
        .filter(WaitlistEntry.offer_expires_at < utcnow())
        .all()
    )
    for entry in expired:
        entry.status = "expired"
        booking = entry.offered_booking
        db.session.flush()
        if booking and booking.status == BOOKING_RELEASED:
            notify.send_offer_expired(entry.user, booking)
            offer_next_from_waitlist(booking, app)
    if expired:
        db.session.commit()
    return len(expired)


def accept_waitlist_offer(entry: WaitlistEntry, user: User) -> Booking | None:
    booking = entry.offered_booking
    if booking is None or entry.status != "offered" or entry.is_offer_expired:
        return None
    booking.previous_user_id = booking.user_id
    booking.user_id = user.id
    booking.status = BOOKING_ACTIVE
    booking.origin = "takeover"
    entry.status = "accepted"
    db.session.commit()
    notify.send_waitlist_confirmed(user, booking)
    if booking.previous_user_id:
        previous = db.session.get(User, booking.previous_user_id)
        if previous:
            notify.send_takeover_announcement(previous, booking)
    db.session.commit()
    return booking


# ---------------------------------------------------------------------------
# Release / cancel / takeover
# ---------------------------------------------------------------------------

def release_booking(booking: Booking, user: User, note: str = "") -> bool:
    """Give a slot back so somebody else can use it.

    Two groups are informed: people who registered interest in exactly this
    slot (they get a one-click link) and the waitlist of the time window.
    """
    if booking.status != BOOKING_ACTIVE:
        return False
    booking.status = BOOKING_RELEASED
    booking.released_at = utcnow()
    booking.release_note = note[:255] if note else None
    db.session.commit()

    interested = notify_interested_users(booking)
    entry = offer_next_from_waitlist(booking)
    notify.send_release_confirmation(
        user, booking, waitlisted=entry is not None, interested=interested
    )
    audit(
        "booking.release",
        f"Buchung #{booking.id} freigegeben ({interested} Interessierte informiert)",
        user,
    )
    db.session.commit()
    return True


def cancel_booking(booking: Booking, user: User, free_for_others: bool = True) -> bool:
    if booking.status not in (BOOKING_ACTIVE, BOOKING_RELEASED):
        return False
    was_active = booking.status == BOOKING_ACTIVE
    booking.status = BOOKING_CANCELLED
    db.session.commit()
    if was_active and free_for_others:
        offer_next_from_waitlist(booking)
    notify.send_booking_cancelled(user, booking)
    audit("booking.cancel", f"Buchung #{booking.id} storniert", user)
    db.session.commit()
    return True


def take_over_booking(booking: Booking, user: User) -> bool:
    """Take over a released slot directly."""
    if booking.status != BOOKING_RELEASED or booking.user_id == user.id:
        return False
    if has_booking_at(user, _monday(booking.start), booking.weekday, booking.window_index):
        return False
    previous_id = booking.user_id
    booking.previous_user_id = previous_id
    booking.user_id = user.id
    booking.status = BOOKING_ACTIVE
    booking.origin = "takeover"
    for entry in WaitlistEntry.query.filter_by(
        offered_booking_id=booking.id, status="offered"
    ).all():
        entry.status = "accepted"
    db.session.commit()
    notify.send_waitlist_confirmed(user, booking)
    previous = db.session.get(User, previous_id)
    if previous:
        notify.send_takeover_announcement(previous, booking)
    audit("booking.takeover", f"Buchung #{booking.id} übernommen", user)
    db.session.commit()
    return True


def check_in(booking: Booking) -> None:
    booking.checked_in_at = utcnow()
    db.session.commit()


# ---------------------------------------------------------------------------
# Swaps
# ---------------------------------------------------------------------------

def create_swap_offer(booking: Booking, user: User, wanted_weekday: int | None,
                      wanted_window_index: int | None, message: str = "",
                      app=None) -> SwapOffer:
    offer = SwapOffer(
        booking_id=booking.id,
        user_id=user.id,
        wanted_weekday=wanted_weekday,
        wanted_window_index=wanted_window_index,
        message=message[:400] if message else None,
    )
    db.session.add(offer)
    booking.status = BOOKING_RELEASED
    booking.released_at = utcnow()
    booking.release_note = "Tauschangebot"
    db.session.commit()

    wanted = "beliebiger Termin"
    if wanted_weekday is not None and wanted_window_index is not None:
        wanted = f"{['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag'][wanted_weekday]} {window_label(windows(), wanted_window_index)}"

    # Notify candidates that hold a matching slot in the same week.
    week = _monday(booking.start)
    candidates = [
        item for item in bookings_of_week(week)
        if item.user_id != user.id
        and (wanted_weekday is None or item.weekday == wanted_weekday)
        and (wanted_window_index is None or item.window_index == wanted_window_index)
    ]
    base_url = (app.config.get("BASE_URL") if app else "") or ""
    url = f"{base_url}/tausch" if base_url else "/tausch"
    for candidate in candidates:
        notify.send_swap_offer(user, candidate.user, booking, wanted, message, url)
    db.session.commit()
    audit("swap.create", f"Tauschangebot für Buchung #{booking.id}", user)
    return offer


def accept_swap_offer(offer: SwapOffer, user: User, own_booking: Booking) -> bool:
    if offer.status != "open" or offer.user_id == user.id:
        return False
    week = _monday(offer.booking.start)
    if own_booking.user_id != user.id or _monday(own_booking.start) != week:
        return False

    offer_booking = offer.booking
    offer_booking.user_id, own_booking.user_id = user.id, offer.user_id
    offer_booking.status = BOOKING_ACTIVE
    own_booking.status = BOOKING_ACTIVE
    offer_booking.origin = "swap"
    own_booking.origin = "swap"
    offer.status = "accepted"
    offer.accepted_by_id = user.id
    offer.accepted_booking_id = own_booking.id
    db.session.commit()

    notify.send_swap_accepted(offer.user, user, own_booking)
    notify.send_swap_accepted(user, offer.user, offer_booking)
    audit("swap.accept", f"Tausch von Buchung #{offer_booking.id} und #{own_booking.id}", user)
    db.session.commit()
    return True


def cancel_swap_offer(offer: SwapOffer, user: User) -> bool:
    if offer.status != "open" or offer.user_id != user.id:
        return False
    offer.status = "cancelled"
    if offer.booking:
        offer.booking.status = BOOKING_ACTIVE
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Reminders and housekeeping
# ---------------------------------------------------------------------------

def _reminder_already_sent(booking: Booking, kind: str) -> bool:
    from .models import EmailMessage

    return (
        EmailMessage.query
        .filter_by(booking_id=booking.id, kind=kind)
        .count() > 0
    )


def send_due_reminders(window_minutes: int = 45, app=None) -> int:
    """Queue start/end reminders for bookings that begin or end soon.

    The start reminder carries a signed one-click release link.
    """
    from flask import current_app
    from .tokens import release_token

    app = app or current_app
    base_url = (app.config.get("BASE_URL") or "").rstrip("/")
    now = utcnow()
    horizon = now + timedelta(minutes=window_minutes)
    sent = 0
    upcoming = (
        Booking.query
        .filter(Booking.status.in_((BOOKING_ACTIVE, BOOKING_RELEASED)))
        .filter(Booking.start > now, Booking.start <= horizon)
        .all()
    )
    for booking in upcoming:
        user = booking.user
        if not user or not user.reminders_enabled:
            continue
        if _reminder_already_sent(booking, "reminder_start"):
            continue
        token = release_token(app.config["SECRET_KEY"], booking.id, user.id)
        url = f"{base_url}/freigeben/{token}" if base_url else f"/freigeben/{token}"
        notify.send_reminder_start(user, booking, release_url=url)
        sent += 1

    ending = (
        Booking.query
        .filter(Booking.status.in_((BOOKING_ACTIVE, BOOKING_RELEASED)))
        .filter(Booking.end > now, Booking.end <= now + timedelta(minutes=20))
        .all()
    )
    for booking in ending:
        user = booking.user
        if not user or not user.reminders_enabled:
            continue
        if _reminder_already_sent(booking, "reminder_end"):
            continue
        notify.send_reminder_end(user, booking)
        sent += 1
    if sent:
        db.session.commit()
    return sent


# ---------------------------------------------------------------------------
# Feedback / board
# ---------------------------------------------------------------------------

def wants_admin_notice(topic: str) -> bool:
    """Whether the administration should be notified about a new post."""
    return topic == TOPIC_FEEDBACK


def notify_admins_about_feedback(post: Post, app=None) -> int:
    """Send a short notice to every administrator about a new suggestion."""
    from flask import current_app

    app = app or current_app
    base_url = (app.config.get("BASE_URL") or "").rstrip("/")
    url = f"{base_url}/feedback" if base_url else "/feedback"
    queued = 0
    for admin in User.query.filter_by(role="admin", is_active=True).all():
        notify.send_feedback_notice(admin, post, url)
        queued += 1
    if queued:
        db.session.commit()
    return queued


def answer_feedback(post: Post, reply_text: str, status: str, actor: User | None = None) -> bool:
    """Record the administration's answer to an improvement suggestion.

    The answer is stored on the post *and* added as a comment, so the thread
    stays readable for everybody who follows the suggestion.
    """
    if not post.is_feedback:
        return False
    post.status = status if status in ("open", "in_progress", "done", "declined") else "open"
    post.admin_reply = reply_text[:4000] if reply_text else None
    post.replied_at = utcnow() if reply_text else None

    if reply_text and actor is not None:
        db.session.add(
            Comment(
                post_id=post.id,
                user_id=actor.id,
                content=f"Rückmeldung der Administration: {reply_text}",
            )
        )
        notify.send_feedback_response(post.user, post, reply_text)

    audit("feedback.answer", f"Vorschlag #{post.id} → {post.status_label}", actor)
    db.session.commit()
    return True


def feedback_overview(limit: int = 50) -> list[Post]:
    """Improvement suggestions, open ones first."""
    return (
        Post.query
        .filter(Post.topic == TOPIC_FEEDBACK)
        .order_by(Post.status.asc(), Post.is_pinned.desc(), Post.created_at.desc())
        .limit(limit)
        .all()
    )


def board_posts(topic: str | None = None, exclude_feedback: bool = True,
                limit: int = 60) -> list[Post]:
    """Board posts, optionally filtered by topic."""
    query = Post.query
    if topic:
        query = query.filter(Post.topic == topic)
    elif exclude_feedback:
        query = query.filter(Post.topic != TOPIC_FEEDBACK)
    return query.order_by(Post.is_pinned.desc(), Post.created_at.desc()).limit(limit).all()


def close_past_bookings(grace_hours: int = 3) -> int:
    """Close bookings whose slot has ended and flag unused ones (transparency).

    Unused slots are recorded so the administration can see whether capacity is
    wasted - there is no deduction, no penalty and no score attached to it.
    """
    limit = utcnow() - timedelta(hours=grace_hours)
    closed = 0
    past = (
        Booking.query
        .filter(Booking.status.in_((BOOKING_ACTIVE, BOOKING_RELEASED)))
        .filter(Booking.end < limit)
        .all()
    )
    for booking in past:
        booking.status = BOOKING_COMPLETED
        booking.no_show_reported = booking.checked_in_at is None
        closed += 1
    if closed:
        db.session.commit()
        _notify_frequent_no_shows()
    return closed


def _notify_frequent_no_shows(threshold: int = 3, weeks: int = 4) -> None:
    """Send a friendly reminder to users who repeatedly did not charge.

    Purely informational: it points out that the slot could have been released
    for somebody else. No points are deducted.
    """
    from .models import EmailMessage

    since = utcnow() - timedelta(weeks=weeks)
    rows = (
        db.session.query(Booking.user_id, db.func.count(Booking.id))
        .filter(Booking.start >= since, Booking.no_show_reported.is_(True))
        .group_by(Booking.user_id)
        .having(db.func.count(Booking.id) >= threshold)
        .all()
    )
    for user_id, count in rows:
        already = (
            EmailMessage.query
            .filter(EmailMessage.user_id == user_id, EmailMessage.kind == "no_show_notice")
            .filter(EmailMessage.created_at >= since)
            .count()
        )
        if already:
            continue
        user = db.session.get(User, user_id)
        if user:
            notify.send_no_show_notice(user, count, weeks)
    db.session.commit()


# ---------------------------------------------------------------------------
# Calendar view model
# ---------------------------------------------------------------------------

def week_overview(week_start: date, viewer: User) -> dict:
    """Build the data structure the calendar template renders."""
    capacity = capacity_per_slot()
    counts, _ = occupancy(week_start)
    my_bookings = user_bookings_in_week(viewer, week_start)

    rows = []
    for index in range(len(windows())):
        cells = []
        for day in workdays():
            key = (day, index)
            used = counts.get(key, 0)
            my_here = [b for b in my_bookings if b.weekday == day and b.window_index == index]
            free_here = max(0, capacity - used)
            released = [
                b for b in bookings_of_week(week_start)
                if b.weekday == day and b.window_index == index and b.status == BOOKING_RELEASED
            ]
            waiting = len(waitlist_for_slot(week_start, day, index))
            if my_here:
                state = "mine"
            elif released:
                state = "released"
            elif free_here > 0:
                state = "free"
            else:
                state = "full"
            cells.append({
                "day": day,
                "window": index,
                "state": state,
                "used": used,
                "free": free_here,
                "capacity": capacity,
                "mine": my_here,
                "released": released,
                "waiting": waiting,
                "label": window_label(windows(), index),
                "date": week_start + timedelta(days=day),
            })
        rows.append({"index": index, "label": window_label(windows(), index), "cells": cells})

    return {
        "week_start": week_start,
        "week_end": week_end(week_start),
        "calendar_week": week_start.isocalendar().week,
        "days": [week_start + timedelta(days=day) for day in workdays()],
        "workdays": list(workdays()),
        "rows": rows,
        "capacity": capacity,
        "my_bookings": my_bookings,
        "totals": {
            "used": sum(counts.values()),
            "capacity": capacity * len(workdays()) * len(windows()),
        },
    }


_ = (random, SlotInterest)  # explicit re-exports for tooling
