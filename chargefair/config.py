"""Application configuration.

Every value can be overridden through an environment variable (12-factor style)
so the same code base runs locally, inside the Docker container and on a company
network without changes.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path(os.environ.get("CHARGEFAIR_DATA", BASE_DIR / "data"))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- Basics ---------------------------------------------------------
    COMPANY_NAME = os.environ.get("COMPANY_NAME", "ChargeFair Demo GmbH")
    APP_NAME = os.environ.get("APP_NAME", "ChargeFair")
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    BASE_URL = os.environ.get("BASE_URL", "")

    # --- Database -------------------------------------------------------
    _db_url = os.environ.get("DATABASE_URL")
    if not _db_url:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db_url = f"sqlite:///{DEFAULT_DATA_DIR / 'chargefair.db'}"
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 1800}

    # --- Locale / time --------------------------------------------------
    DEFAULT_LOCALE = "de"
    TIMEZONE = os.environ.get("TIMEZONE", "Europe/Berlin")

    # --- Security -------------------------------------------------------
    PASSWORD_MIN_LENGTH = _env_int("PASSWORD_MIN_LENGTH", 10)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12
    WTF_CSRF_TIME_LIMIT = None
    LOGIN_MAX_ATTEMPTS = _env_int("LOGIN_MAX_ATTEMPTS", 8)
    LOGIN_LOCK_MINUTES = _env_int("LOGIN_LOCK_MINUTES", 15)

    # --- Registration ---------------------------------------------------
    ALLOWED_EMAIL_DOMAINS = os.environ.get("ALLOWED_EMAIL_DOMAINS", "")
    REGISTRATION_REQUIRES_TOKEN = _env_bool("REGISTRATION_REQUIRES_TOKEN", True)
    REQUIRE_EMAIL_VERIFICATION = _env_bool("REQUIRE_EMAIL_VERIFICATION", True)
    EMAIL_VERIFICATION_HOURS = _env_int("EMAIL_VERIFICATION_HOURS", 48)

    # --- Email ----------------------------------------------------------
    MAIL_FROM = os.environ.get("MAIL_FROM", "chargefair@example.com")
    MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "ChargeFair")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = _env_int("SMTP_PORT", 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_TLS = _env_bool("SMTP_TLS", True)
    MAIL_DRY_RUN = _env_bool("MAIL_DRY_RUN", True)
    MAIL_WORKER_INTERVAL = _env_int("MAIL_WORKER_INTERVAL", 15)
    MAIL_MAX_ATTEMPTS = _env_int("MAIL_MAX_ATTEMPTS", 5)
    MAIL_BATCH_SIZE = _env_int("MAIL_BATCH_SIZE", 50)
    SMTP_TIMEOUT = _env_int("SMTP_TIMEOUT", 30)
    # Optional token for the cron endpoint ``/cron/send-emails``.
    CRON_TOKEN = os.environ.get("CRON_TOKEN", "")

    # --- Allocation / booking -------------------------------------------
    ALLOCATION_METHOD_DEFAULT = os.environ.get("ALLOCATION_METHOD", "lexicographic")
    MAX_SLOTS_PER_WEEK_DEFAULT = _env_int("MAX_SLOTS_PER_WEEK", 3)
    GUARANTEED_SLOTS_PER_WEEK_DEFAULT = _env_int("GUARANTEED_SLOTS_PER_WEEK", 1)
    PHASE_WEEKS_DEFAULT = _env_int("PHASE_WEEKS", 4)
    PHASE_REMINDER_DAYS_DEFAULT = _env_int("PHASE_REMINDER_DAYS", 2)
    SLOT_REMINDER_MINUTES_DEFAULT = _env_int("SLOT_REMINDER_MINUTES", 45)
    SOLVER_TIME_LIMIT = float(os.environ.get("SOLVER_TIME_LIMIT", "20"))
    SOLVER_WORKERS = _env_int("SOLVER_WORKERS", 8)
    BOOKING_HORIZON_WEEKS = _env_int("BOOKING_HORIZON_WEEKS", 10)
    WAITLIST_OFFER_MINUTES = _env_int("WAITLIST_OFFER_MINUTES", 30)

    # --- Server ---------------------------------------------------------
    JSON_AS_ASCII = False
    TEMPLATES_AUTO_RELOAD = _env_bool("TEMPLATES_AUTO_RELOAD", False)
