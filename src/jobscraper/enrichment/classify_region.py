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

Separately, `is_excluded` gates a job out of the sheet ENTIRELY -- PAC or
RoW, doesn't matter -- for a country where relocation for a Singapore-based
candidate is a non-starter: pay isn't comparable and there's no incentive
to move. This is not just "not PAC": India, for instance, has always been
RoW rather than PAC, but until this existed an India posting would still
land in a RoW tab like any other. `_EXCLUDED_COUNTRIES` is a separate set
from `_PAC_COUNTRIES` -- a country can be neither (most of RoW), PAC, or
excluded, but never more than one of those. See `is_excluded` for the
"any office counts for PAC, but ALL offices must be excluded to drop the
whole posting" precedence -- a multi-location posting pairing an excluded
country with a real PAC or RoW office still gets kept and classified
normally by classify_region, same as any other multi-location posting.
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

# Countries this project never posts a job for at all, regardless of
# PAC/RoW -- see the module docstring's "Separately" paragraph. Disjoint
# from _PAC_COUNTRIES by construction (nothing here should ever also be a
# PAC country); kept as its own set rather than folded into a bigger
# lookup so is_excluded's "every office must match" logic stays entirely
# separate from classify_region's "any office matches" one.
_EXCLUDED_COUNTRIES = {
    "Vietnam",
    "Malaysia",
    "Indonesia",
    "India",
}


def _offices(location: str) -> list[str]:
    """`location` split on " | " into its individual, stripped offices,
    blank pieces dropped -- the shared parsing classify_region and
    is_excluded both build on.
    """
    return [office.strip() for office in location.split("|") if office.strip()]


def classify_region(location: str) -> str:
    """"PAC" if ANY office in `location` is in a PAC country, else "RoW".

    `location` is Job.location -- possibly several normalize_location.py
    outputs joined with " | " (see job_boards/greenhouse/client.py). "any
    office counts", not "every office must": the point of a PAC tab is
    surfacing a role a Singapore-based candidate could actually get into,
    and a posting listing Singapore alongside London still qualifies for
    that even though it's also open elsewhere.
    """
    for office in _offices(location):
        if _country_of(office) in _PAC_COUNTRIES:
            return "PAC"
    return "RoW"


def is_excluded(location: str) -> bool:
    """True if EVERY office in `location` is in an excluded country --
    the whole posting should be dropped, not just kept out of PAC.

    The opposite quantifier from classify_region's "any office is PAC"
    deliberately: a posting listing Ho Chi Minh City ALONGSIDE Singapore
    still has a real, keepable reason to be on the sheet (the Singapore
    office), so it's classified normally (into PAC, same PAC-wins
    precedence as always) rather than dropped just because one of its
    several offices happens to be excluded. Only a posting with NO
    non-excluded office anywhere is excluded outright. An empty/unparseable
    location is never excluded -- same "don't guess" default
    classify_region itself falls back to (RoW) for that case.
    """
    offices = _offices(location)
    if not offices:
        return False
    return all(_country_of(office) in _EXCLUDED_COUNTRIES for office in offices)


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
