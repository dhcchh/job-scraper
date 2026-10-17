"""Tests for classify_region -- PAC vs RoW, and tie-breaks across offices."""

import pytest

from jobscraper.enrichment.classify_region import (
    _EXCLUDED_COUNTRIES,
    _PAC_COUNTRIES,
    _ROW_COUNTRIES,
    _check_disjoint,
    classify_region,
    is_excluded,
    tab_name,
    unresolved_offices,
)


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
        # A role open across Asia-Pacific ("Software Engineer (Sydney,
        # Shanghai, Singapore)", location "APAC") belongs on PAC tabs.
        ("Asia-Pacific", "PAC"),
        ("Remote, APAC", "PAC"),
        ("APJ", "PAC"),
        ("EMEA", "RoW"),
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


def test_pac_and_excluded_countries_are_disjoint():
    # _check_disjoint already runs at import time (module-level, not an
    # `assert` -- see classify_region.py), so this mainly guards against a
    # future edit reintroducing an overlap without anyone noticing the
    # import-time check fire; it's a real `pytest` assertion here, not a
    # substitute for that raise-based check.
    assert not (_PAC_COUNTRIES & _EXCLUDED_COUNTRIES)


def test_check_disjoint_raises_on_overlap():
    with pytest.raises(ValueError, match="Singapore"):
        _check_disjoint({"Singapore"}, {"Singapore", "Vietnam"})


def test_row_countries_share_nothing_with_pac_or_excluded():
    assert not (_ROW_COUNTRIES & (_PAC_COUNTRIES | _EXCLUDED_COUNTRIES))


@pytest.mark.parametrize(
    "location",
    [
        "Singapore",
        "Tokyo, Japan",
        "Hanoi, Vietnam",
        "London, United Kingdom",
        "New York, NY",
        "Denver, Colorado",
        "Toronto, ON",
        "Remote",
        "EMEA",
        "Asia-Pacific",
    ],
)
def test_unresolved_offices_accepts_known_country_parts(location):
    assert unresolved_offices(location) == []


@pytest.mark.parametrize(
    "location",
    [
        "Hobart",  # a PAC city the lookup table doesn't know
        "Japan-Osaka",
        "Seoul, Korea",
        "Pune, Gera Commerzone SEZ",  # a building where the country should be
        "Bangalore, IN",  # IN is India here, not Indiana
        "Karachi, Pakistan",  # no PAC/excluded decision yet
    ],
)
def test_unresolved_offices_reports_hidden_countries(location):
    assert unresolved_offices(location) == [location]


def test_unresolved_offices_returns_only_the_unresolved_offices():
    location = "London, United Kingdom | Hobart | Japan-Osaka"
    assert unresolved_offices(location) == ["Hobart", "Japan-Osaka"]
