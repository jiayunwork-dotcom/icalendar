"""Private, per-calendar time zone scopes.

When a calendar is parsed with ``local_timezones=True`` its ``VTIMEZONE``
components define time zones for *that calendar only*. The definitions never
enter the global :data:`~icalendar.timezone.tzp` cache, and two calendars may
declare the same ``TZID`` with different rules without ever influencing each
other.

The isolation is provided by :class:`LocalTimezoneScope`, an object owned by
the parsed :class:`~icalendar.cal.Calendar`. The parser resolves every TZID
through the scope of the calendar it is currently reading:

* a TZID with a private ``VTIMEZONE`` definition in the same calendar resolves
  to the :class:`datetime.tzinfo` built from that definition;
* any other TZID is resolved solely through the active *standard* timezone
  provider (:meth:`icalendar.timezone.tzp.TZP.standard_timezone`), never
  through definitions left behind by another calendar;
* a TZID that can be resolved neither way raises :class:`ValueError`.

Scopes are plain per-parse objects. They hold no module level state, so two
threads parsing different calendars cannot interfere and a scope -- together
with the private time zone objects it created -- is collected as soon as the
owning calendar is no longer referenced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from icalendar.timezone import tzp

if TYPE_CHECKING:
    from datetime import tzinfo

    from icalendar.cal.timezone import Timezone


class LocalTimezoneError(ValueError):
    """A TZID cannot be resolved inside a private time zone scope.

    This is a :class:`ValueError` so callers that already catch
    :class:`ValueError` for unresolvable time zones keep working.
    """


class LocalTimezoneScope:
    """The private time zone definitions of one ``VCALENDAR``.

    Each calendar parsed with ``local_timezones=True`` receives its own
    scope. The scope stores the ``VTIMEZONE`` components declared in that
    calendar and the :class:`datetime.tzinfo` objects derived from them.

    Forward references are supported: a ``VTIMEZONE`` may be declared after
    the ``VEVENT`` that references it because resolution happens lazily, the
    first time the TZID is looked up.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, Timezone] = {}
        self._timezones: dict[str, tzinfo] = {}

    def add_definition(self, timezone_component: Timezone) -> None:
        """Register a ``VTIMEZONE`` component under its TZID.

        Parameters:
            timezone_component: The calendar component defining a time zone.

        Raises:
            LocalTimezoneError: If the TZID is declared more than once in
                this calendar, even when both definitions are identical.
        """
        tzid = str(timezone_component["TZID"])
        cleaned = tzp.clean_timezone_id(tzid)
        existing = self._definitions.get(cleaned)
        if existing is not None or cleaned in self._timezones:
            raise LocalTimezoneError(
                f"Duplicate VTIMEZONE definition for private TZID {tzid!r} "
                "within the same VCALENDAR"
            )
        self._definitions[cleaned] = timezone_component

    def knows_private(self, tzid: str) -> bool:
        """Whether *tzid* has a private definition in this scope."""
        return tzp.clean_timezone_id(tzid) in self._definitions

    def _private_timezone(self, tzid: str) -> tzinfo | None:
        """Build (and cache) the tzinfo for a private definition."""
        cleaned = tzp.clean_timezone_id(tzid)
        definition = self._definitions.get(cleaned)
        if definition is None:
            return None
        zone = self._timezones.get(cleaned)
        if zone is None:
            # Build directly from the component; never look the id up in the
            # global provider so a standard or previously cached zone cannot
            # shadow this private definition.
            zone = tzp.create_timezone(definition)
            self._timezones[cleaned] = zone
        return zone

    def timezone(self, tzid: str) -> tzinfo | None:
        """Resolve *tzid* within this scope, or ``None`` if it is unknown.

        Private definitions win over the standard library; everything else
        is answered by the standard provider only. The global cache of
        parsed ``VTIMEZONE`` components is never consulted.
        """
        zone = self._private_timezone(tzid)
        if zone is not None:
            return zone
        return tzp.standard_timezone(tzid)

    def resolve(self, tzid: str, *, property_name: str) -> tzinfo:
        """Resolve *tzid* or raise with a message naming the property.

        Parameters:
            tzid: The TZID parameter value to resolve.
            property_name: The iCalendar property carrying the TZID, used in
                the error message.

        Raises:
            LocalTimezoneError: If neither a private definition nor the
                standard timezone library knows the TZID.
        """
        zone = self.timezone(tzid)
        if zone is None:
            raise LocalTimezoneError(
                f"Cannot resolve TZID {tzid!r} referenced by property "
                f"{property_name!r}: no matching VTIMEZONE in this calendar "
                "and it is not a known standard time zone"
            )
        return zone

    @property
    def private_tzids(self) -> tuple[str, ...]:
        """The cleaned TZIDs that have private definitions in this scope."""
        return tuple(self._definitions)

    def get_definition(self, tzid: str) -> Timezone | None:
        """Return the private ``VTIMEZONE`` component for *tzid*."""
        return self._definitions.get(tzp.clean_timezone_id(tzid))

    def register_alias(self, alias: str, definition: Timezone, zone: tzinfo) -> None:
        """Register an already-aliased definition produced by a merge.

        This is used by the merge builder to install a renamed ``VTIMEZONE``
        together with the timezone object bound to the merged events. The
        alias is assumed to be unique within this scope.
        """
        self._definitions[alias] = definition
        self._timezones[alias] = zone


__all__ = ["LocalTimezoneError", "LocalTimezoneScope"]
