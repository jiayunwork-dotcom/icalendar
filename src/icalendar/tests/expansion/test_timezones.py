"""Time zone and DST semantics for expansion.

Expected UTC instants are computed from the wall-clock rules by hand:
- Europe/Berlin 2024 spring gap: 02:00 -> 03:00 on March 31.
- Europe/Berlin 2024 fall-back: 03:00 -> 02:00 on October 27.
- America/New_York 2024 spring gap: 02:00 -> 03:00 on March 10.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from icalendar import Calendar

from .conftest_helpers import (
    BERLIN,
    BERLIN_VTIMEZONE,
    CUSTOM_VTIMEZONE,
    NEW_YORK,
    NEW_YORK_VTIMEZONE,
    UTC,
    calendar,
    event,
    parse,
    utc,
)


def test_berlin_spring_gap_skips_nonexistent_rule_time():
    # Daily at 02:30 Berlin starting Mar 30 (02:30 CET exists). Mar 31 02:30
    # does not exist; COUNT=3 survivors are Mar 30, Apr 1, Apr 2.
    ics = calendar(
        event(
            "gap",
            "DTSTART;TZID=Europe/Berlin:20240330T023000\r\n"
            "RRULE:FREQ=DAILY;COUNT=3",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 3)
    )
    expected_starts = [
        utc(2024, 3, 30, 1, 30),  # 02:30 CET (+01:00)
        utc(2024, 4, 1, 0, 30),   # 02:30 CEST (+02:00)
        utc(2024, 4, 2, 0, 30),
    ]
    assert [o.start for o in result] == expected_starts
    # the gap day produced no record
    assert all(o.start.date() != date(2024, 3, 31) for o in result)


def test_berlin_spring_gap_does_not_consume_count():
    # If the gap consumed COUNT, only two survivors would appear.
    ics = calendar(
        event(
            "gap",
            "DTSTART;TZID=Europe/Berlin:20240330T023000\r\n"
            "RRULE:FREQ=DAILY;COUNT=3",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 4)
    )
    assert len(result) == 3


def test_berlin_fall_back_uses_first_occurrence():
    # Daily at 02:30 Berlin from Oct 26. Oct 27 02:30 is ambiguous; the first
    # occurrence is 02:30 CEST (+02:00) == 00:30 UTC.
    ics = calendar(
        event(
            "fall",
            "DTSTART;TZID=Europe/Berlin:20241026T023000\r\n"
            "RRULE:FREQ=DAILY;COUNT=3",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 10, 25), utc(2024, 10, 30)
    )
    assert [o.start for o in result] == [
        utc(2024, 10, 26, 0, 30),  # CEST
        utc(2024, 10, 27, 0, 30),  # first occurrence: still CEST
        utc(2024, 10, 28, 1, 30),  # CET
    ]


def test_explicit_dtstart_in_a_gap_is_interpreted_not_skipped():
    # Explicit DATE-TIME values follow RFC 3.3.5, not rule gap skipping.
    # 02:30 on Mar 31 Berlin does not exist; it is shifted forward past the
    # gap to 03:30 CEST == 01:30 UTC.
    ics = calendar(
        event(
            "explicit",
            "DTSTART;TZID=Europe/Berlin:20240331T023000",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 30), utc(2024, 4, 1)
    )
    assert len(result) == 1
    assert result[0].start == utc(2024, 3, 31, 1, 30)


def test_new_york_spring_gap_skips_rule_time():
    # NYC jumps 02:00 -> 03:00 on Mar 10 2024. Daily 02:30 from Mar 9.
    ics = calendar(
        event(
            "ny",
            "DTSTART;TZID=America/New_York:20240309T023000\r\n"
            "RRULE:FREQ=DAILY;COUNT=3",
        ),
        timezones=NEW_YORK_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 8), utc(2024, 3, 13)
    )
    assert [o.start for o in result] == [
        utc(2024, 3, 9, 7, 30),   # EST (-05:00)
        utc(2024, 3, 11, 6, 30),  # EDT (-04:00), Mar 10 skipped
        utc(2024, 3, 12, 6, 30),
    ]


def test_floating_datetime_uses_default_tz_not_host_zone():
    ics = calendar(
        event(
            "float",
            "DTSTART:20240330T023000\r\nRRULE:FREQ=DAILY;COUNT=2",
        )
    )
    # With Berlin default, the Mar 31 gap skips the second rule value.
    berlin_result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 3), default_tz="Europe/Berlin"
    )
    assert [o.start for o in berlin_result] == [
        utc(2024, 3, 30, 1, 30),
        utc(2024, 4, 1, 0, 30),
    ]
    # With New York default there is no gap on those dates; both survive.
    ny_result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 3), default_tz="America/New_York"
    )
    assert [o.start for o in ny_result] == [
        utc(2024, 3, 30, 6, 30),
        utc(2024, 3, 31, 6, 30),
    ]


def test_all_day_boundaries_follow_default_tz_across_dst():
    # All-day events are midnight-to-midnight in the default zone. The
    # Mar 31 Berlin day is only 23 hours long.
    ics = calendar(
        event(
            "allday",
            "DTSTART;VALUE=DATE:20240330\r\nRRULE:FREQ=DAILY;COUNT=2",
        )
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 2), default_tz="Europe/Berlin"
    )
    first, second = result
    assert first.recurrence_id == date(2024, 3, 30)
    assert first.start == utc(2024, 3, 29, 23)  # midnight CET
    assert first.end == utc(2024, 3, 30, 23)    # 24h later
    assert (first.end - first.start) == timedelta(hours=24)
    assert second.recurrence_id == date(2024, 3, 31)
    assert second.start == utc(2024, 3, 30, 23)  # midnight CEST
    assert second.end == utc(2024, 3, 31, 22)
    assert (second.end - second.start) == timedelta(hours=23)


def test_custom_vtimezone_resolves_and_skips_its_gap():
    # The custom zone mirrors Berlin transitions but is defined purely via
    # an inline VTIMEZONE with a non-IANA TZID.
    ics = calendar(
        event(
            "custom",
            "DTSTART;TZID=Custom/TwoOffsets:20240330T023000\r\n"
            "RRULE:FREQ=DAILY;COUNT=3",
        ),
        timezones=CUSTOM_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 3)
    )
    assert [o.start for o in result] == [
        utc(2024, 3, 30, 1, 30),
        utc(2024, 4, 1, 0, 30),
        utc(2024, 4, 2, 0, 30),
    ]


def test_unresolvable_tzid_raises_not_floating():
    ics = calendar(
        event("bad", "DTSTART;TZID=Mars/Olympus:20240101T100000")
    )
    with pytest.raises(ValueError):
        parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))


def test_utc_and_equivalent_tzid_expressions_match():
    # 10:00 UTC == 12:00 Berlin in June (no transition nearby).
    ics = calendar(
        event("u", "DTSTART:20240601T100000Z\r\nSUMMARY:u"),
        event(
            "z",
            "DTSTART;TZID=Europe/Berlin:20240601T120000\r\nSUMMARY:z",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 5, 1), utc(2024, 7, 1)
    )
    starts = {o.uid: o.start for o in result}
    assert starts["u"] == starts["z"] == utc(2024, 6, 1, 10)


def test_rule_keeps_local_wall_time_after_dst_change():
    # A Berlin 09:00 daily rule stays at 09:00 local before and after the
    # March 31 transition; only the UTC offset changes.
    ics = calendar(
        event(
            "wall",
            "DTSTART;TZID=Europe/Berlin:20240328T090000\r\n"
            "RRULE:FREQ=DAILY;COUNT=5",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 27), utc(2024, 4, 3)
    )
    local_hours = [
        o.start.astimezone(BERLIN).replace(tzinfo=None)
        for o in result
    ]
    assert all(d.hour == 9 and d.minute == 0 for d in local_hours)
    assert [o.start.astimezone(BERLIN).utcoffset() for o in result] == [
        timedelta(hours=1),
        timedelta(hours=1),
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=2),
    ]


def test_duration_p1d_vs_pt24h_differ_across_gap():
    # At a noon start the nominal P1D keeps the 12:00 wall clock (23 measured
    # hours over the gap), while an exact PT24H is a fixed 24-hour offset that
    # lands at 13:00 local. The two spellings therefore diverge observably.
    base = (
        "DTSTAMP:20240101T000000Z\r\n"
        "DTSTART;TZID=Europe/Berlin:20240330T120000\r\nDURATION:{duration}"
    )
    ics = calendar(
        event("nominal", base.format(duration="P1D")),
        event("exact", base.format(duration="PT24H")),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 3)
    )
    by_uid = {o.uid: o for o in result}
    nominal = by_uid["nominal"]
    exact = by_uid["exact"]
    assert nominal.end.astimezone(BERLIN).replace(tzinfo=None) == datetime(
        2024, 3, 31, 12, 0
    )
    assert (nominal.end - nominal.start) == timedelta(hours=23)
    assert exact.end.astimezone(BERLIN).replace(tzinfo=None) == datetime(
        2024, 3, 31, 13, 0
    )
    assert (exact.end - exact.start) == timedelta(hours=24)
    assert exact.end != nominal.end


def test_explicit_rdate_in_gap_is_not_skipped():
    # Rule-generated values skip gaps; explicit RDATE values never do.
    ics = calendar(
        event(
            "rdate-gap",
            "DTSTART;TZID=Europe/Berlin:20240330T023000\r\n"
            "RDATE;TZID=Europe/Berlin:20240331T023000",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 2)
    )
    starts = sorted(o.start for o in result)
    # Mar 30 02:30 CET and the explicit (gap-shifted) Mar 31 03:30 CEST.
    assert starts == [
        utc(2024, 3, 30, 1, 30),
        utc(2024, 3, 31, 1, 30),
    ]


def test_explicit_exdate_in_gap_still_excludes_by_identity():
    # An EXDATE naming a gap wall time references that identity explicitly;
    # the rule would skip it anyway, but a matching RDATE identity must be
    # removed because EXDATE wins.
    ics = calendar(
        event(
            "ex",
            "DTSTART;TZID=Europe/Berlin:20240330T023000\r\n"
            "RDATE;TZID=Europe/Berlin:20240331T023000\r\n"
            "EXDATE;TZID=Europe/Berlin:20240331T023000",
        ),
        timezones=BERLIN_VTIMEZONE,
    )
    result = parse(ics).expand_events(
        utc(2024, 3, 29), utc(2024, 4, 2)
    )
    assert [o.start for o in result] == [utc(2024, 3, 30, 1, 30)]


def test_window_timezone_does_not_change_occurrence_utc_instants():
    ics = calendar(
        event(
            "fixed",
            "DTSTART;TZID=America/New_York:20240601T090000",
        ),
        timezones=NEW_YORK_VTIMEZONE,
    )
    cal = parse(ics)
    via_utc = cal.expand_events(
        datetime(2024, 6, 1, 13, 0, tzinfo=UTC),
        datetime(2024, 6, 2, 13, 0, tzinfo=UTC),
    )
    via_berlin = cal.expand_events(
        datetime(2024, 6, 1, 15, 0, tzinfo=BERLIN),
        datetime(2024, 6, 2, 15, 0, tzinfo=BERLIN),
    )
    assert via_utc == via_berlin
    assert via_utc[0].start == utc(2024, 6, 1, 13)
