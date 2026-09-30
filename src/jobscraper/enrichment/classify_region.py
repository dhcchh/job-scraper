"""Classify normalized locations as PAC, RoW, or excluded."""

REGIONS: tuple[str, ...] = ("PAC", "RoW")

_PAC_COUNTRIES = {
    "Singapore",
    "Hong Kong",
    "Australia",
    "Japan",
    "South Korea",
    "China",
    "Taiwan",
    "New Zealand",
    "Thailand",
}

_EXCLUDED_COUNTRIES = {
    "Vietnam",
    "Malaysia",
    "Indonesia",
    "India",
    "Armenia",
    "Uruguay",
}

# Every other country part the region rules can meet -- all RoW. Listed only
# so unresolved_offices can tell "a place in a RoW country" from a format that
# hides its country ("Japan-Osaka", "Melbourne", a building name). Asian
# countries with no PAC/excluded decision yet (Macau, Pakistan, ...) are left
# out on purpose, so they're reported rather than silently RoW.
_ROW_COUNTRIES = {
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Argentina",
    "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Barbados", "Belarus",
    "Belgium", "Belize", "Benin", "Bermuda", "Bolivia",
    "Bosnia and Herzegovina", "Botswana", "Brazil", "Bulgaria",
    "Burkina Faso", "Cameroon", "Canada", "Cayman Islands", "Channel Islands",
    "Chile", "Colombia", "Costa Rica", "Croatia", "Cuba", "Cyprus",
    "Czech Republic", "Czechia", "Denmark", "Dominican Republic", "Ecuador",
    "Egypt", "El Salvador", "England", "Estonia", "Ethiopia", "Finland",
    "France", "Georgia", "Germany", "Ghana", "Gibraltar", "Greece",
    "Guatemala", "Guernsey", "Honduras", "Hungary", "Iceland", "Iran", "Iraq",
    "Ireland", "Isle of Man", "Israel", "Italy", "Ivory Coast", "Jamaica",
    "Jersey", "Jordan", "Kazakhstan", "Kenya", "Kingdom of Saudi Arabia",
    "Kosovo", "Kuwait", "Kyrgyzstan", "Latvia", "Lebanon", "Liechtenstein",
    "Lithuania", "Luxembourg", "Madagascar", "Malta", "Mauritius", "Mexico",
    "Moldova", "Monaco", "Montenegro", "Morocco", "Mozambique", "Namibia",
    "Netherlands", "Nicaragua", "Nigeria", "North Macedonia",
    "Northern Ireland", "Norway", "Oman", "Panama", "Paraguay", "Peru",
    "Poland", "Portugal", "Puerto Rico", "Qatar", "Romania", "Russia",
    "Rwanda", "Saudi Arabia", "Scotland", "Senegal", "Serbia", "Slovakia",
    "Slovenia", "South Africa", "Spain", "Sweden", "Switzerland", "Tanzania",
    "The Netherlands", "Trinidad and Tobago", "Tunisia", "Turkey", "Türkiye",
    "Uganda", "Ukraine", "UAE", "United Arab Emirates", "United Kingdom",
    "UK", "United States", "United States of America", "US", "USA", "U.S.",
    "Uzbekistan", "Venezuela", "Wales", "Zambia", "Zimbabwe",
    # RoW by decision, not default -- see _PAC_COUNTRIES' ASEAN note.
    "Cambodia", "Philippines",
}

# US states (plus DC) and Canadian provinces, by code and by name. "IN" and
# "ID" are left out on purpose: as a suffix they're likelier India/Indonesia
# (both excluded) than Indiana/Idaho, so they should be reported.
_SUBDIVISIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "IL",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "D.C.", "Washington DC", "Washington D.C.",
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Hawaii", "Idaho", "Illinois",
    "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
    "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
    "ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT",
    "NU", "Ontario", "Quebec", "British Columbia", "Alberta", "Manitoba",
    "Saskatchewan", "Nova Scotia", "New Brunswick",
    "Newfoundland and Labrador", "Prince Edward Island",
}

# Whole-location values that name no single country, all fine as RoW.
_ROW_AREAS = {
    "Remote", "Global", "Worldwide", "Anywhere", "EMEA", "Europe", "AMER",
    "Americas", "North America", "LATAM", "Latin America", "Middle East",
}


def _check_disjoint(a: set[str], b: set[str]) -> None:
    """Reject a country listed in two region sets."""
    overlap = a & b
    if overlap:
        raise ValueError(
            f"country/countries {sorted(overlap)} in two of _PAC_COUNTRIES/"
            "_EXCLUDED_COUNTRIES/_ROW_COUNTRIES -- a country gets exactly one "
            "region decision"
        )


_check_disjoint(_PAC_COUNTRIES, _EXCLUDED_COUNTRIES)
_check_disjoint(_PAC_COUNTRIES | _EXCLUDED_COUNTRIES, _ROW_COUNTRIES)

_KNOWN_COUNTRY_PARTS = frozenset(
    _PAC_COUNTRIES | _EXCLUDED_COUNTRIES | _ROW_COUNTRIES | _SUBDIVISIONS | _ROW_AREAS
)


def _offices(location: str) -> list[str]:
    """Split a joined location into non-empty offices."""
    return [office.strip() for office in location.split("|") if office.strip()]


def classify_region(location: str) -> str:
    """Return PAC when any office is in a PAC country; otherwise RoW."""
    for office in _offices(location):
        if _country_of(office) in _PAC_COUNTRIES:
            return "PAC"
    return "RoW"


def is_excluded(location: str) -> bool:
    """Return true only when every listed office is in an excluded country."""
    offices = _offices(location)
    if not offices:
        return False
    return all(_country_of(office) in _EXCLUDED_COUNTRIES for office in offices)


def office_countries(location: str) -> list[str]:
    """Return each office's country-like part, in order."""
    return [_country_of(office) for office in _offices(location)]


def unresolved_offices(location: str) -> list[str]:
    """Return offices whose country part names no known country or state.

    Such an office falls through to RoW and can't be excluded, which is
    right only if it isn't really in a PAC or excluded country.
    """
    return [office for office in _offices(location) if not names_a_country(office)]


def names_a_country(place: str) -> bool:
    """Whether a place's country part is a known country, state or area."""
    return _country_of(place) in _KNOWN_COUNTRY_PARTS


def _country_of(place: str) -> str:
    """Extract the country-like suffix used by the region rules."""
    if "," in place:
        return place.rsplit(",", 1)[1].strip()
    return place.strip()


def tab_name(category: str, region: str) -> str:
    """Return the sheet tab name for a category and region."""
    return f"{category} - {region}"
