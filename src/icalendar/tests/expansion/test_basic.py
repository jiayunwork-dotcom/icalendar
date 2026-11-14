"""Core expansion behavior: records, selection, ordering and identity."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from icalendar import Calendar, Occurrence

from .conftest_helpers import (
    BERLIN,
    BERLIN_VTIMEZONE,
    UTC,
    calendar,
    event,
    parse,
    utc,
)

WINDOW_START = utc(2024, 1, 1)
WINDOW_END = utc(2024, 2, 1)


def _rows(occurrences):
    """Project occurrences to comparable, hand-checkable tuples."""
    return [
        (
            o.uid,
            o.recurrence_id,
            o.start,
            o.end,
            o.summary,
            o.all_day,
        )
        for o in occurrences
    ]


def test_empty_calendar_returns_empty_list():
    cal = parse(calendar())
    assert cal.expand_events(WINDOW_START, WINDOW_END) == []


def test_non_recurring_utc_event_all_fields():
    ics = calendar(
        event(
            "one",
            "DTSTART:20240110T100000Z\r\n"
            "DTEND:20240110T113000Z\r\n"
            "SUMMARY:Single",
        )
    )
    result = parse(ics).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 11)
    )
    assert _rows(result) == [
        (
            "one",
            utc(2024, 1, 10, 10),
            utc(2024, 1, 10, 10),
            utc(2024, 1, 10, 11, 30),
            "Single",
            False,
        )
    ]


def test_missing_summary_defaults_to_empty_string():
    ics = calendar(event("nosummary", "DTSTART:20240110T100000Z"))
    result = parse(ics).expand_events(utc(2024, 1, 10), utc(2024, 1, 11))
    assert result[0].summary == ""
    assert isinstance(result[0].summary, str)


def test_timed_event_without_end_is_zero_length():
    ics = calendar(event("zero", "DTSTART:20240110T100000Z"))
    result = parse(ics).expand_events(utc(2024, 1, 10), utc(2024, 1, 11))
    occurrence = result[0]
    assert occurrence.start == occurrence.end == utc(2024, 1, 10, 10)
    assert occurrence.all_day is False


def test_all_day_event_defaults_to_one_calendar_day():
    ics = calendar(
        event("day", "DTSTART;VALUE=DATE:20240115\r\nSUMMARY:Holiday")
    )
    result = parse(ics).expand_events(
        utc(2024, 1, 15), utc(2024, 1, 16), default_tz="UTC"
    )
    occurrence = result[0]
    assert occurrence.all_day is True
    assert occurrence.recurrence_id == date(2024, 1, 15)
    assert occurrence.start == utc(2024, 1, 15)
    assert occurrence.end == utc(2024, 1, 16)


def test_occurrence_is_readonly():
    ics = calendar(event("ro", "DTSTART:20240110T100000Z"))
    occurrence = parse(ics).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 11)
    )[0]
    with pytest.raises(FrozenInstanceError):
        occurrence.summary = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        occurrence.uid = "other"  # type: ignore[misc]
    assert tuple(occurrence.keys()) == (
        "uid",
        "recurrence_id",
        "start",
        "end",
        "summary",
        "all_day",
    )
    assert occurrence["uid"] == "ro"
    with pytest.raises(KeyError):
        occurrence["unknown"]  # noqa: B018
    assert dict(occurrence) == occurrence.as_dict()


def test_window_is_half_open_on_final_start():
    # occurrence at exactly window_end is excluded; at window_start included
    ics = calendar(
        event("edge", "DTSTART:20240115T000000Z\r\nDTEND:20240116T000000Z")
    )
    cal = parse(ics)
    inside = cal.expand_events(utc(2024, 1, 15), utc(2024, 1, 16))
    assert len(inside) == 1
    on_boundary = cal.expand_events(utc(2024, 1, 14), utc(2024, 1, 15))
    assert on_boundary == []


def test_selection_uses_start_not_interval_overlap():
    # An event starting before the window but ending inside it is excluded.
    ics = calendar(
        event("long", "DTSTART:20240109T230000Z\r\nDTEND:20240110T010000Z")
    )
    cal = parse(ics)
    assert cal.expand_events(utc(2024, 1, 10), utc(2024, 1, 11)) == []
    assert len(cal.expand_events(utc(2024, 1, 9), utc(2024, 1, 10))) == 1


def test_results_sorted_by_start_uid_recurrence_id():
    ics = calendar(
        event(
            "zeta",
            "DTSTART:20240105T100000Z\r\nRRULE:FREQ=DAILY;COUNT=2",
        ),
        event("alpha", "DTSTART:20240105T100000Z"),
        event("beta", "DTSTART:20240103T100000Z"),
    )
    result = parse(ics).expand_events(utc(2024, 1, 1), utc(2024, 2, 1))
    keys = [
        (o.start.isoformat(), o.uid, o.recurrence_id.isoformat())
        for o in result
    ]
    assert keys == sorted(keys)
    # same start, UID ordering decides
    same_start = [o for o in result if o.start == utc(2024, 1, 5, 10)]
    assert [o.uid for o in same_start] == ["alpha", "zeta"]


def test_multiple_uids_are_independent():
    ics = calendar(
        event(
            "alpha",
            "DTSTART;VALUE=DATE:20240101\r\nRRULE:FREQ=DAILY;COUNT=2\r\n"
            "SUMMARY:A",
        ),
        event("beta", "DTSTART;VALUE=DATE:20240103\r\nSUMMARY:B"),
    )
    result = parse(ics).expand_events(
        utc(2024, 1, 1), utc(2024, 1, 10), default_tz="UTC"
    )
    by_uid: dict[str, list] = {}
    for occurrence in result:
        by_uid.setdefault(occurrence.uid, []).append(
            occurrence.recurrence_id
        )
    assert by_uid == {
        "alpha": [date(2024, 1, 1), date(2024, 1, 2)],
        "beta": [date(2024, 1, 3)],
    }


def test_window_endpoints_accept_other_aware_timezones():
    ics = calendar(event("w", "DTSTART:20240110T120000Z"))
    cal = parse(ics)
    # window expressed in Berlin time: 2024-01-10 13:00 +01:00 == 12:00 UTC
    start = datetime(2024, 1, 10, 13, 0, tzinfo=BERLIN)
    end = datetime(2024, 1, 11, 13, 0, tzinfo=BERLIN)
    result = cal.expand_events(start, end)
    assert len(result) == 1
    assert result[0].start.tzinfo == UTC


def test_naive_window_is_rejected():
    cal = parse(calendar(event("w", "DTSTART:20240110T100000Z")))
    with pytest.raises(ValueError):
        cal.expand_events(
            datetime(2024, 1, 10), datetime(2024, 1, 11, tzinfo=timezone.utc)
        )
    with pytest.raises(ValueError):
        cal.expand_events(
            datetime(2024, 1, 10, tzinfo=timezone.utc), datetime(2024, 1, 11)
        )


def test_reversed_window_is_rejected():
    cal = parse(calendar(event("w", "DTSTART:20240110T100000Z")))
    with pytest.raises(ValueError):
        cal.expand_events(utc(2024, 1, 11), utc(2024, 1, 10))


def test_equal_window_bounds_are_rejected():
    cal = parse(calendar(event("w", "DTSTART:20240110T100000Z")))
    with pytest.raises(ValueError):
        cal.expand_events(utc(2024, 1, 10), utc(2024, 1, 10))


@pytest.mark.parametrize("bad_tz", ["", "Not/AZone", "Mars/Olympus", 123])
def test_bad_default_tz_rejected(bad_tz):
    cal = parse(calendar(event("w", "DTSTART:20240110T100000Z")))
    with pytest.raises(ValueError):
        cal.expand_events(utc(2024, 1, 10), utc(2024, 1, 11), default_tz=bad_tz)


@pytest.mark.parametrize("bad_cap", [0, -1, True, False, 1.5, "10"])
def test_bad_max_instances_rejected(bad_cap):
    cal = parse(calendar(event("w", "DTSTART:20240110T100000Z")))
    with pytest.raises(ValueError):
        cal.expand_events(
            utc(2024, 1, 10), utc(2024, 1, 11), max_instances=bad_cap
        )


def test_overflow_raises_instead_of_truncating():
    ics = calendar(
        event(
            "many",
            "DTSTART:20240101T000000Z\r\nRRULE:FREQ=DAILY",
        )
    )
    cal = parse(ics)
    with pytest.raises(OverflowError):
        cal.expand_events(
            utc(2024, 1, 1), utc(2024, 1, 6), max_instances=3
        )


def test_other_components_are_ignored():
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
        "BEGIN:VTODO\r\nUID:todo-1\r\nDTSTAMP:20240101T000000Z\r\n"
        "DTSTART:20240110T100000Z\r\nEND:VTODO\r\n"
        + event("real", "DTSTART:20240110T100000Z")
        + "\r\nEND:VCALENDAR\r\n"
    )
    result = parse(ics).expand_events(utc(2024, 1, 10), utc(2024, 1, 11))
    assert [o.uid for o in result] == ["real"]


def test_occurrence_is_exported_from_top_level_package():
    import icalendar

    assert icalendar.Occurrence is Occurrence
