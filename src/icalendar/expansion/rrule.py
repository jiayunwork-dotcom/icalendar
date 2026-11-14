"""A focused :rfc:`5545#section-3.3.10` recurrence rule generator.

Only the rule parts required by :meth:`icalendar.Calendar.expand_events` are
supported (``FREQ`` of DAILY/WEEKLY/MONTHLY/YEARLY plus ``INTERVAL``,
``COUNT``, ``UNTIL``, ``BYDAY`` with ordinal numbers, ``BYMONTHDAY``,
``BYMONTH``, ``BYSETPOS`` and ``WKST``). Anything else is rejected while the
calendar is being validated, so it can never be silently ignored.

The generator works on *naive wall-clock* datetimes: :rfc:`5545` rules expand
in the event's own calendar time, and the DST interpretation (gap skipping,
first-occurrence fall-back) is applied afterwards by
:mod:`icalendar.expansion.time`. This keeps the local clock time of every
generated candidate equal to the DTSTART clock time, exactly as the RFC
requires.

Iteration is lazy and bounded by either ``COUNT``/``UNTIL`` or a caller
supplied wall-clock limit; an infinite rule over a finite query window
therefore terminates without enumerating history.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Iterator

from icalendar.prop.recur.weekday import vWeekday

if TYPE_CHECKING:
    from icalendar.prop.recur.recur import vRecur

#: Rule parts this generator understands.
SUPPORTED_PARTS = frozenset(
    {
        "FREQ",
        "INTERVAL",
        "COUNT",
        "UNTIL",
        "BYDAY",
        "BYMONTHDAY",
        "BYMONTH",
        "BYSETPOS",
        "WKST",
    }
)

SUPPORTED_FREQUENCIES = frozenset({"DAILY", "WEEKLY", "MONTHLY", "YEARLY"})

#: Monday .. Sunday as Python weekday() numbers.
_WEEKDAY_NUMBER = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


class RuleError(ValueError):
    """An RRULE cannot be expanded because it is unsupported or inconsistent."""


@dataclass(frozen=True, slots=True)
class ByDay:
    """One BYDAY entry, e.g. ``2MO`` (``ordinal=2``) or ``MO`` (``ordinal=None``)."""

    weekday: int
    ordinal: int | None = None

    @classmethod
    def parse(cls, value: vWeekday | str) -> ByDay:
        weekday = value if isinstance(value, vWeekday) else vWeekday(value)
        return cls(_WEEKDAY_NUMBER[str(weekday.weekday)], weekday.relative)


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """A validated RRULE expressed in Python-native values."""

    freq: str
    interval: int = 1
    count: int | None = None
    until: datetime | None = None
    byday: tuple[ByDay, ...] = ()
    bymonthday: tuple[int, ...] = ()
    bymonth: tuple[int, ...] = ()
    bysetpos: tuple[int, ...] = ()
    wkst: int = 0  # Monday, the RFC 5545 default
    raw_parts: frozenset[str] = frozenset()

    @classmethod
    def from_vrecur(cls, rule: vRecur) -> RuleSpec:
        """Validate and convert a parsed :class:`~icalendar.prop.vRecur`."""
        parts = frozenset(rule.keys())
        unsupported = parts - SUPPORTED_PARTS
        if unsupported:
            msg = ", ".join(sorted(unsupported))
            raise RuleError(f"unsupported RRULE part(s): {msg}")

        freq_values = rule.get("FREQ")
        if not freq_values:
            raise RuleError("RRULE is missing FREQ")
        freq = str(freq_values[0]).upper()
        if freq not in SUPPORTED_FREQUENCIES:
            raise RuleError(f"unsupported RRULE FREQ {freq!r}")

        interval = _first_int(rule, "INTERVAL", default=1)
        if interval < 1:
            raise RuleError(f"RRULE INTERVAL must be positive, got {interval}")

        count = _first_int(rule, "COUNT", default=None)
        if count is not None and count < 1:
            raise RuleError(f"RRULE COUNT must be positive, got {count}")

        until_values = rule.get("UNTIL")
        until = until_values[0] if until_values else None
        if until is not None:
            if not isinstance(until, datetime):
                raise RuleError(
                    "RRULE UNTIL must be a DATE-TIME for this implementation"
                )
            if until.tzinfo is None:
                raise RuleError("RRULE UNTIL must carry its time zone reference")
            if count is not None:
                raise RuleError("RRULE COUNT and UNTIL are mutually exclusive")

        byday = tuple(ByDay.parse(entry) for entry in (rule.get("BYDAY") or ()))
        bymonthday = tuple(int(v) for v in (rule.get("BYMONTHDAY") or ()))
        if any(not -31 <= v <= 31 or v == 0 for v in bymonthday):
            raise RuleError(
                "RRULE BYMONTHDAY values must be in -31..-1 or 1..31, got "
                f"{bymonthday}"
            )
        bymonth = tuple(int(v) for v in (rule.get("BYMONTH") or ()))
        if any(not 1 <= v <= 12 for v in bymonth):
            raise RuleError(
                f"RRULE BYMONTH values must be in 1..12, got {bymonth}"
            )
        bysetpos = tuple(int(v) for v in (rule.get("BYSETPOS") or ()))
        if any(v == 0 for v in bysetpos):
            raise RuleError("RRULE BYSETPOS values must not be zero")

        wkst_values = rule.get("WKST")
        wkst = 0
        if wkst_values:
            wkst = _WEEKDAY_NUMBER[str(wkst_values[0].weekday)]

        _validate_combination(freq, byday, bymonthday, bymonth, bysetpos)

        return cls(
            freq=freq,
            interval=interval,
            count=count,
            until=until,
            byday=byday,
            bymonthday=bymonthday,
            bymonth=bymonth,
            bysetpos=bysetpos,
            wkst=wkst,
            raw_parts=parts,
        )


def _first_int(rule: vRecur, key: str, default: int | None) -> int | None:
    values = rule.get(key)
    if not values:
        return default
    return int(values[0])


def _validate_combination(
    freq: str,
    byday: tuple[ByDay, ...],
    bymonthday: tuple[int, ...],
    bymonth: tuple[int, ...],
    bysetpos: tuple[int, ...],
) -> None:
    """Reject combinations with no defined :rfc:`5545` meaning."""
    if bysetpos and not (byday or bymonthday or freq in {"MONTHLY", "YEARLY"}):
        raise RuleError(
            "RRULE BYSETPOS requires BYDAY, BYMONTHDAY or a MONTHLY/YEARLY "
            "frequency"
        )
    for entry in byday:
        if entry.ordinal is not None and freq in {"DAILY", "WEEKLY"}:
            raise RuleError(
                f"ordinal BYDAY ({entry.ordinal:+d}) is not defined for "
                f"FREQ={freq}"
            )
    if freq == "WEEKLY" and bymonthday:
        raise RuleError("BYMONTHDAY cannot be combined with FREQ=WEEKLY")
    if freq == "DAILY" and (bymonthday or bymonth):
        raise RuleError("BYMONTHDAY/BYMONTH cannot be combined with FREQ=DAILY")


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _resolve_negative_monthday(day: int, year: int, month: int) -> int | None:
    if day > 0:
        return day if day <= _days_in_month(year, month) else None
    resolved = _days_in_month(year, month) + 1 + day
    return resolved if resolved >= 1 else None


def _weekday_matches_in_month(
    year: int, month: int, target_weekday: int
) -> list[int]:
    last = _days_in_month(year, month)
    return [
        day
        for day in range(1, last + 1)
        if date(year, month, day).weekday() == target_weekday
    ]


def _byday_days_in_month(year: int, month: int, entry: ByDay) -> list[int]:
    matches = _weekday_matches_in_month(year, month, entry.weekday)
    if entry.ordinal is None:
        return matches
    ordinal = entry.ordinal
    if ordinal > 0:
        return [matches[ordinal - 1]] if ordinal <= len(matches) else []
    index = len(matches) + ordinal
    return [matches[index]] if index >= 0 else []


def _candidate_days_in_month(
    spec: RuleSpec, year: int, month: int, anchor_day: int
) -> list[date]:
    days: set[int] = set()
    if spec.bymonthday:
        for day in spec.bymonthday:
            resolved = _resolve_negative_monthday(day, year, month)
            if resolved is not None:
                days.add(resolved)
    if spec.byday:
        for entry in spec.byday:
            days.update(_byday_days_in_month(year, month, entry))
    if not spec.bymonthday and not spec.byday:
        # DTSTART's day of month is the implicit anchor (RFC 5545).
        if anchor_day <= _days_in_month(year, month):
            days.add(anchor_day)
    return [date(year, month, day) for day in sorted(days)]


def _shift_months(anchor: date, months: int) -> date:
    total = anchor.year * 12 + (anchor.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    last = _days_in_month(year, month)
    return date(year, month, min(anchor.day, last))


def iter_rule_dates(
    spec: RuleSpec,
    dtstart: date,
    hard_limit: date,
) -> Iterator[date]:
    """Lazily yield the calendar dates generated by ``spec``.

    Parameters:
        spec: The validated rule.
        dtstart: The DTSTART date. The first expansion period is anchored on
            it and intervals count from it. DTSTART itself is always the first
            generated date, even when BYSETPOS would not select it from the
            first period's set.
        hard_limit: Iteration stops once a period starts at or after this
            date. Callers pass a bound just past the query window/UNTIL.

    COUNT counts only actually emitted dates. DST gap skipping happens later,
    so this iterator emits every wall-clock candidate and COUNT is adjusted by
    the caller when gap candidates are dropped.

    BYSETPOS numbers positions within each complete frequency period's set;
    only afterwards are dates before DTSTART (other than DTSTART itself)
    discarded.
    """
    freq = spec.freq
    anchor_day = dtstart.day
    emitted = 0

    if freq == "DAILY":
        day = dtstart
        while day < hard_limit:
            yield day
            emitted += 1
            if spec.count is not None and emitted >= spec.count:
                return
            day += timedelta(days=spec.interval)
        return

    if freq == "WEEKLY":
        shift = (dtstart.weekday() - spec.wkst) % 7
        period_start = dtstart - timedelta(days=shift)
        wanted = {entry.weekday for entry in spec.byday}
        first_period = True
        while period_start < hard_limit:
            if wanted:
                candidates = [
                    period_start + timedelta(days=offset)
                    for offset in range(7)
                    if (period_start + timedelta(days=offset)).weekday()
                    in wanted
                ]
            else:
                # No BYDAY: only the DTSTART weekday repeats.
                candidates = [
                    period_start + timedelta(days=offset)
                    for offset in range(7)
                    if (period_start + timedelta(days=offset)).weekday()
                    == dtstart.weekday()
                ]
            # BYSETPOS numbers positions within the full weekly set.
            if spec.bysetpos:
                candidates = _apply_bysetpos(spec, candidates)
            for day in candidates:
                if first_period and day < dtstart:
                    continue
                if day >= hard_limit:
                    return
                yield day
                emitted += 1
                if spec.count is not None and emitted >= spec.count:
                    return
            first_period = False
            period_start += timedelta(weeks=spec.interval)
        return

    # MONTHLY / YEARLY: walk anchor periods, then every month inside a year.
    if freq == "MONTHLY":
        anchor_period = date(dtstart.year, dtstart.month, 1)
        step_months = spec.interval
        period_index = 0
        while True:
            period_start = _shift_months(anchor_period, step_months * period_index)
            if period_start >= hard_limit:
                return
            candidates = _candidate_days_in_month(
                spec, period_start.year, period_start.month, anchor_day
            )
            if spec.bysetpos:
                candidates = _apply_bysetpos(spec, candidates)
            first_period = period_index == 0
            for day in candidates:
                if first_period and day < dtstart:
                    continue
                if day >= hard_limit:
                    return
                yield day
                emitted += 1
                if spec.count is not None and emitted >= spec.count:
                    return
            period_index += 1
        return

    # YEARLY
    anchor_period = date(dtstart.year, 1, 1)
    period_index = 0
    while True:
        year = anchor_period.year + spec.interval * period_index
        if date(year, 1, 1) >= hard_limit:
            return
        months = spec.bymonth or (dtstart.month,)
        year_candidates: list[date] = []
        for month in months:
            year_candidates.extend(
                _candidate_days_in_month(spec, year, month, anchor_day)
            )
        year_candidates.sort()
        if spec.bysetpos:
            year_candidates = _apply_bysetpos(spec, year_candidates)
        first_period = period_index == 0
        for day in year_candidates:
            if first_period and day < dtstart:
                continue
            if day >= hard_limit:
                return
            yield day
            emitted += 1
            if spec.count is not None and emitted >= spec.count:
                return
        period_index += 1


def _apply_bysetpos(spec: RuleSpec, candidates: list[date]) -> list[date]:
    if not candidates:
        return []
    result = []
    length = len(candidates)
    for position in spec.bysetpos:
        index = position - 1 if position > 0 else length + position
        if 0 <= index < length:
            result.append(candidates[index])
    return sorted(set(result))


def wall_clock_datetime(day: date, clock: datetime) -> datetime:
    """Combine a candidate day with DTSTART's wall-clock time."""
    return datetime(
        day.year, day.month, day.day,
        clock.hour, clock.minute, clock.second, clock.microsecond,
    )


__all__ = [
    "ByDay",
    "RuleError",
    "RuleSpec",
    "SUPPORTED_FREQUENCIES",
    "SUPPORTED_PARTS",
    "iter_rule_dates",
    "wall_clock_datetime",
]
