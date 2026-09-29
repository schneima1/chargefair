"""Signed, expiring tokens for one-click links inside emails.

Emails need links that work without a login: releasing your own slot, taking
over a freed slot, confirming an interest. Such links must not be guessable and
must expire, so all of them are signed with the application secret key.

Tokens are *not* stored in the database: the payload (action, object id, owner
id) plus a signature is enough. Rotating ``SECRET_KEY`` invalidates every
outstanding link, which is the desired behaviour after an incident.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Lifetime of the different link types, in seconds.
RELEASE_MAX_AGE = 60 * 60 * 12        # release your own slot (same day)
TAKEOVER_MAX_AGE = 60 * 60 * 6        # take over a slot somebody freed
INTEREST_MAX_AGE = 60 * 60 * 24 * 14  # confirm/withdraw an interest


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="chargefair-link")


def issue(secret_key: str, action: str, **payload) -> str:
    """Create a token for ``action`` carrying the given payload."""
    return _serializer(secret_key).dumps({"action": action, **payload})


def read(secret_key: str, token: str, action: str, max_age: int) -> dict | None:
    """Return the payload if the token is valid, not expired and matches the action.

    Returns ``None`` for anything that does not verify, so callers can show a
    friendly message instead of an error page.
    """
    try:
        data = _serializer(secret_key).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("action") != action:
        return None
    return data


def release_token(secret_key: str, booking_id: int, user_id: int) -> str:
    """Token that lets the owner release their own booking with one click."""
    return issue(secret_key, "release", booking=booking_id, user=user_id)


def read_release_token(secret_key: str, token: str) -> dict | None:
    return read(secret_key, token, "release", RELEASE_MAX_AGE)


def takeover_token(secret_key: str, booking_id: int, user_id: int) -> str:
    """Token that lets an interested person take over a freed slot."""
    return issue(secret_key, "takeover", booking=booking_id, user=user_id)


def read_takeover_token(secret_key: str, token: str) -> dict | None:
    return read(secret_key, token, "takeover", TAKEOVER_MAX_AGE)


def interest_token(secret_key: str, interest_id: int, user_id: int) -> str:
    """Token that lets somebody withdraw a registered interest without logging in."""
    return issue(secret_key, "interest", interest=interest_id, user=user_id)


def read_interest_token(secret_key: str, token: str) -> dict | None:
    return read(secret_key, token, "interest", INTEREST_MAX_AGE)
