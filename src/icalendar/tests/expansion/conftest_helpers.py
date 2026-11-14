"""Shared helpers and hand-written ICS fixtures for expansion tests.

Every expected record in this package is computed by hand and written down
explicitly; the tests never call the expansion code under test to generate the
expected values. ICS strings are intentionally small so each rule and each
exception can be checked against independently derived datetimes.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar

UTC = ZoneInfo("UTC")
BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")


def utc(year, month, day, hour=0, minute=0, second=0) -> datetime:
    """Build a UTC-aware datetime with explicit arguments."""
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def parse(ics: str | bytes) -> Calendar:
    """Parse an ICS payload."""
    if isinstance(ics, str):
        ics = ics.encode("utf-8")
    return Calendar.from_ical(ics)


#: A minimal Europe/Berlin VTIMEZONE (EU rules since 1996).
BERLIN_VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
END:DAYLIGHT
END:VTIMEZONE"""

#: A minimal America/New_York VTIMEZONE.
NEW_YORK_VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
END:DAYLIGHT
END:VTIMEZONE"""

#: A custom, deliberately non-IANA time zone defined only via VTIMEZONE.
CUSTOM_VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Custom/TwoOffsets
BEGIN:STANDARD
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CST
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CDT
END:DAYLIGHT
END:VTIMEZONE"""


def calendar(*components: str, timezones: str = "") -> str:
    """Wrap VEVENT blocks (and optional VTIMEZONE blocks) into a VCALENDAR."""
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//expansion-tests//EN\r\n"
        f"{timezones}\r\n"
        + "\r\n".join(components)
        + "\r\nEND:VCALENDAR\r\n"
    )


def event(
    uid: str,
    body: str,
    recurrence_id: str | None = None,
) -> str:
    """Build a VEVENT block with a DTSTAMP and UID."""
    rec = f"RECURRENCE-ID:{recurrence_id}\r\n" if recurrence_id else ""
    return (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        "DTSTAMP:20240101T000000Z\r\n"
        f"{rec}{body}\r\n"
        "END:VEVENT"
    )


# Hand-computed reference data. These are independent of the implementation:
# they come from reading the RFC semantics and wall clocks directly.

#: (uid, recurrence_id, start, end, summary, all_day) rows for a simple
#: DAILY;COUNT=3 UTC event with DTEND one hour after DTSTART.
DAILY_COUNT3_EXPECTED = [
    ("meet", utc(2024, 1, 1, 10), utc(2024, 1, 1, 11), "Standup", False),
    ("meet", utc(2024, 1, 2, 10), utc(2024, 1, 2, 11), "Standup", False),
    ("meet", utc(2024, 1, 3, 10), utc(2024, 1, 3, 11), "Standup", False),
]

#: Two independent UIDs in one calendar.
MULTI_UID_DATES = {
    "alpha": [date(2024, 1, 1), date(2024, 1, 2)],
    "beta": [date(2024, 1, 3)],
}
