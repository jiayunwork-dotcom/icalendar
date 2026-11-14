"""RDATE/EXDATE inclusion, exclusion and VEVENT exception merging."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from icalendar import Calendar

from .conftest_helpers import UTC, calendar, event, parse, utc


def test_rdate_adds_occurrences_without_a_rule():
    ics = calendar(
        event(
            "r",
            "DTSTART:20240101T100000Z\r\n"
            "RDATE:20240103T100000Z,20240105T100000Z",
        )
    )
    result = parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))
    assert [o.start.day for o in result] == [1, 3, 5]


def test_rdate_dedupes_against_rule_output():
    ics = calendar(
        event(
            "r",
            "DTSTART:20240101T100000Z\r\n"
            "RRULE:FREQ=DAILY;COUNT=4\r\n"
            "RDATE:20240102T100000Z,20240105T100000Z",
        )
    )
    result = parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))
    # rule gives 1-4; RDATE duplicates the 2nd and adds the 5th.
    assert [o.start.day for o in result] == [1, 2, 3, 4, 5]


def test_exdate_removes_first_occurrence():
    ics = calendar(
        event(
            "e",
            "DTSTART:20240101T100000Z\r\n"
            "RRULE:FREQ=DAILY;COUNT=3\r\n"
            "EXDATE:20240101T100000Z",
        )
    )
    result = parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))
    assert [o.start.day for o in result] == [2, 3]


def test_exdate_wins_over_rdate():
    ics = calendar(
        event(
            "e",
            "DTSTART:20240101T100000Z\r\n"
            "RDATE:20240103T100000Z\r\n"
            "EXDATE:20240103T100000Z",
        )
    )
    result = parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))
    assert [o.start.day for o in result] == [1]


def test_exdate_does_not_change_count_of_rule():
    # COUNT=3 rule, first excluded: the rule still generates its three
    # survivors on the 1st, 2nd and 3rd; removing the first leaves two.
    ics = calendar(
        event(
            "e",
            "DTSTART:20240101T100000Z\r\n"
            "RRULE:FREQ=DAILY;COUNT=3\r\n"
            "EXDATE:20240101T100000Z",
        )
    )
    result = parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))
    assert [o.start.day for o in result] == [2, 3]


def test_all_day_rdate_and_exdate_use_date_identity():
    ics = calendar(
        event(
            "ad",
            "DTSTART;VALUE=DATE:20240101\r\n"
            "RRULE:FREQ=DAILY;COUNT=4\r\n"
            "RDATE;VALUE=DATE:20240106\r\n"
            "EXDATE;VALUE=DATE:20240102",
        )
    )
    result = parse(ics).expand_events(
        utc(2024, 1, 1), utc(2024, 2, 1), default_tz="UTC"
    )
    assert [o.recurrence_id for o in result] == [
        date(2024, 1, 1),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 6),
    ]
    assert all(o.all_day for o in result)


def test_rescheduled_exception_uses_original_identity():
    master = event(
        "move",
        "DTSTART:20240110T100000Z\r\n"
        "RRULE:FREQ=DAILY;COUNT=4\r\n"
        "SUMMARY:Regular",
    )
    exception = event(
        "move",
        "DTSTART:20240120T150000Z\r\nDTEND:20240120T160000Z\r\n"
        "SUMMARY:Rescheduled",
        recurrence_id="20240111T100000Z",
    )
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 1), utc(2024, 2, 1)
    )
    moved = next(o for o in result if o.summary == "Rescheduled")
    # identity stays on the original slot; final start moved.
    assert moved.recurrence_id == utc(2024, 1, 11, 10)
    assert moved.start == utc(2024, 1, 20, 15)
    assert moved.end == utc(2024, 1, 20, 16)


def test_exception_moved_out_of_window_leaves_no_record():
    master = event(
        "out",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3",
    )
    exception = event(
        "out",
        "DTSTART:20240220T100000Z",
        recurrence_id="20240111T100000Z",
    )
    # Window covers January only; the rescheduled identity now lives in Feb.
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 20)
    )
    assert [o.recurrence_id for o in result] == [
        utc(2024, 1, 10, 10),
        utc(2024, 1, 12, 10),
    ]


def test_exception_moved_into_window_is_included():
    master = event(
        "into",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3",
    )
    # Original identity Jan 11; moved to Jan 15, inside a window that does
    # not contain the original Jan 11 slot.
    exception = event(
        "into",
        "DTSTART:20240115T100000Z",
        recurrence_id="20240111T100000Z",
    )
    # Original identity Jan 11; moved to Jan 15. A window covering only
    # Jan 13-16 contains the moved occurrence but none of the original slots.
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 13), utc(2024, 1, 17)
    )
    assert len(result) == 1
    assert result[0].recurrence_id == utc(2024, 1, 11, 10)
    assert result[0].start == utc(2024, 1, 15, 10)


def test_two_identities_moved_to_same_instant_both_kept():
    master = event(
        "twins",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=2",
    )
    first = event(
        "twins",
        "DTSTART:20240115T120000Z\r\nSUMMARY:first",
        recurrence_id="20240110T100000Z",
    )
    second = event(
        "twins",
        "DTSTART:20240115T120000Z\r\nSUMMARY:second",
        recurrence_id="20240111T100000Z",
    )
    result = parse(calendar(master, first, second)).expand_events(
        utc(2024, 1, 14), utc(2024, 1, 16)
    )
    assert len(result) == 2
    assert {o.recurrence_id for o in result} == {
        utc(2024, 1, 10, 10),
        utc(2024, 1, 11, 10),
    }
    assert all(o.start == utc(2024, 1, 15, 12) for o in result)
    assert {o.summary for o in result} == {"first", "second"}


def test_cancelled_exception_removes_only_that_identity():
    master = event(
        "cancel",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3",
    )
    cancelled = event(
        "cancel",
        "STATUS:CANCELLED",
        recurrence_id="20240111T100000Z",
    )
    result = parse(calendar(master, cancelled)).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 20)
    )
    assert [o.recurrence_id for o in result] == [
        utc(2024, 1, 10, 10),
        utc(2024, 1, 12, 10),
    ]


def test_cancelled_master_emits_nothing():
    master = event(
        "dead",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\n"
        "STATUS:CANCELLED",
    )
    assert parse(calendar(master)).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 20)
    ) == []


def test_exception_without_summary_inherits_master_summary():
    master = event(
        "inh",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=2\r\n"
        "SUMMARY:Master text",
    )
    exception = event(
        "inh",
        "DTSTART:20240111T110000Z",
        recurrence_id="20240111T100000Z",
    )
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 20)
    )
    assert {o.summary for o in result} == {"Master text"}


def test_exception_explicit_empty_summary_is_preserved():
    master = event(
        "empty",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=2\r\n"
        "SUMMARY:Master text",
    )
    exception = event(
        "empty",
        "DTSTART:20240111T110000Z\r\nSUMMARY:",
        recurrence_id="20240111T100000Z",
    )
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 20)
    )
    summaries = {o.recurrence_id: o.summary for o in result}
    assert summaries[utc(2024, 1, 10, 10)] == "Master text"
    assert summaries[utc(2024, 1, 11, 10)] == ""


def test_exception_without_end_inherits_master_duration():
    master = event(
        "dur",
        "DTSTART:20240110T100000Z\r\nDTEND:20240110T123000Z\r\n"
        "RRULE:FREQ=DAILY;COUNT=2",
    )
    exception = event(
        "dur",
        # moved one hour later, no DTEND: keep the master's 2h30m duration
        "DTSTART:20240111T110000Z",
        recurrence_id="20240111T100000Z",
    )
    result = parse(calendar(master, exception)).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 20)
    )
    moved = next(o for o in result if o.recurrence_id == utc(2024, 1, 11, 10))
    assert moved.start == utc(2024, 1, 11, 11)
    assert moved.end == utc(2024, 1, 11, 13, 30)


def test_exception_recurrence_id_must_match_a_real_identity():
    master = event(
        "bad",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=2",
    )
    # Jan 20 is not generated by the COUNT=2 daily set (10th, 11th).
    exception = event(
        "bad",
        "DTSTART:20240115T100000Z",
        recurrence_id="20240120T100000Z",
    )
    with pytest.raises(ValueError):
        parse(calendar(master, exception)).expand_events(
            utc(2024, 1, 10), utc(2024, 2, 1)
        )


def test_exception_recurrence_id_excluded_by_exdate_rejected():
    master = event(
        "bad",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\n"
        "EXDATE:20240111T100000Z",
    )
    exception = event(
        "bad",
        "DTSTART:20240115T100000Z",
        recurrence_id="20240111T100000Z",
    )
    with pytest.raises(ValueError):
        parse(calendar(master, exception)).expand_events(
            utc(2024, 1, 10), utc(2024, 2, 1)
        )


def test_duplicate_exception_identity_rejected():
    master = event(
        "dup",
        "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3",
    )
    one = event(
        "dup", "DTSTART:20240112T100000Z", recurrence_id="20240111T100000Z"
    )
    two = event(
        "dup", "DTSTART:20240113T100000Z", recurrence_id="20240111T100000Z"
    )
    with pytest.raises(ValueError):
        parse(calendar(master, one, two)).expand_events(
            utc(2024, 1, 10), utc(2024, 2, 1)
        )


def test_duplicate_master_rejected():
    first = event("twom", "DTSTART:20240110T100000Z")
    second = event("twom", "DTSTART:20240111T100000Z")
    with pytest.raises(ValueError):
        parse(calendar(first, second)).expand_events(
            utc(2024, 1, 10), utc(2024, 1, 20)
        )


def test_exception_with_recurrence_properties_rejected():
    master = event(
        "x", "DTSTART:20240110T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3"
    )
    for prop in ("RRULE:FREQ=DAILY", "RDATE:20240115T100000Z",
                 "EXDATE:20240111T100000Z"):
        exception = event(
            "x", f"DTSTART:20240112T100000Z\r\n{prop}",
            recurrence_id="20240111T100000Z",
        )
        with pytest.raises(ValueError):
            parse(calendar(master, exception)).expand_events(
                utc(2024, 1, 10), utc(2024, 2, 1)
            )
