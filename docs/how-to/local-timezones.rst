Merge calendars that use private time zones
============================================

Two iCalendar sources can both call their office time zone ``Local/Office``
while giving it completely different rules. For example, source A may be fixed
at ``+0100`` and source B at ``+0300``, so the same local clock time
(``2026-01-02 10:00``) corresponds to two different UTC instants.

Ordinary parsing keeps parsed ``VTIMEZONE`` components in a process-wide cache:
the first definition of a given ``TZID`` wins for every later calendar. That is
fine for standard IANA names but wrong for private, source-specific names. This
page shows how to keep each calendar's time zones private and how to merge such
calendars without losing either meaning.

The examples below define two single-calendar strings. The only difference
between them is the offset of the same-named ``Local/Office`` zone::

    >>> source_a = """BEGIN:VCALENDAR
    ... VERSION:2.0
    ... PRODID:office-a
    ... BEGIN:VTIMEZONE
    ... TZID:Local/Office
    ... BEGIN:STANDARD
    ... DTSTART:19700101T000000
    ... TZOFFSETFROM:+0100
    ... TZOFFSETTO:+0100
    ... TZNAME:OFF1
    ... END:STANDARD
    ... END:VTIMEZONE
    ... BEGIN:VEVENT
    ... UID:a@example.com
    ... DTSTAMP:20250101T000000Z
    ... SUMMARY:A meeting
    ... DTSTART;TZID=Local/Office:20260102T100000
    ... END:VEVENT
    ... END:VCALENDAR
    ... """
    >>> source_b = source_a.replace("+0100", "+0300").replace(
    ...     "OFF1", "OFF3"
    ... ).replace("a@example.com", "b@example.com").replace(
    ...     "office-a", "office-b"
    ... ).replace("A meeting", "B meeting")
    >>> from datetime import datetime, timezone
    >>> from icalendar import Calendar

Parsing one calendar with private time zones
---------------------------------------------

Pass ``local_timezones=True`` to :meth:`icalendar.Calendar.from_ical`. Every
``VCALENDAR`` in the data then resolves its ``TZID`` references against its own
``VTIMEZONE`` components first. Definitions never enter the global cache, so two
calendars may reuse the same ``TZID`` with different rules.

    >>> calendar_a = Calendar.from_ical(source_a, local_timezones=True)
    >>> calendar_b = Calendar.from_ical(source_b, local_timezones=True)
    >>> calendar_a.events[0].start.astimezone(timezone.utc).isoformat()
    '2026-01-02T09:00:00+00:00'
    >>> calendar_b.events[0].start.astimezone(timezone.utc).isoformat()
    '2026-01-02T07:00:00+00:00'

The wall-clock value (``10:00``) is preserved in both calendars; only the UTC
instant differs. Reading the calendars in either order, or interleaved from
different threads, gives the same result because each calendar owns an isolated
time-zone scope and the global timezone provider is never modified.

A ``VTIMEZONE`` may appear before *or* after the event that references it;
forward references resolve because the whole calendar is scanned first. A
``TZID`` with no local definition is still resolved through the active standard
timezone library (for example ``Europe/Berlin``). If it cannot be resolved at
all, a :class:`ValueError` names the offending ``TZID`` and property.

Declaring the same private ``TZID`` twice in one calendar is rejected, even when
the two definitions are identical. With ``multiple=True`` every calendar in the
stream receives its own scope.

The default (``local_timezones=False``) and every other existing call keep their
previous, cache-based behaviour. Passing any non-bool value raises
:class:`TypeError`.

Merging several sources
-----------------------

:meth:`icalendar.Calendar.merge_ical` takes a non-empty, finite iterable of
single-calendar strings or byte strings and returns one new
:class:`~icalendar.Calendar`. Each source is interpreted with the local
time-zone semantics above, so same-named private zones keep their distinct
rules.

    >>> merged = Calendar.merge_ical([source_a, source_b])

A single output calendar cannot hold two different definitions under one name,
so colliding private definitions are emitted under unique aliases. The alias is
carried both by the output ``VTIMEZONE`` and by the timezone object bound to the
event datetimes, so the object model and the serialized text always describe the
same zone. You do not have to rename anything yourself or clear any cache.

    >>> blob = merged.to_ical()
    >>> [str(zone["TZID"]) for zone in merged.timezones]
    ['Local/Office', 'icalendar-merge/1-0/Local/Office']
    >>> for event in merged.events:
    ...     start = event.start
    ...     print(event["UID"], start.astimezone(timezone.utc).isoformat())
    a@example.com 2026-01-02T09:00:00+00:00
    b@example.com 2026-01-02T07:00:00+00:00

The output is an ordinary ``VERSION:2.0`` calendar. Reading it back with a plain
:meth:`~icalendar.Calendar.from_ical` -- even in a fresh process -- reconstructs
the same UTC instants, and expanding occurrences gives the same answer:

    >>> reread = Calendar.from_ical(blob)
    >>> window = (
    ...     datetime(2026, 1, 1, tzinfo=timezone.utc),
    ...     datetime(2026, 2, 1, tzinfo=timezone.utc),
    ... )
    >>> occurrences = merged.expand_events(*window)
    >>> reread.expand_events(*window) == occurrences
    True

What is preserved
~~~~~~~~~~~~~~~~~

* Source order and the order of events within each source.
* ``DTSTART``, ``DTEND``, ``RECURRENCE-ID`` and every item of ``RDATE`` /
  ``EXDATE``, including their original value type, wall-clock value and time
  zone meaning.
* ``RRULE``, ``DURATION``, ``SUMMARY``, ``VALARM`` components, unknown ``X-``
  properties and parameters other than ``TZID``.
* ``DATE``, floating and UTC values, which are never assigned a private zone.
* Seasonal ``STANDARD``/``DAYLIGHT`` transitions: a recurring event keeps its
  local clock time after a transition, and exclusions or rescheduled exceptions
  keep pointing at their original occurrence identity.

Input rules and errors
~~~~~~~~~~~~~~~~~~~~~~

Each source must contain exactly one ``VCALENDAR`` limited to ``VERSION:2.0``
and ``PRODID`` at the top level and ``VEVENT``/``VTIMEZONE`` sub-components
(``VEVENT`` may contain ``VALARM`` and custom properties). Every event needs a
non-empty ``UID``; a master event and its exceptions may share a ``UID`` within
one source, but the same ``UID`` must not appear in two different sources. An
empty, event-less calendar is valid; an empty *sources* iterable is not.

Errors name the 0-based source index and the relevant UID, TZID or property:

* wrong *sources* or element type raises :class:`TypeError`;
* empty input, multiple calendars in one item, out-of-scope components or
  properties, duplicate UIDs, duplicate local definitions or unresolvable
  TZIDs raise :class:`ValueError`.

The source iterable is consumed exactly once, any exception it raises is passed
through unchanged, and a failed merge never returns a half-built calendar or
modifies the calendars parsed earlier.

Design note
-----------

The isolation is provided by a per-calendar
:class:`~icalendar.timezone.local.LocalTimezoneScope` object attached to the
parsed calendar, rather than by clearing and restoring a shared cache. Two
organizations were considered:

1. **Context-local global state** -- swap a thread-local provider around each
   parse. It reuses the existing lookup code but makes forward references
   fragile, requires careful save/restore on every error path, and does not
   extend cleanly to interleaved parsing from multiple threads.
2. **Explicit per-calendar scope (chosen)** -- the parser resolves each TZID
   through the scope of the calendar it is reading. This makes forward
   references a matter of pre-registering the calendar's definitions, keeps
   same-named definitions permanently distinct, is naturally reentrant across
   threads, and leaves no global state behind when the calendar is garbage
   collected.

Merging builds on the same scopes and adds only output-local aliases, so the
bound datetime values and the serialized references can never drift apart.
