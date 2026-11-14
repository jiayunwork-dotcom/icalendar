"""Expand recurring :rfc:`5545` calendars into concrete occurrences.

This package implements :meth:`icalendar.Calendar.expand_events`, which turns
the recurrence *definitions* stored in a calendar (master ``VEVENT`` components,
``RRULE``/``RDATE``/``EXDATE`` properties and exception components carrying a
``RECURRENCE-ID``) into the concrete list of :class:`Occurrence` records that
actually happen inside a queried time window.

The code is intentionally split by responsibility:

- :mod:`icalendar.expansion.occurrence` defines the public, read-only
  :class:`~icalendar.expansion.occurrence.Occurrence` record.
- :mod:`icalendar.expansion.time` interprets DATE / DATE-TIME values, floating
  times and time zones, including the :rfc:`5545` DST rules.
- :mod:`icalendar.expansion.rrule` generates the occurrences of a single
  ``RRULE`` without ever materializing an infinite sequence.
- :mod:`icalendar.expansion.model` validates and normalizes one UID's
  components.
- :mod:`icalendar.expansion.engine` merges rules, inclusion/exclusion dates and
  exception components and applies the query window.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING

from icalendar.expansion.engine import expand
from icalendar.expansion.occurrence import Occurrence

if TYPE_CHECKING:
    from icalendar.cal.event import Event


def expand_events(
    events: Iterable[Event],
    window_start: datetime,
    window_end: datetime,
    *,
    default_tz: str = "UTC",
    max_instances: int = 10000,
) -> list[Occurrence]:
    """Expand VEVENT components into the occurrences inside a time window.

    This is the implementation behind
    :meth:`icalendar.Calendar.expand_events`; see that method for the full
    description of the parameters, return value and exceptions.
    """
    return expand(
        events,
        window_start,
        window_end,
        default_tz=default_tz,
        max_instances=max_instances,
    )


__all__ = ["Occurrence", "expand_events"]
