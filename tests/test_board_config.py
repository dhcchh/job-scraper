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
    assert all(boards_by_provider.values())


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


@pytest.mark.parametrize(
    ("filename", "token"),
    [
        ("greenhouse_boards.toml", "not a slug"),
        ("workday_boards.toml", "acme/External_Careers"),
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
