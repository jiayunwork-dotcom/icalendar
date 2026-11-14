"""Collection helper for turning occurrences into ETL row dictionaries.

This is the small, dependency-free adapter referenced in the expansion
how-to guide. It converts an :class:`~icalendar.expansion.occurrence.Occurrence`
into a plain dictionary whose values are JSON-serializable strings/bools,
which is the typical input shape for data warehouses and event pipelines.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from icalendar.expansion.occurrence import Occurrence


def occurrence_to_row(occurrence: Occurrence) -> dict[str, Any]:
    """Convert one occurrence into an ETL row dictionary.

    The datetimes are rendered as ISO 8601 strings in UTC and the all-day
    recurrence id keeps its ``YYYY-MM-DD`` form::

        {
            "uid": "meet@example.com",
            "recurrence_id": "2024-01-02T10:00:00+00:00",
            "start_utc": "2024-01-15T12:00:00+00:00",
            "end_utc": "2024-01-15T13:00:00+00:00",
            "summary": "Rescheduled planning",
            "all_day": False,
        }

    Raises:
        TypeError: If ``occurrence`` is not an :class:`Occurrence`.
    """
    if not isinstance(occurrence, Occurrence):
        msg = f"expected an Occurrence, got {type(occurrence).__name__}"
        raise TypeError(msg)
    return {
        "uid": occurrence.uid,
        "recurrence_id": occurrence.recurrence_id.isoformat(),
        "start_utc": occurrence.start.isoformat(),
        "end_utc": occurrence.end.isoformat(),
        "summary": occurrence.summary,
        "all_day": occurrence.all_day,
    }


def occurrences_to_rows(
    occurrences: Iterable[Occurrence],
) -> list[dict[str, Any]]:
    """Convert an iterable of occurrences into a list of ETL row dictionaries."""
    return [occurrence_to_row(occurrence) for occurrence in occurrences]


__all__ = ["occurrence_to_row", "occurrences_to_rows"]
