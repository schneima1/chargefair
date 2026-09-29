"""German notification texts and the calls that queue them.

The web request only inserts rows into ``email_queue``; the worker thread (or
the separate ``python -m chargefair.mail_worker`` process) performs the actual
SMTP delivery.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from .extensions import db
from .models import Booking, User, WaitlistEntry, utcnow
from .services import queue_mail
from .slots import DAY_NAMES, window_label

FOOTER = (
    "\n\n--\n"
    "Diese Nachricht wurde automatisch von {app} erzeugt.\n"
    "Antworten auf diese E-Mail werden nicht gelesen."
)


def _signature(app_name: str = "ChargeFair") -> str:
    return FOOTER.format(app=app_name)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def format_booking(booking: Booking) -> str:
    city = "Ladepunkt"
    point = booking.charging_point.name if booking.charging_point else "?"
    space = booking.parking_space.name if booking.parking_space else None
    lines = [
        f"Datum:      {DAY_NAMES[booking.weekday]}, {booking.date_label}",
        f"Uhrzeit:    {booking.window_label} Uhr",
        f"{city}:     {point}",
    ]
    if space:
        lines.append(f"Stellplatz: {space}")
    return "\n".join(lines)


def _recipient(user: User) -> str:
    return user.email


# ---------------------------------------------------------------------------
# Notification types
# ---------------------------------------------------------------------------

def send_verification(user: User, token: str, url: str) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Bitte bestätige deine E-Mail-Adresse",
        f"Guten Tag {user.first_name},\n\n"
        f"dein Konto für die Ladeplatzvergabe wurde angelegt.\n"
        f"Bitte bestätige deine E-Mail-Adresse über diesen Link:\n\n{url}\n\n"
        f"Der Link ist 48 Stunden gültig. Falls du dich nicht registriert hast, "
        f"kannst du diese Nachricht ignorieren.",
        kind="verification",
        user=user,
    )


def send_password_reset(user: User, url: str) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Passwort zurücksetzen",
        f"Guten Tag {user.first_name},\n\n"
        f"über diesen Link kannst du ein neues Passwort vergeben:\n\n{url}\n\n"
        f"Der Link ist 2 Stunden gültig. Wenn du das nicht angefordert hast, "
        f"ändere bitte dein Passwort nicht.",
        kind="password_reset",
        user=user,
    )


def send_round_opened(user: User, week: date, closes: datetime, url: str,
                      rules: dict | None = None) -> None:
    rules = rules or {}
    extra = ""
    if rules:
        extra = (
            f"\nDein Kontingent in dieser Woche: {rules.get('slots_per_user', 1)} Ladezeit(en).\n"
        )
    queue_mail(
        _recipient(user),
        f"ChargeFair: Wünsche für KW {week.isocalendar().week} abgeben",
        f"Guten Tag {user.first_name},\n\n"
        f"für die Woche ab {week.strftime('%d.%m.%Y')} kannst du jetzt deine "
        f"Wunschzeiten eintragen.\n"
        f"Frist: {closes.strftime('%d.%m.%Y um %H:%M')} Uhr.{extra}\n"
        f"Wünsche eintragen: {url}\n\n"
        f"Die Vergabe erfolgt nicht in der Reihenfolge des Eingangs: Zuerst wird "
        f"die Grundversorgung aller Personen sichergestellt, danach entscheidet "
        f"bei knappen Plätzen der Zufall.",
        kind="round_opened",
        user=user,
    )


def send_round_result(user: User, week: date, bookings: list[Booking],
                      rejected: list[str], url: str, desired: int | None = None,
                      guarantee: int = 1) -> None:
    guarantee = max(1, guarantee)
    if bookings:
        granted = "\n\n".join(format_booking(booking) for booking in bookings)
        head = f"Du hast {len(bookings)} Ladezeit(en) erhalten:\n\n{granted}"
    else:
        head = "Leider konnte dir in dieser Woche kein Ladeplatz zugeteilt werden."

    # Explain the difference between the guaranteed slot and extra wishes.
    if desired is not None:
        if len(bookings) >= desired:
            head += (
                f"\n\nDamit sind alle {desired} von dir gewünschten Ladezeiten "
                f"berücksichtigt."
            )
        elif len(bookings) >= guarantee:
            head += (
                f"\n\nDeine garantierte Ladezeit ist damit erfüllt. Du hattest "
                f"{desired} Ladezeiten gewünscht; zusätzliche Ladezeiten werden nur "
                f"verteilt, wenn nach der Grundversorgung aller Personen noch "
                f"Kapazität frei ist. Für die kommende Woche war das nicht der Fall."
            )

    tail = ""
    if rejected:
        tail = "\n\nNicht berücksichtigt:\n" + "\n".join(f"- {item}" for item in rejected)

    if not bookings:
        tail += (
            "\n\nDu kannst dich auf der Warteliste eintragen – frei werdende Plätze "
            "werden dir dann bevorzugt angeboten."
        )

    queue_mail(
        _recipient(user),
        f"ChargeFair: Vergabeergebnis KW {week.isocalendar().week}",
        f"Guten Tag {user.first_name},\n\n{head}{tail}\n\nÜbersicht: {url}",
        kind="round_result",
        user=user,
    )


def send_booking_confirmed(user: User, booking: Booking) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Ladeplatz bestätigt",
        f"Guten Tag {user.first_name},\n\n"
        f"dein Ladeplatz ist bestätigt:\n\n{format_booking(booking)}",
        kind="booking_confirmed",
        user=user,
        booking=booking,
    )


def send_booking_cancelled(user: User, booking: Booking) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Ladeplatz storniert",
        f"Guten Tag {user.first_name},\n\n"
        f"folgender Ladeplatz wurde storniert:\n\n{format_booking(booking)}",
        kind="booking_cancelled",
        user=user,
        booking=booking,
    )


def send_release_confirmation(user: User, booking: Booking, waitlisted: bool,
                              interested: int = 0) -> None:
    if interested:
        extra = (
            f"\n\n{interested} Person(en) haben Interesse an genau diesem Termin "
            f"angemeldet und wurden sofort per E-Mail informiert."
        )
    elif waitlisted:
        extra = (
            "\n\nAuf diesen Slot wartet bereits jemand – der Platz wird sofort "
            "weitergegeben. Vielen Dank!"
        )
    else:
        extra = "\n\nAndere Kolleginnen und Kollegen können den Platz jetzt übernehmen."
    queue_mail(
        _recipient(user),
        "ChargeFair: Ladezeit freigegeben",
        f"Guten Tag {user.first_name},\n\n"
        f"du hast folgenden Termin freigegeben:\n\n{format_booking(booking)}{extra}",
        kind="release",
        user=user,
        booking=booking,
    )


def send_offer_to_waitlist(user: User, entry: WaitlistEntry, booking: Booking,
                           minutes: int, url: str) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Ein Ladeplatz ist frei geworden",
        f"Guten Tag {user.first_name},\n\n"
        f"ein Ladeplatz aus deiner Warteliste ist frei geworden:\n\n"
        f"{format_booking(booking)}\n\n"
        f"Du hast {minutes} Minuten Zeit, den Platz zu übernehmen:\n{url}\n\n"
        f"Reagierst du nicht, wird der Platz an die nächste Person weitergegeben.",
        kind="waitlist_offer",
        user=user,
        booking=booking,
    )


def send_offer_expired(user: User, booking: Booking) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Ladeplatz weitergegeben",
        f"Guten Tag {user.first_name},\n\n"
        f"die Frist für folgenden Ladeplatz ist abgelaufen, der Platz wurde "
        f"weitergegeben:\n\n{format_booking(booking)}",
        kind="waitlist_expired",
        user=user,
        booking=booking,
    )


def send_waitlist_confirmed(user: User, booking: Booking) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Ladeplatz übernommen",
        f"Guten Tag {user.first_name},\n\n"
        f"du hast folgenden Ladeplatz übernommen:\n\n{format_booking(booking)}\n\n"
        f"Bitte gib den Platz rechtzeitig frei, falls du nicht laden kannst.",
        kind="waitlist_confirmed",
        user=user,
        booking=booking,
    )


def send_takeover_announcement(user: User, booking: Booking) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Dein freigegebener Platz wurde übernommen",
        f"Guten Tag {user.first_name},\n\n"
        f"dein freigegebener Ladeplatz wurde von einer Kollegin oder einem "
        f"Kollegen übernommen:\n\n{format_booking(booking)}",
        kind="takeover_announcement",
        user=user,
        booking=booking,
    )


def send_swap_offer(user: User, other: User, booking: Booking,
                    wanted: str, message: str, url: str) -> None:
    queue_mail(
        _recipient(other),
        "ChargeFair: Tauschangebot",
        f"Guten Tag {other.first_name},\n\n"
        f"{user.full_name} bietet folgenden Ladeplatz zum Tausch an:\n\n"
        f"{format_booking(booking)}\n\n"
        f"Wunschtermin: {wanted}\n"
        + (f"Nachricht: {message}\n\n" if message else "\n")
        + f"Angebot ansehen: {url}",
        kind="swap_offer",
        user=other,
        booking=booking,
    )


def send_swap_accepted(user: User, other: User, booking: Booking) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Tauschangebot angenommen",
        f"Guten Tag {user.first_name},\n\n"
        f"{other.full_name} hat dein Tauschangebot angenommen:\n\n"
        f"{format_booking(booking)}\n\n"
        f"Der Termin ist jetzt dir zugeordnet.",
        kind="swap_accepted",
        user=user,
        booking=booking,
    )


def send_phase_opened(user: User, start_week: date, weeks: int, end_week: date,
                      closes: datetime, url: str, guarantee: int = 1,
                      maximum: int = 3) -> None:
    """Announce a new allocation phase covering several weeks at once."""
    deadline = closes.strftime('%d.%m.%Y um %H:%M') if closes else "bald"
    queue_mail(
        _recipient(user),
        f"ChargeFair: Neue Vergabephase ab KW {start_week.isocalendar().week}",
        f"Guten Tag {user.first_name},\n\n"
        f"die nächste Vergabephase ist geöffnet. Du kannst jetzt für "
        f"{weeks} Wochen auf einmal deine Wunschzeiten eintragen:\n\n"
        f"Zeitraum:   KW {start_week.isocalendar().week} bis "
        f"KW {end_week.isocalendar().week} "
        f"({start_week.strftime('%d.%m.')}–{(end_week + timedelta(days=6)).strftime('%d.%m.%Y')})\n"
        f"Frist:      {deadline}\n"
        f"Dein Anspruch: {guarantee} Ladezeit pro Woche, "
        f"maximal {maximum} pro Woche\n\n"
        f"Wünsche eintragen: {url}\n\n"
        f"Die Vergabe erfolgt nicht nach der Reihenfolge des Eingangs: Zuerst "
        f"bekommt jede Person ihre garantierte Ladezeit, danach werden "
        f"Zusatzwünsche verteilt. Bei knappen Plätzen entscheidet der Zufall.",
        kind="phase_opened",
        user=user,
    )


def send_phase_reminder(user: User, start_week: date, closes: datetime, url: str,
                        has_wishes: bool = False) -> None:
    """Deadline reminder for a phase."""
    if has_wishes:
        body = (
            f"Guten Tag {user.first_name},\n\n"
            f"du hast bereits Wünsche für die Phase ab KW "
            f"{start_week.isocalendar().week} hinterlegt – bis zum "
            f"{closes.strftime('%d.%m.%Y um %H:%M')} Uhr kannst du sie noch "
            f"anpassen.\n\nWünsche prüfen: {url}"
        )
    else:
        body = (
            f"Guten Tag {user.first_name},\n\n"
            f"bisher liegen von dir keine Wünsche für die Phase ab KW "
            f"{start_week.isocalendar().week} vor. Wenn du laden möchtest, "
            f"trage sie bitte bis zum {closes.strftime('%d.%m.%Y um %H:%M')} Uhr ein "
            f"– danach läuft die Vergabe.\n\nGanz ohne Wunschzeit bekommst du "
            f"keinen Platz zugeteilt.\n\nWünsche eintragen: {url}"
        )
    queue_mail(
        _recipient(user),
        f"ChargeFair: Frist für KW {start_week.isocalendar().week} läuft ab",
        body,
        kind="phase_reminder",
        user=user,
    )


def send_interest_available(user: User, booking: Booking, url: str) -> None:
    """A slot somebody registered interest in has become free."""
    queue_mail(
        _recipient(user),
        "ChargeFair: Dein Wunschtermin ist frei geworden",
        f"Guten Tag {user.first_name},\n\n"
        f"du hattest Interesse an diesem Termin angemeldet – er ist jetzt frei:\n\n"
        f"{format_booking(booking)}\n\n"
        f"Mit einem Klick übernehmen:\n{url}\n\n"
        f"Der Link ist nur für dich gültig und läuft nach einiger Zeit ab. "
        f"Wer zuerst klickt, bekommt den Platz – es gibt hier bewusst keine "
        f"Reihung.",
        kind="interest_available",
        user=user,
        booking=booking,
    )


def send_interest_confirmation(user: User) -> None:
    """Confirmation after registering interest, including the withdraw link."""
    queue_mail(
        _recipient(user),
        "ChargeFair: Interesse vorgemerkt",
        f"Guten Tag {user.first_name},\n\n"
        f"dein Interesse an einem belegten Ladeplatz ist vorgemerkt. Sobald die "
        f"Person den Termin freigibt, erhältst du automatisch eine E-Mail mit "
        f"einem Übernahme-Link.\n\n"
        f"Deine vorgemerkten Termine siehst du hier:\n{app_url()}/uebersicht",
        kind="interest_confirmation",
        user=user,
    )


def send_contact_message(recipient: User, sender: User, subject: str, message: str,
                         booking: Booking | None = None) -> None:
    """Pass on a message that was sent through the contact form."""
    context = ""
    if booking is not None:
        point = booking.charging_point.name if booking.charging_point else "?"
        context = (
            f"\nBezug: {DAY_NAMES[booking.weekday]}, {booking.date_label}, "
            f"{booking.window_label} Uhr, Ladepunkt {point}\n"
        )
    queue_mail(
        recipient.email,
        f"ChargeFair: Nachricht von {sender.full_name}",
        f"Guten Tag {recipient.first_name},\n\n"
        f"{sender.full_name} hat dir über ChargeFair geschrieben.\n"
        f"Deine E-Mail-Adresse wird dabei nicht weitergegeben, damit du "
        f"entscheiden kannst, ob und wie du antwortest.\n"
        f"\nBetreff: {subject}{context}\n{message}\n\n"
        f"Wenn du antworten möchtest, kannst du das direkt tun – die Nachricht "
        f"wurde im Auftrag von {sender.full_name} versendet.",
        kind="contact",
        user=recipient,
        booking=booking,
    )


def send_feedback_notice(admin: User, post, url: str) -> None:
    """Notify the administration about a new improvement suggestion."""
    author = "anonym" if getattr(post, "anonymous", False) else post.user.full_name
    queue_mail(
        admin.email,
        "ChargeFair: Neuer Verbesserungsvorschlag",
        f"Guten Tag,\n\n"
        f"es liegt ein neuer Verbesserungsvorschlag vor.\n\n"
        f"Von:    {author}\n"
        f"Titel:  {post.title}\n\n{post.content}\n\n"
        f"Im System ansehen: {url}",
        kind="feedback",
        user=admin,
    )


def send_feedback_response(user: User, post, reply: str) -> None:
    """Tell the author that the administration reacted to their suggestion."""
    queue_mail(
        _recipient(user),
        "ChargeFair: Rückmeldung zu deinem Verbesserungsvorschlag",
        f"Guten Tag {user.first_name},\n\n"
        f"danke für deinen Vorschlag „{post.title}“.\n\n"
        f"Rückmeldung der Administration:\n{reply}\n\n"
        f"Du kannst im Board weiter diskutieren.",
        kind="feedback_response",
        user=user,
    )


def app_url() -> str:
    """Base URL of the installation, used for plain links in emails."""
    return (os.environ.get("BASE_URL") or "").rstrip("/")


def send_reminder_start(user: User, booking: Booking, release_url: str = "") -> None:
    """Reminder shortly before the charging slot begins.

    Contains a one-click link to release the slot in case something came up -
    that is exactly the moment when people know they cannot make it.
    """
    hint = ""
    if release_url:
        hint = (
            f"\n\nKannst du den Termin doch nicht wahrnehmen? Dann gib ihn mit "
            f"einem Klick frei – alle, die Interesse an diesem Termin haben, "
            f"werden sofort informiert:\n{release_url}"
        )
    queue_mail(
        _recipient(user),
        "ChargeFair: Ladezeit beginnt in 30 Minuten",
        f"Guten Tag {user.first_name},\n\n"
        f"dein Ladezeitraum beginnt in etwa 30 Minuten:\n\n{format_booking(booking)}\n\n"
        f"Bitte denk daran, das Fahrzeug anzuschließen.{hint}",
        kind="reminder_start",
        user=user,
        booking=booking,
    )


def send_reminder_end(user: User, booking: Booking) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Ladezeit endet in 15 Minuten",
        f"Guten Tag {user.first_name},\n\n"
        f"dein Ladezeitraum endet in etwa 15 Minuten:\n\n{format_booking(booking)}\n\n"
        f"Bitte parke das Fahrzeug anschließend um, damit die nächste Person "
        f"laden kann.",
        kind="reminder_end",
        user=user,
        booking=booking,
    )


def send_no_show_notice(user: User, count: int, weeks: int) -> None:
    queue_mail(
        _recipient(user),
        "ChargeFair: Hinweis zu nicht genutzten Ladezeiten",
        f"Guten Tag {user.first_name},\n\n"
        f"du hast in den letzten {weeks} Wochen {count} gebuchte Ladezeit(en) "
        f"nicht genutzt.\n\n"
        f"Bitte gib Termine, die du nicht wahrnehmen kannst, künftig frei. So "
        f"können Kolleginnen und Kollegen den Platz nutzen.\n\n"
        f"Diese Nachricht ist ein Hinweis, keine Sperre.",
        kind="no_show_notice",
        user=user,
    )


def send_admin_digest(admin: User, round_obj, stats: dict, url: str) -> None:
    lines = [
        f"Woche:            {round_obj.week_start.strftime('%d.%m.%Y')}",
        f"Verfahren:        {round_obj.method}",
        f"Versorgte Nutzer: {stats.get('supplied', 0)}",
        f"Verteilte Slots:  {stats.get('assigned', 0)}",
        f"Ohne Ladeplatz:   {stats.get('unassigned', 0)}",
        f"Auslastung:       {stats.get('utilisation', 0) * 100:.1f} %",
    ]
    queue_mail(
        _recipient(admin),
        f"ChargeFair: Vergabe KW {round_obj.week_start.isocalendar().week} abgeschlossen",
        "Guten Tag,\n\ndie Vergaberunde wurde abgeschlossen:\n\n"
        + "\n".join(lines)
        + f"\n\nDetails: {url}",
        kind="admin_digest",
        user=admin,
    )


def send_welcome(user: User, url: str) -> None:
    queue_mail(
        _recipient(user),
        "Willkommen bei ChargeFair",
        f"Guten Tag {user.first_name},\n\n"
        f"dein Konto ist aktiv. Ab jetzt kannst du Ladezeiten anfragen, "
        f"freigeben und tauschen.\n\nLos geht es hier: {url}",
        kind="welcome",
        user=user,
    )


def render_preview(app_name: str = "ChargeFair") -> str:
    return _signature(app_name)


_ = (db, utcnow, window_label, DAY_NAMES)  # keep imports explicit for linters
