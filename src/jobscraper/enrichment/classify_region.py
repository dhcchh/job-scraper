"""Buckets a job's (already-normalized) location into PAC or RoW.

Splits the current 5 role categories into 10 spreadsheet tabs -- one PAC
and one RoW per category -- so the target audience (based in Singapore)
can go straight to the roles they could actually relocate-free into,
instead of scanning every region mixed together. See `tab_name` for the
"{category} - {region}" naming this project's spreadsheet tabs already use.

PAC here means Pacific-adjacent from a Singapore vantage point: Singapore,
Hong Kong, Australia, Japan, South Korea, China, Taiwan, New Zealand,
Thailand. Everything else -- Europe, the Americas, the Middle East, and
South Asia (e.g. Gurgaon, Mumbai, Bangalore) -- is RoW. This is a product
choice, not a geographic one (India is "Asia-Pacific" in plenty of other
contexts); if that's ever wrong for a posting, fix it here, not by
special-casing main.py.

Within ASEAN specifically, Thailand is the one deliberate exception:
comparable pay to Singapore, unlike Malaysia/Indonesia/Vietnam/the
Philippines/Cambodia, which route to RoW alongside India on the same
pay/immigration-viability basis (see `_PAC_COUNTRIES` below and the
`classify_region` docstring for the ASEAN carve-out history). Thailand is
whole-country, not Bangkok-only -- consistent with every other entry here
being a country, not a city.

Same tuning-pass caveat as classify_roles.py/classify_seniority.py/
normalize_location.py: _PAC_COUNTRIES was seeded from real data (today's
boards only mention Singapore/Hong Kong/Sydney/Australia/Japan/Taiwan by
name, no New Zealand or South Korea posting exists yet to verify against)
-- a country here with no matching entry yet in normalize_location.py's
table (e.g. a future "Auckland" or "Tokyo" office) won't be recognized as
PAC until that city is added there too, since this module only ever reads
the country normalize_location.py already put after the last comma.
"""

from __future__ import annotations

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


def classify_region(location: str) -> str:
    """"PAC" if ANY office in `location` is in a PAC country, else "RoW".

    `location` is Job.location -- possibly several normalize_location.py
    outputs joined with " | " (see job_boards/greenhouse/client.py). "any
    office counts", not "every office must": the point of a PAC tab is
    surfacing a role a Singapore-based candidate could actually get into,
    and a posting listing Singapore alongside London still qualifies for
    that even though it's also open elsewhere.
    """
    for office in location.split("|"):
        office = office.strip()
        if office and _country_of(office) in _PAC_COUNTRIES:
            return "PAC"
    return "RoW"


def _country_of(place: str) -> str:
    """"Sydney, Australia" -> "Australia"; "Singapore" -> "Singapore".

    A city-state/territory normalize_location.py leaves with no trailing
    country (Singapore, Hong Kong) is its own "country" for this purpose --
    there's nothing after a comma to split on, so the whole cleaned string
    is compared directly against _PAC_COUNTRIES.
    """
    if "," in place:
        return place.rsplit(",", 1)[1].strip()
    return place.strip()


def tab_name(category: str, region: str) -> str:
    """The spreadsheet tab name for one (category, region) pair.

    "{category} - {region}", exactly matching this project's existing
    tabs (e.g. "SWE/DE - PAC") -- kept as one function, not string-
    formatted ad hoc at each call site, so the convention only needs to
    change in one place if it ever does.
    """
    return f"{category} - {region}"
