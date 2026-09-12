"""Canonicalizes one Greenhouse office/location name into a consistent string.

Real board data is messy in a way a single regex can't fix: the same city
shows up under several spellings on different boards (or even the same
board) -- "New York", "New York City", "NYC", "New York, NY", "New York, NY
- 120 Broadway" are all one city. This module's answer is the same one
classify_roles.py/classify_seniority.py already use for messy title text: a
hand-curated, lowercase-keyed lookup table, seeded by dry-running against
every distinct office name across this project's current boards (see
CLAUDE.md's "Known gaps" -- same kind of tuning pass, and it'll need the
same ongoing care as new firms are added).

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
    "tel aviv": "Tel Aviv, Israel",
    "apac": "Asia-Pacific",
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
    return _CANONICAL_LOCATIONS.get(cleaned.lower(), cleaned)
