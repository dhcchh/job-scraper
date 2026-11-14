"""Load and validate checked-in job-board configuration."""

import re
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict


# Each provider's name in the sheet's source column. Clients stamp it on
# every job and Full Refresh rewrites rows from it, and provider-specific
# classifier rules are keyed by it, so all three read this one table.
PROVIDER_LABELS: dict[str, str] = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "eightfold": "Eightfold",
    "workday": "Workday",
    "recruitee": "Recruitee",
    "citadel": "Citadel",
    "amazon": "Amazon",
}

PROVIDERS: tuple[str, ...] = tuple(PROVIDER_LABELS)

_PACKAGE_BOARD_DIR = Path(__file__).resolve().parent / "company_toml"
DEFAULT_BOARD_DIR = (
    _PACKAGE_BOARD_DIR
    if _PACKAGE_BOARD_DIR.is_dir()
    else Path(__file__).resolve().parents[2] / "company_toml"
)

_REQUIRED_FIELDS = ("token", "firm", "company_type")
_COMMON_FIELDS = frozenset(_REQUIRED_FIELDS)
_ALLOWED_FIELDS = {
    provider: _COMMON_FIELDS | ({"location_scope"} if provider == "workday" else set())
    for provider in PROVIDERS
}

_SLUG_TOKEN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_WORKDAY_TOKEN_RE = re.compile(r"^[A-Za-z0-9-]+\.wd\d+/[^/\s]+$")
# An amazon.jobs category slug, such as "software-development".
_AMAZON_TOKEN_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class BoardConfig(TypedDict):
    token: str
    firm: str
    company_type: str
    location_scope: NotRequired[str]


def load_boards(path: Path, provider: str | None = None) -> list[BoardConfig]:
    """Load one provider file and reject unsafe or ambiguous entries."""
    provider = provider or _provider_from_path(path)
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported board provider {provider!r}")

    with open(path, "rb") as file:
        payload = tomllib.load(file)

    unknown_sections = sorted(set(payload) - {"boards"})
    if unknown_sections:
        raise ValueError(
            f"{path.name}: unsupported top-level field(s): "
            f"{', '.join(unknown_sections)}"
        )

    raw_boards = payload.get("boards")
    if not isinstance(raw_boards, list):
        raise ValueError(f"{path.name}: expected [[boards]] to be an array of tables")

    boards: list[BoardConfig] = []
    tokens: dict[str, int] = {}
    for index, raw in enumerate(raw_boards, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: [[boards]] entry #{index} must be a table")

        identifier = raw.get("firm") or raw.get("token") or f"#{index}"
        unknown = sorted(set(raw) - _ALLOWED_FIELDS[provider])
        if unknown:
            raise ValueError(
                f"{path.name}: [[boards]] entry {identifier!r} has unsupported "
                f"field(s): {', '.join(unknown)}"
            )

        string_fields = [*_REQUIRED_FIELDS]
        if "location_scope" in raw:
            string_fields.append("location_scope")
        missing = [
            field
            for field in string_fields
            if not isinstance(raw.get(field), str) or not raw[field].strip()
        ]
        if missing:
            raise ValueError(
                f"{path.name}: [[boards]] entry {identifier!r} is missing "
                f"required non-blank string field(s): {', '.join(missing)}"
            )

        for field in string_fields:
            value = raw[field]
            if value != value.strip():
                raise ValueError(
                    f"{path.name}: [[boards]] entry {identifier!r} field "
                    f"{field!r} has surrounding whitespace"
                )

        token = raw["token"]
        _validate_token(path, provider, identifier, token)
        token_key = token.casefold()
        if token_key in tokens:
            raise ValueError(
                f"{path.name}: duplicate token {token!r} in [[boards]] entries "
                f"#{tokens[token_key]} and #{index}"
            )
        tokens[token_key] = index

        board: BoardConfig = {
            "token": token,
            "firm": raw["firm"],
            "company_type": raw["company_type"],
        }
        if "location_scope" in raw:
            board["location_scope"] = raw["location_scope"]
        boards.append(board)

    return boards


def load_all_boards(
    directory: Path = DEFAULT_BOARD_DIR,
) -> dict[str, list[BoardConfig]]:
    """Load every provider and validate firm metadata across files."""
    boards_by_provider = {
        provider: load_boards(directory / f"{provider}_boards.toml", provider)
        for provider in PROVIDERS
    }

    firms: dict[str, tuple[str, str, str]] = {}
    for provider, boards in boards_by_provider.items():
        for board in boards:
            firm_key = board["firm"].casefold()
            previous = firms.get(firm_key)
            if previous is None:
                firms[firm_key] = (board["firm"], board["company_type"], provider)
                continue
            previous_name, previous_type, previous_provider = previous
            if board["firm"] != previous_name:
                raise ValueError(
                    f"firm {board['firm']!r} in {provider}_boards.toml differs in "
                    f"capitalization from {previous_name!r} in "
                    f"{previous_provider}_boards.toml"
                )
            if board["company_type"] != previous_type:
                raise ValueError(
                    f"firm {board['firm']!r} uses company_type "
                    f"{board['company_type']!r} in {provider}_boards.toml but "
                    f"{previous_type!r} in {previous_provider}_boards.toml"
                )

    return boards_by_provider


def _provider_from_path(path: Path) -> str:
    suffix = "_boards"
    if not path.stem.endswith(suffix):
        raise ValueError(
            f"{path.name}: filename must be '<provider>_boards.toml'"
        )
    return path.stem.removesuffix(suffix)


def _validate_token(
    path: Path, provider: str, identifier: object, token: str
) -> None:
    if provider == "citadel":
        if token == "citadel":
            return
        raise ValueError(
            f"{path.name}: [[boards]] entry {identifier!r} has invalid token "
            f"{token!r}; expected 'citadel'"
        )
    pattern, expected = {
        "workday": (_WORKDAY_TOKEN_RE, "'<tenant>.wd<number>/<site>'"),
        "amazon": (
            _AMAZON_TOKEN_RE,
            "an amazon.jobs category slug such as 'software-development'",
        ),
    }.get(
        provider,
        (_SLUG_TOKEN_RE, "a URL slug containing only letters, numbers, '.', '_' or '-'"),
    )
    if pattern.fullmatch(token):
        return
    raise ValueError(
        f"{path.name}: [[boards]] entry {identifier!r} has invalid token "
        f"{token!r}; expected {expected}"
    )
