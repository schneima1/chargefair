"""Flask application factory."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import current_user

from .config import Config
from .extensions import csrf, db, login_manager
from .models import Booking, User, utcnow
from .services import current_allocation_method


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_object)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (register mappings)

    register_blueprints(app)
    register_jinja(app)
    register_errors(app)
    register_cli(app)
    register_request_hooks(app)

    if os.environ.get("START_MAIL_WORKER", "1") not in {"0", "false", "no"}:
        _maybe_start_mail_worker(app)

    return app


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------

def register_blueprints(app: Flask) -> None:
    from .blueprints.admin import bp as admin_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def register_jinja(app: Flask) -> None:
    @app.template_filter("de_date")
    def de_date(value, fmt: str = "%d.%m.%Y"):
        if isinstance(value, (date, datetime)):
            return value.strftime(fmt)
        return value or ""

    @app.template_filter("de_datetime")
    def de_datetime(value, fmt: str = "%d.%m.%Y %H:%M"):
        if isinstance(value, (date, datetime)):
            return value.strftime(fmt)
        return value or ""

    @app.template_filter("weekday")
    def weekday_filter(value):
        from .slots import DAY_NAMES

        try:
            return DAY_NAMES[int(value)]
        except (TypeError, ValueError, IndexError):
            return value

    @app.template_filter("euro")
    def euro_filter(value):
        try:
            return f"{float(value):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return value

    @app.context_processor
    def inject_globals():
        from .allocation import METHODS
        from .slots import calendar_week, window_label
        from .services import windows

        def window_label_at(index):
            return window_label(windows(), index)

        def week_span(monday):
            return (
                f"KW {calendar_week(monday)} · "
                f"{monday.strftime('%d.%m.')} bis "
                f"{(monday + timedelta(days=6)).strftime('%d.%m.%Y')}"
            )

        return {
            "app_name": app.config["APP_NAME"],
            "company_name": app.config["COMPANY_NAME"],
            "current_year": utcnow().year,
            "active_method": current_allocation_method(),
            "method_labels": {key: spec.label for key, spec in METHODS.items()},
            "window_label_at": window_label_at,
            "week_span": week_span,
            "window_slot_label": week_span,
            "timedelta": timedelta,
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def register_errors(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(error):  # pragma: no cover - simple template
        return render_template("errors/error.html", code=403,
                               message="Kein Zugriff auf diesen Bereich."), 403

    @app.errorhandler(404)
    def not_found(error):  # pragma: no cover
        return render_template("errors/error.html", code=404,
                               message="Diese Seite gibt es nicht."), 404

    @app.errorhandler(500)
    def server_error(error):  # pragma: no cover
        db.session.rollback()
        return render_template("errors/error.html", code=500,
                               message="Es ist ein interner Fehler aufgetreten."), 500

    @app.errorhandler(413)
    def too_large(error):  # pragma: no cover
        flash("Die Anfrage war zu groß.", "error")
        return redirect(url_for("main.dashboard"))


# ---------------------------------------------------------------------------
# Request hooks
# ---------------------------------------------------------------------------

def register_request_hooks(app: Flask) -> None:
    @app.before_request
    def housekeeping():
        """Cheap maintenance that does not need a scheduler."""
        if request.endpoint in {"static", None}:
            return
        if not getattr(app, "_housekeeping_tick", None) or (
            utcnow() - app._housekeeping_tick
        ).total_seconds() > 60:
            app._housekeeping_tick = utcnow()
            try:
                from .allocation_service import close_past_bookings, expire_waitlist_offers
                from .services import process_email_queue_start

                expire_waitlist_offers(app)
                if os.environ.get("DISPATCH_MAIL_INLINE", "1") not in {"0", "false", "no"}:
                    process_email_queue_start(app)
                if os.environ.get("DISPATCH_HOUSEKEEPING", "1") not in {"0", "false", "no"}:
                    close_past_bookings()
            except Exception:  # pragma: no cover - never break a request
                db.session.rollback()
                app.logger.exception("housekeeping failed")

    @app.before_request
    def enforce_verification():
        """Unverified accounts may only reach the verification screens."""
        if not current_user.is_authenticated:
            return None
        allowed = {
            "auth.logout",
            "auth.verify",
            "auth.resend_verification",
            "main.help",
            "static",
        }
        if request.endpoint in allowed:
            return None
        if not current_user.email_verified and current_user.role != "admin":
            return redirect(url_for("auth.verify"))
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def register_cli(app: Flask) -> None:
    import click

    @app.cli.command("init-db")
    def init_db_command():
        """Create tables and seed infrastructure, admin and demo data."""
        from .seed import init_db

        init_db(app)
        click.echo("Datenbank initialisiert.")

    @app.cli.command("seed-history")
    @click.option("--weeks", default=8, help="Anzahl vergangener Wochen")
    def seed_history_command(weeks: int):
        """Create past bookings so the distribution dashboard has data."""
        from .seed import seed_demo_history

        with app.app_context():
            seed_demo_history(weeks)
        click.echo(f"Historie für {weeks} Wochen erzeugt.")

    @app.cli.command("open-round")
    @click.option("--week", default=None, help="Montag der Zielwoche (YYYY-MM-DD)")
    def open_round_command(week: str | None):
        """Open an allocation round (default: next week)."""
        from .allocation_service import default_round_week, get_or_create_round

        target = date.fromisoformat(week) if week else default_round_week()
        with app.app_context():
            round_obj = get_or_create_round(target)
        click.echo(f"Runde für {target} #{round_obj.id} ist {round_obj.status}.")

    @app.cli.command("allocate")
    @click.option("--week", default=None, help="Montag der Zielwoche (YYYY-MM-DD)")
    @click.option("--method", default=None, help="Vergabeverfahren")
    def allocate_command(week: str | None, method: str | None):
        """Run the allocation for a week."""
        from .allocation_service import default_round_week, get_or_create_round, allocate_round

        target = date.fromisoformat(week) if week else default_round_week()
        with app.app_context():
            round_obj = get_or_create_round(target, method=method)
            if method:
                round_obj.method = method
                db.session.commit()
            stats = allocate_round(round_obj, app=app)
        click.echo(
            f"{stats['assigned']} Slots an {stats['supplied']} Nutzer verteilt "
            f"({stats['method']}, {stats['wall_time']}s)."
        )

    @app.cli.command("dispatch-mail")
    def dispatch_mail_command():
        """Send pending emails once."""
        from .services import process_email_queue

        stats = process_email_queue(app, limit=200)
        click.echo(f"versendet: {stats['sent']}, Fehler: {stats['failed']}")

    @app.cli.command("remind")
    def remind_command():
        """Queue reminders for the next 45 minutes."""
        from .allocation_service import send_due_reminders

        with app.app_context():
            count = send_due_reminders()
        click.echo(f"{count} Erinnerungen eingereiht.")


# ---------------------------------------------------------------------------
# Mail worker
# ---------------------------------------------------------------------------

def _maybe_start_mail_worker(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    if os.environ.get("FLASK_RUN_FROM_CLI") and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    if os.environ.get("MAIL_WORKER_IN_PROCESS", "1") in {"0", "false", "no"}:
        return
    from .services import start_mail_worker

    start_mail_worker(app)


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


_ = Booking
