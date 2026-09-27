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

def _check_disjoint(a: set[str], b: set[str]) -> None:
    """Reject overlapping PAC and excluded country sets."""
    overlap = a & b
    if overlap:
        raise ValueError(
            f"country/countries {sorted(overlap)} in both _PAC_COUNTRIES and "
            "_EXCLUDED_COUNTRIES -- a country can't be both a PAC country "
            "and a deliberate exclusion"
        )


_check_disjoint(_PAC_COUNTRIES, _EXCLUDED_COUNTRIES)


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


def _country_of(place: str) -> str:
    """Extract the country-like suffix used by the region rules."""
    if "," in place:
        return place.rsplit(",", 1)[1].strip()
    return place.strip()


def tab_name(category: str, region: str) -> str:
    """Return the sheet tab name for a category and region."""
    return f"{category} - {region}"
