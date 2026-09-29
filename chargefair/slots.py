"""Time grid: weekdays, charging windows and ISO week arithmetic.

The grid is the basis of every booking and of the allocation. By default there
are four charging windows per working day, separated by a 30 minute buffer so
vehicles can be moved.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

DAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
DAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# (start minute, end minute) counted from midnight
DEFAULT_WINDOWS: tuple[tuple[int, int], ...] = (
    (6 * 60, 9 * 60),            # 06:00 - 09:00
    (9 * 60 + 30, 12 * 60 + 30),  # 09:30 - 12:30
    (13 * 60, 16 * 60),          # 13:00 - 16:00
    (16 * 60 + 30, 20 * 60),     # 16:30 - 20:00
)

DEFAULT_WORKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)

# Default preference order of the windows (midday is the most popular one)
DEFAULT_WINDOW_ORDER = (1, 2, 3, 0)

VEHICLE_TYPES = ("BEV", "PHEV")


def parse_windows(raw) -> tuple[tuple[int, int], ...]:
    """Convert a list of ``[start, end]`` pairs (minutes) into normalised tuples."""
    windows: list[tuple[int, int]] = []
    if not raw:
        return DEFAULT_WINDOWS
    for item in raw:
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= start < end <= 24 * 60:
            windows.append((start, end))
    return tuple(windows) or DEFAULT_WINDOWS


def minutes_to_time(value: int) -> time:
    return time(hour=value // 60, minute=value % 60)


def window_bounds(windows, window_index: int) -> tuple[int, int]:
    return windows[window_index]


def window_label(windows, window_index: int) -> str:
    start, end = windows[window_index]
    return f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"


def window_short(windows, window_index: int) -> str:
    start, end = windows[window_index]
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def slot_label(windows, weekday: int, window_index: int, short: bool = False) -> str:
    days = DAY_SHORT if short else DAY_NAMES
    return f"{days[weekday]} {window_label(windows, window_index)}"


def window_duration_hours(windows, window_index: int) -> float:
    start, end = windows[window_index]
    return (end - start) / 60.0


# --- Week arithmetic ----------------------------------------------------

def week_start(value: date) -> date:
    """Monday of the week that contains ``value``."""
    return value - timedelta(days=value.weekday())


def week_dates(monday: date, workdays=DEFAULT_WORKDAYS) -> list[date]:
    return [monday + timedelta(days=offset) for offset in sorted(workdays)]


def calendar_week(value: date) -> int:
    return value.isocalendar().week


def is_even_week(value: date) -> bool:
    return calendar_week(value) % 2 == 0


def recurrence_matches(recurrence: str, day: date) -> bool:
    """Check whether a recurring series has an occurrence on ``day``."""
    if recurrence in (None, "", "none", "once"):
        return True
    if recurrence == "weekly":
        return True
    if recurrence == "odd_weeks":
        return not is_even_week(day)
    if recurrence == "even_weeks":
        return is_even_week(day)
    if recurrence == "biweekly":
        return calendar_week(day) % 2 == 0
    return True


RECURRENCE_LABELS = {
    "once": "Einmalig",
    "weekly": "Jede Woche",
    "odd_weeks": "Ungerade Kalenderwochen",
    "even_weeks": "Gerade Kalenderwochen",
}


def recurrence_label(recurrence: str) -> str:
    return RECURRENCE_LABELS.get(recurrence or "once", recurrence)


def combine(day: date, minute_of_day: int) -> datetime:
    return datetime.combine(day, minutes_to_time(minute_of_day))
