"""Tiny date/time helper shared across the app."""
from datetime import datetime, timezone


def utcnow_naive():
    """Drop-in replacement for the deprecated datetime.utcnow(): current UTC
    time, but with tzinfo stripped. Every timestamp this app stores or
    compares against (password_resets.expires_at, conversation message
    timestamps, etc.) uses Postgres's naive TIMESTAMP (no time zone) type —
    mixing that with a timezone-aware datetime would raise
    "can't compare offset-naive and offset-aware datetimes" the moment two
    of them meet in a comparison. Using timezone.utc internally and then
    stripping it keeps the exact behavior of the old utcnow() call without
    the deprecation warning."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
