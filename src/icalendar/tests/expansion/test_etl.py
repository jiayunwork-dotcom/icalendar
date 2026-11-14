"""The ETL row-builder example adapter."""

from __future__ import annotations

from datetime import date

import pytest

from icalendar import Calendar
from icalendar.expansion.etl import occurrence_to_row, occurrences_to_rows

from .conftest_helpers import calendar, event, parse, utc


def test_occurrence_to_row_fields():
    ics = calendar(
        event(
            "meet@example.com",
            "DTSTART:20240110T100000Z\r\nDTEND:20240110T110000Z\r\n"
            "SUMMARY:Planning",
        )
    )
    occurrence = parse(ics).expand_events(
        utc(2024, 1, 10), utc(2024, 1, 11)
    )[0]
    row = occurrence_to_row(occurrence)
    assert row == {
        "uid": "meet@example.com",
        "recurrence_id": "2024-01-10T10:00:00+00:00",
        "start_utc": "2024-01-10T10:00:00+00:00",
        "end_utc": "2024-01-10T11:00:00+00:00",
        "summary": "Planning",
        "all_day": False,
    }


def test_all_day_row_keeps_date_recurrence_id():
    ics = calendar(
        event("hol", "DTSTART;VALUE=DATE:20240115\r\nSUMMARY:Holiday")
    )
    occurrence = parse(ics).expand_events(
        utc(2024, 1, 15), utc(2024, 1, 16)
    )[0]
    row = occurrence_to_row(occurrence)
    assert row["recurrence_id"] == "2024-01-15"
    assert isinstance(occurrence.recurrence_id, date)
    assert row["all_day"] is True


def test_occurrences_to_rows_round_trips_a_full_window():
    ics = calendar(
        event(
            "series",
            "DTSTART:20240101T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3",
        )
    )
    cal = parse(ics)
    rows = occurrences_to_rows(
        cal.expand_events(utc(2024, 1, 1), utc(2024, 1, 10))
    )
    assert len(rows) == 3
    assert [row["start_utc"] for row in rows] == [
        "2024-01-01T10:00:00+00:00",
        "2024-01-02T10:00:00+00:00",
        "2024-01-03T10:00:00+00:00",
    ]
    # rows are plain JSON-friendly primitives
    import json

    json.dumps(rows)


def test_row_builder_rejects_non_occurrence():
    with pytest.raises(TypeError):
        occurrence_to_row({"uid": "not an occurrence"})  # type: ignore[arg-type]
