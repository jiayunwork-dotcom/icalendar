"""Fixtures shared by the expansion test package.

The repository's top-level ``tzp`` fixture (see
:mod:`icalendar.tests.conftest`) parametrizes tests across the zoneinfo and
pytz providers. Importing it here makes every expansion test run under both
providers without each module having to request the fixture explicitly.
"""

from __future__ import annotations

import pytest

# Re-export the helpers so test modules can import everything from one place.
from .conftest_helpers import (  # noqa: F401
    BERLIN,
    BERLIN_VTIMEZONE,
    CUSTOM_VTIMEZONE,
    NEW_YORK,
    NEW_YORK_VTIMEZONE,
    UTC,
    calendar,
    event,
    parse,
    utc,
)


@pytest.fixture(autouse=True)
def _use_repository_tzp(tzp):
    """Run each expansion test under both the zoneinfo and pytz providers."""
    yield tzp
