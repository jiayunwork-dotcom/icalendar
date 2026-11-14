"""Merge recurrence definitions, exceptions and the query window.

The engine consumes the validated :class:`~icalendar.expansion.model.EventGroup`
objects and produces :class:`~icalendar.expansion.occurrence.Occurrence`
records. All selection happens on the *final* (post-reschedule) start instant
while inclusion, exclusion, cancellation and exception matching happen on the
*original* recurrence identity, exactly as :rfc:`5545` requires.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING

from icalendar.expansion.model import (
    EndSpec,
    EventGroup,
    ExceptionEvent,
    ExpansionValidationError,
    Interpreted,
    MasterEvent,
)
from icalendar.expansion.occurrence import Occurrence
from icalendar.expansion.rrule import iter_rule_dates, wall_clock_datetime
from icalendar.expansion.time import (
    UTC,
    ExpansionTimeError,
    add_exact_duration,
    add_nominal_duration,
    as_utc,
    date_to_utc,
    get_default_zone,
    localize_explicit,
    localize_rule_value,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from icalendar.cal.event import Event


class OccurrenceOverflow(OverflowError):
    """More occurrences matched than the caller's ``max_instances`` cap."""


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def _identity_key(value: Interpreted) -> tuple:
    """Hashable identity in the event's own calendar frame.

    Timed events with a wall clock (floating or TZID) identify by their naive
    wall time; UTC events identify by the UTC instant; all-day events by date.
    """
    if value.all_day:
        return ("date", value.day)
    if value.wall is not None:
        return ("wall", value.wall)
    return ("utc", value.aware.astimezone(UTC))


def _recurrence_id_value(value: Interpreted):
    """The public recurrence_id value: date for all-day, UTC instant else."""
    if value.all_day:
        return value.day
    return value.aware.astimezone(UTC)


# ---------------------------------------------------------------------------
# Recurrence set construction
# ---------------------------------------------------------------------------


def _rule_identities(
    master: MasterEvent, default_zone: tzinfo, limit_day: date
) -> Iterator[tuple[tuple, Interpreted]]:
    """Yield ``(identity key, interpreted value)`` from the master's RRULE.

    DST gaps are skipped; COUNT counts only surviving (non-gap) candidates per
    :rfc:`5545#section-3.3.10`, so rule generation continues past skipped gaps
    until COUNT survivors have been emitted.
    """
    spec = master.rule
    assert spec is not None
    start = master.dtstart_interpreted

    # UNTIL, expressed as an exclusive day bound in the event's own frame.
    if spec.until is not None:
        until_utc = spec.until.astimezone(UTC)
        if start.all_day:
            # UNTIL is an inclusive UTC instant; derive the last eligible day
            # from its wall date in the default zone.
            until_day = until_utc.astimezone(default_zone).date()
        elif start.kind == "utc":
            until_day = until_utc.date()
        else:
            # tzid rule: UNTIL is UTC; convert to the event zone for the bound.
            until_day = until_utc.astimezone(start.zone).date()
        limit_day = min(limit_day, until_day + timedelta(days=1))

    clock = (
        start.wall
        if start.wall is not None
        else start.aware.astimezone(start.zone or default_zone).replace(tzinfo=None)
    )
    zone = start.zone or default_zone

    if spec.count is not None:
        # COUNT counts surviving candidates, so drive the date generator
        # without its internal COUNT cap and count here instead.
        spec = dataclass_replace(spec, count=None)
        survivors = 0
        for day in iter_rule_dates(spec, _start_date(start), limit_day):
            interpreted = _materialize_rule_candidate(
                master, day, clock, zone, default_zone
            )
            if interpreted is None:
                continue
            survivors += 1
            yield _identity_key(interpreted), interpreted
            if survivors >= master.rule.count:
                return
        return

    for day in iter_rule_dates(spec, _start_date(start), limit_day):
        interpreted = _materialize_rule_candidate(
            master, day, clock, zone, default_zone
        )
        if interpreted is not None:
            yield _identity_key(interpreted), interpreted


def _start_date(start: Interpreted) -> date:
    if start.all_day:
        return start.day
    if start.wall is not None:
        return start.wall.date()
    return start.aware.astimezone(start.zone or UTC).date()


def _materialize_rule_candidate(
    master: MasterEvent,
    day: date,
    clock: datetime,
    zone: tzinfo,
    default_zone: tzinfo,
) -> Interpreted | None:
    start = master.dtstart_interpreted
    if start.all_day:
        return _date_interpreted(day, default_zone)
    naive = wall_clock_datetime(day, clock)
    aware = localize_rule_value(naive, zone)
    if aware is None:
        return None  # forward DST gap: skip without consuming COUNT
    if start.kind == "utc":
        # UTC values carry no naive wall identity.
        return Interpreted(
            all_day=False, wall=None, aware=aware.astimezone(UTC), day=None,
            zone=None, kind="utc",
        )
    return Interpreted(
        all_day=False, wall=naive, aware=aware, day=None,
        zone=zone, kind=start.kind,
    )


def _date_interpreted(day: date, default_zone: tzinfo) -> Interpreted:
    midnight = datetime(day.year, day.month, day.day)
    return Interpreted(
        all_day=True, wall=midnight,
        aware=localize_explicit(midnight, default_zone),
        day=day, zone=None, kind="date",
    )


def build_inclusion_set(
    group: EventGroup, default_zone: tzinfo, limit_day: date
) -> dict[tuple, Interpreted]:
    """All original identities of the master's recurrence set.

    Includes DTSTART, RRULE output and RDATE values, de-duplicated. The
    returned mapping keeps an :class:`Interpreted` representative for each
    identity, used to compute occurrence times.
    """
    master = group.master
    start = master.dtstart_interpreted
    identities: dict[tuple, Interpreted] = {}

    def add(interpreted: Interpreted) -> None:
        identities.setdefault(_identity_key(interpreted), interpreted)

    add(start)
    if master.rule is not None:
        for key, interpreted in _rule_identities(
            master, default_zone, limit_day
        ):
            identities.setdefault(key, interpreted)
    for rdate in master.rdates:
        add(rdate)
    return identities


def exclusion_set(master: MasterEvent) -> set[tuple]:
    """Identity keys removed by EXDATE (EXDATE wins over inclusions)."""
    return {_identity_key(value) for value in master.exdates}


# ---------------------------------------------------------------------------
# Occurrence time computation
# ---------------------------------------------------------------------------


def _occurrence_start(
    master: MasterEvent, identity: Interpreted, default_zone: tzinfo
) -> Interpreted:
    """The effective start for an identity without an overriding exception."""
    if _identity_key(identity) == _identity_key(master.dtstart_interpreted):
        return master.dtstart_interpreted
    return identity


def compute_end(
    start: Interpreted, spec: EndSpec, default_zone: tzinfo
) -> datetime:
    """Compute the aware UTC end for an occurrence start.

    Per :rfc:`5545#section-3.8.5.3`, a recurring ``DTEND`` defines one exact
    duration that applies to every occurrence, so it is turned into a timedelta
    measured against the master start. A ``DURATION`` keeps its nominal
    day/week semantics (wall-clock arithmetic, so a one-day duration can be 23
    or 25 hours across a DST transition) versus exact hour/minute/second
    arithmetic.
    """
    aware_start = start.aware
    if spec.kind == "none":
        if start.all_day:
            # one calendar day, midnight boundary to next midnight
            return as_utc(date_to_utc(start.day + timedelta(days=1), default_zone))
        return aware_start.astimezone(UTC)  # zero length
    if spec.kind == "dtend":
        return spec.dtend.astimezone(UTC)
    # duration
    duration = spec.duration
    if start.all_day:
        # nominal whole-day/week duration: add calendar days to the date, then
        # convert boundaries, so DST days need not be 24 hours.
        end_day = start.day + timedelta(days=spec.nominal_days)
        return as_utc(date_to_utc(end_day, default_zone))
    if spec.nominal:
        # nominal day/week duration: keep the local wall-clock time.
        return add_nominal_duration(aware_start, duration).astimezone(UTC)
    # exact hour/minute/second duration: instant offset, even when zero-length
    return add_exact_duration(aware_start, duration).astimezone(UTC)


def _summary_for(
    master: MasterEvent, exception: ExceptionEvent | None
) -> str:
    if exception is not None and exception.summary_present:
        return exception.summary or ""
    return master.summary


# ---------------------------------------------------------------------------
# Per-group expansion
# ---------------------------------------------------------------------------


def expand_group(
    group: EventGroup,
    window_start_utc: datetime,
    window_end_utc: datetime,
    default_zone: tzinfo,
    limit_day: date,
) -> list[Occurrence]:
    """Expand one UID into the occurrences selected by the window."""
    master = group.master
    if master.cancelled:
        return []

    inclusions = build_inclusion_set(group, default_zone, limit_day)
    exclusions = exclusion_set(master)

    exceptions_by_identity: dict[tuple, ExceptionEvent] = {}
    for exception in group.exceptions:
        key = _identity_key(exception.recurrence_id)
        if key not in inclusions:
            raise ExpansionValidationError(
                f"UID {group.uid!r}: exception RECURRENCE-ID "
                f"{_recurrence_id_value(exception.recurrence_id)} is not part "
                "of the master recurrence set"
            )
        if key in exclusions:
            raise ExpansionValidationError(
                f"UID {group.uid!r}: exception RECURRENCE-ID "
                f"{_recurrence_id_value(exception.recurrence_id)} is excluded "
                "by EXDATE"
            )
        exceptions_by_identity[key] = exception

    results: list[Occurrence] = []
    for key, identity in inclusions.items():
        if key in exclusions:
            continue
        exception = exceptions_by_identity.get(key)
        if exception is not None and exception.cancelled:
            continue

        if exception is not None and exception.new_start is not None:
            effective_start = exception.new_start
            if exception.end is not None and exception.end.kind != "none":
                end_spec = exception.end
            else:
                # No DTEND/DURATION on the exception: inherit the master's
                # duration caliber.
                end_spec = _inherited_end(master)
        else:
            effective_start = _occurrence_start(master, identity, default_zone)
            is_master_instance = (
                _identity_key(identity)
                == _identity_key(master.dtstart_interpreted)
            )
            if master.end.kind == "dtend" and not is_master_instance:
                # Recurring DTEND: same exact duration on every occurrence.
                end_spec = _inherited_end(master)
            else:
                end_spec = master.end

        start_utc = effective_start.aware.astimezone(UTC)
        if not (window_start_utc <= start_utc < window_end_utc):
            continue
        end_utc = compute_end(effective_start, end_spec, default_zone)
        results.append(
            Occurrence(
                uid=group.uid,
                recurrence_id=_recurrence_id_value(identity),
                start=start_utc,
                end=end_utc,
                summary=_summary_for(master, exception),
                all_day=master.all_day,
            )
        )
    return results


def _inherited_end(master: MasterEvent) -> EndSpec:
    """Re-apply the master's duration caliber to a shifted occurrence."""
    master_end = master.end
    if master_end.kind == "none":
        return EndSpec(kind="none")
    if master_end.kind == "duration":
        return EndSpec(
            kind="duration",
            duration=master_end.duration,
            nominal_days=master_end.nominal_days,
            nominal=master_end.nominal,
        )
    # DTEND: inherit the master's exact duration as a timedelta offset.
    exact_duration = (
        master_end.dtend.astimezone(UTC)
        - master.dtstart_interpreted.aware.astimezone(UTC)
    )
    return EndSpec(kind="duration", duration=exact_duration, nominal=False)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def validate_window(
    window_start: datetime, window_end: datetime
) -> tuple[datetime, datetime]:
    """Validate and normalize the query window to UTC."""
    for name, value in (("window_start", window_start), ("window_end", window_end)):
        # A datetime is also a date; require the concrete datetime type so a
        # pure date cannot slip through.
        if type(value) is not datetime:
            raise ExpansionTimeError(
                f"{name} must be a timezone-aware datetime, got {value!r}"
            )
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExpansionTimeError(f"{name} must be timezone-aware")
    start = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    if not start < end:
        raise ExpansionTimeError("window_start must be strictly before window_end")
    return start, end


def validate_max_instances(max_instances: int) -> int:
    """Validate the ``max_instances`` argument."""
    if isinstance(max_instances, bool) or not isinstance(max_instances, int):
        raise ExpansionTimeError(
            "max_instances must be a non-bool positive integer, got "
            f"{max_instances!r}"
        )
    if max_instances <= 0:
        raise ExpansionTimeError(
            f"max_instances must be positive, got {max_instances}"
        )
    return max_instances


def limit_day_for(window_start_utc: datetime, window_end_utc: datetime,
                  default_zone: tzinfo) -> date:
    """A wall-date bound safely beyond anything the window can select.

    The bound is derived only from the window (plus one safety day), never
    from DTSTART, so no history is ever enumerated.
    """
    last = window_end_utc.astimezone(default_zone).date()
    return last + timedelta(days=2)


def group_events(
    events: Iterable[Event],
) -> list[tuple[str, list[tuple[Event, int]]]]:
    """Group VEVENT components by UID, validating UID presence."""
    groups: dict[str, list[tuple[Event, int]]] = {}
    order: list[str] = []
    for index, event in enumerate(events):
        uid_prop = event.get("UID")
        if uid_prop is None:
            raise ExpansionValidationError(
                f"VEVENT at index {index} is missing a UID"
            )
        uid = str(uid_prop)
        if not uid:
            raise ExpansionValidationError(
                f"VEVENT at index {index} has an empty UID"
            )
        if uid not in groups:
            groups[uid] = []
            order.append(uid)
        groups[uid].append((event, index))
    return [(uid, groups[uid]) for uid in order]


def expand(
    events: Iterable[Event],
    window_start: datetime,
    window_end: datetime,
    default_tz: str,
    max_instances: int,
) -> list[Occurrence]:
    """Validate, expand and sort every UID's events for the given window."""
    window_start_utc, window_end_utc = validate_window(window_start, window_end)
    cap = validate_max_instances(max_instances)
    default_zone = get_default_zone(default_tz)
    limit_day = limit_day_for(window_start_utc, window_end_utc, default_zone)

    all_occurrences: list[Occurrence] = []
    for uid, components in group_events(events):
        group = build_group_public(uid, components, default_tz)
        all_occurrences.extend(
            expand_group(
                group, window_start_utc, window_end_utc, default_zone, limit_day
            )
        )

    if len(all_occurrences) > cap:
        raise OccurrenceOverflow(
            f"expansion produced {len(all_occurrences)} occurrences, exceeding "
            f"max_instances={cap}"
        )

    all_occurrences.sort(
        key=lambda occurrence: (
            occurrence.start.isoformat(),
            occurrence.uid,
            _recurrence_id_iso(occurrence.recurrence_id),
        )
    )
    return all_occurrences


def _recurrence_id_iso(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value.isoformat()


# imported here to avoid a circular import at module load
from icalendar.expansion.model import build_group as build_group_public  # noqa: E402

__all__ = [
    "OccurrenceOverflow",
    "compute_end",
    "expand",
    "validate_max_instances",
    "validate_window",
]
