Add private, per-calendar time zone isolation and calendar merging.

``Calendar.from_ical`` now accepts a keyword-only ``local_timezones`` flag.
When it is true, each ``VCALENDAR``'s ``VTIMEZONE`` components define time
zones that are private to that calendar: the same ``TZID`` can carry
different rules in different calendars without one overriding the other, the
global timezone provider and its cache are never modified, a TZID without a
local definition is resolved only through the standard timezone library, and
an unresolvable TZID raises a ``ValueError``. Forward references,
``multiple=True`` and concurrent parsing are supported with isolated scopes.

The new ``Calendar.merge_ical`` class method merges several single-calendar
sources under these semantics into one ordinary ``VERSION:2.0`` calendar.
Colliding private definitions are emitted under unique, deterministic aliases
while the bound datetimes keep their original UTC meaning, so the merged
calendar serializes and round-trips (including in a fresh process) to the
same expansion results.
