"""Additional edge cases for value-type consistency and time handling."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from icalendar import Calendar

from .conftest_helpers import (
    BERLIN_VTIMEZONE,
    UTC,
    calendar,
    event,
    parse,
    utc,
)


def test_tzid_datetime_equivalent_to_utc_instant():
    # 10:00 UTC == 12:00 Berlin (June)
    ics = calendar(
        event("u", "DTSTART:20240601T100000Z"),
        event(
            "z",
            "DTSTART;TZID=Europe/Berlin:20240601T120000",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 5, 1), utc(2024, 7, 1)
    )
    starts = {o.uid: o.start for o in result}
    assert starts["u"] == starts["z"] == utc(2024, 6, 1, 10)


def test_floating_until_rule_rejected():
    ics = calendar(
        event(
            "f",
            "DTSTART:20240101T100000\r\n"
            "RRULE:FREQ=DAILY;UNTIL=20240105T100000",
        )
    )
    with pytest.raises(ValueError, match="UNTIL"):
        parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))


def test_rdate_value_type_must_match_dtstart():
    ics = calendar(
        event(
            "bad",
            "DTSTART;VALUE=DATE:20240101\r\n"
            "RDATE:20240103T100000Z",
        )
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(
            utc(2024, 1, 1), utc(2024, 2, 1), default_tz="UTC"
        )


def test_exception_value_type_must_match_master():
    master = event(
        "m",
        "DTSTART;VALUE=DATE:20240101\r\nRRULE:FREQ=DAILY;COUNT=3",
    )
    exception = (
        "BEGIN:VEVENT\r\nUID:m\r\nDTSTAMP:20240101T000000Z\r\n"
        "RECURRENCE-ID;VALUE=DATE:20240102\r\n"
        "DTSTART:20240102T100000Z\r\nEND:VEVENT"
    )
    with pytest.raises(ValueError):
        parse(calendar(master, exception)).expand_events(
            utc(2024, 1, 1), utc(2024, 2, 1), default_tz="UTC"
        )


def test_all_day_exception_uses_date_recurrence_id():
    master = event(
        "ad",
        "DTSTART;VALUE=DATE:20240101\r\nRRULE:FREQ=DAILY;COUNT=3\r\n"
        "SUMMARY:holiday",
    )
    exception = (
        "BEGIN:VEVENT\r\nUID:ad\r\nDTSTAMP:20240101T000000Z\r\n"
        "RECURRENCE-ID;VALUE=DATE:20240102\r\n"
        "DTSTART;VALUE=DATE:20240120\r\nSUMMARY:moved\r\nEND:VEVENT"
    )
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 1), utc(2024, 2, 1), default_tz="UTC"
    )
    moved = next(o for o in result if o.summary == "moved")
    assert moved.recurrence_id == date(2024, 1, 2)
    assert moved.all_day is True
    assert moved.start == utc(2024, 1, 20)


def test_exception_with_end_but_no_dtstart_rejected():
    master = event(
        "m", "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=2"
    )
    exception = (
        "BEGIN:VEVENT\r\nUID:m\r\nDTSTAMP:20240101T000000Z\r\n"
        "RECURRENCE-ID:20240102T100000Z\r\nDTEND:20240102T110000Z\r\n"
        "END:VEVENT"
    )
    with pytest.raises(ValueError):
        parse(calendar(master, exception)).expand_events(
            utc(2024, 1, 1), utc(2024, 2, 1)
        )


def test_negative_bymonthday_resolves_to_end_of_month():
    ics = calendar(
        event(
            "neg",
            "DTSTART:20240131T100000Z\r\n"
            "RRULE:FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3",
        )
    )
    result = parse(ics).expand_events(
        utc(2024, 1, 1), utc(2024, 5, 1)
    )
    assert [o.recurrence_id.date() for o in result] == [
        date(2024, 1, 31),
        date(2024, 2, 29),  # leap year
        date(2024, 3, 31),
    ]


def test_bymonth_restricts_yearly_expansion():
    ics = calendar(
        event(
            "bm",
            "DTSTART:20240220T100000Z\r\n"
            "RRULE:FREQ=YEARLY;BYMONTH=2,8;BYMONTHDAY=20;COUNT=3",
        )
    )
    result = parse(ics).expand_events(
        utc(2024, 1, 1), utc(2026, 1, 1)
    )
    assert [o.recurrence_id.date() for o in result] == [
        date(2024, 2, 20),
        date(2024, 8, 20),
        date(2025, 2, 20),
    ]


def test_bysetpos_rejects_zero():
    ics = calendar(
        event(
            "zero",
            "DTSTART:20240101T100000Z\r\n"
            "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=0",
        )
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(utc(2024, 1, 1), utc(2025, 1, 1))


def test_non_repeating_event_still_has_recurrence_identity():
    ics = calendar(event("once", "DTSTART:20240110T100000Z"))
    occurrence = parse(ics).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 11)
    )[0]
    assert occurrence.recurrence_id == utc(2024, 1, 10, 10)
    assert isinstance(occurrence.recurrence_id, datetime)


def test_all_results_are_utc_aware():
    ics = calendar(
        event(
            "tz",
            "DTSTART;TZID=Europe/Berlin:20240601T120000",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    occurrence = parse(ics).expand_events(
        utc(2024, 5, 1), utc(2024, 7, 1)
    )[0]
    assert occurrence.start.tzinfo is not None
    assert occurrence.end.tzinfo is not None
    assert occurrence.start.utcoffset() == timezone.utc.utcoffset(
        occurrence.start
    )
    assert occurrence.recurrence_id.tzinfo is not None
