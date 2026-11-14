"""The public :class:`Occurrence` record returned by event expansion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One concrete happening of an event.

    Each occurrence is identified by the pair (:attr:`uid`,
    :attr:`recurrence_id`): :attr:`recurrence_id` names the *original*
    occurrence of the event series, even when an exception component moved the
    happening to a different time. Two different identities that were both
    rescheduled onto the same instant therefore produce two distinct
    occurrences.

    The fields are read-only:

    -   :attr:`uid`: the ``UID`` shared by the master component and all of its
        exception components, as a :class:`str`.
    -   :attr:`recurrence_id`: the original identity of the happening. This is
        a :class:`~datetime.date` for an all-day event and a UTC-aware
        :class:`~datetime.datetime` for a timed event. A non-recurring event
        has this identity too.
    -   :attr:`start`: the (possibly rescheduled) start as a UTC-aware
        :class:`~datetime.datetime`.
    -   :attr:`end`: the non-inclusive end as a UTC-aware
        :class:`~datetime.datetime`. It may equal :attr:`start` for a timed
        event without an explicit end.
    -   :attr:`summary`: the effective summary as a :class:`str`; an absent
        summary becomes an empty string.
    -   :attr:`all_day`: :data:`True` when the event is defined with DATE
        values rather than DATE-TIME values.

    Instances are immutable and hashable. They support attribute access and
    mapping-style access; they deliberately do not support equality against
    plain tuples or dictionaries.
    """

    uid: str
    recurrence_id: date | datetime
    start: datetime
    end: datetime
    summary: str
    all_day: bool

    def __getitem__(self, key: str) -> Any:
        """Read a field by name, mapping-style.

        Raises:
            KeyError: If ``key`` is not one of the field names.
        """
        try:
            return getattr(self, key)
        except AttributeError as e:
            raise KeyError(key) from e

    def keys(self) -> tuple[str, ...]:
        """The field names, in declaration order."""
        return self.__dataclass_fields__.keys()  # type: ignore[attr-defined]

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def as_dict(self) -> dict[str, Any]:
        """Return a shallow dictionary copy of the record.

        The returned dictionary can be modified without affecting the
        occurrence. It is a convenient input for ETL row builders.
        """
        return {
            "uid": self.uid,
            "recurrence_id": self.recurrence_id,
            "start": self.start,
            "end": self.end,
            "summary": self.summary,
            "all_day": self.all_day,
        }


__all__ = ["Occurrence"]
