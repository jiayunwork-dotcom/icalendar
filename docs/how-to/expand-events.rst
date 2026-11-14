Expand recurring events into occurrences
=========================================

A calendar does not store one component per happening. A recurring event is a
*definition*: a master ``VEVENT`` with an ``RRULE``/``RDATE``/``EXDATE`` set,
plus optional exception components carrying a ``RECURRENCE-ID`` that move or
cancel single happenings. :meth:`icalendar.Calendar.expand_events` resolves
those definitions into the concrete :class:`~icalendar.Occurrence` records that
actually take place inside a time window, ready for ETL pipelines.

.. code-block:: python

    from datetime import datetime, timezone
    from icalendar import Calendar

    calendar = Calendar.from_ical(ics_bytes)
    occurrences = calendar.expand_events(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 2, 1, tzinfo=timezone.utc),
    )

Query window
------------

Both arguments must be timezone-aware datetimes; they are converted to UTC
internally. The window is **half open** on the final start instant:
``window_start <= occurrence.start < window_end``. Selection always uses the
*final* start after rescheduling, so an exception moved into the window is
included while the same identity moved out of it leaves no record behind.

``default_tz`` (default ``"UTC"``) names the IANA zone used to interpret:

- floating DATE-TIME values (no ``Z`` suffix and no ``TZID``);
- all-day DATE boundaries (midnight to midnight in that zone).

It never depends on the host machine's local time zone. A ``TZID`` that cannot
be resolved raises :class:`ValueError` instead of silently becoming floating.

``max_instances`` (default ``10000``) caps the result size. It must be a
non-bool positive integer; exceeding it raises :class:`OverflowError` rather
than truncating the list.

The Occurrence record
---------------------

Each :class:`~icalendar.Occurrence` is an immutable, hashable record with six
read-only fields:

============== ================================================================
field          meaning
============== ================================================================
``uid``        the shared UID of the master and its exceptions
``recurrence_id``
               the *original* identity of the happening -- a
               :class:`datetime.date` for all-day events, otherwise a UTC
               aware :class:`datetime.datetime`. A one-off event has this
               identity too.
``start``      the effective (possibly rescheduled) start, UTC aware
``end``        the non-inclusive end, UTC aware
``summary``    the effective summary, ``""`` when absent
``all_day``    ``True`` for DATE-valued events
============== ================================================================

Records are sorted by the ISO strings of ``(start, uid, recurrence_id)``. The
recurrence identity is what makes rescheduling safe: two different identities
moved onto the same instant produce two distinct occurrences.

How identity, rescheduling and cancellation work
-------------------------------------------------

- Inclusion (``RRULE``, ``RDATE`` and the master ``DTSTART``) is de-duplicated.
- ``EXDATE`` always wins: an excluded identity is removed even if an
  ``RDATE``/``RRULE`` also generates it.
- An exception component matches an identity by its ``RECURRENCE-ID``, never by
  its (possibly changed) ``DTSTART``.
- An exception without a summary inherits the master's; an explicit empty
  ``SUMMARY:`` is preserved as ``""``.
- An exception without ``DTEND``/``DURATION`` keeps the master's duration
  caliber applied to its new start.
- ``STATUS:CANCELLED`` on an exception removes only that identity; on the
  master it removes the whole UID.
- Matching, exclusion and cancellation use the original identity, while window
  selection uses the final start.

Recurrence rule support
-----------------------

The supported ``RRULE`` parts are ``FREQ`` of ``DAILY``/``WEEKLY``/
``MONTHLY``/``YEARLY`` plus ``INTERVAL``, ``COUNT``, ``UNTIL``, ``BYDAY``
(including ordinal forms like ``2FR`` and ``-1SU``), ``BYMONTHDAY``,
``BYMONTH``, ``BYSETPOS`` and ``WKST``. Any other part, a second ``RRULE``, an
``EXRULE``, a PERIOD-valued ``RDATE`` or a ``RECURRENCE-ID`` with a ``RANGE``
parameter raises :class:`ValueError`.

Daylight-saving semantics
-------------------------

Rules expand in the event's own calendar time, so a daily 09:00 rule stays at
09:00 local after a DST transition. For rule-generated wall-clock times:

- a wall time that does not exist after a forward jump is **skipped**, and the
  skip does not consume ``COUNT``;
- an ambiguous wall time at a fall-back resolves to its **first** occurrence.

Explicit DATE-TIME properties (``DTSTART``, ``RDATE``, ``EXDATE``,
``RECURRENCE-ID`` and exception ``DTSTART``) follow the explicit-value rules
instead: gap times are shifted forward rather than skipped.

A ``DURATION`` with day/week parts (``P1D``, ``P2W``) is nominal calendar
arithmetic, while a pure time duration (``PT24H``) is an exact instant offset;
the two can differ across a DST transition. A recurring ``DTEND`` defines one
exact duration reused on every occurrence.

Bounded memory and validation
------------------------------

Expansion is lazy: an infinite rule queried over a finite window terminates
without enumerating the rule's history, and only the window's results are
retained. Every UID is fully validated even when it has no occurrence in the
query window, so a structurally invalid calendar always fails. The calendar and
its components are never modified, repeated queries return identical results,
and the active time-zone provider is not switched.

Turning occurrences into ETL rows
---------------------------------

Because each occurrence is a simple value object, it maps directly onto a row
dictionary:

.. code-block:: python

    def to_row(occurrence):
        return {
            "uid": occurrence.uid,
            "recurrence_id": occurrence.recurrence_id.isoformat(),
            "start_utc": occurrence.start.isoformat(),
            "end_utc": occurrence.end.isoformat(),
            "summary": occurrence.summary,
            "all_day": occurrence.all_day,
        }

    rows = [to_row(o) for o in occurrences]

A complete runnable adapter lives in
:mod:`icalendar.expansion.etl` (see
:func:`~icalendar.expansion.etl.occurrence_to_row`) and the field-level
reference is in :class:`icalendar.Occurrence`.
