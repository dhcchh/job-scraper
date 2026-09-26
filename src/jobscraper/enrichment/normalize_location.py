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

# Dataiku's Greenhouse board writes a remote office as "<Country>, Remote"
# ("France, Remote", "United States, Remote"). classify_region reads the
# country off whatever's after the LAST comma, i.e. "Remote" -- so a
# "Singapore, Remote" would silently land in RoW. Stripped like the other
# remote annotations above, leaving the bare country.
_REMOTE_COMMA_SUFFIX_RE = re.compile(r",\s*remote$", re.IGNORECASE)

# MongoDB and Cloudflare use a dashless variant of the same annotation --
# "Remote Singapore", "Remote Australia" -- which _REMOTE_PREFIX_RE's
# required "-" doesn't match, so a real PAC posting was silently landing in
# RoW the same way an unstripped "Remote - Singapore" would. The lookahead
# keeps this from also eating "Remote (United States)" (Perplexity), where
# the word after "Remote" is a paren, not a place name to unwrap.
_REMOTE_PREFIX_NO_DASH_RE = re.compile(r"^remote\s+(?=[A-Za-z])", re.IGNORECASE)

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

# Visa's Workday board prefixes EVERY location with that same spaced ISO code,
# but in front of a full "City, Country"/"City, ST" -- "US - Austin, TX", "AE
# - Dubai, United Arab Emirates" -- or a "Remote - <country>" label ("IN -
# Remote - IND", "AM - Remote - Armenia"), not a bare city. There the code
# only repeats what follows it, and collapsing it into "US-Austin, TX" (as
# _ISO_CODE_SPACING_RE would) leaves a key no table entry matches and, for
# "IN-Remote - IND"/"AM-Remote - Armenia", a string classify_region.
# is_excluded can't read the country off. So the prefix is dropped entirely
# instead, checked before _ISO_CODE_SPACING_RE; a bare city ("AU - Sydney",
# "SG - Singapore", "SG - Remote") doesn't match the lookahead and still goes
# through the "xx-city" entries below.
_ISO_CODE_PREFIX_RE = re.compile(
    r"^[A-Za-z]{2}\s+-\s+(?=[^,]+,|remote\s*-\s*\S)", re.IGNORECASE
)

# Mastercard's Workday board appends a building or acquired-company name in
# parens after an otherwise plain "City, Country" -- "Jakarta, Indonesia (6th
# Floor)", "Navi Mumbai, India (Finicity)", "London, England (Angel Lane)".
# Left in place, classify_region reads "Indonesia (6th Floor)" as the country,
# so a posting is_excluded should drop stays in scope. Only stripped after a
# comma, so a paren that IS the useful part of a no-comma name ("Remote
# (United States)", "Singapore (Guoco)") is left for the table to handle.
_BUILDING_SUFFIX_RE = re.compile(r"^([^()]+,[^()]+?)\s*\([^()]*\)$")

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
    # Bare-city fallback for Eightfold's own "City, Region, Country" entries
    # once _location() (job_boards/eightfold/client.py) truncates to just
    # the leading city -- see the "City, Region, Country" comment further
    # down for why the matching whole-string entries never actually get
    # looked up and can't be relied on for this.
    "dallas": "Dallas, TX",
    "edison": "Edison, NJ",
    "greenwich": "Greenwich, CT",
    "houston": "Houston, TX",
    "miami": "Miami, FL",
    "new york": "New York, NY",
    "new york city": "New York, NY",
    "new york, ny": "New York, NY",
    "new york, ny - 120 broadway": "New York, NY",
    "nyc": "New York, NY",
    "san francisco, ca": "San Francisco, CA",
    "seattle": "Seattle, WA",
    "secaucus": "Secaucus, NJ",
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
    # GitLab's live board uses the ISO/formal country name rather than a
    # city -- "Korea, Republic of" has its own comma, so classify_region's
    # comma-split would otherwise read "Republic of" as the "country" and
    # miss the real one entirely.
    "korea, republic of": "South Korea",
    "mumbai": "Mumbai, India",
    "seoul": "Seoul, South Korea",
    "shanghai": "Shanghai, China",
    "singapore": "Singapore",
    # Agoda-specific noise: its Greenhouse board names its actual office
    # buildings rather than a bare city ("Bangkok (One Bangkok Office)",
    # "Singapore (Guoco) " -- trailing space in the raw data too). Left
    # unmapped, neither exact-matches the lookup table or classify_region's
    # bare-country check, so a real PAC posting (Thailand, Singapore) would
    # silently land in RoW -- same failure shape as every other office-name
    # wrapper on this page.
    "bangkok (one bangkok office)": "Bangkok, Thailand",
    "singapore (guoco)": "Singapore",
    "foshan": "Foshan, China",
    "yokohama": "Yokohama, Japan",
    # Bali is Indonesia, one of classify_region.is_excluded's excluded
    # countries -- unmapped, it wouldn't resolve to a "City, Country" form
    # is_excluded can match on, so an Agoda Bali posting would wrongly stay
    # in scope.
    "bali": "Bali, Indonesia",
    # Agoda's Pune office carries an internal building code
    # ("Pune (AP81)-PNQ") its Greenhouse board's offices[].name never
    # spells out plainly elsewhere -- same is_excluded risk as "bali" above
    # (India is excluded), mapped as a whole string per the "chicago qts"/
    # Snowflake "-mso" convention above since the code isn't a fixed,
    # safely-stripped suffix.
    "pune (ap81)-pnq": "Pune, India",
    "sydney": "Sydney, Australia",
    "sydney, new south wales, australia": "Sydney, Australia",
    "taipei": "Taipei, Taiwan",
    "tel aviv": "Tel Aviv, Israel",
    "apac": "Asia-Pacific",
    # Vietnam is entirely excluded in classify_region's list (pay not
    # comparable to Singapore's -- see classify_region.py's is_excluded),
    # but these cities still get the same "City, Country" treatment as every
    # other city above rather than being left as raw board noise --
    # normalization doesn't depend on the PAC/RoW/excluded split. Confirmed
    # on WorldQuant's live Greenhouse board ("Hanoi", "Ho Chi Minh City") and
    # Spotify's live Lever board ("Ho Chi Minh", without "City").
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
    # Seeded from Grab's and Wise's live SmartRecruiters boards, both of
    # which hand this project a single plain `location.city` string per
    # posting (see job_boards/smartrecruiters/client.py) rather than a
    # pre-split offices array -- same "City, Country"/bare-city-state
    # convention as every entry above once decoded.
    "petaling jaya": "Petaling Jaya, Malaysia",
    "johor bahru": "Johor Bahru, Malaysia",
    "kuala lumpur": "Kuala Lumpur, Malaysia",
    "jakarta": "Jakarta, Indonesia",
    "bandung": "Bandung, Indonesia",
    "medan": "Medan, Indonesia",
    "palembang": "Palembang, Indonesia",
    "batam": "Batam, Indonesia",
    "pangkal pinang": "Pangkal Pinang, Indonesia",
    "tangerang": "Tangerang, Indonesia",
    # SmartRecruiters' own `location.city` value for one Grab posting --
    # BSD City is a district of Tangerang, not a separate city.
    "bsd, tangerang": "Tangerang, Indonesia",
    "bangkok": "Bangkok, Thailand",
    "chiang mai": "Chiang Mai, Thailand",
    "pasig city": "Pasig City, Philippines",
    "phnom penh": "Phnom Penh, Cambodia",
    "suzhou": "Suzhou, China",
    # Vietnam is excluded entirely (see the "hanoi"/"ho chi minh" comment
    # above); HCMC is Grab's own abbreviation for the city already mapped
    # above as "Ho Chi Minh"/"Ho Chi Minh City".
    "hcmc": "Ho Chi Minh City, Vietnam",
    "cluj napoca": "Cluj-Napoca, Romania",
    "tallinn": "Tallinn, Estonia",
    "sao paulo": "Sao Paulo, Brazil",
    "são paulo": "Sao Paulo, Brazil",
    "hyderabad": "Hyderabad, India",
    "berlin": "Berlin, Germany",
    "brussels": "Brussels, Belgium",
    "bogota": "Bogota, Colombia",
    # Added alongside Armenia/Uruguay joining
    # classify_region._EXCLUDED_COUNTRIES -- not yet confirmed against a
    # specific live board's exact raw value like most entries here, but
    # needed so a bare city office normalizes to a real "City, Country"
    # is_excluded can actually match on.
    "montevideo": "Montevideo, Uruguay",
    "yerevan": "Yerevan, Armenia",
    "johannesburg": "Johannesburg, South Africa",
    "ottawa": "Ottawa, Canada",
    "ciudad de méxico": "Mexico City, Mexico",
    # A ward of Tokyo on Wise's board, not a separate city.
    "chuo city": "Tokyo, Japan",
    # Eightfold's own `locations` entries already come as "City, Country"
    # (see job_boards/eightfold/client.py's module docstring) -- but
    # Millennium's live board also mixes in a THIRD shape neither format
    # above covers: a full "City, State/Region, Country" triple (e.g.
    # "Tokyo, Tokyo, Japan", where the region repeats the city name, same
    # idea as the "City, City" wrapper _DUPLICATE_NAME_RE handles for a
    # 2-part string, just one comma over). These whole-string entries are
    # NOT actually what fixes Eightfold's own normalization today --
    # job_boards/eightfold/client.py's `_location` always truncates to the
    # leading city segment before ever calling normalize_location(), so
    # only the bare city (see the entries above, e.g. "dallas"/"miami"/
    # "secaucus") is ever looked up on that path, and the correct
    # bare-city entry is what actually needs to exist for each one. Kept
    # here anyway as a record of the raw triples seen on Millennium's live
    # board, and in case some future caller ever normalizes the untruncated
    # string directly; classify_region reads the country correctly off
    # either an unmapped bare city OR an unmapped full triple (it only ever
    # needs whatever's after the LAST comma, or the whole string if there's
    # no comma at all), so a missing entry on EITHER path is a display-only
    # inconsistency, never a PAC/RoW misclassification.
    "new york, new york, united states of america": "New York, NY",
    "miami, florida, united states of america": "Miami, FL",
    "dallas, texas, united states of america": "Dallas, TX",
    "secaucus, new jersey, united states of america": "Secaucus, NJ",
    "chicago, illinois, united states of america": "Chicago, IL",
    "bangalore, karnataka, india": "Bangalore, India",
    "mumbai, maharashtra, india": "Mumbai, India",
    "tokyo, tokyo, japan": "Tokyo, Japan",
    "shanghai, shanghai, china": "Shanghai, China",
    # Eightfold spells this out as the full country name rather than the
    # "UAE" abbreviation every other board's raw data already uses.
    "dubai, united arab emirates": "Dubai, UAE",
    # "London, London, United Kingdom" -- same repeated-name idea as
    # _DUPLICATE_NAME_RE's "City, City" wrapper, one comma over.
    "london, london, united kingdom": "London, United Kingdom",
    # Seeded from Visa's and Mastercard's live Workday boards (see
    # job_boards/workday/client.py), after _ISO_CODE_PREFIX_RE/
    # _BUILDING_SUFFIX_RE above have stripped their wrappers. Visa's
    # "Singapore, SingPost" (an office inside SingPost's building) reads as
    # country "SingPost" to classify_region, so a real Singapore posting
    # would silently land in RoW.
    "singapore, singpost": "Singapore",
    # What's left of Visa's "IN - Remote - IND"/"GB - Remote - GBR" once the
    # prefixes are stripped -- 3-letter ISO codes. India matters most:
    # unmapped, is_excluded can't recognize it.
    "ind": "India",
    "gbr": "United Kingdom",
    "hong kong, china": "Hong Kong",
    "wanchai, hong kong": "Hong Kong",
    "manilla, philippines": "Manila, Philippines",
    "pasay, philippines - manila": "Pasay, Philippines",
    "london, england": "London, United Kingdom",
    "besiktas, istanbul turkey": "Istanbul, Turkey",
    "são paulo, brazil": "Sao Paulo, Brazil",
    # Mastercard spells out the full US state name rather than this
    # module's "City, ST" convention.
    "o'fallon, missouri": "O'Fallon, MO",
    "purchase, new york": "Purchase, NY",
    "new york city, new york": "New York, NY",
    "arlington, virginia": "Arlington, VA",
    "atlanta, georgia": "Atlanta, GA",
    "chicago, illinois": "Chicago, IL",
    "miami, florida": "Miami, FL",
    "san francisco, california": "San Francisco, CA",
    "salt lake city, utah": "Salt Lake City, UT",
    "boston, massachusetts": "Boston, MA",
    "austin, texas": "Austin, TX",
    # Dataiku's Greenhouse board writes "Country, City" -- the reverse of
    # every other board. classify_region reads the part after the LAST
    # comma as the country, so unmapped, "Japan, Tokyo"/"South Korea,
    # Seoul" postings would silently land in RoW instead of PAC.
    "japan, tokyo": "Tokyo, Japan",
    "south korea, seoul": "Seoul, South Korea",
    "france, paris": "Paris, France",
    "united kingdom, london": "London, United Kingdom",
    "united states, new york": "New York, NY",
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
    cleaned = _ISO_CODE_PREFIX_RE.sub("", cleaned).strip()
    cleaned = _ISO_CODE_SPACING_RE.sub(r"\1-", cleaned).strip()
    cleaned = _REMOTE_PREFIX_RE.sub("", cleaned).strip()
    cleaned = _REMOTE_PREFIX_NO_DASH_RE.sub("", cleaned).strip()
    cleaned = _REMOTE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _REMOTE_COMMA_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _COUNTRY_CODE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _WORK_MODE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _BUILDING_SUFFIX_RE.sub(r"\1", cleaned).strip()
    cleaned = _DUPLICATE_NAME_RE.sub(r"\1", cleaned).strip()
    return _CANONICAL_LOCATIONS.get(cleaned.lower(), cleaned)
