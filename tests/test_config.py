"""Tests for config using fake integrations."""

import pytest

from jobscraper.config import _optional_int, _optional_set


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


def test_optional_set_splits_and_trims(monkeypatch):
    monkeypatch.setenv("SOME_SET", " Acme, Beta Capital ,,")
    assert _optional_set("SOME_SET") == frozenset({"Acme", "Beta Capital"})


def test_optional_set_empty_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_SET", raising=False)
    assert _optional_set("SOME_SET") == frozenset()


def test_missing_sheet_credentials_fail_only_when_opening_a_sheet(monkeypatch):
    from jobscraper import config
    from jobscraper.sheets import writer

    monkeypatch.setattr(config, "GOOGLE_SHEET_ID", "")
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", "")
    with pytest.raises(ValueError, match="Missing required sheet settings"):
        writer.get_spreadsheet()
