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


def test_tab_name_format():
    assert tab_name("SWE/DE", "PAC") == "SWE/DE - PAC"
    assert tab_name("QT/QR", "RoW") == "QT/QR - RoW"
