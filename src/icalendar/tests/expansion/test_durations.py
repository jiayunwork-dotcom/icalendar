"""DTEND vs DURATION semantics, default ends and value-type handling."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from icalendar import Calendar

from .conftest_helpers import (
    BERLIN,
    BERLIN_VTIMEZONE,
    UTC,
    calendar,
    event,
    parse,
    utc,
)


def test_dtend_exact_duration_repeats_on_every_occurrence():
    # One-hour DTEND: every recurrence keeps the same exact 1h length even
    # though DTEND names an absolute instant on the master day.
    ics = calendar(
        event(
            "dtend",
            "DTSTART:20240101T100000Z\r\nDTEND:20240101T110000Z\r\n"
            "RRULE:FREQ=DAILY;COUNT=3",
        )
    )
    result = parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))
    assert all((o.end - o.start) == timedelta(hours=1) for o in result)
    assert [o.end for o in result] == [
        utc(2024, 1, 1, 11),
        utc(2024, 1, 2, 11),
        utc(2024, 1, 3, 11),
    ]


def test_duration_p1d_is_nominal_across_dst():
    # A nominal day duration keeps the same wall-clock time across a normal
    # DST boundary: noon CET on Mar 30 maps to noon CEST on Mar 31, even though
    # the measured length is only 23 hours.
    ics = calendar(
        event(
            "p1d",
            "DTSTART;TZID=Europe/Berlin:20240330T120000\r\nDURATION:P1D",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 2)
    )[0]
    # wall clock preserved at 12:00 local
    assert result.end.astimezone(BERLIN).replace(tzinfo=None) == datetime(
        2024, 3, 31, 12, 0
    )
    # 23 measured hours because the gap night is one hour short
    assert (result.end - result.start) == timedelta(hours=23)


def test_duration_pt24h_is_exact_across_dst():
    # An exact PT24H is a fixed 24-hour instant offset. At the same noon start
    # it lands one wall-clock hour later than the nominal P1D, because the
    # exact offset ignores the DST shift.
    exact_ics = calendar(
        event(
            "pt24h",
            "DTSTART;TZID=Europe/Berlin:20240330T120000\r\nDURATION:PT24H",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    exact = parse(exact_ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 2)
    )[0]
    assert (exact.end - exact.start) == timedelta(hours=24)
    assert exact.end.astimezone(BERLIN).replace(tzinfo=None) == datetime(
        2024, 3, 31, 13, 0
    )

    nominal_ics = calendar(
        event(
            "p1d",
            "DTSTART;TZID=Europe/Berlin:20240330T120000\r\nDURATION:P1D",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    nominal = parse(nominal_ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 2)
    )[0]
    # the two spellings produce different end instants at a noon start
    assert exact.end != nominal.end


def test_nominal_duration_marker_survives_parsing():
    # The library must not collapse P1D and PT24H into the same semantics.
    from icalendar.prop.dt.duration import NominalDuration

    for spelling, expected in (("P1D", True), ("P2W", True),
                               ("PT24H", False), ("PT1H", False),
                               ("P1DT1H", True)):
        ics = calendar(
            event("m", f"DTSTART:20240101T100000Z\r\nDURATION:{spelling}")
        )
        duration = parse(ics).events[0]["DURATION"].dt
        assert isinstance(duration, timedelta)
        assert isinstance(duration, NominalDuration) is expected
        assert getattr(duration, "nominal", False) is expected


def test_timed_event_without_end_is_zero_length():
    ics = calendar(event("zero", "DTSTART:20240101T100000Z"))
    occurrence = parse(ics).expand_events(
        utc(2024, 1, 1), utc(2024, 1, 2)
    )[0]
    assert occurrence.start == occurrence.end


def test_all_day_event_without_end_last_one_calendar_day():
    ics = calendar(event("day", "DTSTART;VALUE=DATE:20240110"))
    occurrence = parse(ics).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 12), default_tz="UTC"
    )[0]
    assert occurrence.end - occurrence.start == timedelta(hours=24)
    assert occurrence.recurrence_id == date(2024, 1, 10)


def test_all_day_dtend_spans_multiple_days():
    # DTEND is exclusive: Jan 10 -> Jan 13 covers three calendar days.
    ics = calendar(
        event(
            "multi",
            "DTSTART;VALUE=DATE:20240110\r\nDTEND;VALUE=DATE:20240113",
        )
    )
    occurrence = parse(ics).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 14), default_tz="UTC"
    )[0]
    assert occurrence.start == utc(2024, 1, 10)
    assert occurrence.end == utc(2024, 1, 13)


def test_all_day_duration_accepts_whole_days_and_weeks():
    for duration, days in (("P3D", 3), ("P1W", 7), ("P2W", 14)):
        ics = calendar(
            event(
                duration,
                f"DTSTART;VALUE=DATE:20240110\r\nDURATION:{duration}",
            )
        )
        occurrence = parse(ics).expand_events(
            utc(2024, 1, 10), utc(2024, 2, 1), default_tz="UTC"
        )[0]
        assert occurrence.end == utc(2024, 1, 10 + days)


def test_all_day_duration_rejects_hours():
    ics = calendar(
        event("bad", "DTSTART;VALUE=DATE:20240110\r\nDURATION:PT1H")
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(
            utc(2024, 1, 10), utc(2024, 1, 12), default_tz="UTC"
        )


def test_dtend_and_duration_are_mutually_exclusive():
    ics = calendar(
        event(
            "bad",
            "DTSTART:20240101T100000Z\r\nDTEND:20240101T110000Z\r\n"
            "DURATION:PT1H",
        )
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 1, 2))


def test_explicit_end_must_be_later_than_start():
    ics = calendar(
        event("bad", "DTSTART:20240101T100000Z\r\nDTEND:20240101T100000Z")
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 1, 2))


def test_duration_must_be_positive():
    ics = calendar(
        event("bad", "DTSTART:20240101T100000Z\r\nDURATION:PT0S")
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 1, 2))


def test_all_day_recurring_duration_uses_calendar_days_across_dst():
    # P2D nominal: each occurrence spans two calendar days in the default zone.
    # Both spans (Mar 30->Apr 1 and Mar 31->Apr 2) cross the Mar 31 gap night,
    # so each is 47 measured hours but exactly two calendar days long.
    ics = calendar(
        event(
            "two-days",
            "DTSTART;VALUE=DATE:20240330\r\nRRULE:FREQ=DAILY;COUNT=2\r\n"
            "DURATION:P2D",
        )
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 4), default_tz="Europe/Berlin"
    )
    assert len(result) == 2
    for occurrence in result:
        # exactly two calendar days between local boundaries
        local_start = occurrence.start.astimezone(BERLIN).date()
        local_end = occurrence.end.astimezone(BERLIN).date()
        assert (local_end - local_start).days == 2
        # 47 measured hours because each span crosses the one-hour gap
        assert occurrence.end - occurrence.start == timedelta(hours=47)


def test_exception_own_dtend_is_honored():
    master = event(
        "own-end",
        "DTSTART:20240110T100000Z\r\nDTEND:20240110T110000Z\r\n"
        "RRULE:FREQ=DAILY;COUNT=2",
    )
    exception = event(
        "own-end",
        "DTSTART:20240111T120000Z\r\nDTEND:20240111T140000Z",
        recurrence_id="20240111T100000Z",
    )
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 20)
    )
    moved = next(o for o in result if o.recurrence_id == utc(2024, 1, 11, 10))
    assert moved.start == utc(2024, 1, 11, 12)
    assert moved.end == utc(2024, 1, 11, 14)


def test_exception_own_duration_uses_nominal_semantics():
    # Noon start avoids DST gaps; the exception's own P1D keeps the 12:00 wall
    # clock on the next day (the nominal calendar-day semantics).
    master = event(
        "own-dur",
        "DTSTART;TZID=Europe/Berlin:20240330T120000\r\n"
        "RRULE:FREQ=DAILY;COUNT=2",
    )
    exception = (
        "BEGIN:VEVENT\r\nUID:own-dur\r\nDTSTAMP:20240101T000000Z\r\n"
        "RECURRENCE-ID;TZID=Europe/Berlin:20240331T120000\r\n"
        "DTSTART;TZID=Europe/Berlin:20240331T120000\r\nDURATION:P1D\r\n"
        "END:VEVENT"
    )
    result = parse(
        calendar(master, exception, timezones=BERLIN_VTIMEZONE)
    ).expand_events(utc(2024, 3, 29), utc(2024, 4, 3))
    moved = next(
        o
        for o in result
        if o.recurrence_id == utc(2024, 3, 31, 10)  # 12:00 CEST
    )
    # nominal day keeps the 12:00 wall clock on the following calendar day
    assert moved.end.astimezone(BERLIN).replace(tzinfo=None) == datetime(
        2024, 4, 1, 12, 0
    )


def test_value_type_mismatch_between_dtstart_and_dtend_rejected():
    ics = calendar(
        event(
            "bad",
            "DTSTART;VALUE=DATE:20240110\r\nDTEND:20240111T100000Z",
        )
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(
            utc(2024, 1, 10), utc(2024, 1, 12), default_tz="UTC"
        )
