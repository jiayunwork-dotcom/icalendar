"""Time and time-zone interpretation for event expansion.

The helpers here centralize the :rfc:`5545` rules that ordinary
:class:`datetime` arithmetic does not provide:

- floating DATE-TIME values and DATE values are interpreted in the caller's
  ``default_tz`` rather than in the host machine's local zone;
- a ``TZID`` that cannot be resolved is an error, never a silent fallback to
  floating time;
- rule-generated wall-clock times that do not exist because of a forward DST
  jump are skipped, while ambiguous wall-clock times at a fall-back resolve to
  the first occurrence;
- explicit DATE-TIME properties (``DTSTART``, ``RDATE``, ``EXDATE``,
  ``RECURRENCE-ID``, exception ``DTSTART``) follow the explicit-value rules
  instead of the rule-generation rules.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from dateutil.tz import datetime_ambiguous, datetime_exists

from icalendar.tools import is_pytz

UTC = ZoneInfo("UTC")


class ExpansionTimeError(ValueError):
    """A time property cannot be interpreted for expansion.

    This is a :class:`ValueError` so that every expansion failure keeps the
    exception type promised by the public API.
    """


def get_default_zone(default_tz: str) -> ZoneInfo:
    """Resolve the ``default_tz`` argument to an IANA time zone.

    Raises:
        ExpansionTimeError: If ``default_tz`` is not a valid IANA name.
    """
    if not isinstance(default_tz, str) or not default_tz:
        raise ExpansionTimeError(
            f"default_tz must be a non-empty IANA time zone name, got {default_tz!r}"
        )
    try:
        return ZoneInfo(default_tz)
    except Exception as e:  # ZoneInfoNotFoundError, ValueError
        raise ExpansionTimeError(
            f"default_tz {default_tz!r} is not a valid IANA time zone name"
        ) from e


def _localize_naive(naive: datetime, zone: tzinfo) -> datetime:
    """Attach ``zone`` to a naive datetime using the provider's semantics."""
    if is_pytz(zone):
        # pytz refuses fold and needs localize(); the first of the two
        # ambiguous instants is the default (is_dst picks the DST side only
        # when a wall time genuinely occurs twice -- we instead ask for the
        # standard/first interpretation explicitly below).
        return zone.localize(naive)  # type: ignore[attr-defined, no-any-return]
    return naive.replace(tzinfo=zone)


def _pytz_first_occurrence(naive: datetime, zone: tzinfo) -> datetime:
    """Localize with pytz choosing the first instant at a fall-back."""
    std = zone.localize(naive, is_dst=False)  # type: ignore[attr-defined]
    try:
        dst = zone.localize(naive, is_dst=True)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - pytz always accepts is_dst=True
        return std
    if std == dst:
        return std
    return min(std, dst)


def localize_explicit(naive: datetime, zone: tzinfo) -> datetime:
    """Interpret an *explicit* DATE-TIME value in ``zone``.

    Explicit values (DTSTART/RDATE/EXDATE/RECURRENCE-ID/exception DTSTART) use
    the :rfc:`5545#section-3.3.5` DATE-TIME rules: an ambiguous wall-clock time
    resolves to its first occurrence and a non-existent wall-clock time is
    shifted forward by the size of the gap rather than rejected.
    """
    if is_pytz(zone):
        # Detect the situation structurally: pytz chooses an offset silently,
        # so compare the localized result against an unambiguous round trip.
        try:
            localized = zone.localize(naive, is_dst=None)  # type: ignore[attr-defined]
        except Exception as e:
            if type(e).__name__ == "AmbiguousTimeError":
                return _pytz_first_occurrence(naive, zone)
            if type(e).__name__ != "NonExistentTimeError":
                raise
            return _pytz_gap_shift(naive, zone)
        # localize(is_dst=None) succeeded: an unambiguous time. It can still be
        # a gap when pytz resolves it without raising, so verify existence.
        if not datetime_exists(localized):
            return _pytz_gap_shift(naive, zone)
        return localized
    candidate = naive.replace(tzinfo=zone)
    if not datetime_exists(candidate):
        # Forward jump: PEP 495 maps fold=0 to the pre-transition offset. The
        # post-transition instant is reached by adding the gap.
        pre = candidate
        post = naive.replace(fold=1, tzinfo=zone)
        gap = post.utcoffset() - pre.utcoffset()
        return post + gap
    return candidate  # fold=0 is the first occurrence at a fall-back


def _pytz_gap_shift(naive: datetime, zone: tzinfo) -> datetime:
    """Shift a non-existent pytz wall time to the first valid instant."""
    pre = zone.localize(naive, is_dst=False)  # type: ignore[attr-defined]
    post = zone.localize(naive, is_dst=True)  # type: ignore[attr-defined]
    gap = post.utcoffset() - pre.utcoffset()
    return post + gap


def localize_rule_value(naive: datetime, zone: tzinfo) -> datetime | None:
    """Interpret a wall-clock time produced by an RRULE.

    Returns the concrete datetime, or :data:`None` when the wall-clock time
    does not exist because of a forward DST transition. Such generated values
    are skipped and, per :rfc:`5545#section-3.3.10`, do not consume COUNT.

    Ambiguous wall-clock times resolve to their first (fold=0) occurrence.
    """
    if is_pytz(zone):
        try:
            localized = zone.localize(naive, is_dst=None)  # type: ignore[attr-defined]
        except Exception as e:
            if type(e).__name__ == "NonExistentTimeError":
                return None
            if type(e).__name__ == "AmbiguousTimeError":
                return _pytz_first_occurrence(naive, zone)
            raise
        # pytz may resolve a gap without raising; check existence explicitly.
        if not datetime_exists(localized):
            return None
        return localized
    candidate = naive.replace(tzinfo=zone)
    if not datetime_exists(candidate):
        return None
    return candidate  # fold=0 == first occurrence


def is_gap(dt: datetime) -> bool:
    """Whether an aware datetime lands in a forward DST gap."""
    return not datetime_exists(dt)


def is_ambiguous(dt: datetime) -> bool:
    """Whether an aware datetime is an ambiguous fall-back wall-clock time."""
    return datetime_ambiguous(dt)


def as_utc(dt: datetime) -> datetime:
    """Return ``dt`` converted to an aware UTC :class:`datetime`."""
    return dt.astimezone(UTC)


def date_to_utc(day: date, zone: tzinfo) -> datetime:
    """Interpret a DATE value as the midnight boundary in ``zone``."""
    return localize_explicit(datetime(day.year, day.month, day.day), zone)


def to_utc_datetime(value: date | datetime, default_zone: tzinfo) -> datetime:
    """Convert an already interpreted aware value/date to a UTC instant.

    Naive datetimes must have been localized by the caller; this function only
    accepts aware datetimes and pure :class:`~datetime.date` values.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            msg = "naive datetime reached to_utc_datetime without localization"
            raise ExpansionTimeError(msg)
        return as_utc(value)
    # pure date
    return as_utc(date_to_utc(value, default_zone))


def add_nominal_duration(start: datetime, duration: timedelta) -> datetime:
    """Add a DURATION using wall-clock calendar arithmetic.

    :rfc:`5545#section-3.3.6` nominal durations (days/weeks plus optional
    hours/minutes/seconds) keep the local clock time: the day and the
    hour/minute/second parts are added separately on the naive wall clock and
    the result is interpreted again in the start's time zone. Across a DST
    transition a one-day duration therefore need not be 24 hours.
    """
    zone = start.tzinfo
    naive = start.replace(tzinfo=None) + duration
    if zone is None:
        return naive
    return localize_explicit(naive, zone)


def add_exact_duration(start: datetime, duration: timedelta) -> datetime:
    """Add an exact (hour/minute/second only) duration as an instant offset.

    A timedelta added directly to an aware datetime is applied in wall-clock
    time, which silently absorbs DST shifts. To get a true instant offset, the
    arithmetic is done in UTC and the result is converted back to the start's
    time zone.
    """
    zone = start.tzinfo
    if zone is None:
        return start + duration
    utc_end = start.astimezone(UTC) + duration
    return utc_end.astimezone(zone)


def end_from_dtend(start: datetime, dtend: datetime) -> datetime:
    """Return an explicit DTEND, validated against the start.

    Both values are aware datetimes in the same reference frame after parsing.
    """
    if dtend <= start:
        raise ExpansionTimeError(
            f"DTEND {dtend.isoformat()} must be later than DTSTART "
            f"{start.isoformat()}"
        )
    return dtend


def day_after(day: date) -> date:
    """The following calendar day."""
    return day + timedelta(days=1)


__all__ = [
    "UTC",
    "ExpansionTimeError",
    "add_exact_duration",
    "add_nominal_duration",
    "as_utc",
    "date_to_utc",
    "day_after",
    "end_from_dtend",
    "get_default_zone",
    "is_ambiguous",
    "is_gap",
    "localize_explicit",
    "localize_rule_value",
    "to_utc_datetime",
]
