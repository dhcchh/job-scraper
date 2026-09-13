"""Canonicalizes one Greenhouse office/location name into a consistent string.

Real board data is messy in a way a single regex can't fix: the same city
shows up under several spellings on different boards (or even the same
board) -- "New York", "New York City", "NYC", "New York, NY", "New York, NY
- 120 Broadway" are all one city. This module's answer is the same one
classify_roles.py/classify_seniority.py already use for messy title text: a
hand-curated, lowercase-keyed lookup table, seeded by dry-running against
every distinct office name across this project's current boards. It needs the
same ongoing care as new firms are added.

Convention: `"City, ST"` (US state abbreviation) for US cities -- matching
how the raw Greenhouse data already writes them (e.g. "Charleston, SC") --
and `"City, Country"` everywhere else (e.g. "Aarhus, Denmark"). A
country/region-only label ("Australia", "APAC", "North America") is left
as-is rather than guessing a city that was never named.

What this module does NOT do: decide how many locations a posting has, or
split a combined string into several -- that's job_boards/greenhouse/
client.py's job (it has Greenhouse's own `offices` array to work from,
which is already split per-office and sidesteps the comma-inside-a-
city-name-vs-comma-between-cities ambiguity entirely) and
sheets/backfill.py's job (splitting an already-`" | "`-joined sheet cell
back apart). This function only ever normalizes one name at a time.
"""

from __future__ import annotations

import re

_OFFICE_SUFFIX_RE = re.compile(r"\s+office$", re.IGNORECASE)

# Some boards (Coinbase, OKX) wrap an otherwise-recognizable place name in a
# "remote" annotation -- "Remote - Singapore", "Taiwan (Remote)" -- which
# would otherwise dodge both the lookup table AND classify_region's PAC
# check (neither "Remote - Singapore" nor "Taiwan (Remote)" is an exact
# match, so a real Singapore/Taiwan posting would silently land in RoW).
# Stripped before lookup, same as the " office" suffix above, so the place
# name underneath still gets normalized normally.
_REMOTE_PREFIX_RE = re.compile(r"^remote\s*-\s*", re.IGNORECASE)
_REMOTE_SUFFIX_RE = re.compile(r"\s*\(remote\)$", re.IGNORECASE)

# Stripe groups several physical offices in one country under a single
# umbrella `offices[].name` instead of listing them individually -- e.g.
# "Singapore Locations", "Australia Locations", "Japan Locations" (confirmed
# against Stripe's live board: two dozen countries use this pattern, several
# of them PAC). Left unstripped, none of these exact-match the lookup table
# below (or, for a bare country name with no entry needed, classify_region's
# own PAC check), so a real PAC posting would silently land in RoW -- same
# failure shape as the "Remote - "/" (Remote)" annotations above, same fix:
# strip the noise suffix before lookup so the country name underneath is
# left to resolve normally (via the table for a city, or unchanged for a
# bare country classify_region already recognizes).
_LOCATION_SUFFIX_RE = re.compile(r"\s+locations?$", re.IGNORECASE)

# Two more board-specific noise wrappers, both confirmed on live Lever
# boards (ShopBack, Spotify) once Lever support was added: a trailing
# two-letter ISO country code in parens ("Singapore (SG)") and Spotify's own
# work-arrangement annotation ("Taiwan (Home Mix)", "United States of
# America (Home Mix)"). Same failure shape as every other wrapper on this
# page -- left in place, neither exact-matches the lookup table below or
# classify_region's bare-country check, so a real PAC posting (Singapore,
# Taiwan) would silently land in RoW.
_COUNTRY_CODE_SUFFIX_RE = re.compile(r"\s*\([A-Z]{2}\)$")
_WORK_MODE_SUFFIX_RE = re.compile(r"\s*\([^()]*\bmix\)$", re.IGNORECASE)

# Lever's `categories.allLocations` sometimes repeats a city-state's name on
# both sides of the comma instead of pairing a city with a country --
# "Singapore, Singapore" (Palantir, CSIT), "Hong Kong, Hong Kong"
# (ShopBack). Still resolves to the right PAC country either way (nothing
# after classify_region's last comma cares about the duplication), but
# collapsing it here keeps the sheet's location column as clean as the
# already-canonical bare form.
_DUPLICATE_NAME_RE = re.compile(r"^(.+?),\s*\1$", re.IGNORECASE)

# Several Ashby boards (Airwallex, Snowflake) write an ISO-3166 country-code
# prefix with spaces around the dash -- "AU - Sydney", "SG - Singapore",
# "CN - Beijing" -- instead of the tight "AU-Sydney" form Stripe's board
# already seeded the table below with. Collapsing the spacing here (rather
# than doubling every "xx-city" entry below with a spaced variant) means
# both forms resolve through the same lookup key. Anchored to exactly two
# letters so it can't misfire on a real word like "Remote - Singapore"
# (handled separately by _REMOTE_PREFIX_RE above).
_ISO_CODE_SPACING_RE = re.compile(r"^([A-Za-z]{2})\s+-\s+")

# Keyed by the office name lowercased (after stripping a trailing " office"
# noise suffix -- covers "Amsterdam office", "Chicago Office", etc. without
# needing a separate entry per suffix variant). Unmapped names pass through
# this module unchanged (cleaned, not guessed at) -- see normalize_location.
_CANONICAL_LOCATIONS: dict[str, str] = {
    # -- Americas --
    "austin": "Austin, TX",
    "austin, tx": "Austin, TX",
    "bellevue": "Bellevue, WA",
    "boston": "Boston, MA",
    "boulder": "Boulder, CO",
    "carteret": "Carteret, NJ",
    "charleston, sc": "Charleston, SC",
    "chicago": "Chicago, IL",
    "chicago qts": "Chicago, IL",
    "chicago, il": "Chicago, IL",
    "edison": "Edison, NJ",
    "greenwich": "Greenwich, CT",
    "houston": "Houston, TX",
    "new york": "New York, NY",
    "new york city": "New York, NY",
    "new york, ny": "New York, NY",
    "new york, ny - 120 broadway": "New York, NY",
    "nyc": "New York, NY",
    "san francisco, ca": "San Francisco, CA",
    "seattle": "Seattle, WA",
    "stamford, ct": "Stamford, CT",
    "west palm beach, fl": "West Palm Beach, FL",
    "westchester": "Westchester, NY",
    "montreal": "Montreal, Canada",
    "montréal": "Montreal, Canada",
    "grand cayman": "Grand Cayman, Cayman Islands",
    # -- Europe --
    "aarhus": "Aarhus, Denmark",
    "amsterdam": "Amsterdam, Netherlands",
    "bristol": "Bristol, United Kingdom",
    "budapest": "Budapest, Hungary",
    "dublin": "Dublin, Ireland",
    "geneva": "Geneva, Switzerland",
    "jersey": "Jersey, Channel Islands",
    "london": "London, United Kingdom",
    "madrid": "Madrid, Spain",
    "paris": "Paris, France",
    "uk": "United Kingdom",
    "warsaw": "Warsaw, Poland",
    "wrocław": "Wroclaw, Poland",
    "wroclaw": "Wroclaw, Poland",
    "zug": "Zug, Switzerland",
    "zurich": "Zurich, Switzerland",
    # -- Middle East / Asia-Pacific --
    "bangalore": "Bangalore, India",
    "beijing": "Beijing, China",
    "dubai": "Dubai, UAE",
    "gift city": "GIFT City, India",
    "gurgaon": "Gurgaon, India",
    "hong kong": "Hong Kong",
    "mumbai": "Mumbai, India",
    "seoul": "Seoul, South Korea",
    "shanghai": "Shanghai, China",
    "singapore": "Singapore",
    "sydney": "Sydney, Australia",
    "sydney, new south wales, australia": "Sydney, Australia",
    "taipei": "Taipei, Taiwan",
    "tel aviv": "Tel Aviv, Israel",
    "apac": "Asia-Pacific",
    # Vietnam is a PAC country in classify_region's list; these cities get
    # the same "City, Country" treatment as every other PAC/RoW city above
    # rather than being left as raw board noise. Confirmed on WorldQuant's
    # live Greenhouse board ("Hanoi", "Ho Chi Minh City") and Spotify's live
    # Lever board ("Ho Chi Minh", without "City").
    "hanoi": "Hanoi, Vietnam",
    "ho chi minh": "Ho Chi Minh City, Vietnam",
    "ho chi minh city": "Ho Chi Minh City, Vietnam",
    # Same ISO-3166-prefixed convention as the "xx-city" entries below,
    # confirmed on Airwallex's live Ashby board.
    "vn-hanoi": "Hanoi, Vietnam",
    "vn-ho chi minh city": "Ho Chi Minh City, Vietnam",
    # ISO-3166 country-code-prefixed city names, seeded from Stripe's board
    # ("AU-Sydney", "GB-London") -- same "City, Country" convention as every
    # other entry above once decoded.
    "au-sydney": "Sydney, Australia",
    "ca-toronto": "Toronto, Canada",
    "es-barcelona": "Barcelona, Spain",
    "gb-london": "London, United Kingdom",
    "ie-dublin": "Dublin, Ireland",
    "in-bengaluru": "Bengaluru, India",
    # More of the same ISO-3166-prefixed convention, seeded from Ashby
    # boards (Airwallex, Snowflake) once Ashby support was added -- reached
    # via _ISO_CODE_SPACING_RE above when the raw string spaces the dash
    # out ("AU - Melbourne"), or directly when it doesn't ("AU-Melbourne").
    "au-melbourne": "Melbourne, Australia",
    "cn-beijing": "Beijing, China",
    "cn-changsha": "Changsha, China",
    "cn-chengdu": "Chengdu, China",
    "cn-chongqing": "Chongqing, China",
    "cn-fuzhou": "Fuzhou, China",
    "cn-guangzhou": "Guangzhou, China",
    "cn-hangzhou": "Hangzhou, China",
    "cn-nanjing": "Nanjing, China",
    "cn-ningbo": "Ningbo, China",
    "cn-qingdao": "Qingdao, China",
    "cn-shanghai": "Shanghai, China",
    "cn-shenzhen": "Shenzhen, China",
    "cn-tianjin": "Tianjin, China",
    "cn-wuhan": "Wuhan, China",
    "cn-xi'an": "Xi'an, China",
    "cn-xiamen": "Xiamen, China",
    "cn-yiwu": "Yiwu, China",
    "hk-hong kong sar": "Hong Kong",
    "jp-tokyo": "Tokyo, Japan",
    "kr-seoul": "Seoul, South Korea",
    "nz-auckland": "Auckland, New Zealand",
    "sg-singapore": "Singapore",
    # A bare city with no country/code prefix at all -- confirmed on Sierra's
    # live Ashby board.
    "tokyo": "Tokyo, Japan",
    # OKX-specific noise: an internal region-tag suffix on an otherwise
    # plain country name.
    "china mainland_internal": "China",
    # Grasshopper-specific noise: its Greenhouse board's offices[].name is
    # an internal short code, not a readable place name like every other
    # Greenhouse board configured here -- confirmed against its live board
    # (offices[].location, a separate field this project doesn't otherwise
    # read, spells these out as "Singapore"/"Singapore"/"Vietnam"
    # respectively). Left unmapped, none of these exact-match the lookup
    # table or classify_region's bare-country check, so a real PAC posting
    # (Singapore) would silently land in RoW.
    "ghp-sg": "Singapore",
    "ghp-vn": "Vietnam",
    "gam": "Singapore",
    # Snowflake-specific noise: its Ashby board appends an internal
    # office/building code after the city ("-MSO", "-WW") that a general
    # suffix regex can't safely strip (no fixed set of codes observed), so
    # -- same approach as "chicago qts" above -- these are mapped as whole
    # strings rather than decomposed.
    "au-canberra-mso": "Canberra, Australia",
    "cn-beijing-mso": "Beijing, China",
    "cn-shanghai-ww": "Shanghai, China",
    "kr-south korea-remote": "South Korea",
    # OpenAI-specific noise: "Remote" is a suffix here, not a prefix
    # _REMOTE_PREFIX_RE would strip, and there's no specific city to name.
    "australia - remote": "Australia",
    # -- Not a place: remote/flexible labels some boards use interchangeably --
    "remote": "Remote",
    "virtual": "Remote",
    "anywhere (subject to approval)": "Remote",
}


def normalize_location(raw: str) -> str:
    """Canonical form of one office/location name, or the input cleaned but
    otherwise unchanged if it's not in `_CANONICAL_LOCATIONS`.

    Falling through unmapped is a real, common outcome, not a bug to chase
    away -- some labels genuinely don't name one city to canonicalize to
    ("Australia", "North America", "APAC" if new APAC-style labels show up
    the table doesn't cover yet) and some are internal Greenhouse noise a
    lookup table can't safely guess at (e.g. a division name entered into
    the office field by mistake). Same philosophy as classify_seniority's
    "Unspecified": don't fabricate a signal the data doesn't contain.
    """
    cleaned = _OFFICE_SUFFIX_RE.sub("", raw).strip()
    cleaned = _LOCATION_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _ISO_CODE_SPACING_RE.sub(r"\1-", cleaned).strip()
    cleaned = _REMOTE_PREFIX_RE.sub("", cleaned).strip()
    cleaned = _REMOTE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _COUNTRY_CODE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _WORK_MODE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _DUPLICATE_NAME_RE.sub(r"\1", cleaned).strip()
    return _CANONICAL_LOCATIONS.get(cleaned.lower(), cleaned)
