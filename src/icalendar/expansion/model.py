"""Validation and normalization of the VEVENT components of one UID.

A UID's components consist of exactly one master component (no
``RECURRENCE-ID``) and zero or more single-instance exception components.
This module turns the parsed, loosely-typed icalendar objects into small
frozen value objects (:class:`MasterEvent`, :class:`ExceptionEvent`) while
enforcing every structural rule that :meth:`icalendar.Calendar.expand_events`
promises to reject, regardless of the query window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any

from icalendar.enums import STATUS
from icalendar.expansion.rrule import RuleError, RuleSpec
from icalendar.expansion.time import (
    UTC,
    get_default_zone,
    localize_explicit,
)
from icalendar.tools import is_date, is_datetime
from icalendar.timezone import tzp

if TYPE_CHECKING:
    from collections.abc import Sequence

    from icalendar.cal.event import Event

CANCELLED = str(STATUS.CANCELLED)

#: Properties that define the recurrence set; exceptions may not carry them.
RECURRENCE_PROPERTIES = ("RRULE", "RDATE", "EXDATE", "EXRULE")


class ExpansionValidationError(ValueError):
    """A calendar component is structurally invalid for expansion."""


@dataclass(frozen=True, slots=True)
class Interpreted:
    """A time value resolved to a concrete reference frame."""

    all_day: bool
    #: naive wall-clock time; kept for identity matching in the event's frame
    wall: datetime | None
    #: aware datetime (UTC for UTC inputs, default zone for floating)
    aware: datetime | None
    #: original pure date for all-day values, else None
    day: date | None
    #: tzinfo of a TZID-referenced value, None for UTC/floating/date
    zone: tzinfo | None
    kind: str  # "date" | "utc" | "tzid" | "floating"


@dataclass(frozen=True, slots=True)
class EndSpec:
    """How an occurrence's end is computed."""

    kind: str  # "none" | "dtend" | "duration"
    dtend: datetime | None = None  # aware, same frame as the start
    duration: timedelta | None = None
    nominal_days: int | None = None  # whole days/weeks for all-day DURATION
    #: True for nominal day/week DURATION (wall-clock arithmetic); False for
    #: an exact hour/minute/second DURATION (instant offset).
    nominal: bool = False


@dataclass(frozen=True, slots=True)
class MasterEvent:
    """The validated master component of one UID."""

    uid: str
    all_day: bool
    dtstart_interpreted: Interpreted
    end: EndSpec
    summary: str
    cancelled: bool
    rule: RuleSpec | None
    rdates: tuple[Interpreted, ...]
    exdates: tuple[Interpreted, ...]
    location_index: int


@dataclass(frozen=True, slots=True)
class ExceptionEvent:
    """A validated single-instance exception component."""

    uid: str
    all_day: bool
    recurrence_id: Interpreted
    new_start: Interpreted | None  # None when the exception only cancels
    end: EndSpec | None  # None: inherit the master's duration
    summary: str | None  # None: inherit the master summary
    summary_present: bool
    cancelled: bool
    location_index: int


@dataclass(frozen=True, slots=True)
class EventGroup:
    """All validated components sharing one UID."""

    uid: str
    master: MasterEvent
    exceptions: tuple[ExceptionEvent, ...] = field(default_factory=tuple)


def interpret_time(
    prop: Any,
    component_label: str,
    default_zone: tzinfo,
    *,
    allow_date: bool = True,
) -> Interpreted:
    """Interpret a parsed DATE/DATE-TIME property.

    Raises:
        ExpansionValidationError: On unresolvable TZIDs, broken values or type
            problems, with a message naming the owning property.
    """
    label = component_label
    if prop is None:
        raise ExpansionValidationError(f"{label} is missing")
    # vBroken raises on attribute access; identify it explicitly so the error
    # keeps the owning component and property context.
    from icalendar.prop.broken import vBroken

    if isinstance(prop, vBroken):
        raise ExpansionValidationError(
            f"{label} has an unparseable value {str(prop)!r}"
        )
    if not hasattr(prop, "dt"):
        raise ExpansionValidationError(
            f"{label} has an unparseable value {prop!r}"
        )
    try:
        value = prop.dt
    except Exception as e:  # pragma: no cover - defensive
        raise ExpansionValidationError(f"{label} could not be parsed: {e}") from e

    tzid_param = prop.params.get("TZID") if hasattr(prop, "params") else None
    if tzid_param == "UTC":
        tzid_param = None  # UTC is expressed with the Z suffix, not a TZID

    if is_date(value):
        if not allow_date:
            raise ExpansionValidationError(f"{label} must be a DATE-TIME")
        midnight = datetime(value.year, value.month, value.day)
        aware = localize_explicit(midnight, default_zone)
        return Interpreted(
            all_day=True, wall=midnight, aware=aware, day=value,
            zone=None, kind="date",
        )

    if not is_datetime(value):
        raise ExpansionValidationError(
            f"{label} must be a DATE or DATE-TIME, got {type(value).__name__}"
        )

    if value.tzinfo is None:
        if tzid_param:
            # Parsing left the value floating because the TZID could not be
            # resolved; retry through the active provider and VTIMEZONE cache.
            zone = tzp.timezone(tzid_param)
            if zone is None:
                raise ExpansionValidationError(
                    f"{label} references unresolvable TZID {tzid_param!r}"
                )
            wall = value
            aware = localize_explicit(wall, zone)
            return Interpreted(
                all_day=False, wall=wall, aware=aware, day=None,
                zone=zone, kind="tzid",
            )
        # genuinely floating
        wall = value
        aware = localize_explicit(wall, default_zone)
        return Interpreted(
            all_day=False, wall=wall, aware=aware, day=None,
            zone=None, kind="floating",
        )

    # aware datetime
    if tzid_param and not _tz_matches(value.tzinfo, tzid_param):
        raise ExpansionValidationError(
            f"{label} TZID {tzid_param!r} does not match its resolved time zone"
        )
    kind = "utc" if value.utcoffset() == timedelta(0) and _is_utc(value) else "tzid"
    if kind == "utc":
        return Interpreted(
            all_day=False, wall=None, aware=value.astimezone(UTC), day=None,
            zone=None, kind="utc",
        )
    # Re-localize explicit TZID values so the RFC 5545 rules apply even when
    # the parser (notably pytz) already picked an offset for an ambiguous or
    # non-existent wall time. This enforces the first-occurrence fall-back and
    # the forward-gap shift consistently across providers.
    wall = value.replace(tzinfo=None)
    aware = localize_explicit(wall, value.tzinfo)
    return Interpreted(
        all_day=False, wall=wall, aware=aware, day=None,
        zone=value.tzinfo, kind="tzid",
    )


def _is_utc(value: datetime) -> bool:
    from icalendar.timezone.tzid import is_utc

    return is_utc(value)


def _tz_matches(zone: tzinfo, tzid: str) -> bool:
    from icalendar.timezone.tzid import tzid_from_tzinfo

    resolved = tzid_from_tzinfo(zone)
    if resolved is None:
        return True  # custom tzinfo without an id; parsing already accepted it
    cleaned = tzp.clean_timezone_id(tzid)
    return resolved == cleaned or resolved == tzid


def _text(component: Event, name: str) -> tuple[str | None, bool]:
    """Return (value, present). Missing -> (None, False); empty -> ('', True)."""
    prop = component.get(name)
    if prop is None:
        return None, False
    return str(prop), True


def _status_cancelled(component: Event) -> bool:
    status = component.get("STATUS")
    return status is not None and str(status).upper() == CANCELLED


def _parse_end(
    component: Event,
    start: Interpreted,
    component_label: str,
    default_zone: tzinfo,
) -> EndSpec:
    label = f"{component_label}"
    dtend_prop = component.get("DTEND")
    duration_prop = component.get("DURATION")
    if dtend_prop is not None and duration_prop is not None:
        raise ExpansionValidationError(
            f"{label}: DTEND and DURATION are mutually exclusive"
        )
    if dtend_prop is not None:
        dtend = interpret_time(
            dtend_prop, f"{label} DTEND", default_zone, allow_date=start.all_day
        )
        if dtend.all_day != start.all_day:
            raise ExpansionValidationError(
                f"{label}: DTEND value type must match DTSTART"
            )
        _ensure_same_frame(start, dtend, f"{label} DTEND")
        if dtend.aware <= start.aware:
            raise ExpansionValidationError(
                f"{label}: DTEND must be later than DTSTART"
            )
        return EndSpec(kind="dtend", dtend=dtend.aware)
    if duration_prop is not None:
        try:
            duration = duration_prop.dt
        except Exception as e:
            raise ExpansionValidationError(
                f"{label}: DURATION could not be parsed: {e}"
            ) from e
        if not isinstance(duration, timedelta):
            raise ExpansionValidationError(f"{label}: DURATION must be a duration")
        if duration <= timedelta(0):
            raise ExpansionValidationError(f"{label}: DURATION must be positive")
        # A timedelta alone cannot distinguish P1D from PT24H; the parser marks
        # nominal day/week spellings with the NominalDuration subclass.
        nominal = bool(getattr(duration, "nominal", False))
        if start.all_day:
            if duration.seconds != 0 or duration.microseconds != 0:
                raise ExpansionValidationError(
                    f"{label}: DURATION on a DATE event must be whole days or weeks"
                )
            return EndSpec(
                kind="duration",
                duration=duration,
                nominal_days=duration.days,
                nominal=True,
            )
        return EndSpec(
            kind="duration", duration=duration, nominal=nominal
        )
    return EndSpec(kind="none")


def _ensure_same_frame(
    start: Interpreted, other: Interpreted, label: str
) -> None:
    if start.all_day != other.all_day:
        raise ExpansionValidationError(f"{label}: value type must match DTSTART")
    if start.kind != other.kind:
        raise ExpansionValidationError(
            f"{label}: time type must match DTSTART ({start.kind} vs {other.kind})"
        )
    if start.zone is not None and other.zone is not None and start.zone != other.zone:
        raise ExpansionValidationError(
            f"{label}: TZID must match DTSTART"
        )


def _require_value_type_consistency(
    values: Sequence[Interpreted], start: Interpreted, label: str
) -> None:
    for value in values:
        if value.all_day != start.all_day:
            raise ExpansionValidationError(
                f"{label}: value type must match DTSTART (DATE vs DATE-TIME)"
            )
        if not value.all_day and value.kind != start.kind:
            raise ExpansionValidationError(
                f"{label}: time type must match DTSTART ({start.kind} vs "
                f"{value.kind})"
            )
        if (
            not value.all_day
            and start.zone is not None
            and value.zone is not None
            and value.zone != start.zone
        ):
            raise ExpansionValidationError(
                f"{label}: TZID must match DTSTART"
            )


def _collect_ddd(
    component: Event, name: str, default_zone: tzinfo, label: str
) -> list[Interpreted]:
    prop = component.get(name)
    if prop is None:
        return []
    props = prop if isinstance(prop, list) else [prop]
    out: list[Interpreted] = []
    for index, ddd_list in enumerate(props):
        if hasattr(ddd_list, "params") and ddd_list.params.get("VALUE") == "PERIOD":
            raise ExpansionValidationError(
                f"{label}: PERIOD values in {name} are not supported"
            )
        if not hasattr(ddd_list, "dts"):
            raise ExpansionValidationError(
                f"{label}: {name}[{index}] has an invalid value"
            )
        for ddd in ddd_list.dts:
            if isinstance(ddd.dt, tuple):
                raise ExpansionValidationError(
                    f"{label}: PERIOD values in {name} are not supported"
                )
            out.append(interpret_time(ddd, f"{label} {name}", default_zone))
    return out


def _check_unsupported_recurrence(
    component: Event, label: str, *, allow_full: bool
) -> None:
    if component.get("EXRULE") is not None:
        raise ExpansionValidationError(f"{label}: EXRULE is not supported")
    if not allow_full:
        for name in ("RRULE", "RDATE", "EXDATE"):
            if component.get(name) is not None:
                raise ExpansionValidationError(
                    f"{label}: exception components must not carry {name}"
                )
    rrules = component.get("RRULE")
    if rrules is not None:
        rules = rrules if isinstance(rrules, list) else [rrules]
        if len(rules) > 1:
            raise ExpansionValidationError(
                f"{label}: more than one RRULE is not supported"
            )
    # RANGE on RECURRENCE-ID
    rec_id = component.get("RECURRENCE-ID")
    if rec_id is not None and rec_id.params.get("RANGE") is not None:
        raise ExpansionValidationError(
            f"{label}: RECURRENCE-ID RANGE parameter is not supported"
        )


def build_group(
    uid: str,
    components: Sequence[tuple[Event, int]],
    default_zone_name: str,
) -> EventGroup:
    """Validate every component of one UID and build an :class:`EventGroup`.

    ``components`` pairs each event with its index among the calendar's VEVENT
    components, so error messages can locate a component even when the UID is
    the same across all of them.
    """
    default_zone = get_default_zone(default_zone_name)
    masters: list[tuple[Event, int]] = []
    exceptions: list[tuple[Event, int]] = []
    for event, index in components:
        if event.get("RECURRENCE-ID") is None:
            masters.append((event, index))
        else:
            exceptions.append((event, index))

    if len(masters) != 1:
        raise ExpansionValidationError(
            f"UID {uid!r}: expected exactly one master VEVENT without "
            f"RECURRENCE-ID, found {len(masters)}"
        )
    master_event, master_index = masters[0]
    master = _build_master(uid, master_event, master_index, default_zone)

    built_exceptions: list[ExceptionEvent] = []
    seen_identities: set[tuple] = set()
    for event, index in exceptions:
        exc = _build_exception(uid, event, index, default_zone)
        if exc.all_day != master.all_day:
            raise ExpansionValidationError(
                f"UID {uid!r}: exception at index {index} mixes DATE and "
                "DATE-TIME value types with its master"
            )
        identity_key = _identity_key(exc.recurrence_id)
        if identity_key in seen_identities:
            raise ExpansionValidationError(
                f"UID {uid!r}: duplicate exception for RECURRENCE-ID "
                f"{_identity_text(exc.recurrence_id)}"
            )
        seen_identities.add(identity_key)
        built_exceptions.append(exc)

    return EventGroup(uid=uid, master=master, exceptions=tuple(built_exceptions))


def _build_master(
    uid: str, event: Event, index: int, default_zone: tzinfo
) -> MasterEvent:
    label = f"UID {uid!r}, master VEVENT at index {index}"
    _check_unsupported_recurrence(event, label, allow_full=True)
    dtstart_prop = event.get("DTSTART")
    if dtstart_prop is None:
        raise ExpansionValidationError(f"{label}: DTSTART is required")
    start = interpret_time(dtstart_prop, f"{label} DTSTART", default_zone)
    end = _parse_end(event, start, label, default_zone)
    summary, _ = _text(event, "SUMMARY")
    rdates = _collect_ddd(event, "RDATE", default_zone, label)
    exdates = _collect_ddd(event, "EXDATE", default_zone, label)
    _require_value_type_consistency(rdates, start, f"{label} RDATE")
    _require_value_type_consistency(exdates, start, f"{label} EXDATE")

    rule: RuleSpec | None = None
    rrules = event.get("RRULE")
    if rrules is not None:
        rules = rrules if isinstance(rrules, list) else [rrules]
        if len(rules) > 1:
            raise ExpansionValidationError(
                f"{label}: more than one RRULE is not supported"
            )
        try:
            rule = RuleSpec.from_vrecur(rules[0])
        except RuleError as e:
            raise ExpansionValidationError(f"{label}: {e}") from e
        _validate_until(rule, start, label)

    return MasterEvent(
        uid=uid,
        all_day=start.all_day,
        dtstart_interpreted=start,
        end=end,
        summary=summary or "",
        cancelled=_status_cancelled(event),
        rule=rule,
        rdates=tuple(rdates),
        exdates=tuple(exdates),
        location_index=index,
    )


def _validate_until(rule: RuleSpec, start: Interpreted, label: str) -> None:
    if rule.until is None:
        return
    until = rule.until
    if start.all_day:
        # UNTIL for a DATE-valued rule must be a UTC DATE-TIME ending the day;
        # a pure date UNTIL was rejected in RuleSpec. Compare against the
        # midnight UTC boundary of the same day as the wall date.
        if until.tzinfo is None or until.utcoffset() != timedelta(0):
            raise ExpansionValidationError(
                f"{label}: UNTIL of a DATE rule must be a UTC DATE-TIME"
            )
    else:
        # For timed events UNTIL shares the DTSTART time reference: UTC rules
        # use UTC UNTIL; TZID rules use a UTC UNTIL per RFC 5545. Floating
        # rules cannot define a comparable UNTIL.
        if start.kind == "floating":
            raise ExpansionValidationError(
                f"{label}: UNTIL is not defined for a floating DATE-TIME rule"
            )
        if until.utcoffset() != timedelta(0):
            raise ExpansionValidationError(f"{label}: UNTIL must be expressed in UTC")


def _build_exception(
    uid: str, event: Event, index: int, default_zone: tzinfo
) -> ExceptionEvent:
    label = f"UID {uid!r}, exception VEVENT at index {index}"
    _check_unsupported_recurrence(event, label, allow_full=False)
    rec_prop = event.get("RECURRENCE-ID")
    recurrence_id = interpret_time(
        rec_prop, f"{label} RECURRENCE-ID", default_zone
    )

    new_start: Interpreted | None = None
    end: EndSpec | None = None
    dtstart_prop = event.get("DTSTART")
    if dtstart_prop is not None:
        new_start = interpret_time(dtstart_prop, f"{label} DTSTART", default_zone)
        if new_start.all_day != recurrence_id.all_day:
            raise ExpansionValidationError(
                f"{label}: exception DTSTART value type must match "
                "RECURRENCE-ID/master"
            )
        if not new_start.all_day and new_start.kind != recurrence_id.kind:
            raise ExpansionValidationError(
                f"{label}: exception DTSTART time type must match RECURRENCE-ID"
            )
        end = _parse_end(event, new_start, label, default_zone)
    elif event.get("DTEND") is not None or event.get("DURATION") is not None:
        raise ExpansionValidationError(
            f"{label}: DTEND/DURATION require a DTSTART on the exception"
        )

    summary, summary_present = _text(event, "SUMMARY")
    return ExceptionEvent(
        uid=uid,
        all_day=recurrence_id.all_day,
        recurrence_id=recurrence_id,
        new_start=new_start,
        end=end,
        summary=summary,
        summary_present=summary_present,
        cancelled=_status_cancelled(event),
        location_index=index,
    )


def _identity_key(value: Interpreted) -> tuple[bool, date, datetime | None]:
    """A hashable identity in the event's own calendar frame."""
    if value.all_day:
        return (True, value.day, None)
    return (False, value.day, value.wall if value.wall is not None else value.aware)


def _identity_text(value: Interpreted) -> str:
    if value.all_day:
        return value.day.isoformat()
    base = value.wall if value.wall is not None else value.aware
    return base.isoformat()


__all__ = [
    "EndSpec",
    "EventGroup",
    "ExceptionEvent",
    "ExpansionValidationError",
    "Interpreted",
    "MasterEvent",
    "build_group",
]
