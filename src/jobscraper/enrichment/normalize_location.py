"""Canonicalize provider-specific location labels."""

import re

_OFFICE_SUFFIX_RE = re.compile(r"\s+office$", re.IGNORECASE)

_REMOTE_PREFIX_RE = re.compile(r"^remote\s*-\s*", re.IGNORECASE)
_REMOTE_SUFFIX_RE = re.compile(r"\s*\(remote\)$", re.IGNORECASE)

_REMOTE_COMMA_SUFFIX_RE = re.compile(r",\s*remote$", re.IGNORECASE)

_REMOTE_PREFIX_NO_DASH_RE = re.compile(r"^remote\s+(?=[A-Za-z])", re.IGNORECASE)

_LOCATION_SUFFIX_RE = re.compile(r"\s+locations?$", re.IGNORECASE)

_COUNTRY_CODE_SUFFIX_RE = re.compile(r"\s*\([A-Z]{2}\)$")
_WORK_MODE_SUFFIX_RE = re.compile(r"\s*\([^()]*\bmix\)$", re.IGNORECASE)

_DUPLICATE_NAME_RE = re.compile(r"^(.+?),\s*\1$", re.IGNORECASE)

_ISO_CODE_SPACING_RE = re.compile(r"^([A-Za-z]{2})\s+-\s+")

_ISO_CODE_PREFIX_RE = re.compile(
    r"^[A-Za-z]{2}\s+-\s+(?=[^,]+,|remote\s*-\s*\S)", re.IGNORECASE
)

_BUILDING_SUFFIX_RE = re.compile(r"^([^()]+,[^()]+?)\s*\([^()]*\)$")

_OFFICE_CODE_SUFFIX_RE = re.compile(r"\s*\([A-Z]{2}-[A-Z]{2,3}-[A-Z0-9]+\)$")

_WORK_MODE_WRAPPER_RE = re.compile(r"^(?:remote|hybrid)\s*\(([^()]+)\)$", re.IGNORECASE)

_ALPHA3_PREFIX_RE = re.compile(r"^([A-Z]{3})\s+-\s+(.+)$")
_ALPHA3_SUFFIX_RE = re.compile(r"^(.+),\s*([A-Z]{3})(?:\s+-\s+(?i:remote))?$")
_REGION_SITE_RE = re.compile(r"^(?:APAC|AMER|EMEA|LATAM)\s+-\s+(.+)$")
_ALPHA3_COUNTRIES = {
    "SGP": "Singapore", "HKG": "Hong Kong", "AUS": "Australia", "JPN": "Japan",
    "KOR": "South Korea", "CHN": "China", "TWN": "Taiwan", "NZL": "New Zealand",
    "THA": "Thailand",
    "IND": "India", "VNM": "Vietnam", "MYS": "Malaysia", "IDN": "Indonesia",
    "ARM": "Armenia", "URY": "Uruguay",
    "USA": "United States", "CAN": "Canada", "GBR": "United Kingdom",
    "IRL": "Ireland", "DEU": "Germany", "FRA": "France", "NLD": "Netherlands",
    "ESP": "Spain", "SRB": "Serbia", "CRI": "Costa Rica", "POL": "Poland",
    "CHE": "Switzerland", "SWE": "Sweden", "ISR": "Israel", "BRA": "Brazil",
    "MEX": "Mexico", "ARE": "United Arab Emirates", "PHL": "Philippines",
}
_SITE_COUNTRIES = {
    **{country.casefold(): country for country in _ALPHA3_COUNTRIES.values()},
    "korea": "South Korea",
}
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)$")

_CANONICAL_LOCATIONS: dict[str, str] = {
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
    "bangalore": "Bangalore, India",
    "bengaluru": "Bangalore, India",
    "beijing": "Beijing, China",
    "dubai": "Dubai, UAE",
    "gift city": "GIFT City, India",
    "gurgaon": "Gurgaon, India",
    "hong kong": "Hong Kong",
    "korea, republic of": "South Korea",
    "mumbai": "Mumbai, India",
    "seoul": "Seoul, South Korea",
    "shanghai": "Shanghai, China",
    "singapore": "Singapore",
    "bangkok (one bangkok office)": "Bangkok, Thailand",
    "singapore (guoco)": "Singapore",
    "foshan": "Foshan, China",
    "yokohama": "Yokohama, Japan",
    "bali": "Bali, Indonesia",
    "pune (ap81)-pnq": "Pune, India",
    "sydney": "Sydney, Australia",
    "sydney, new south wales, australia": "Sydney, Australia",
    "taipei": "Taipei, Taiwan",
    "taipei city": "Taipei, Taiwan",
    "xinyi district": "Taipei, Taiwan",
    "hsinchu": "Hsinchu, Taiwan",
    "tel aviv": "Tel Aviv, Israel",
    "apac": "Asia-Pacific",
    "hanoi": "Hanoi, Vietnam",
    "ho chi minh": "Ho Chi Minh City, Vietnam",
    "ho chi minh city": "Ho Chi Minh City, Vietnam",
    "vn-hanoi": "Hanoi, Vietnam",
    "vn-ho chi minh city": "Ho Chi Minh City, Vietnam",
    "au-sydney": "Sydney, Australia",
    "ca-toronto": "Toronto, Canada",
    "es-barcelona": "Barcelona, Spain",
    "gb-london": "London, United Kingdom",
    "ie-dublin": "Dublin, Ireland",
    "in-bengaluru": "Bengaluru, India",
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
    "tokyo": "Tokyo, Japan",
    "china mainland_internal": "China",
    "ghp-sg": "Singapore",
    "ghp-vn": "Vietnam",
    "gam": "Singapore",
    "au-canberra-mso": "Canberra, Australia",
    "cn-beijing-mso": "Beijing, China",
    "cn-shanghai-ww": "Shanghai, China",
    "kr-south korea-remote": "South Korea",
    "australia - remote": "Australia",
    "remote": "Remote",
    "virtual": "Remote",
    "anywhere (subject to approval)": "Remote",
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
    "bsd, tangerang": "Tangerang, Indonesia",
    "bangkok": "Bangkok, Thailand",
    "chiang mai": "Chiang Mai, Thailand",
    "pasig city": "Pasig City, Philippines",
    "phnom penh": "Phnom Penh, Cambodia",
    "suzhou": "Suzhou, China",
    "hcmc": "Ho Chi Minh City, Vietnam",
    "cluj napoca": "Cluj-Napoca, Romania",
    "tallinn": "Tallinn, Estonia",
    "sao paulo": "Sao Paulo, Brazil",
    "são paulo": "Sao Paulo, Brazil",
    "hyderabad": "Hyderabad, India",
    "berlin": "Berlin, Germany",
    "brussels": "Brussels, Belgium",
    "bogota": "Bogota, Colombia",
    "montevideo": "Montevideo, Uruguay",
    "yerevan": "Yerevan, Armenia",
    "johannesburg": "Johannesburg, South Africa",
    "ottawa": "Ottawa, Canada",
    "ciudad de méxico": "Mexico City, Mexico",
    "chuo city": "Tokyo, Japan",
    "new york, new york, united states of america": "New York, NY",
    "miami, florida, united states of america": "Miami, FL",
    "dallas, texas, united states of america": "Dallas, TX",
    "secaucus, new jersey, united states of america": "Secaucus, NJ",
    "chicago, illinois, united states of america": "Chicago, IL",
    "bangalore, karnataka, india": "Bangalore, India",
    "mumbai, maharashtra, india": "Mumbai, India",
    "tokyo, tokyo, japan": "Tokyo, Japan",
    "shanghai, shanghai, china": "Shanghai, China",
    "dubai, united arab emirates": "Dubai, UAE",
    "london, london, united kingdom": "London, United Kingdom",
    "singapore, singpost": "Singapore",
    "singapore-capitasky": "Singapore",
    "singapore, marina bay financial tower 2": "Singapore",
    "singapore, pasir panjang": "Singapore",
    "singapore, singapore-suntec tower": "Singapore",
    "singapore - singapore": "Singapore",
    "ind": "India",
    "gbr": "United Kingdom",
    "hong kong, china": "Hong Kong",
    "wanchai, hong kong": "Hong Kong",
    "manilla, philippines": "Manila, Philippines",
    "pasay, philippines - manila": "Pasay, Philippines",
    "london, england": "London, United Kingdom",
    "besiktas, istanbul turkey": "Istanbul, Turkey",
    "são paulo, brazil": "Sao Paulo, Brazil",
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
    "japan, tokyo": "Tokyo, Japan",
    "south korea, seoul": "Seoul, South Korea",
    "france, paris": "Paris, France",
    "united kingdom, london": "London, United Kingdom",
    "united states, new york": "New York, NY",
}


_KNOWN_LOCATIONS = frozenset(_CANONICAL_LOCATIONS.values())


def _alpha3_location(country: str, rest: str) -> str:
    """Normalize a location prefixed by an ISO alpha-3 country code."""
    place = _TRAILING_PAREN_RE.sub("", rest).strip()
    if not place or place.lower().startswith("remote"):
        return country
    normalized = normalize_location(place)
    if normalized in _KNOWN_LOCATIONS:
        return normalized
    return f"{normalized}, {country}"


def _region_site_location(rest: str) -> str | None:
    """Normalize a "<Country> - [<State> -] <City> - <Building>" site name."""
    country_part, *parts = (part.strip() for part in rest.split(" - "))
    country = _SITE_COUNTRIES.get(country_part.casefold())
    if country is None:
        return None
    for part in parts:
        normalized = normalize_location(part)
        if normalized == country or normalized.endswith(f", {country}"):
            return normalized
    return country


def normalize_location(raw: str) -> str:
    """Return a canonical location or a cleaned, unchanged fallback."""
    alpha3 = _ALPHA3_PREFIX_RE.match(raw.strip())
    if alpha3 and alpha3.group(1) in _ALPHA3_COUNTRIES:
        return _alpha3_location(_ALPHA3_COUNTRIES[alpha3.group(1)], alpha3.group(2))
    alpha3 = _ALPHA3_SUFFIX_RE.match(raw.strip())
    if alpha3 and alpha3.group(2) in _ALPHA3_COUNTRIES:
        return _alpha3_location(_ALPHA3_COUNTRIES[alpha3.group(2)], alpha3.group(1))
    region_site = _REGION_SITE_RE.match(raw.strip())
    if region_site:
        location = _region_site_location(region_site.group(1))
        if location:
            return location
    cleaned = _OFFICE_SUFFIX_RE.sub("", raw.strip()).strip()
    cleaned = _LOCATION_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _OFFICE_CODE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _WORK_MODE_WRAPPER_RE.sub(r"\1", cleaned).strip()
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


def normalize_locations(locations: list[str | None]) -> str:
    """Normalize, deduplicate, and pipe-join locations in input order."""
    normalized = (normalize_location(location or "") for location in locations)
    return " | ".join(dict.fromkeys(filter(None, normalized)))
