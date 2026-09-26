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


def test_normalize_location_strips_remote_annotation_without_a_dash():
    # MongoDB and Cloudflare annotate with a space instead of a dash --
    # "Remote Singapore", "Remote Australia" -- which the dash-only prefix
    # regex above doesn't match, so a real PAC posting was silently landing
    # in RoW.
    assert normalize_location("Remote Singapore") == "Singapore"
    assert normalize_location("Remote Australia") == "Australia"


def test_normalize_location_does_not_strip_remote_before_a_parenthetical():
    # Perplexity's "Remote (United States)" must not become "(United
    # States)" -- the word after "Remote" here is a paren, not a place name
    # to unwrap.
    assert normalize_location("Remote (United States)") == "Remote (United States)"


def test_normalize_location_maps_korea_republic_of():
    # GitLab's live board uses the ISO/formal name instead of a city --
    # unmapped, its own comma would make classify_region read "Republic of"
    # as the country and miss South Korea entirely.
    assert normalize_location("Korea, Republic of") == "South Korea"


def test_normalize_location_leaves_remote_region_codes_unmapped():
    # A "Remote - " prefix is still stripped, but a code with no place-name
    # entry to resolve to (NAMER, EMEA, CIS) is unmapped, same as any other
    # unrecognized cleaned string.
    assert normalize_location("Remote - NAMER") == "NAMER"


def test_normalize_location_maps_iso_country_code_prefixed_cities():
    assert normalize_location("AU-Sydney") == "Sydney, Australia"
    assert normalize_location("GB-London") == "London, United Kingdom"


def test_normalize_location_strips_trailing_country_code_in_parens():
    # ShopBack's live Lever board writes Singapore as "Singapore (SG)" --
    # unstripped, neither the lookup table nor classify_region's bare-country
    # check exact-matches it, so a real PAC posting would silently land in
    # RoW.
    assert normalize_location("Singapore (SG)") == "Singapore"


def test_normalize_location_strips_work_mode_annotation():
    # Spotify's live Lever board annotates some locations with its own
    # work-arrangement label -- same failure shape as the country-code
    # suffix above.
    assert normalize_location("Taiwan (Home Mix)") == "Taiwan"
    assert (
        normalize_location("United States of America (Home Mix)")
        == "United States of America"
    )


def test_normalize_location_collapses_repeated_city_state_name():
    # Lever's categories.allLocations pairs some city-states with themselves
    # instead of a country -- "Singapore, Singapore" (Palantir, CSIT),
    # "Hong Kong, Hong Kong" (ShopBack). Already resolves to the right PAC
    # country either way, but this keeps the sheet's location column as
    # clean as the bare canonical form.
    assert normalize_location("Singapore, Singapore") == "Singapore"
    assert normalize_location("Hong Kong, Hong Kong") == "Hong Kong"


def test_normalize_location_maps_iso_country_code_prefixed_cities_with_spaced_dash():
    # Ashby boards (Airwallex, Snowflake) write the same ISO-3166 prefix
    # convention as Stripe's Greenhouse board, but with spaces around the
    # dash -- both forms must resolve to the same canonical city.
    assert normalize_location("AU - Sydney") == "Sydney, Australia"
    assert normalize_location("AU-Sydney") == "Sydney, Australia"
    assert normalize_location("SG - Singapore") == "Singapore"
    assert normalize_location("SG-Singapore") == "Singapore"
    assert normalize_location("CN - Shanghai") == "Shanghai, China"
    assert normalize_location("JP - Tokyo") == "Tokyo, Japan"
    assert normalize_location("KR - Seoul") == "Seoul, South Korea"
    assert normalize_location("HK - Hong Kong SAR") == "Hong Kong"
    assert normalize_location("NZ - Auckland") == "Auckland, New Zealand"


def test_normalize_location_does_not_collapse_spacing_on_a_non_code_prefix():
    # The ISO-code-spacing collapse is anchored to exactly two leading
    # letters so it can't misfire on an ordinary word like "Australia".
    assert normalize_location("Australia - Remote") == "Australia"


def test_normalize_location_maps_snowflake_office_code_suffixes():
    # Snowflake's Ashby board appends an internal office/building code after
    # the city ("-MSO", "-WW") -- confirmed live, mapped as whole strings
    # since there's no fixed set of codes to strip generically.
    assert normalize_location("AU-Canberra-MSO") == "Canberra, Australia"
    assert normalize_location("CN-Beijing-MSO") == "Beijing, China"
    assert normalize_location("CN-Shanghai-WW") == "Shanghai, China"
    assert normalize_location("KR-South Korea-Remote") == "South Korea"


def test_normalize_location_maps_bare_tokyo():
    # Confirmed on Sierra's live Ashby board: no country/code prefix at all.
    assert normalize_location("Tokyo") == "Tokyo, Japan"


def test_normalize_location_maps_bare_taipei_to_taiwan():
    # Confirmed on WorldQuant's live Greenhouse board: a bare "Taipei" (no
    # trailing ", Taiwan") was falling through unmapped, and since Taiwan is
    # a PAC country, classify_region had no country to match against --
    # silently landing a real PAC posting in RoW.
    assert normalize_location("Taipei") == "Taipei, Taiwan"


def test_normalize_location_maps_vietnamese_cities():
    # Confirmed on WorldQuant's Greenhouse board, Spotify's Lever board, and
    # Airwallex's Ashby board.
    assert normalize_location("Hanoi") == "Hanoi, Vietnam"
    assert normalize_location("Ho Chi Minh") == "Ho Chi Minh City, Vietnam"
    assert normalize_location("Ho Chi Minh City") == "Ho Chi Minh City, Vietnam"
    assert normalize_location("VN-Hanoi") == "Hanoi, Vietnam"
    assert normalize_location("VN-Ho Chi Minh City") == "Ho Chi Minh City, Vietnam"


def test_normalize_location_maps_montevideo_and_yerevan():
    # Added alongside Uruguay/Armenia joining
    # classify_region._EXCLUDED_COUNTRIES -- not confirmed against a
    # specific live board yet, unlike most entries here (see
    # normalize_location.py's comment).
    assert normalize_location("Montevideo") == "Montevideo, Uruguay"
    assert normalize_location("Yerevan") == "Yerevan, Armenia"


def test_normalize_location_maps_grasshopper_internal_office_codes():
    # Grasshopper's live Greenhouse board sets offices[].name to an internal
    # short code rather than a readable place name -- confirmed against
    # offices[].location, a separate field naming the real place.
    assert normalize_location("GHP-SG") == "Singapore"
    assert normalize_location("GHP-VN") == "Vietnam"
    assert normalize_location("GAM") == "Singapore"


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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("US - Austin, TX", "Austin, TX"),
        ("US - New York, NY", "New York, NY"),
        ("AE - Dubai, United Arab Emirates", "Dubai, UAE"),
        ("GB - London, United Kingdom", "London, United Kingdom"),
        ("HK - Hong Kong, China", "Hong Kong"),
        ("BR - São Paulo, Brazil", "Sao Paulo, Brazil"),
        ("PH - Pasay, Philippines - Manila", "Pasay, Philippines"),
        # "Remote - <country>" after the code: India/Armenia must come out
        # as something classify_region.is_excluded recognizes.
        ("IN - Remote - IND", "India"),
        ("AM - Remote - Armenia", "Armenia"),
        ("GB - Remote - GBR", "United Kingdom"),
    ],
)
def test_normalize_location_drops_iso_code_prefix_before_a_full_location(raw, expected):
    # Visa's live Workday board prefixes every location with an ISO code
    # that only repeats the country already named after it.
    assert normalize_location(raw) == expected


def test_normalize_location_keeps_iso_code_prefix_before_a_bare_city():
    # No comma after the code -- still the "xx-city" lookup path Ashby's
    # boards rely on, not the full-location prefix drop above.
    assert normalize_location("AU - Sydney") == "Sydney, Australia"
    assert normalize_location("SG - Singapore") == "Singapore"
    assert normalize_location("SG - Remote") == "SG-Remote"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Jakarta, Indonesia (6th Floor)", "Jakarta, Indonesia"),
        ("Navi Mumbai, India (Finicity)", "Navi Mumbai, India"),
        ("London, England (Angel Lane)", "London, United Kingdom"),
        ("Manilla, Philippines (Transfast)", "Manila, Philippines"),
        ("Salt Lake City, Utah (Holladay)", "Salt Lake City, UT"),
    ],
)
def test_normalize_location_strips_a_building_suffix_after_city_and_country(raw, expected):
    # Mastercard's live Workday board appends a building/acquired-company
    # name -- unstripped, classify_region reads "Indonesia (6th Floor)" as
    # the country and is_excluded misses it.
    assert normalize_location(raw) == expected


def test_normalize_location_leaves_a_paren_with_no_comma_before_it():
    assert normalize_location("Remote (United States)") == "Remote (United States)"
    assert normalize_location("Singapore (Guoco)") == "Singapore"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Singapore, SingPost", "Singapore"),
        ("Wanchai, Hong Kong", "Hong Kong"),
        ("Besiktas, Istanbul Turkey", "Istanbul, Turkey"),
        ("O'Fallon, Missouri", "O'Fallon, MO"),
        ("Purchase, New York", "Purchase, NY"),
        ("New York City, New York", "New York, NY"),
        ("Atlanta, Georgia", "Atlanta, GA"),
        ("Boston, Massachusetts", "Boston, MA"),
        ("Austin, Texas", "Austin, TX"),
    ],
)
def test_normalize_location_maps_visa_and_mastercard_office_names(raw, expected):
    assert normalize_location(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Japan, Tokyo", "Tokyo, Japan"),
        ("South Korea, Seoul", "Seoul, South Korea"),
        ("France, Paris", "Paris, France"),
        ("United Kingdom, London", "London, United Kingdom"),
        ("United States, New York", "New York, NY"),
        ("France, Remote", "France"),
        ("United States, Remote", "United States"),
        ("Singapore, Remote", "Singapore"),
    ],
)
def test_normalize_location_maps_dataiku_country_first_offices(raw, expected):
    # Dataiku's live Greenhouse board writes "Country, City" / "Country,
    # Remote" -- unmapped, classify_region reads "Tokyo"/"Remote" as the
    # country and a real PAC posting lands in RoW.
    assert normalize_location(raw) == expected


def test_normalize_location_strips_office_suffix_despite_trailing_whitespace():
    # Flow Traders' live Greenhouse board has "London Office " with a
    # trailing space -- the suffix must still come off.
    assert normalize_location("London Office ") == "London, United Kingdom"
