"""Tests for config._load_boards -- against a temp fixture file, not the
real greenhouse_boards.toml, so this doesn't churn every time a firm is
added or removed.
"""

from jobscraper.config import _load_boards


def test_load_boards_parses_token_and_firm(tmp_path):
    boards_file = tmp_path / "greenhouse_boards.toml"
    boards_file.write_text(
        """
        [[boards]]
        token = "acme"
        firm = "Acme Corp"

        [[boards]]
        token = "beta"
        firm = "Beta Inc"
        """
    )

    boards = _load_boards(boards_file)

    assert boards == [
        {"token": "acme", "firm": "Acme Corp"},
        {"token": "beta", "firm": "Beta Inc"},
    ]
