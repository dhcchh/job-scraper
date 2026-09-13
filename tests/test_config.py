"""Tests for config._load_boards -- against a temp fixture file, not the
real company_toml/greenhouse_boards.toml, so this doesn't churn every time
a firm is added or removed.
"""

import pytest

from jobscraper.config import _load_boards


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

    boards = _load_boards(boards_file)

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
        _load_boards(boards_file)


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
        _load_boards(boards_file)
