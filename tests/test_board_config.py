"""Validation tests for checked-in board configuration."""

import textwrap

import pytest

from jobscraper.board_config import PROVIDERS, load_all_boards, load_boards


def _write(tmp_path, filename, body):
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body))
    return path


def test_checked_in_board_configuration_is_valid():
    boards_by_provider = load_all_boards()

    assert tuple(boards_by_provider) == PROVIDERS
    assert all(boards for provider, boards in boards_by_provider.items() if provider != "citadel")
    assert boards_by_provider["citadel"] == []  # Disabled pending a Cloudflare access fix.


def test_load_boards_rejects_duplicate_tokens_ignoring_case(tmp_path):
    path = _write(
        tmp_path,
        "greenhouse_boards.toml",
        """
        [[boards]]
        token = "acme"
        firm = "Acme"
        company_type = "Startup"

        [[boards]]
        token = "ACME"
        firm = "Other Acme"
        company_type = "Startup"
        """,
    )

    with pytest.raises(ValueError, match="duplicate token"):
        load_boards(path)


def test_load_boards_accepts_fixed_citadel_token(tmp_path):
    path = _write(tmp_path, "citadel_boards.toml", '''
        [[boards]]
        token = "citadel"
        firm = "Citadel"
        company_type = "Prop Trading/Hedge Fund"
    ''')
    assert load_boards(path)[0]["token"] == "citadel"


def test_load_boards_accepts_amazon_category_slugs_under_one_firm(tmp_path):
    path = _write(tmp_path, "amazon_boards.toml", '''
        [[boards]]
        token = "software-development"
        firm = "Amazon"
        company_type = "Consumer/Marketplace"

        [[boards]]
        token = "systems-quality-security-engineering"
        firm = "Amazon"
        company_type = "Consumer/Marketplace"
    ''')
    assert [board["token"] for board in load_boards(path)] == [
        "software-development",
        "systems-quality-security-engineering",
    ]


def test_load_boards_rejects_unknown_fields(tmp_path):
    path = _write(
        tmp_path,
        "greenhouse_boards.toml",
        """
        [[boards]]
        token = "acme"
        firm = "Acme"
        company_type = "Startup"
        location_scope = "Singapore"
        """,
    )

    with pytest.raises(ValueError, match="unsupported field.*location_scope"):
        load_boards(path)


def test_load_boards_accepts_workday_location_scope(tmp_path):
    path = _write(
        tmp_path,
        "workday_boards.toml",
        """
        [[boards]]
        token = "acme.wd5/External_Careers"
        firm = "Acme"
        company_type = "Startup"
        location_scope = "Singapore"
        """,
    )

    assert load_boards(path) == [
        {
            "token": "acme.wd5/External_Careers",
            "firm": "Acme",
            "company_type": "Startup",
            "location_scope": "Singapore",
        }
    ]


def test_load_boards_rejects_blank_workday_location_scope(tmp_path):
    path = _write(
        tmp_path,
        "workday_boards.toml",
        """
        [[boards]]
        token = "acme.wd5/External_Careers"
        firm = "Acme"
        company_type = "Startup"
        location_scope = " "
        """,
    )

    with pytest.raises(ValueError, match="non-blank string field.*location_scope"):
        load_boards(path)


@pytest.mark.parametrize(
    ("filename", "token"),
    [
        ("greenhouse_boards.toml", "not a slug"),
        ("workday_boards.toml", "acme/External_Careers"),
        ("citadel_boards.toml", "citadel-securities"),
        ("amazon_boards.toml", "Software-Development"),
        ("amazon_boards.toml", "software_development"),
        ("amazon_boards.toml", "software-development-"),
    ],
)
def test_load_boards_rejects_malformed_provider_tokens(tmp_path, filename, token):
    path = _write(
        tmp_path,
        filename,
        f"""
        [[boards]]
        token = "{token}"
        firm = "Acme"
        company_type = "Startup"
        """,
    )

    with pytest.raises(ValueError, match="invalid token"):
        load_boards(path)


def test_load_boards_rejects_surrounding_whitespace(tmp_path):
    path = _write(
        tmp_path,
        "lever_boards.toml",
        """
        [[boards]]
        token = "acme"
        firm = " Acme"
        company_type = "Startup"
        """,
    )

    with pytest.raises(ValueError, match="surrounding whitespace"):
        load_boards(path)


def test_load_all_boards_rejects_inconsistent_company_type(tmp_path):
    for provider in PROVIDERS:
        _write(tmp_path, f"{provider}_boards.toml", "boards = []")
    _write(
        tmp_path,
        "greenhouse_boards.toml",
        """
        [[boards]]
        token = "acme"
        firm = "Acme"
        company_type = "Startup"
        """,
    )
    _write(
        tmp_path,
        "lever_boards.toml",
        """
        [[boards]]
        token = "acme-jobs"
        firm = "Acme"
        company_type = "Big Tech"
        """,
    )

    with pytest.raises(ValueError, match="uses company_type"):
        load_all_boards(tmp_path)


def test_load_boards_parses_token_firm_and_company_type(tmp_path):
    boards_file = tmp_path / "greenhouse_boards.toml"
    boards_file.write_text(
        """
        [[boards]]
        token = "acme"
        firm = "Acme Corp"
        company_type = "Big Tech"

        [[boards]]
        token = "beta"
        firm = "Beta Inc"
        company_type = "Startup"
        """
    )

    boards = load_boards(boards_file)

    assert boards == [
        {"token": "acme", "firm": "Acme Corp", "company_type": "Big Tech"},
        {"token": "beta", "firm": "Beta Inc", "company_type": "Startup"},
    ]


@pytest.mark.parametrize("missing_field", ["token", "firm", "company_type"])
def test_load_boards_raises_on_missing_field(tmp_path, missing_field):
    fields = {"token": "acme", "firm": "Acme Corp", "company_type": "Big Tech"}
    del fields[missing_field]

    boards_file = tmp_path / "greenhouse_boards.toml"
    entries = "\n".join(f'{key} = "{value}"' for key, value in fields.items())
    boards_file.write_text(f"[[boards]]\n{entries}\n")

    with pytest.raises(ValueError, match=missing_field):
        load_boards(boards_file)


def test_load_boards_error_identifies_entry_by_firm(tmp_path):
    boards_file = tmp_path / "greenhouse_boards.toml"
    boards_file.write_text(
        """
        [[boards]]
        token = "acme"
        firm = "Acme Corp"
        company_type = "Big Tech"

        [[boards]]
        token = "beta"
        firm = "Beta Inc"
        """
    )

    with pytest.raises(ValueError, match="Beta Inc"):
        load_boards(boards_file)
