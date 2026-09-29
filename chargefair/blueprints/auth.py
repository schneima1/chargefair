"""Registration, login, email verification, password reset and profile."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta
from functools import wraps

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
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import (
    RegistrationToken,
    Setting,
    User,
    Vehicle,
    utcnow,
)
from ..notifications import send_password_reset, send_verification, send_welcome
from ..services import audit
from ..slots import DEFAULT_WINDOWS, VEHICLE_TYPES

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _password_problem(password: str, repeat: str) -> str | None:
    minimum = current_app.config["PASSWORD_MIN_LENGTH"]
    if len(password) < minimum:
        return f"Das Passwort muss mindestens {minimum} Zeichen lang sein."
    if not re.search(r"[A-Za-zÄÖÜäöü]", password):
        return "Das Passwort muss mindestens einen Buchstaben enthalten."
    if not re.search(r"\d", password):
        return "Das Passwort muss mindestens eine Ziffer enthalten."
    if password != repeat:
        return "Die beiden Passwörter stimmen nicht überein."
    return None


def _allowed_domain(email: str) -> bool:
    raw = current_app.config["ALLOWED_EMAIL_DOMAINS"]
    if not raw:
        return True
    domain = email.rsplit("@", 1)[-1].lower()
    allowed = {item.strip().lstrip("*").lstrip("@").lower() for item in raw.split(",") if item.strip()}
    return domain in allowed


def _base_url() -> str:
    configured = current_app.config.get("BASE_URL")
    if configured:
        return configured.rstrip("/")
    return request.url_root.rstrip("/")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@bp.route("/registrieren", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = request.form
    values = {key: form.get(key, "").strip() for key in form}

    if request.method == "POST":
        email = values.get("email", "").lower()
        first_name = values.get("first_name", "")
        last_name = values.get("last_name", "")
        password = form.get("password", "")
        repeat = form.get("password_repeat", "")
        token_value = values.get("token", "").upper()

        problem = None
        if not first_name or not last_name:
            problem = "Bitte gib Vor- und Nachnamen an."
        elif not EMAIL_RE.match(email):
            problem = "Bitte gib eine gültige E-Mail-Adresse an."
        elif not _allowed_domain(email):
            domains = current_app.config["ALLOWED_EMAIL_DOMAINS"]
            problem = f"Diese E-Mail-Domain ist nicht zugelassen (erlaubt: {domains})."
        elif User.query.filter_by(email=email).first():
            problem = "Für diese E-Mail-Adresse existiert bereits ein Konto."
        else:
            problem = _password_problem(password, repeat)

        token = None
        if problem is None and current_app.config["REGISTRATION_REQUIRES_TOKEN"]:
            token = RegistrationToken.query.filter_by(token=token_value).first()
            if token is None or not token.is_valid:
                problem = "Der Registrierungscode ist ungültig oder wurde bereits verbraucht."

        if problem:
            flash(problem, "error")
            return render_template("auth/register.html", values=values)

        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=values.get("phone") or None,
            department=values.get("department") or None,
            role="user",
            is_active=True,
            email_verified=not current_app.config["REQUIRE_EMAIL_VERIFICATION"],
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        vehicle_type = values.get("vehicle_type", "BEV")
        if vehicle_type not in VEHICLE_TYPES:
            vehicle_type = "BEV"
        db.session.add(
            Vehicle(
                user_id=user.id,
                type=vehicle_type,
                manufacturer=values.get("manufacturer") or None,
                model=values.get("model") or None,
                license_plate=values.get("license_plate") or None,
                power_kw=float(values.get("power_kw") or 11.0),
                battery_kwh=float(values["battery_kwh"]) if values.get("battery_kwh") else None,
            )
        )

        if token is not None:
            token.uses += 1

        if current_app.config["REQUIRE_EMAIL_VERIFICATION"]:
            _issue_verification(user)

        audit("auth.register", f"Registrierung {user.email}", user, request.remote_addr)
        db.session.commit()

        if current_app.config["REQUIRE_EMAIL_VERIFICATION"]:
            flash(
                "Konto angelegt. Bitte bestätige jetzt den Link in der E-Mail, "
                "die wir dir geschickt haben.",
                "success",
            )
            return redirect(url_for("auth.verify"))

        login_user(user)
        send_welcome(user, _base_url())
        db.session.commit()
        flash("Willkommen bei ChargeFair!", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/register.html", values=values)


def _issue_verification(user: User) -> None:
    hours = current_app.config["EMAIL_VERIFICATION_HOURS"]
    user.verification_token = secrets.token_urlsafe(24)
    user.verification_expires = utcnow() + timedelta(hours=hours)
    send_verification(
        user,
        user.verification_token,
        f"{_base_url()}{url_for('auth.verify_token', token=user.verification_token)}",
    )


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

@bp.route("/anmelden", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.is_locked:
            minutes = int((user.locked_until - utcnow()).total_seconds() // 60) + 1
            flash(
                f"Das Konto ist vorübergehend gesperrt. Bitte in {minutes} Minuten "
                f"erneut versuchen.",
                "error",
            )
            return render_template("auth/login.html", email=email)

        if user is None or not user.check_password(password):
            if user is not None:
                user.failed_logins = (user.failed_logins or 0) + 1
                if user.failed_logins >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
                    user.locked_until = utcnow() + timedelta(
                        minutes=current_app.config["LOGIN_LOCK_MINUTES"]
                    )
                    user.failed_logins = 0
                db.session.commit()
                audit("auth.login_failed", f"Fehlversuch für {email}", None, request.remote_addr)
                db.session.commit()
            flash("E-Mail-Adresse oder Passwort ist falsch.", "error")
            return render_template("auth/login.html", email=email)

        if not user.is_active:
            flash("Dieses Konto wurde deaktiviert. Bitte wende dich an die Administration.", "error")
            return render_template("auth/login.html", email=email)

        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = utcnow()
        db.session.commit()

        login_user(user, remember=bool(request.form.get("remember")))
        audit("auth.login", f"Anmeldung {user.email}", user, request.remote_addr)
        db.session.commit()

        if not user.email_verified and not user.is_admin:
            return redirect(url_for("auth.verify"))

        next_url = request.args.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html", email="")


@bp.route("/abmelden", methods=["POST", "GET"])
@login_required
def logout():
    audit("auth.logout", f"Abmeldung {current_user.email}", current_user, request.remote_addr)
    db.session.commit()
    logout_user()
    flash("Du bist abgemeldet.", "info")
    return redirect(url_for("main.landing"))


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

@bp.route("/bestaetigen")
@login_required
def verify():
    if current_user.email_verified:
        return redirect(url_for("main.dashboard"))
    return render_template("auth/verify.html")


@bp.route("/bestaetigen/<token>")
def verify_token(token: str):
    user = User.query.filter_by(verification_token=token).first()
    if user is None:
        flash("Dieser Bestätigungslink ist ungültig.", "error")
        return redirect(url_for("auth.login"))

    if user.verification_expires and user.verification_expires < utcnow():
        _issue_verification(user)
        db.session.commit()
        flash("Der Link war abgelaufen. Wir haben dir einen neuen geschickt.", "info")
        return redirect(url_for("auth.verify") if current_user.is_authenticated else url_for("auth.login"))

    user.email_verified = True
    user.verification_token = None
    user.verification_expires = None
    send_welcome(user, _base_url())
    audit("auth.verified", f"{user.email} bestätigt", user)
    db.session.commit()

    flash("E-Mail-Adresse bestätigt. Dein Konto ist aktiv.", "success")
    if not current_user.is_authenticated:
        login_user(user)
    return redirect(url_for("main.dashboard"))


@bp.route("/bestaetigen/erneut", methods=["POST"])
@login_required
def resend_verification():
    if current_user.email_verified:
        return redirect(url_for("main.dashboard"))
    _issue_verification(current_user)
    db.session.commit()
    flash("Wir haben dir eine neue Bestätigungsmail geschickt.", "success")
    return redirect(url_for("auth.verify"))


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@bp.route("/passwort-vergessen", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.is_active:
            user.reset_token = secrets.token_urlsafe(24)
            user.reset_expires = utcnow() + timedelta(hours=2)
            send_password_reset(
                user, f"{_base_url()}{url_for('auth.reset_password', token=user.reset_token)}"
            )
            db.session.commit()
        # Always answer the same way so accounts cannot be enumerated.
        flash(
            "Wenn ein Konto mit dieser Adresse existiert, haben wir dir einen Link "
            "zum Zurücksetzen geschickt.",
            "info",
        )
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")


@bp.route("/passwort-zuruecksetzen/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    user = User.query.filter_by(reset_token=token).first()
    if user is None or (user.reset_expires and user.reset_expires < utcnow()):
        flash("Dieser Link ist ungültig oder abgelaufen.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        problem = _password_problem(
            request.form.get("password", ""), request.form.get("password_repeat", "")
        )
        if problem:
            flash(problem, "error")
        else:
            user.set_password(request.form.get("password", ""))
            user.reset_token = None
            user.reset_expires = None
            user.failed_logins = 0
            user.locked_until = None
            audit("auth.password_reset", f"Passwort zurückgesetzt für {user.email}", user)
            db.session.commit()
            flash("Das Passwort wurde geändert. Du kannst dich jetzt anmelden.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/reset.html", token=token)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        form = request.form
        current_user.first_name = form.get("first_name", "").strip() or current_user.first_name
        current_user.last_name = form.get("last_name", "").strip() or current_user.last_name
        current_user.phone = form.get("phone", "").strip() or None
        current_user.department = form.get("department", "").strip() or None

        days = [str(index) for index in range(7) if form.get(f"day_{index}")]
        current_user.preferred_days = ",".join(days) or current_user.preferred_days

        order = []
        for index in range(len(DEFAULT_WINDOWS)):
            raw = form.get(f"window_rank_{index}")
            if raw and raw.isdigit() and int(raw) not in order:
                order.append(int(raw))
        if order:
            current_user.preferred_windows = ",".join(str(value) for value in order)

        current_user.shift_model = form.get("shift_model", current_user.shift_model)
        limit = form.get("max_weekly_slots", "")
        current_user.max_weekly_slots = int(limit) if limit.isdigit() and int(limit) > 0 else None

        vehicle = current_user.primary_vehicle
        if vehicle is None:
            vehicle = Vehicle(user_id=current_user.id)
            db.session.add(vehicle)
        vehicle.type = form.get("vehicle_type", vehicle.type or "BEV")
        vehicle.manufacturer = form.get("manufacturer", "").strip() or None
        vehicle.model = form.get("model", "").strip() or None
        vehicle.license_plate = form.get("license_plate", "").strip() or None
        try:
            vehicle.power_kw = float(form.get("power_kw") or vehicle.power_kw or 11.0)
        except ValueError:
            vehicle.power_kw = 11.0
        battery = form.get("battery_kwh", "")
        try:
            vehicle.battery_kwh = float(battery) if battery else vehicle.battery_kwh
        except ValueError:
            pass

        audit("profile.update", "Profil geändert", current_user, request.remote_addr)
        db.session.commit()
        flash("Profil gespeichert.", "success")
        return redirect(url_for("auth.profile"))

    from ..services import windows as window_list

    return render_template("auth/profile.html", window_label_count=len(window_list()))


@bp.route("/profil/passwort", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current_password", "")
    if not current_user.check_password(current):
        flash("Das aktuelle Passwort ist nicht korrekt.", "error")
        return redirect(url_for("auth.profile"))

    problem = _password_problem(
        request.form.get("new_password", ""), request.form.get("new_password_repeat", "")
    )
    if problem:
        flash(problem, "error")
        return redirect(url_for("auth.profile"))

    current_user.set_password(request.form.get("new_password", ""))
    audit("profile.password", "Passwort geändert", current_user, request.remote_addr)
    db.session.commit()
    flash("Passwort geändert.", "success")
    return redirect(url_for("auth.profile"))


@bp.route("/profil/loeschen", methods=["POST"])
@login_required
def delete_account():
    """GDPR self-service: anonymise the account and deactivate it."""
    from ..allocation_service import bookings_of_week  # noqa: F401  (ensure import graph)
    from ..services import anonymize_user

    from ..models import Booking

    open_bookings = (
        Booking.query
        .filter(Booking.user_id == current_user.id)
        .filter(Booking.status.in_(("active", "released")))
        .filter(Booking.end >= utcnow())
        .count()
    )
    if open_bookings:
        flash(
            "Bitte gib zuerst alle zukünftigen Ladezeiten frei oder storniere sie.",
            "error",
        )
        return redirect(url_for("main.my_bookings"))

    user = current_user
    logout_user()
    anonymize_user(user, keep_statistics=True)
    audit("profile.delete", f"Konto #{user.id} anonymisiert")
    db.session.commit()
    flash("Das Konto wurde anonymisiert und deaktiviert.", "info")
    return redirect(url_for("main.landing"))


_ = (Setting,)
