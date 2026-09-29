"""Standalone mail worker.

    python -m chargefair.mail_worker

Runs the email queue and the periodic housekeeping (reminders, expired
waitlist offers, closing finished bookings) outside of the web process. Useful
when the application runs behind a WSGI server with several workers.
"""
from __future__ import annotations

import os
import signal
import threading
import time

from .allocation_service import close_past_bookings, expire_waitlist_offers, send_due_reminders
from .app import create_app
from .extensions import db
from .models import Setting
from .services import process_email_queue

_running = True


def _stop(*_args) -> None:
    global _running
    _running = False


def main() -> None:  # pragma: no cover - long running process
    os.environ.setdefault("START_MAIL_WORKER", "0")
    os.environ.setdefault("MAIL_WORKER_IN_PROCESS", "0")
    app = create_app()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    interval = max(5, app.config["MAIL_WORKER_INTERVAL"])
    housekeeping_every = 60
    last_housekeeping = 0.0

    print("ChargeFair Mail-Worker gestartet.", flush=True)
    while _running:
        try:
            stats = process_email_queue(app, limit=100)
            if stats["sent"] or stats["failed"]:
                print(f"E-Mails: {stats}", flush=True)

            now = time.monotonic()
            if now - last_housekeeping >= housekeeping_every:
                last_housekeeping = now
                with app.app_context():
                    expired = expire_waitlist_offers(app)
                    reminders = (
                        send_due_reminders()
                        if Setting.get_bool("reminder.enabled", True)
                        else 0
                    )
                    closed = close_past_bookings()
                    if expired or reminders or closed:
                        print(
                            f"Wartung: {expired} Angebote abgelaufen, "
                            f"{reminders} Erinnerungen, {closed} Buchungen geschlossen",
                            flush=True,
                        )
        except Exception as exc:  # pragma: no cover
            print(f"Fehler im Worker: {exc}", flush=True)
            db.session.rollback()
        threading.Event().wait(interval)

    print("ChargeFair Mail-Worker beendet.", flush=True)


if __name__ == "__main__":  # pragma: no cover
    main()
