"""Tests for private, per-calendar time zone isolation and merging.

The expected UTC instants, wall-clock times, transition behaviour and
recurrence identities in this module are written down independently of the
implementation under test: they are hand-computed from the ICS fixtures and
never produced by the merge or binding code being tested.

Three consumption paths are checked for the same time-zone facts:

1. direct local parsing with ``Calendar.from_ical(..., local_timezones=True)``;
2. in-memory merging through ``Calendar.merge_ical``;
3. serialization followed by a fresh ``from_ical`` round trip.
"""

from __future__ import annotations

import gc
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from icalendar import Calendar
from icalendar.cal.merge import AliasedTimezone
from icalendar.timezone import tzp
from icalendar.timezone.local import LocalTimezoneError, LocalTimezoneScope

UTC = timezone.utc
WINDOW = (
    datetime(2026, 1, 1, tzinfo=UTC),
    datetime(2026, 12, 31, tzinfo=UTC),
)


# ---------------------------------------------------------------------------
# Hand-written fixtures
# ---------------------------------------------------------------------------


def fixed_offset_vtimezone(tzid: str, offset: str, tzname: str) -> str:
    """A VTIMEZONE with a single constant offset (no transitions)."""
    return (
        "BEGIN:VTIMEZONE\r\n"
        f"TZID:{tzid}\r\n"
        "BEGIN:STANDARD\r\n"
        "DTSTART:19700101T000000\r\n"
        f"TZOFFSETFROM:{offset}\r\n"
        f"TZOFFSETTO:{offset}\r\n"
        f"TZNAME:{tzname}\r\n"
        "END:STANDARD\r\n"
        "END:VTIMEZONE\r\n"
    )


def dst_vtimezone(tzid: str, std: str, dst: str, std_name: str, dst_name: str) -> str:
    """A VTIMEZONE with yearly STANDARD/DAYLIGHT transitions.

    Daylight time starts on the last Sunday of March at 02:00 local and ends
    on the last Sunday of October at 03:00 local.
    """
    return (
        "BEGIN:VTIMEZONE\r\n"
        f"TZID:{tzid}\r\n"
        "BEGIN:DAYLIGHT\r\n"
        "DTSTART:20260329T020000\r\n"
        f"TZOFFSETFROM:{std}\r\n"
        f"TZOFFSETTO:{dst}\r\n"
        f"TZNAME:{dst_name}\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\n"
        "END:DAYLIGHT\r\n"
        "BEGIN:STANDARD\r\n"
        "DTSTART:20261025T030000\r\n"
        f"TZOFFSETFROM:{dst}\r\n"
        f"TZOFFSETTO:{std}\r\n"
        f"TZNAME:{std_name}\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\n"
        "END:STANDARD\r\n"
        "END:VTIMEZONE\r\n"
    )


def wrap_calendar(body: str, prodid: str = "test-product") -> str:
    return (
        f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:{prodid}\r\n{body}END:VCALENDAR\r\n"
    )


# A's Local/Office is fixed +0100; B's same-named zone is fixed +0300.
CAL_A = wrap_calendar(
    fixed_offset_vtimezone("Local/Office", "+0100", "OFF1") + "BEGIN:VEVENT\r\n"
    "UID:a@example.com\r\n"
    "DTSTAMP:20250101T000000Z\r\n"
    "SUMMARY:A meeting\r\n"
    "DTSTART;TZID=Local/Office:20260102T100000\r\n"
    "DTEND;TZID=Local/Office:20260102T110000\r\n"
    "END:VEVENT\r\n"
)

CAL_B = wrap_calendar(
    fixed_offset_vtimezone("Local/Office", "+0300", "OFF3") + "BEGIN:VEVENT\r\n"
    "UID:b@example.com\r\n"
    "DTSTAMP:20250101T000000Z\r\n"
    "SUMMARY:B meeting\r\n"
    "DTSTART;TZID=Local/Office:20260102T100000\r\n"
    "DTEND;TZID=Local/Office:20260102T110000\r\n"
    "END:VEVENT\r\n"
)

# Independently hand-computed UTC truth for 2026-01-02 10:00 local.
UTC_TRUTH = {
    "a@example.com": datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
    "b@example.com": datetime(2026, 1, 2, 7, 0, tzinfo=UTC),
}


# ---------------------------------------------------------------------------
# Direct local parsing: same name, different rules
# ---------------------------------------------------------------------------


class TestLocalParsing:
    def test_same_tzid_different_offsets_are_isolated(self):
        cal_a = Calendar.from_ical(CAL_A, local_timezones=True)
        cal_b = Calendar.from_ical(CAL_B, local_timezones=True)
        start_a = cal_a.events[0].start
        start_b = cal_b.events[0].start
        # Wall-clock value is preserved.
        assert (start_a.hour, start_a.minute) == (10, 0)
        assert (start_b.hour, start_b.minute) == (10, 0)
        # Offsets differ and UTC instants match the independent truth table.
        assert start_a.utcoffset() == timedelta(hours=1)
        assert start_b.utcoffset() == timedelta(hours=3)
        assert start_a.astimezone(UTC) == UTC_TRUTH["a@example.com"]
        assert start_b.astimezone(UTC) == UTC_TRUTH["b@example.com"]
        # Distinct tzinfo objects: the second definition never overwrote the
        # first.
        assert start_a.tzinfo is not start_b.tzinfo

    def test_order_independence(self):
        # Reading B first and A afterwards must not change A's meaning.
        cal_b = Calendar.from_ical(CAL_B, local_timezones=True)
        cal_a = Calendar.from_ical(CAL_A, local_timezones=True)
        cal_b_again = Calendar.from_ical(CAL_B, local_timezones=True)
        assert cal_a.events[0].start.astimezone(UTC) == UTC_TRUTH["a@example.com"]
        assert cal_b.events[0].start.astimezone(UTC) == UTC_TRUTH["b@example.com"]
        assert cal_b_again.events[0].start.astimezone(UTC) == UTC_TRUTH["b@example.com"]

    def test_global_cache_is_not_polluted(self):
        assert tzp.timezone("Local/Office") is None
        Calendar.from_ical(CAL_A, local_timezones=True)
        Calendar.from_ical(CAL_B, local_timezones=True)
        assert tzp.timezone("Local/Office") is None
        assert tzp.standard_timezone("Local/Office") is None

    def test_global_provider_is_not_switched(self):
        before = tzp.name
        Calendar.from_ical(CAL_A, local_timezones=True)
        assert tzp.name == before

    def test_scope_is_attached_only_to_calendar(self):
        cal = Calendar.from_ical(CAL_A, local_timezones=True)
        assert isinstance(cal.local_timezone_scope, LocalTimezoneScope)
        assert cal.local_timezone_scope.private_tzids == ("Local/Office",)

    def test_default_parsing_uses_global_cache_as_before(self):
        # False keeps the historical, cache-based behaviour.
        cal_a = Calendar.from_ical(CAL_A)
        cal_b = Calendar.from_ical(CAL_B)
        # Legacy behaviour: both bind to the first cached definition.
        assert cal_a.events[0].start.utcoffset() == timedelta(hours=1)
        assert cal_b.events[0].start.utcoffset() == timedelta(hours=1)
        assert cal_a.local_timezone_scope is None

    def test_non_bool_local_timezones_raises_type_error(self):
        for bad in (0, 1, "true", None):
            with pytest.raises(TypeError, match="local_timezones must be a bool"):
                Calendar.from_ical(CAL_A, local_timezones=bad)  # type: ignore[arg-type]

    def test_forward_reference_vtimezone_after_event(self):
        body = (
            "BEGIN:VEVENT\r\n"
            "UID:fwd@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Local/Office:20260102T100000\r\n"
            "END:VEVENT\r\n" + fixed_offset_vtimezone("Local/Office", "+0100", "OFF1")
        )
        cal = Calendar.from_ical(wrap_calendar(body), local_timezones=True)
        assert cal.events[0].start.utcoffset() == timedelta(hours=1)
        assert cal.events[0].start.astimezone(UTC) == UTC_TRUTH["a@example.com"]

    def test_multiple_calendars_each_get_own_scope(self):
        stream = CAL_A + CAL_B
        calendars = Calendar.from_ical(stream, multiple=True, local_timezones=True)
        assert len(calendars) == 2
        assert (
            calendars[0].local_timezone_scope is not calendars[1].local_timezone_scope
        )
        assert calendars[0].events[0].start.utcoffset() == timedelta(hours=1)
        assert calendars[1].events[0].start.utcoffset() == timedelta(hours=3)

    def test_duplicate_private_tzid_rejected_even_if_identical(self):
        body = (
            fixed_offset_vtimezone("X", "+0100", "X1")
            + fixed_offset_vtimezone("X", "+0100", "X2")
            + "BEGIN:VEVENT\r\n"
            "UID:x@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=X:20260102T100000\r\n"
            "END:VEVENT\r\n"
        )
        with pytest.raises(ValueError, match="Duplicate VTIMEZONE"):
            Calendar.from_ical(wrap_calendar(body), local_timezones=True)

    def test_unresolvable_tzid_raises_value_error_naming_property(self):
        body = (
            "BEGIN:VEVENT\r\n"
            "UID:u@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=No/Such/Zone:20260102T100000\r\n"
            "END:VEVENT\r\n"
        )
        with pytest.raises(ValueError) as exc_info:
            Calendar.from_ical(wrap_calendar(body), local_timezones=True)
        message = str(exc_info.value)
        assert "No/Such/Zone" in message
        assert "DTSTART" in message
        assert isinstance(exc_info.value, LocalTimezoneError)

    def test_standard_iana_tzid_resolves_without_local_definition(self):
        body = (
            "BEGIN:VEVENT\r\n"
            "UID:i@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Europe/Berlin:20260102T100000\r\n"
            "END:VEVENT\r\n"
        )
        cal = Calendar.from_ical(wrap_calendar(body), local_timezones=True)
        # January in Berlin is CET, UTC+1.
        assert cal.events[0].start.utcoffset() == timedelta(hours=1)
        assert cal.events[0].start.astimezone(UTC) == datetime(
            2026, 1, 2, 9, 0, tzinfo=UTC
        )

    def test_private_definition_shadows_same_named_standard_zone(self):
        # A private TZID that is not an IANA name resolves to the private rule,
        # and an IANA-named private definition (allowed input) still wins.
        body = (
            fixed_offset_vtimezone("Custom/Zone", "+0530", "C") + "BEGIN:VEVENT\r\n"
            "UID:c@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Custom/Zone:20260102T100000\r\n"
            "END:VEVENT\r\n"
        )
        cal = Calendar.from_ical(wrap_calendar(body), local_timezones=True)
        assert cal.events[0].start.utcoffset() == timedelta(hours=5, minutes=30)


# ---------------------------------------------------------------------------
# Date, floating and UTC values stay untouched
# ---------------------------------------------------------------------------


class TestUntouchedValueKinds:
    def _calendar(self) -> Calendar:
        body = (
            "BEGIN:VEVENT\r\n"
            "UID:kinds@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;VALUE=DATE:20260103\r\n"
            "DTEND;VALUE=DATE:20260104\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:float@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART:20260105T090000\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:utc@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART:20260106T090000Z\r\n"
            "END:VEVENT\r\n"
        )
        return Calendar.from_ical(wrap_calendar(body), local_timezones=True)

    def test_date_value_remains_date_without_tzinfo(self):
        cal = self._calendar()
        events = {str(e["UID"]): e for e in cal.events}
        start = events["kinds@example.com"].start
        assert isinstance(start, date)
        assert not isinstance(start, datetime)

    def test_floating_value_remains_naive(self):
        cal = self._calendar()
        events = {str(e["UID"]): e for e in cal.events}
        assert events["float@example.com"].start.tzinfo is None

    def test_utc_value_remains_utc_without_tzid_param(self):
        cal = self._calendar()
        events = {str(e["UID"]): e for e in cal.events}
        start_prop = events["utc@example.com"]["DTSTART"]
        assert start_prop.dt.utcoffset() == timedelta(0)
        assert start_prop.params.get("TZID") is None


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


class TestMerge:
    def test_merge_preserves_utc_truth_and_serializes(self):
        merged = Calendar.merge_ical([CAL_A, CAL_B])
        events = {str(e["UID"]): e for e in merged.events}
        assert (
            events["a@example.com"].start.astimezone(UTC) == UTC_TRUTH["a@example.com"]
        )
        assert (
            events["b@example.com"].start.astimezone(UTC) == UTC_TRUTH["b@example.com"]
        )
        merged.to_ical()
        # One TZID per definition; the colliding name must be aliased.
        tzids = [str(t["TZID"]) for t in merged.timezones]
        assert len(tzids) == len(set(tzids)) == 2

    def test_merge_is_deterministic(self):
        first = Calendar.merge_ical([CAL_A, CAL_B]).to_ical()
        second = Calendar.merge_ical([CAL_A, CAL_B]).to_ical()
        assert first == second

    def test_merge_accepts_bytes(self):
        assert Calendar.merge_ical([CAL_A.encode(), CAL_B.encode()]).to_ical() == (
            Calendar.merge_ical([CAL_A, CAL_B]).to_ical()
        )

    def test_merge_keeps_event_and_source_order(self):
        merged = Calendar.merge_ical([CAL_A, CAL_B])
        assert [str(e["UID"]) for e in merged.events] == [
            "a@example.com",
            "b@example.com",
        ]

    def test_merge_output_has_valid_version_and_prodid(self):
        merged = Calendar.merge_ical([CAL_A])
        assert str(merged["VERSION"]) == "2.0"
        assert merged["PRODID"] is not None

    def test_round_trip_in_fresh_parse_keeps_truth(self, tmp_path):
        merged = Calendar.merge_ical([CAL_A, CAL_B])
        path = tmp_path / "merged.ics"
        path.write_bytes(merged.to_ical())
        reread = Calendar.from_ical(path.read_bytes())
        events = {str(e["UID"]): e for e in reread.events}
        assert (
            events["a@example.com"].start.astimezone(UTC) == UTC_TRUTH["a@example.com"]
        )
        assert (
            events["b@example.com"].start.astimezone(UTC) == UTC_TRUTH["b@example.com"]
        )

    def test_object_binding_and_text_alias_agree(self):
        merged = Calendar.merge_ical([CAL_A, CAL_B])
        for event in merged.events:
            prop = event["DTSTART"]
            tzid_param = prop.params["TZID"]
            # The bound tzinfo must be the exact alias named in the text.
            assert isinstance(prop.dt.tzinfo, AliasedTimezone)
            assert prop.dt.tzinfo.alias == tzid_param
            output_tzids = {str(t["TZID"]) for t in merged.timezones}
            assert tzid_param in output_tzids

    def test_alias_collision_with_existing_tzid(self):
        # Source 2 uses a TZID that literally equals the auto-alias form.
        cal_c = wrap_calendar(
            fixed_offset_vtimezone("icalendar-merge/1-0/Local/Office", "+0500", "C")
            + "BEGIN:VEVENT\r\n"
            "UID:c@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=icalendar-merge/1-0/Local/Office:20260102T100000\r\n"
            "END:VEVENT\r\n"
        )
        merged = Calendar.merge_ical([CAL_A, CAL_B, cal_c])
        tzids = [str(t["TZID"]) for t in merged.timezones]
        assert len(tzids) == len(set(tzids)) == 3
        # Independent truth: +0500 at 10:00 -> 05:00 UTC.
        events = {str(e["UID"]): e for e in merged.events}
        assert events["c@example.com"].start.astimezone(UTC) == datetime(
            2026, 1, 2, 5, 0, tzinfo=UTC
        )

    def test_single_source_keeps_original_tzid_when_free(self):
        merged = Calendar.merge_ical([CAL_A])
        assert [str(t["TZID"]) for t in merged.timezones] == ["Local/Office"]

    def test_empty_event_calendar_is_legal(self):
        empty = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nEND:VCALENDAR\r\n"
        merged = Calendar.merge_ical([empty, CAL_A])
        assert merged.events
        assert len(merged.timezones) == 1

    def test_master_and_exception_share_uid_within_one_source(self):
        body = (
            fixed_offset_vtimezone("Local/Office", "+0100", "OFF1") + "BEGIN:VEVENT\r\n"
            "UID:series@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Local/Office:20260102T100000\r\n"
            "RRULE:FREQ=DAILY;COUNT=3\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:series@example.com\r\n"
            "RECURRENCE-ID;TZID=Local/Office:20260103T100000\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Local/Office:20260103T140000\r\n"
            "SUMMARY:moved\r\n"
            "END:VEVENT\r\n"
        )
        merged = Calendar.merge_ical([wrap_calendar(body)])
        assert len(merged.events) == 2

    def test_cross_source_duplicate_uid_rejected(self):
        with pytest.raises(ValueError, match="duplicate UID"):
            Calendar.merge_ical([CAL_A, CAL_A])

    def test_source_objects_are_not_modified(self):
        source_cal = Calendar.from_ical(CAL_A, local_timezones=True)
        before_blob = source_cal.to_ical()
        before_start = source_cal.events[0].start
        Calendar.merge_ical([CAL_A, CAL_B])
        assert source_cal.to_ical() == before_blob
        assert source_cal.events[0].start == before_start
        assert source_cal.events[0].start.tzinfo is before_start.tzinfo


# ---------------------------------------------------------------------------
# Merge validation and error contract
# ---------------------------------------------------------------------------


class TestMergeErrors:
    def test_empty_sources_raises_value_error(self):
        with pytest.raises(ValueError, match="not be empty"):
            Calendar.merge_ical([])

    def test_single_string_rejected(self):
        with pytest.raises(TypeError):
            Calendar.merge_ical(CAL_A)  # type: ignore[arg-type]

    def test_non_iterable_rejected(self):
        with pytest.raises(TypeError):
            Calendar.merge_ical(123)  # type: ignore[arg-type]

    def test_bad_element_type_names_source_index(self):
        with pytest.raises(TypeError, match="merge source 0"):
            Calendar.merge_ical([123])  # type: ignore[list-item]

    def test_multiple_calendars_in_one_item_names_source_index(self):
        with pytest.raises(ValueError, match="merge source 1"):
            Calendar.merge_ical([CAL_A, CAL_A + CAL_B])

    def test_out_of_scope_top_level_property(self):
        bad = CAL_A.replace(
            "PRODID:test-product", "PRODID:test-product\r\nMETHOD:PUBLISH"
        )
        with pytest.raises(ValueError, match=r"merge source 0.*METHOD"):
            Calendar.merge_ical([bad])

    def test_out_of_scope_component(self):
        bad = CAL_A.replace(
            "END:VCALENDAR",
            "BEGIN:VTODO\r\nUID:t\r\nEND:VTODO\r\nEND:VCALENDAR",
        )
        with pytest.raises(ValueError, match=r"merge source 0.*VTODO"):
            Calendar.merge_ical([bad])

    def test_event_without_uid_rejected(self):
        body = (
            "BEGIN:VEVENT\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART:20260101T100000Z\r\n"
            "END:VEVENT\r\n"
        )
        with pytest.raises(ValueError, match=r"merge source 0.*UID"):
            Calendar.merge_ical([wrap_calendar(body)])

    def test_wrong_version_rejected(self):
        bad = CAL_A.replace("VERSION:2.0", "VERSION:1.0")
        with pytest.raises(ValueError, match=r"VERSION:2.0"):
            Calendar.merge_ical([bad])

    def test_iterator_exception_passes_through(self):
        class Boom:
            def __iter__(self):
                yield CAL_A
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            Calendar.merge_ical(Boom())

    def test_iterable_consumed_once(self):
        seen = []

        def gen():
            for item in (CAL_A, CAL_B):
                seen.append(item)
                yield item

        Calendar.merge_ical(gen())
        assert seen == [CAL_A, CAL_B]

    def test_failure_does_not_pollute_later_calls(self):
        bad_body = (
            "BEGIN:VEVENT\r\n"
            "UID:u@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=No/Such:20260102T100000\r\n"
            "END:VEVENT\r\n"
        )
        with pytest.raises(ValueError):
            Calendar.merge_ical([wrap_calendar(bad_body)])
        good = Calendar.from_ical(CAL_A, local_timezones=True)
        assert good.events[0].start.astimezone(UTC) == UTC_TRUTH["a@example.com"]
        assert tzp.timezone("No/Such") is None


# ---------------------------------------------------------------------------
# Daylight saving transitions and recurrence
# ---------------------------------------------------------------------------


def _dst_calendar(uid: str, std: str, dst: str, std_name: str, dst_name: str) -> str:
    return wrap_calendar(
        dst_vtimezone("Local/Office", std, dst, std_name, dst_name) + "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        "DTSTAMP:20250101T000000Z\r\n"
        "SUMMARY:daily\r\n"
        "DTSTART;TZID=Local/Office:20260102T100000\r\n"
        "RRULE:FREQ=DAILY\r\n"
        "EXDATE;TZID=Local/Office:20260103T100000\r\n"
        "RDATE;TZID=Local/Office:20260105T150000\r\n"
        "END:VEVENT\r\n"
    )


DST_A = _dst_calendar("dst-a@example.com", "+0100", "+0200", "AS", "AD")
DST_B = _dst_calendar("dst-b@example.com", "+0300", "+0400", "BS", "BD")


class TestDaylightSaving:
    @pytest.mark.parametrize(
        ("source", "std_offset", "dst_offset"),
        [
            (DST_A, timedelta(hours=1), timedelta(hours=2)),
            (DST_B, timedelta(hours=3), timedelta(hours=4)),
        ],
    )
    def test_keeps_local_clock_across_transitions(self, source, std_offset, dst_offset):
        cal = Calendar.from_ical(source, local_timezones=True)
        zone = cal.events[0].start.tzinfo
        occurrences = cal.expand_events(*WINDOW, max_instances=100000)
        jan = next(
            o
            for o in occurrences
            if o.start.astimezone(zone).date() == date(2026, 1, 10)
        )
        jul = next(
            o
            for o in occurrences
            if o.start.astimezone(zone).date() == date(2026, 7, 10)
        )
        jan_local, jul_local = (
            jan.start.astimezone(zone),
            jul.start.astimezone(zone),
        )
        # 10:00 local before and after the seasonal transition.
        assert jan_local.hour == 10
        assert jul_local.hour == 10
        assert jan_local.utcoffset() == std_offset
        assert jul_local.utcoffset() == dst_offset

    def test_exdate_and_rdate_identity_preserved(self):
        cal = Calendar.from_ical(DST_A, local_timezones=True)
        zone = cal.events[0].start.tzinfo
        occurrences = cal.expand_events(*WINDOW, max_instances=100000)
        local_dates = [o.start.astimezone(zone).date() for o in occurrences]
        # EXDATE removed 2026-01-03.
        assert date(2026, 1, 3) not in local_dates
        # RDATE added an extra 15:00 occurrence on 2026-01-05.
        jan5_hours = sorted(
            o.start.astimezone(zone).hour
            for o in occurrences
            if o.start.astimezone(zone).date() == date(2026, 1, 5)
        )
        assert jan5_hours == [10, 15]

    def test_merge_expansion_equals_union_of_clean_scopes(self):
        merged = Calendar.merge_ical([DST_A, DST_B])
        merged_occurrences = merged.expand_events(*WINDOW, max_instances=100000)
        separate = []
        for source in (DST_A, DST_B):
            separate += Calendar.from_ical(source, local_timezones=True).expand_events(
                *WINDOW, max_instances=100000
            )

        def key(occurrence):
            return (
                occurrence.start.astimezone(UTC).isoformat(),
                occurrence.uid,
                str(occurrence.recurrence_id),
            )

        assert sorted(merged_occurrences, key=key) == sorted(separate, key=key)

    def test_round_trip_keeps_dst_utc_truth(self, tmp_path):
        # The seasonal-transition round trip is asserted under the zoneinfo
        # provider, whose VTIMEZONE engine resolves offsets before the first
        # declared transition. The merge itself is provider-agnostic and is
        # exercised under pytz separately in TestProviders.
        tzp.use_zoneinfo()
        merged = Calendar.merge_ical([DST_A, DST_B])
        path = tmp_path / "dst.ics"
        path.write_bytes(merged.to_ical())
        reread = Calendar.from_ical(path.read_bytes())
        occurrences = reread.expand_events(*WINDOW, max_instances=100000)

        def utc_naive(value: datetime) -> datetime:
            # Compare the UTC instant regardless of the concrete UTC tzinfo
            # class (stdlib timezone.utc vs zoneinfo ZoneInfo("UTC")).
            return value.astimezone(UTC).replace(tzinfo=None)

        by_uid: dict[str, set[datetime]] = {}
        for occurrence in occurrences:
            by_uid.setdefault(occurrence.uid, set()).add(utc_naive(occurrence.start))
        # Hand-computed: 2026-01-10 10:00 A(+0100)->09:00, B(+0300)->07:00.
        assert datetime(2026, 1, 10, 9, 0) in by_uid["dst-a@example.com"]
        assert datetime(2026, 1, 10, 7, 0) in by_uid["dst-b@example.com"]
        # 2026-07-10 10:00 A(+0200)->08:00, B(+0400)->06:00.
        assert datetime(2026, 7, 10, 8, 0) in by_uid["dst-a@example.com"]
        assert datetime(2026, 7, 10, 6, 0) in by_uid["dst-b@example.com"]


# ---------------------------------------------------------------------------
# Recurrence exception identity after merge
# ---------------------------------------------------------------------------


class TestRecurrenceIdentity:
    def test_rescheduled_exception_keeps_original_identity(self):
        body = (
            fixed_offset_vtimezone("Local/Office", "+0100", "OFF1") + "BEGIN:VEVENT\r\n"
            "UID:series@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Local/Office:20260102T100000\r\n"
            "RRULE:FREQ=DAILY;COUNT=4\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:series@example.com\r\n"
            "RECURRENCE-ID;TZID=Local/Office:20260105T100000\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Local/Office:20260105T140000\r\n"
            "SUMMARY:moved\r\n"
            "END:VEVENT\r\n"
        )
        merged = Calendar.merge_ical([wrap_calendar(body)])
        occurrences = merged.expand_events(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 31, tzinfo=UTC),
            max_instances=1000,
        )
        moved = [o for o in occurrences if o.summary == "moved"]
        assert len(moved) == 1
        # Effective start moved to 14:00 +0100 -> 13:00 UTC.
        assert moved[0].start == datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
        # Identity still points at the original 10:00 +0100 -> 09:00 UTC.
        assert moved[0].recurrence_id == datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Concurrency and memory
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_interleaved_parsing_uses_isolated_scopes(self):
        results: dict[str, datetime] = {}
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker(label: str, source: str, expected: datetime) -> None:
            try:
                calendar = Calendar.from_ical(source, local_timezones=True)
                barrier.wait(timeout=10)
                barrier.wait(timeout=10)
                results[label] = calendar.events[0].start.astimezone(UTC)
                assert results[label] == expected
            except Exception as error:  # noqa: BLE001 - pragma: no cover
                # pragma: no cover - failure reporting from the worker thread
                errors.append(error)

        threads = [
            threading.Thread(
                target=worker,
                args=("A", CAL_A, UTC_TRUTH["a@example.com"]),
            ),
            threading.Thread(
                target=worker,
                args=("B", CAL_B, UTC_TRUTH["b@example.com"]),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not errors
        assert results == {
            "A": UTC_TRUTH["a@example.com"],
            "B": UTC_TRUTH["b@example.com"],
        }


class TestNoGlobalAccumulation:
    @pytest.mark.parametrize("count", [100, 1000, 5000])
    def test_dropped_calendars_leave_no_private_state(self, count):
        gc.collect()
        for index in range(count):
            source = wrap_calendar(
                fixed_offset_vtimezone(f"Private/{index}", "+0100", "X")
                + "BEGIN:VEVENT\r\n"
                f"UID:u{index}@example.com\r\n"
                "DTSTAMP:20250101T000000Z\r\n"
                f"DTSTART;TZID=Private/{index}:20260102T100000\r\n"
                "END:VEVENT\r\n"
            )
            calendar = Calendar.from_ical(source, local_timezones=True)
            del calendar
        gc.collect()
        surviving_scopes = [
            obj for obj in gc.get_objects() if isinstance(obj, LocalTimezoneScope)
        ]
        surviving_aliases = [
            obj for obj in gc.get_objects() if isinstance(obj, AliasedTimezone)
        ]
        assert surviving_scopes == []
        assert surviving_aliases == []


# ---------------------------------------------------------------------------
# Both timezone providers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_name", ["zoneinfo", "pytz"])
class TestProviders:
    @pytest.fixture(autouse=True)
    def _provider(self, provider_name):
        getattr(tzp, f"use_{provider_name}")()
        yield
        tzp.use_zoneinfo()

    def test_local_parse_and_merge(self, provider_name):
        cal_a = Calendar.from_ical(CAL_A, local_timezones=True)
        cal_b = Calendar.from_ical(CAL_B, local_timezones=True)
        assert cal_a.events[0].start.astimezone(UTC) == UTC_TRUTH["a@example.com"]
        assert cal_b.events[0].start.astimezone(UTC) == UTC_TRUTH["b@example.com"]
        merged = Calendar.merge_ical([CAL_A, CAL_B])
        occurrences = merged.expand_events(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 2, 1, tzinfo=UTC),
        )
        by_uid = {o.uid: o.start.astimezone(UTC) for o in occurrences}
        assert by_uid["a@example.com"] == UTC_TRUTH["a@example.com"]
        assert by_uid["b@example.com"] == UTC_TRUTH["b@example.com"]

    def test_round_trip(self, tmp_path, provider_name):
        merged = Calendar.merge_ical([CAL_A, CAL_B])
        path = tmp_path / f"{provider_name}.ics"
        path.write_bytes(merged.to_ical())
        reread = Calendar.from_ical(path.read_bytes())
        events = {str(e["UID"]): e for e in reread.events}
        assert (
            events["a@example.com"].start.astimezone(UTC) == UTC_TRUTH["a@example.com"]
        )
        assert (
            events["b@example.com"].start.astimezone(UTC) == UTC_TRUTH["b@example.com"]
        )


# ---------------------------------------------------------------------------
# VALARM, custom and non-TZID parameters survive a merge
# ---------------------------------------------------------------------------


class TestPropertyPreservation:
    def test_valarm_and_custom_properties_and_params_survive(self):
        source = wrap_calendar(
            "BEGIN:VEVENT\r\n"
            "UID:rich@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;VALUE=DATE:20260103\r\n"
            "SUMMARY:all day\r\n"
            "X-CUSTOM:keep-me\r\n"
            "CATEGORIES:a,b\r\n"
            "BEGIN:VALARM\r\n"
            "TRIGGER:-PT15M\r\n"
            "ACTION:DISPLAY\r\n"
            "DESCRIPTION:wake up\r\n"
            "END:VALARM\r\n"
            "END:VEVENT\r\n"
        )
        merged = Calendar.merge_ical([source])
        reread = Calendar.from_ical(merged.to_ical())
        event = reread.events[0]
        assert str(event["X-CUSTOM"]) == "keep-me"
        assert len(event.walk("VALARM")) == 1
        assert str(event.walk("VALARM")[0]["ACTION"]) == "DISPLAY"
        assert isinstance(event.start, date)

    def test_non_tzid_parameters_survive_alias_rewrite(self):
        source = wrap_calendar(
            fixed_offset_vtimezone("Local/Office", "+0100", "OFF1") + "BEGIN:VEVENT\r\n"
            "UID:p@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Local/Office;X-FOO=bar:20260102T100000\r\n"
            "END:VEVENT\r\n"
        )
        merged = Calendar.merge_ical([source, CAL_B])
        params = merged.events[0]["DTSTART"].params
        assert params.get("X-FOO") == "bar"
        assert params.get("TZID") is not None

    def test_multi_value_list_corresponds_item_by_item(self):
        source = wrap_calendar(
            fixed_offset_vtimezone("Local/Office", "+0100", "OFF1") + "BEGIN:VEVENT\r\n"
            "UID:list@example.com\r\n"
            "DTSTAMP:20250101T000000Z\r\n"
            "DTSTART;TZID=Local/Office:20260102T100000\r\n"
            "RDATE;TZID=Local/Office:20260105T080000,20260105T180000\r\n"
            "EXDATE;TZID=Local/Office:20260103T100000,20260104T100000\r\n"
            "END:VEVENT\r\n"
        )
        merged = Calendar.merge_ical([source])
        event = merged.events[0]
        rdate_values = [leaf.dt for leaf in event["RDATE"].dts]
        exdate_values = [leaf.dt for leaf in event["EXDATE"].dts]
        assert len(rdate_values) == 2
        assert len(exdate_values) == 2
        for value in rdate_values + exdate_values:
            assert value.tzinfo is not None
            assert value.replace(tzinfo=None) in (
                datetime(2026, 1, 5, 8, 0),
                datetime(2026, 1, 5, 18, 0),
                datetime(2026, 1, 3, 10, 0),
                datetime(2026, 1, 4, 10, 0),
            )
