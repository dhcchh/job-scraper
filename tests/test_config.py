"""Tests for config._load_boards -- against a temp fixture file, not the
real company_toml/greenhouse_boards.toml, so this doesn't churn every time
a firm is added or removed.
"""

import pytest

from jobscraper.config import _load_boards, _optional_int


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


def test_load_boards_raises_on_missing_extra_required_field(tmp_path):
    # Eightfold's `domain` -- required on top of token/firm/company_type,
    # see config.EIGHTFOLD_BOARDS -- validates the same way.
    boards_file = tmp_path / "eightfold_boards.toml"
    boards_file.write_text(
        """
        [[boards]]
        token = "mlp"
        firm = "Millennium"
        company_type = "Prop Trading/Hedge Fund"
        """
    )

    with pytest.raises(ValueError, match="domain"):
        _load_boards(boards_file, extra_required=("domain",))


def test_load_boards_parses_extra_required_field_when_present(tmp_path):
    boards_file = tmp_path / "eightfold_boards.toml"
    boards_file.write_text(
        """
        [[boards]]
        token = "mlp"
        firm = "Millennium"
        company_type = "Prop Trading/Hedge Fund"
        domain = "mlp.com"
        """
    )

    boards = _load_boards(boards_file, extra_required=("domain",))

    assert boards == [
        {
            "token": "mlp",
            "firm": "Millennium",
            "company_type": "Prop Trading/Hedge Fund",
            "domain": "mlp.com",
        }
    ]


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


def test_optional_int_parses_a_set_value(monkeypatch):
    monkeypatch.setenv("SOME_INT", "42")
    assert _optional_int("SOME_INT") == 42


def test_optional_int_none_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_INT", raising=False)
    assert _optional_int("SOME_INT") is None


def test_optional_int_none_when_blank(monkeypatch):
    # A GitHub Actions secret that doesn't exist still shows up as "" in the
    # workflow's env block, not simply absent -- must not raise on int("").
    monkeypatch.setenv("SOME_INT", "")
    assert _optional_int("SOME_INT") is None
