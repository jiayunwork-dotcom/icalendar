"""Merging several iCalendar sources into one calendar.

The calendars merged here are interpreted with *local* time zone semantics
(see :mod:`icalendar.timezone.local`): each source's ``VTIMEZONE`` components
are private to that source. Two sources may therefore use the same ``TZID``
with different rules.

Because a single output calendar cannot keep two different definitions under
one name, every private definition that would collide -- and, for a uniform
and predictable result, every private definition of the merged calendar -- is
emitted under a unique alias. The alias is carried both by the output
``VTIMEZONE`` and by a thin :class:`~datetime.tzinfo` wrapper attached to the
bound datetime values, so the object model and the serialized text always
describe the same time zone.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Any

from icalendar.cal.calendar import DEFAULT_PRODID, Calendar
from icalendar.timezone.local import LocalTimezoneScope

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Top-level properties a merge source calendar is allowed to carry.
_ALLOWED_TOP_LEVEL = frozenset({"VERSION", "PRODID"})
#: Sub-components a merge source calendar is allowed to carry.
_ALLOWED_SUBCOMPONENTS = frozenset({"VEVENT", "VTIMEZONE"})
#: Prefix used when a private TZID has to be renamed to avoid a collision.
_ALIAS_PREFIX = "icalendar-merge/"


class AliasedTimezone(tzinfo):
    """Present an existing time zone under a different TZID.

    The offset behaviour (including STANDARD/DAYLIGHT transitions) is taken
    verbatim from the wrapped time zone; only the identifying name changes.
    This lets a merged calendar emit a unique alias while the bound datetimes
    keep the exact UTC semantics of the source definition they were parsed
    against.

    The wrapper delegates :meth:`utcoffset`, :meth:`dst` and :meth:`tzname`,
    so it works identically for :mod:`zoneinfo`/dateutil and :mod:`pytz`
    backed zones. When the wrapped zone is a pytz zone, its ``localize`` and
    ``normalize`` helpers are exposed as well, returning datetimes that still
    carry this alias.
    """

    def __init__(self, alias: str, wrapped: tzinfo) -> None:
        self._alias = alias
        self._wrapped = wrapped

    @property
    def alias(self) -> str:
        """The TZID under which this time zone is serialized."""
        return self._alias

    @property
    def wrapped(self) -> tzinfo:
        """The time zone actually computing offsets."""
        return self._wrapped

    def utcoffset(self, dt):
        return self._wrapped.utcoffset(self._for_wrapped(dt))

    def dst(self, dt):
        return self._wrapped.dst(self._for_wrapped(dt))

    def tzname(self, dt=None):
        return self._alias

    def __getattr__(self, name: str):
        # Expose pytz's helper attributes only when the wrapped zone actually
        # provides them, so ``is_pytz`` (which probes for ``localize``) does
        # not mistake a dateutil/zoneinfo zone for a pytz one. Normal
        # attributes are found on the instance/class and never reach here.
        if name in ("localize", "normalize"):
            wrapped = self.__dict__.get("_wrapped")
            if wrapped is not None and hasattr(wrapped, name):
                return getattr(self, f"_aliased_{name}")
        raise AttributeError(name)

    def _aliased_localize(self, dt, is_dst=False):
        localized = self._wrapped.localize(dt, is_dst=is_dst)
        return localized.replace(tzinfo=self)

    def _aliased_normalize(self, dt):
        normalized = self._wrapped.normalize(dt.replace(tzinfo=self._wrapped))
        return normalized.replace(tzinfo=self)

    def _for_wrapped(self, dt):
        # Hand the underlying zone a datetime expressed in its own tzinfo so
        # pytz and dateutil receive values they understand, preserving the
        # wall-clock fields and fold.
        if dt is None:
            return None
        if dt.tzinfo is self:
            return dt.replace(tzinfo=self._wrapped)
        return dt

    def __repr__(self) -> str:
        return f"AliasedTimezone({self._alias!r}, {self._wrapped!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AliasedTimezone):
            return NotImplemented
        return self._alias == other._alias and self._wrapped == other._wrapped

    def __hash__(self) -> int:
        return hash((AliasedTimezone, self._alias))

    def __deepcopy__(self, memo):
        # The wrapper itself is immutable; share it across copies so every
        # rebound datetime keeps referencing the same aliased zone.
        return self

    def __reduce__(self):
        return (AliasedTimezone, (self._alias, self._wrapped))


def _merge_error(index: int, message: str) -> ValueError:
    """Build a merge error that names the (0-based) source index."""
    return ValueError(f"merge source {index}: {message}")


def _validate_source_calendar(calendar: Calendar, index: int) -> None:
    """Check a parsed source against the structural merge restrictions."""
    for name in calendar:
        if name.upper() not in _ALLOWED_TOP_LEVEL:
            raise _merge_error(
                index,
                f"top-level property {name!r} is outside the merge scope "
                "(only VERSION and PRODID are allowed)",
            )
    version = calendar.get("VERSION")
    if version is None or str(version) != "2.0":
        version_text = None if version is None else str(version)
        raise _merge_error(
            index,
            f"only VERSION:2.0 calendars can be merged, got VERSION={version_text!r}",
        )
    for sub in calendar.subcomponents:
        if sub.name not in _ALLOWED_SUBCOMPONENTS:
            raise _merge_error(
                index,
                f"component {sub.name!r} is outside the merge scope "
                "(only VEVENT and VTIMEZONE are allowed)",
            )
    # Every event needs a non-empty UID. A master event and its exceptions
    # may share a UID *within one source*, so duplicates are only rejected
    # across different sources (checked while the sources are combined).
    for event in calendar.events:
        uid = event.get("UID")
        if uid is None or str(uid) == "":
            raise _merge_error(index, "every VEVENT must have a non-empty UID")


def _make_alias(
    original: str,
    source_index: int,
    definition_index: int,
    taken: set[str],
) -> str:
    """Return a collision-free alias for a private TZID.

    The plain, un-suffixed name is preferred whenever it is still free, so
    single-source merges keep readable names. On collision a deterministic,
    source- and definition-indexed suffix is appended. Generation depends
    only on the inputs, so identical inputs produce identical bytes.
    """
    candidate = original
    if candidate in taken:
        candidate = f"{_ALIAS_PREFIX}{source_index}-{definition_index}/{original}"
        disambiguator = 1
        base = candidate
        while candidate in taken:
            candidate = f"{base}#{disambiguator}"
            disambiguator += 1
    taken.add(candidate)
    return candidate


def _clone_with_aliases(
    component: Any, mapping: dict[str, AliasedTimezone], scope: LocalTimezoneScope
) -> Any:
    """Deep-copy *component*, substituting aliased zones for private ones.

    A :func:`copy.deepcopy` memo is pre-seeded with each source private
    tzinfo mapped to its :class:`AliasedTimezone`. The copy machinery then
    substitutes the alias wherever that tzinfo appears -- inside nested
    datetimes, periods, lists, VALARMs -- without ever invoking the private
    zone's own (for pytz, unresolvable) copy/pickle path. Everything else,
    including parameters, naive/UTC/date values and unknown X- properties, is
    copied normally. The source component is never modified.
    """
    memo: dict[int, Any] = {}
    for original, alias_zone in mapping.items():
        real_zone = scope.timezone(original)
        memo[id(real_zone)] = alias_zone
    clone = deepcopy(component, memo)
    # Parameters carry the TZID as text; rewrite those to the alias so the
    # serialized text matches the rebound tzinfo.
    _rebind_component(clone, mapping)
    return clone


def _rebind_datetime(value, alias_zone: AliasedTimezone):
    """Return *value* with its tzinfo replaced by the aliased zone.

    Naive values (floating), pure dates and UTC values are returned
    unchanged because they never carried a private TZID.
    """
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=alias_zone)
    if isinstance(value, tuple):
        return tuple(_rebind_datetime(item, alias_zone) for item in value)
    if isinstance(value, list):
        return [_rebind_datetime(item, alias_zone) for item in value]
    return value


def _rebind_property(prop: Any, mapping: dict[str, AliasedTimezone]) -> None:
    """Rebind every datetime inside one property value to aliased zones.

    The property's TZID parameter is rewritten to the alias so the serialized
    text matches the bound :class:`datetime.tzinfo`. DATE, floating and UTC
    values are left untouched.
    """
    tzid = prop.params.get("TZID") if hasattr(prop, "params") else None
    if not tzid or tzid not in mapping:
        return
    alias_zone = mapping[tzid]
    prop.params["TZID"] = alias_zone.alias
    dts = getattr(prop, "dts", None)
    if dts is not None:
        # vDDDLists and similar multi-value properties.
        for leaf in dts:
            leaf.dt = _rebind_datetime(leaf.dt, alias_zone)
            if hasattr(leaf, "params") and leaf.params.get("TZID"):
                leaf.params["TZID"] = alias_zone.alias
    elif hasattr(prop, "dt"):
        prop.dt = _rebind_datetime(prop.dt, alias_zone)


def _rebind_component(component: Any, mapping: dict[str, AliasedTimezone]) -> None:
    """Recursively rebind a component and its sub-components."""
    for value in component.values():
        values = value if isinstance(value, list) else [value]
        for prop in values:
            if hasattr(prop, "params"):
                _rebind_property(prop, mapping)
    for sub in component.subcomponents:
        _rebind_component(sub, mapping)


def merge_ical(sources: Iterable[str | bytes]) -> Calendar:
    """Merge multiple single-calendar iCalendar sources into one Calendar.

    See :meth:`icalendar.Calendar.merge_ical` for the public contract; this
    function holds the implementation.
    """
    if isinstance(sources, (str, bytes)):
        raise TypeError(
            "sources must be a non-empty, finite iterable of str or bytes, "
            "not a single str or bytes"
        )
    if not hasattr(sources, "__iter__"):
        raise TypeError("sources must be a non-empty, finite iterable of str or bytes")

    iterator = iter(sources)
    try:
        first = next(iterator)
    except StopIteration:
        raise ValueError("sources must not be empty") from None

    output = Calendar()
    output.add("VERSION", "2.0")
    output.add("PRODID", DEFAULT_PRODID)
    merged_scope = LocalTimezoneScope()
    output._local_timezone_scope = merged_scope

    # Alias names already used in the output, so a generated alias can never
    # collide with a TZID present in another source.
    taken_aliases: set[str] = set()
    global_uids: set[str] = set()

    def process(source: Any, index: int) -> None:
        if not isinstance(source, (str, bytes)):
            raise TypeError(
                f"merge source {index}: every source must be str or bytes, "
                f"got {type(source).__name__}"
            )
        # from_ical with multiple=False guarantees exactly one top-level
        # VCALENDAR and raises ValueError otherwise (multiple calendars,
        # duplicate private TZID, unresolvable TZID, ...). Re-raise with the
        # 0-based source index so every merge error is locatable.
        try:
            calendar = Calendar.from_ical(source, local_timezones=True)
        except ValueError as error:
            raise _merge_error(index, str(error)) from error
        _validate_source_calendar(calendar, index)

        scope = calendar.local_timezone_scope
        # Build an alias and an aliased tzinfo for every private definition.
        source_mapping: dict[str, AliasedTimezone] = {}
        for definition_index, original in enumerate(scope.private_tzids):
            definition = scope.get_definition(original)
            real_zone = scope.timezone(original)
            alias = _make_alias(str(original), index, definition_index, taken_aliases)
            alias_zone = AliasedTimezone(alias, real_zone)
            source_mapping[str(original)] = alias_zone

            # VTIMEZONE components hold only naive local datetimes, never the
            # private tzinfo itself, so a normal deep copy is safe here.
            alias_definition = deepcopy(definition)
            alias_definition["TZID"] = alias
            merged_scope.register_alias(alias, alias_definition, alias_zone)
            output.add_component(alias_definition)

        # UID uniqueness across sources, preserving within-source sharing
        # (a master and its exceptions may share a UID). Compare the set of
        # distinct UIDs introduced by this source against earlier sources.
        source_uids = {str(event["UID"]) for event in calendar.events}
        for uid_text in sorted(source_uids):
            if uid_text in global_uids:
                raise _merge_error(
                    index,
                    f"duplicate UID {uid_text!r} across merge sources",
                )
        global_uids.update(source_uids)

        # Copy the VEVENT components (and their VALARMs / X- props) and rebind
        # their datetimes to the aliased zones. Order is preserved.
        for sub in calendar.subcomponents:
            if sub.name != "VEVENT":
                continue
            event_copy = _clone_with_aliases(sub, source_mapping, scope)
            output.add_component(event_copy)

    process(first, 0)
    for index, source in enumerate(iterator, start=1):
        process(source, index)

    return output


__all__ = ["AliasedTimezone", "merge_ical"]
