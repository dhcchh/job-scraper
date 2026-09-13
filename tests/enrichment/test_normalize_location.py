"""Tests for normalize_location.normalize_location -- lookup + suffix cleanup."""

import pytest

from jobscraper.enrichment.normalize_location import normalize_location


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Aarhus", "Aarhus, Denmark"),
        ("Chicago", "Chicago, IL"),
        ("Chicago, IL", "Chicago, IL"),
        ("Singapore", "Singapore"),
        ("Hong Kong", "Hong Kong"),
        # A raw location.name fallback (no offices data) can be the fuller
        # "City, Region, Country" form, not just office.name's bare city.
        ("Sydney, New South Wales, Australia", "Sydney, Australia"),
    ],
)
def test_normalize_location_maps_known_names(raw, expected):
    assert normalize_location(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("New York", "New York, NY"),
        ("New York City", "New York, NY"),
        ("NYC", "New York, NY"),
        ("New York, NY", "New York, NY"),
        ("New York, NY - 120 Broadway", "New York, NY"),
    ],
)
def test_normalize_location_collapses_known_synonyms(raw, expected):
    # Same underlying city, several spellings across (or within) boards.
    assert normalize_location(raw) == expected


def test_normalize_location_strips_trailing_office_suffix_case_insensitively():
    assert normalize_location("Amsterdam office") == "Amsterdam, Netherlands"
    assert normalize_location("Chicago Office") == "Chicago, IL"


def test_normalize_location_leaves_unmapped_name_unchanged_but_stripped():
    # No fabricated city for a label that doesn't name one -- same
    # philosophy as classify_seniority's "Unspecified".
    assert normalize_location("  Australia  ") == "Australia"
    assert normalize_location("XTX Research") == "XTX Research"


def test_normalize_location_is_case_insensitive_on_lookup():
    assert normalize_location("LONDON") == "London, United Kingdom"
    assert normalize_location("london") == "London, United Kingdom"


def test_normalize_location_collapses_remote_synonyms():
    assert normalize_location("Remote") == "Remote"
    assert normalize_location("Virtual") == "Remote"
    assert normalize_location("Anywhere (subject to approval)") == "Remote"


def test_normalize_location_empty_string_stays_empty():
    assert normalize_location("") == ""


def test_normalize_location_strips_remote_annotation_around_a_known_place():
    # "Remote - Singapore"/"Taiwan (Remote)" must resolve to the underlying
    # place, not fall through unmapped -- classify_region only recognizes an
    # exact country match, so an unstripped annotation would silently
    # misroute a real PAC posting into RoW.
    assert normalize_location("Remote - Singapore") == "Singapore"
    assert normalize_location("Taiwan (Remote)") == "Taiwan"


def test_normalize_location_leaves_remote_region_codes_unmapped():
    # A "Remote - " prefix is still stripped, but a code with no place-name
    # entry to resolve to (NAMER, EMEA, CIS) is unmapped, same as any other
    # unrecognized cleaned string.
    assert normalize_location("Remote - NAMER") == "NAMER"


def test_normalize_location_maps_iso_country_code_prefixed_cities():
    assert normalize_location("AU-Sydney") == "Sydney, Australia"
    assert normalize_location("GB-London") == "London, United Kingdom"


def test_normalize_location_strips_trailing_locations_suffix():
    # Stripe groups several physical offices under one umbrella entry per
    # country instead of listing them individually -- confirmed on its live
    # board across two dozen countries, several of them PAC. Unstripped,
    # "Singapore Locations" doesn't exact-match "Singapore" (or, for a bare
    # country with no lookup entry, classify_region's own PAC check either),
    # so a real PAC posting would silently land in RoW.
    assert normalize_location("Singapore Locations") == "Singapore"
    assert normalize_location("Australia Locations") == "Australia"
    assert normalize_location("Japan Locations") == "Japan"
    assert normalize_location("New Zealand Locations") == "New Zealand"
    # Non-PAC countries pass through the same way -- no lookup entry needed
    # for a bare country name, only the noise suffix has to go.
    assert normalize_location("Germany Locations") == "Germany"
