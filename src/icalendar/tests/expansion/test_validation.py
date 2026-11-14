"""Structural validation, unsupported-feature rejection and query isolation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from icalendar import Calendar

from .conftest_helpers import (
    BERLIN_VTIMEZONE,
    calendar,
    event,
    parse,
    utc,
)

WS = utc(2024, 1, 1)
WE = utc(2024, 2, 1)


def test_missing_uid_reports_component_position():
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//\r\n"
        "BEGIN:VEVENT\r\nDTSTAMP:20240101T000000Z\r\n"
        "DTSTART:20240101T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    with pytest.raises(ValueError, match="index 0"):
        parse(ics).expand_events(WS, WE)


def test_empty_uid_rejected():
    ics = calendar(event("", "DTSTART:20240101T100000Z"))
    with pytest.raises(ValueError, match="empty UID"):
        parse(ics).expand_events(WS, WE)


def test_missing_master_dtstart_rejected():
    ics = calendar(event("x", "SUMMARY:no start"))
    with pytest.raises(ValueError, match="DTSTART"):
        parse(ics).expand_events(WS, WE)


def test_unresolvable_tzid_is_value_error_not_floating():
    ics = calendar(
        event("x", "DTSTART;TZID=Mars/Olympus:20240101T100000")
    )
    with pytest.raises(ValueError, match="TZID"):
        parse(ics).expand_events(WS, WE)


def test_multiple_rrules_rejected():
    ics = calendar(
        event(
            "x",
            "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY\r\nRRULE:FREQ=WEEKLY",
        )
    )
    with pytest.raises(ValueError, match="RRULE"):
        parse(ics).expand_events(WS, WE)


def test_exrule_rejected():
    ics = calendar(
        event("x", "DTSTART:20240101T100000Z\r\nEXRULE:FREQ=DAILY")
    )
    with pytest.raises(ValueError, match="EXRULE"):
        parse(ics).expand_events(WS, WE)


def test_period_rdate_rejected():
    ics = calendar(
        event(
            "x",
            "DTSTART:20240101T100000Z\r\n"
            "RDATE;VALUE=PERIOD:20240201T100000Z/PT1H",
        )
    )
    with pytest.raises(ValueError, match="PERIOD"):
        parse(ics).expand_events(WS, WE)


def test_recurrence_id_range_parameter_rejected():
    master = event(
        "x", "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3"
    )
    exception = (
        "BEGIN:VEVENT\r\nUID:x\r\nDTSTAMP:20240101T000000Z\r\n"
        "RECURRENCE-ID;RANGE=THISANDPRIOR:20240102T100000Z\r\n"
        "DTSTART:20240103T100000Z\r\nEND:VEVENT"
    )
    with pytest.raises(ValueError, match="RANGE"):
        parse(calendar(master, exception)).expand_events(WS, WE)


def test_unsupported_frequencies_rejected():
    for freq in ("SECONDLY", "MINUTELY", "HOURLY"):
        ics = calendar(
            event("x", f"DTSTART:20240101T100000Z\r\nRRULE:FREQ={freq}")
        )
        with pytest.raises(ValueError):
            parse(ics).expand_events(WS, WE)


def test_unsupported_rule_parts_rejected():
    for part in (
        "BYHOUR=10",
        "BYMINUTE=30",
        "BYSECOND=0",
        "BYYEARDAY=100",
        "BYWEEKNO=20",
    ):
        ics = calendar(
            event(
                "x",
                f"DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;{part}",
            )
        )
        with pytest.raises(ValueError):
            parse(ics).expand_events(WS, utc(2025, 1, 1))


def test_invalid_interval_and_count_rejected():
    for rule in (
        "FREQ=DAILY;INTERVAL=0",
        "FREQ=DAILY;COUNT=0",
        "FREQ=DAILY;INTERVAL=-2",
    ):
        ics = calendar(
            event("x", f"DTSTART:20240101T100000Z\r\nRRULE:{rule}")
        )
        with pytest.raises(ValueError):
            parse(ics).expand_events(WS, WE)


def test_count_and_until_together_rejected():
    ics = calendar(
        event(
            "x",
            "DTSTART:20240101T100000Z\r\n"
            "RRULE:FREQ=DAILY;COUNT=2;UNTIL=20240105T100000Z",
        )
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(WS, WE)


def test_validation_runs_even_when_no_occurrence_matches_window():
    # The bad UID has nothing in the queried window but must still be rejected.
    ics = calendar(
        event(
            "far-away-bad",
            "DTSTART:20300101T100000Z\r\nRRULE:FREQ=HOURLY",
        )
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(WS, WE)


def test_failed_query_does_not_affect_later_successful_queries():
    good = parse(
        calendar(
            event(
                "g",
                "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=2",
            )
        )
    )
    bad = parse(
        calendar(
            event("b", "DTSTART;TZID=Mars/Olympus:20240101T100000")
        )
    )
    first = good.expand_events(WS, WE)
    with pytest.raises(ValueError):
        bad.expand_events(WS, WE)
    second = good.expand_events(WS, WE)
    assert first == second


def test_repeated_queries_are_independent_and_identical():
    cal = parse(
        calendar(
            event(
                "r",
                "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3",
            )
        )
    )
    first = cal.expand_events(WS, WE)
    for _ in range(3):
        assert cal.expand_events(WS, WE) == first


def test_input_calendar_is_not_mutated_by_expansion():
    ics = calendar(
        event(
            "r",
            "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\n"
            "EXDATE:20240102T100000Z",
        )
    )
    cal = parse(ics)
    raw_before = cal.to_ical()
    cal.expand_events(WS, WE)
    cal.expand_events(WS, WE, default_tz="America/New_York")
    assert cal.to_ical() == raw_before


def test_window_split_and_merge_equals_single_query():
    ics = calendar(
        event(
            "a",
            "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=10",
        ),
        event("b", "DTSTART:20240103T150000Z"),
    )
    cal = parse(ics)
    whole = cal.expand_events(utc(2024, 1, 1), utc(2024, 1, 12))
    left = cal.expand_events(utc(2024, 1, 1), utc(2024, 1, 6))
    right = cal.expand_events(utc(2024, 1, 6), utc(2024, 1, 12))
    merged = sorted(
        left + right,
        key=lambda o: (
            o.start.isoformat(),
            o.uid,
            o.recurrence_id.isoformat(),
        ),
    )
    assert merged == whole


def test_component_order_does_not_change_result():
    master = event(
        "p",
        "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:m",
    )
    exception = event(
        "p",
        "DTSTART:20240115T100000Z\r\nSUMMARY:x",
        recurrence_id="20240102T100000Z",
    )
    master_first = parse(calendar(master, exception)).expand_events(
        WS, utc(2024, 1, 20)
    )
    exception_first = parse(calendar(exception, master)).expand_events(
        WS, utc(2024, 1, 20)
    )
    assert master_first == exception_first


def test_timezone_provider_is_not_switched_by_expansion():
    from icalendar.timezone import tzp

    before = tzp.name
    cal = parse(
        calendar(
            event(
                "t",
                "DTSTART;TZID=Europe/Berlin:20240101T100000",
            ),
            timezones=BERLIN_VTIMEZONE,
        )
    )
    cal.expand_events(WS, WE)
    assert tzp.name == before


def test_overflow_is_overflow_error_and_keeps_no_truncation():
    ics = calendar(
        event("many", "DTSTART:20240101T000000Z\r\nRRULE:FREQ=DAILY")
    )
    with pytest.raises(OverflowError):
        parse(ics).expand_events(
            WS, utc(2024, 1, 8), max_instances=5
        )


def test_max_instances_boundary_does_not_raise_when_exact():
    ics = calendar(
        event("five", "DTSTART:20240101T000000Z\r\nRRULE:FREQ=DAILY;COUNT=5")
    )
    result = parse(ics).expand_events(WS, utc(2024, 1, 10), max_instances=5)
    assert len(result) == 5


def test_naive_and_date_window_arguments_rejected():
    cal = parse(calendar(event("x", "DTSTART:20240101T100000Z")))
    with pytest.raises(ValueError):
        cal.expand_events(
            datetime(2024, 1, 1), datetime(2024, 1, 2, tzinfo=timezone.utc)
        )
    with pytest.raises(ValueError):
        cal.expand_events(
            datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2)
        )
    # a pure date is not an aware datetime
    import datetime as dt_module

    with pytest.raises(ValueError):
        cal.expand_events(
            dt_module.date(2024, 1, 1),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
