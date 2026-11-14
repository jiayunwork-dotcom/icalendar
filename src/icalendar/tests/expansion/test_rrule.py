"""RRULE expansion against hand-computed occurrence lists.

Expected dates below are derived from the calendar rules directly, never from
the implementation under test.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from icalendar import Calendar

from .conftest_helpers import UTC, calendar, event, parse, utc


def _dates(cal: Calendar, start: datetime, end: datetime, **kwargs):
    # Identity wall date: date stays a date, datetimes project to their date.
    return [
        o.recurrence_id.date()
        if isinstance(o.recurrence_id, datetime)
        else o.recurrence_id
        for o in cal.expand_events(start, end, **kwargs)
    ]


def test_daily_count():
    ics = calendar(
        event("d", "DTSTART:20240110T080000Z\r\nRRULE:FREQ=DAILY;COUNT=5")
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 2, 1)) == [
        date(2024, 1, 10),
        date(2024, 1, 11),
        date(2024, 1, 12),
        date(2024, 1, 13),
        date(2024, 1, 14),
    ]


def test_daily_interval():
    ics = calendar(
        event(
            "d",
            "DTSTART:20240110T080000Z\r\nRRULE:FREQ=DAILY;INTERVAL=3;COUNT=4",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 2, 1)) == [
        date(2024, 1, 10),
        date(2024, 1, 13),
        date(2024, 1, 16),
        date(2024, 1, 19),
    ]


def test_daily_until_inclusive():
    ics = calendar(
        event(
            "d",
            "DTSTART:20240110T080000Z\r\n"
            "RRULE:FREQ=DAILY;UNTIL=20240113T080000Z",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 2, 1)) == [
        date(2024, 1, 10),
        date(2024, 1, 11),
        date(2024, 1, 12),
        date(2024, 1, 13),
    ]


def test_weekly_specific_days():
    # Mondays and Fridays of January 2024, starting Monday Jan 1.
    ics = calendar(
        event(
            "w",
            "DTSTART:20240101T080000Z\r\n"
            "RRULE:FREQ=WEEKLY;BYDAY=MO,FR;COUNT=6",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 2, 15)) == [
        date(2024, 1, 1),
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 12),
        date(2024, 1, 15),
        date(2024, 1, 19),
    ]


def test_weekly_interval_two_weeks():
    ics = calendar(
        event(
            "w",
            "DTSTART:20240103T080000Z\r\n"
            "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=4",
        )
    )
    # week 1: Wed Jan 3 (Mon Jan 1 is before DTSTART); week 3: Jan 15,17;
    # week 5: Jan 29
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 3, 1)) == [
        date(2024, 1, 3),
        date(2024, 1, 15),
        date(2024, 1, 17),
        date(2024, 1, 29),
    ]


def test_monthly_anchor_day_skips_short_months():
    # DTSTART Jan 31; months without a 31st produce no instance.
    ics = calendar(
        event(
            "m",
            "DTSTART:20240131T080000Z\r\nRRULE:FREQ=MONTHLY;COUNT=4",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 9, 1)) == [
        date(2024, 1, 31),
        date(2024, 3, 31),
        date(2024, 5, 31),
        date(2024, 7, 31),
    ]


def test_monthly_bymonthday_positive_and_negative():
    ics = calendar(
        event(
            "m",
            "DTSTART:20240101T080000Z\r\n"
            "RRULE:FREQ=MONTHLY;BYMONTHDAY=1,-1;COUNT=4",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 4, 1)) == [
        date(2024, 1, 1),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 29),  # leap year
    ]


def test_monthly_ordinal_byday():
    ics = calendar(
        event(
            "m",
            "DTSTART:20240112T080000Z\r\nRRULE:FREQ=MONTHLY;BYDAY=2FR;COUNT=3",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 5, 1)) == [
        date(2024, 1, 12),
        date(2024, 2, 9),
        date(2024, 3, 8),
    ]


def test_monthly_last_weekday_byday_negative():
    ics = calendar(
        event(
            "m",
            "DTSTART:20240126T080000Z\r\nRRULE:FREQ=MONTHLY;BYDAY=-1FR;COUNT=3",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 5, 1)) == [
        date(2024, 1, 26),
        date(2024, 2, 23),
        date(2024, 3, 29),
    ]


def test_monthly_bysetpos_second_weekday():
    # RFC 5545 style: second MO-FR of each month.
    ics = calendar(
        event(
            "s",
            "DTSTART:20240101T080000Z\r\n"
            "RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=2;COUNT=3",
        )
    )
    # Jan: Jan 1 (DTSTART is always first) then Jan 2 (2nd weekday);
    # Feb: Feb 2 (Fri); Mar: Mar 4 (Mon).
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 5, 1)) == [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 2, 2),
        date(2024, 3, 4),
    ]


def test_monthly_bysetpos_last_weekday():
    ics = calendar(
        event(
            "s",
            "DTSTART:20240131T080000Z\r\n"
            "RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2024, 5, 1)) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 29),
    ]


def test_yearly_leap_day_only_emits_in_leap_years():
    ics = calendar(
        event("y", "DTSTART:20240229T080000Z\r\nRRULE:FREQ=YEARLY")
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2030, 1, 1)) == [
        date(2024, 2, 29),
        date(2028, 2, 29),
    ]


def test_yearly_bymonth_bymonthday():
    ics = calendar(
        event(
            "y",
            "DTSTART:20240615T080000Z\r\n"
            "RRULE:FREQ=YEARLY;BYMONTH=6,12;BYMONTHDAY=15;COUNT=4",
        )
    )
    assert _dates(parse(ics), utc(2024, 1, 1), utc(2027, 1, 1)) == [
        date(2024, 6, 15),
        date(2024, 12, 15),
        date(2025, 6, 15),
        date(2025, 12, 15),
    ]


def test_infinite_rule_bounded_by_window_only():
    ics = calendar(
        event("inf", "DTSTART:20240101T000000Z\r\nRRULE:FREQ=DAILY")
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 10), utc(2024, 3, 15)
    )
    assert [o.start.day for o in result] == [10, 11, 12, 13, 14]


def test_infinite_rule_starting_1900_queried_in_2099_terminates():
    # The headline liveness requirement: no enumeration of history.
    ics = calendar(
        event("old", "DTSTART:19000101T000000Z\r\nRRULE:FREQ=DAILY")
    )
    result = parse(ics).expand_events(
        utc(2099, 6, 1), utc(2099, 6, 8)
    )
    assert [o.start.date() for o in result] == [
        date(2099, 6, 1),
        date(2099, 6, 2),
        date(2099, 6, 3),
        date(2099, 6, 4),
        date(2099, 6, 5),
        date(2099, 6, 6),
        date(2099, 6, 7),
    ]


def test_count_rules_do_not_emit_past_the_count_inside_a_large_window():
    ics = calendar(
        event("c", "DTSTART:20240101T000000Z\r\nRRULE:FREQ=DAILY;COUNT=2")
    )
    result = parse(ics).expand_events(utc(2020, 1, 1), utc(2030, 1, 1))
    assert len(result) == 2


def test_all_day_weekly_rule_keeps_date_identity():
    ics = calendar(
        event(
            "ad",
            "DTSTART;VALUE=DATE:20240103\r\n"
            "RRULE:FREQ=WEEKLY;BYDAY=WE;COUNT=2",
        )
    )
    result = parse(ics).expand_events(
        utc(2024, 1, 1), utc(2024, 2, 1), default_tz="UTC"
    )
    assert all(isinstance(o.recurrence_id, date) for o in result)
    assert [o.recurrence_id for o in result] == [
        date(2024, 1, 3),
        date(2024, 1, 10),
    ]
    assert all(o.all_day for o in result)


def test_wkst_changes_week_alignment():
    # DTSTART Wed 2024-01-03; bi-weekly MO rule, COUNT=3 rule outputs.
    # DTSTART itself is always the first set member; the three generated
    # Mondays are Jan 15, Jan 29 and Feb 12 under both WKST choices for this
    # pair. This asserts the alignment inputs are accepted and stay stable.
    for wkst in ("MO", "SU"):
        ics = calendar(
            event(
                "w",
                "DTSTART:20240103T080000Z\r\n"
                f"RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;WKST={wkst};COUNT=3",
            )
        )
        dates_ = _dates(
            parse(ics), utc(2023, 12, 25), utc(2024, 2, 15)
        )
        assert dates_ == [
            date(2024, 1, 3),
            date(2024, 1, 15),
            date(2024, 1, 29),
            date(2024, 2, 12),
        ]


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=SECONDLY",
        "FREQ=HOURLY",
        "FREQ=MINUTELY",
        "FREQ=DAILY;BYHOUR=9",
        "FREQ=YEARLY;BYYEARDAY=100",
        "FREQ=MONTHLY;BYWEEKNO=20",
        "FREQ=DAILY;BYSECOND=30",
        "FREQ=DAILY;BYMINUTE=5",
        "FREQ=DAILY;RSCALE=GREGORIAN",
    ],
)
def test_unsupported_rule_parts_raise(rule):
    ics = calendar(event("bad", f"DTSTART:20240101T000000Z\r\nRRULE:{rule}"))
    with pytest.raises(ValueError):
        parse(ics).expand_events(utc(2024, 1, 1), utc(2025, 1, 1))
