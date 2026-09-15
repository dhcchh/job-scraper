"""Tests for classify_region -- PAC vs RoW, and tie-breaks across offices."""

import pytest

from jobscraper.enrichment.classify_region import classify_region, tab_name


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Singapore", "PAC"),
        ("Hong Kong", "PAC"),
        ("Sydney, Australia", "PAC"),
        ("Tokyo, Japan", "PAC"),
        ("Seoul, South Korea", "PAC"),
        ("Beijing, China", "PAC"),
        ("Taiwan", "PAC"),
        ("Auckland, New Zealand", "PAC"),
        ("Hanoi, Vietnam", "RoW"),
        ("London, United Kingdom", "RoW"),
        ("New York, NY", "RoW"),
        ("Gurgaon, India", "RoW"),
        ("Remote", "RoW"),
        ("", "RoW"),
    ],
)
def test_classify_region_single_location(location, expected):
    assert classify_region(location) == expected


def test_classify_region_pac_wins_if_any_office_is_pac():
    # Not "every office must be PAC" -- a Singapore-based candidate could
    # still get into this posting.
    assert classify_region("Singapore | London, United Kingdom") == "PAC"
    assert classify_region("London, United Kingdom | Singapore") == "PAC"


def test_classify_region_all_row_offices_stays_row():
    assert classify_region("London, United Kingdom | New York, NY") == "RoW"


def test_classify_region_tolerates_missing_spaces_around_pipe():
    assert classify_region("Singapore|London, United Kingdom") == "PAC"


@pytest.mark.parametrize(
    "location",
    [
        "Hanoi, Vietnam",
        "Ho Chi Minh City, Vietnam",
        "Kuala Lumpur, Malaysia",
        "Jakarta, Indonesia",
        "Bangkok, Thailand",
        "Pasig City, Philippines",
        "Phnom Penh, Cambodia",
    ],
)
def test_classify_region_deliberately_excluded_countries_are_row(location):
    # These have real posting volume (Grab, WorldQuant) but are kept out of
    # PAC on purpose -- see _EXCLUDED_COUNTRIES in classify_region.py.
    assert classify_region(location) == "RoW"


def test_tab_name_format():
    assert tab_name("SWE/DE", "PAC") == "SWE/DE - PAC"
    assert tab_name("QT/QR", "RoW") == "QT/QR - RoW"
