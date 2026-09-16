"""Tests for classify_region -- PAC vs RoW, and tie-breaks across offices."""

import pytest

from jobscraper.enrichment.classify_region import classify_region, is_excluded, tab_name


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
        ("Bangkok, Thailand", "PAC"),
        ("Chiang Mai, Thailand", "PAC"),
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
        "Pasig City, Philippines",
        "Phnom Penh, Cambodia",
    ],
)
def test_classify_region_deliberately_excluded_countries_are_row(location):
    # These have real posting volume (Grab, WorldQuant) but are kept out of
    # PAC on purpose -- Thailand is the one ASEAN exception, see
    # _PAC_COUNTRIES in classify_region.py.
    assert classify_region(location) == "RoW"


def test_tab_name_format():
    assert tab_name("SWE/DE", "PAC") == "SWE/DE - PAC"
    assert tab_name("QT/QR", "RoW") == "QT/QR - RoW"


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Hanoi, Vietnam", True),
        ("Ho Chi Minh City, Vietnam", True),
        ("Kuala Lumpur, Malaysia", True),
        ("Jakarta, Indonesia", True),
        ("Gurgaon, India", True),
        ("Yerevan, Armenia", True),
        ("Montevideo, Uruguay", True),
        ("Bangkok, Thailand", False),  # ASEAN, but the one PAC exception
        ("London, United Kingdom", False),
        ("Singapore", False),
        ("Remote", False),
        ("", False),
    ],
)
def test_is_excluded_single_location(location, expected):
    assert is_excluded(location) == expected


def test_is_excluded_requires_every_office_to_be_excluded():
    # One non-excluded office (even a RoW one, not just PAC) is reason
    # enough to keep the posting -- opposite quantifier from
    # classify_region's "any office is PAC".
    assert is_excluded("Hanoi, Vietnam | London, United Kingdom") is False
    assert is_excluded("Hanoi, Vietnam | Singapore") is False


def test_is_excluded_true_when_every_office_is_excluded():
    assert is_excluded("Hanoi, Vietnam | Jakarta, Indonesia") is True


def test_is_excluded_tolerates_missing_spaces_around_pipe():
    assert is_excluded("Hanoi, Vietnam|Jakarta, Indonesia") is True
