"""Tests for telegram.py -- pure formatting/grouping/chunking logic plus
send_digest's config-gating and best-effort error handling. No real network
call: requests is monkeypatched, never exercised for real.
"""

import datetime

import pytest

from jobscraper import telegram
from jobscraper.models import Job

_SHEET_URL = "https://docs.google.com/spreadsheets/d/test-sheet-id"


def _job(title, board="Acme", location="Singapore", url="https://example.com/1"):
    return Job(
        board, "1", title, "Eng", location, url, "", datetime.date(2026, 9, 12)
    )


def test_build_digest_text_is_none_when_every_category_is_empty():
    assert telegram.build_digest_text({}, _SHEET_URL) is None
    assert (
        telegram.build_digest_text(
            {"DA/BI": [], "SWE/DE": [], "DS/ML/Algos": []}, _SHEET_URL
        )
        is None
    )


def test_build_digest_text_includes_only_nonempty_categories_in_order():
    text = telegram.build_digest_text(
        {
            "SWE/DE": [_job("Software Engineer")],
            "DA/BI": [_job("Data Analyst")],
        },
        _SHEET_URL,
    )
    assert text is not None
    # DIGEST_CATEGORIES order (DA/BI, DS/ML/Algos, SWE/DE), not insertion order.
    assert text.index("DA/BI") < text.index("SWE/DE")
    assert "DS/ML/Algos" not in text
    assert "2 new PAC role(s)" in text


def test_build_digest_text_groups_by_category_firm_and_location():
    text = telegram.build_digest_text(
        {
            "SWE/DE": [
                _job("Backend Engineer", board="Grab", location="Singapore"),
                _job("Frontend Engineer", board="Grab", location="Singapore"),
                _job("Data Analyst", board="Grab", location="Jakarta, Indonesia"),
                _job("Support Engineer", board="Wise", location="Singapore"),
            ],
        },
        _SHEET_URL,
    )
    assert "Grab x2 (Singapore)" in text
    assert "Grab x1 (Jakarta, Indonesia)" in text
    assert "Wise x1 (Singapore)" in text
    # One shared "SWE/DE" header for all three groups, not repeated per line.
    assert text.count("SWE/DE") == 1
    # Individual job titles are never shown -- see module docstring.
    assert "Backend Engineer" not in text
    assert "Frontend Engineer" not in text


def test_build_digest_text_omits_location_clause_when_absent():
    job = Job("Acme", "1", "Data Analyst", "Eng", "", "", "", datetime.date(2026, 9, 12))
    text = telegram.build_digest_text({"DA/BI": [job]}, _SHEET_URL)
    assert "Acme x1" in text
    assert "(" not in text.split("\n\n")[1]  # the DA/BI category block


def test_build_digest_text_escapes_html_special_characters():
    text = telegram.build_digest_text(
        {"DA/BI": [_job("Data Analyst", board="A&B <Co>", location="R&D")]},
        _SHEET_URL,
    )
    assert "A&amp;B &lt;Co&gt;" in text
    assert "R&amp;D" in text
    assert "<Co>" not in text


def test_build_digest_text_includes_a_link_to_the_spreadsheet():
    text = telegram.build_digest_text({"DA/BI": [_job("Data Analyst")]}, _SHEET_URL)
    assert f'<a href="{_SHEET_URL}">' in text


def test_build_digest_text_escapes_quotes_in_the_sheet_url():
    url = 'https://example.com/sheet?x="y"'
    text = telegram.build_digest_text({"DA/BI": [_job("Data Analyst")]}, url)
    assert 'href="https://example.com/sheet?x=&quot;y&quot;"' in text


def test_chunk_returns_whole_text_when_under_limit():
    assert telegram._chunk("short text", limit=100) == ["short text"]


def test_chunk_splits_on_section_boundaries_without_breaking_a_section():
    text = "header\n\nsection one\nline\n\nsection two\nline"
    chunks = telegram._chunk(text, limit=20)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 20
    # No section's lines got separated from each other.
    assert "section one\nline" in "\n\n".join(chunks)
    assert "section two\nline" in "\n\n".join(chunks)


def test_chunk_splits_a_single_oversized_section_line_by_line():
    text = "a" * 5 + "\n" + "b" * 5 + "\n" + "c" * 5
    chunks = telegram._chunk(text, limit=6)
    assert chunks == ["aaaaa", "bbbbb", "ccccc"]


def test_chunk_carries_a_section_header_into_every_continuation_chunk():
    """A section's first line (e.g. build_digest_text's "<b>Category</b>"
    header) must survive into every chunk the rest of that section gets
    split into -- otherwise a continuation chunk carries none of the
    context explaining what its lines are about."""
    lines = "\n".join(f"item {i}" for i in range(10))
    text = f"<b>Header</b>\n{lines}"
    chunks = telegram._chunk(text, limit=30)
    assert len(chunks) > 1
    for chunk in chunks:
        assert "<b>Header</b>" in chunk
        assert len(chunk) <= 30


def test_build_digest_text_and_chunk_keep_every_message_self_describing():
    """End-to-end reproduction of a busy category: enough distinct (firm,
    location) groups that _chunk must split the category block line by
    line. Every resulting chunk of that block must still name the category,
    matching test_chunk_carries_a_section_header_into_every_continuation_chunk
    but through the real build_digest_text output."""
    jobs = [
        _job(f"Role {i}", board=f"Firm {i}", location=f"City {i}") for i in range(50)
    ]
    text = telegram.build_digest_text({"DA/BI": jobs}, _SHEET_URL)
    chunks = telegram._chunk(text, limit=200)
    assert len(chunks) > 1
    body_chunks = [c for c in chunks if "Firm" in c]
    assert len(body_chunks) > 1  # actually exercises the split, not a fluke
    for chunk in body_chunks:
        assert "DA/BI" in chunk


def test_chunk_splits_cleanly_at_section_boundaries_across_categories():
    """Several categories that are each individually small but together
    exceed the limit should split at the "\\n\\n" section boundaries between
    them, never falling into the line-by-line fallback -- each category's
    header and its lines should always land in the same chunk."""
    text = telegram.build_digest_text(
        {
            "DA/BI": [_job("Data Analyst", board="Acme", location="Singapore")],
            "DS/ML/Algos": [_job("Data Scientist", board="Beta", location="Tokyo")],
            "SWE/DE": [_job("Engineer", board="Gamma", location="Hong Kong")],
        },
        _SHEET_URL,
    )
    chunks = telegram._chunk(text, limit=80)
    assert len(chunks) > 1
    for category, firm in [
        ("DA/BI", "Acme"),
        ("DS/ML/Algos", "Beta"),
        ("SWE/DE", "Gamma"),
    ]:
        matches = [c for c in chunks if category in c and firm in c]
        assert len(matches) == 1, f"{category}/{firm} should share exactly one chunk"


def test_send_digest_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)

    def fail(*args, **kwargs):
        raise AssertionError("must not call requests.post when unconfigured")

    monkeypatch.setattr(telegram.requests, "post", fail)
    telegram.send_digest(
        {"DA/BI": [_job("Data Analyst")]}, spreadsheet_url=_SHEET_URL
    )  # must not raise


def test_send_digest_noop_when_nothing_new(monkeypatch):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)

    def fail(*args, **kwargs):
        raise AssertionError("must not call requests.post with nothing new")

    monkeypatch.setattr(telegram.requests, "post", fail)
    telegram.send_digest({"DA/BI": []}, spreadsheet_url=_SHEET_URL)  # must not raise


def test_send_digest_posts_with_chat_and_thread_and_html_parse_mode(monkeypatch):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", -100123)
    monkeypatch.setattr(telegram.config, "TELEGRAM_THREAD_ID", 42)

    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse()

    monkeypatch.setattr(telegram.requests, "post", fake_post)

    telegram.send_digest({"DA/BI": [_job("Data Analyst")]}, spreadsheet_url=_SHEET_URL)

    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert url == "https://api.telegram.org/botsecret-token/sendMessage"
    assert payload["chat_id"] == -100123
    assert payload["message_thread_id"] == 42
    assert payload["parse_mode"] == "HTML"
    assert "DA/BI" in payload["text"]
    assert _SHEET_URL in payload["text"]


def test_send_digest_omits_thread_id_when_unset(monkeypatch):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)
    monkeypatch.setattr(telegram.config, "TELEGRAM_THREAD_ID", None)

    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append(json)
        return _FakeResponse()

    monkeypatch.setattr(telegram.requests, "post", fake_post)

    telegram.send_digest({"DA/BI": [_job("Data Analyst")]}, spreadsheet_url=_SHEET_URL)

    assert "message_thread_id" not in calls[0]


def test_send_digest_swallows_a_send_failure(monkeypatch):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)

    def fake_post(url, json, timeout):
        raise ConnectionError("network down")

    monkeypatch.setattr(telegram.requests, "post", fake_post)

    telegram.send_digest(
        {"DA/BI": [_job("Data Analyst")]}, spreadsheet_url=_SHEET_URL
    )  # must not raise


def test_send_digest_sends_multiple_messages_when_over_the_limit(monkeypatch):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)
    monkeypatch.setattr(telegram, "_MAX_MESSAGE_LEN", 100)

    # Distinct (firm, location) pairs so grouping can't collapse them into
    # one short line.
    many_jobs = [_job(f"Role {i}", board=f"Firm {i}", location=f"City {i}") for i in range(30)]

    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append(json["text"])
        return _FakeResponse()

    monkeypatch.setattr(telegram.requests, "post", fake_post)

    telegram.send_digest({"DA/BI": many_jobs}, spreadsheet_url=_SHEET_URL)

    assert len(calls) > 1
    for text in calls:
        assert len(text) <= 100


def test_send_digest_stops_after_a_failed_chunk(monkeypatch):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)
    monkeypatch.setattr(telegram, "_MAX_MESSAGE_LEN", 100)

    many_jobs = [_job(f"Role {i}", board=f"Firm {i}", location=f"City {i}") for i in range(30)]

    calls = []

    def fake_post(url, json, timeout):
        calls.append(json["text"])
        raise ConnectionError("network down")

    monkeypatch.setattr(telegram.requests, "post", fake_post)

    telegram.send_digest({"DA/BI": many_jobs}, spreadsheet_url=_SHEET_URL)  # must not raise

    assert len(calls) == 1  # stopped after the first failure, no retry storm


def test_check_connection_reports_not_configured_without_calling_post(
    monkeypatch, capsys
):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)

    def fail(*args, **kwargs):
        raise AssertionError("must not call requests.post when unconfigured")

    monkeypatch.setattr(telegram.requests, "post", fail)

    assert telegram.check_connection() is False
    assert "Not configured" in capsys.readouterr().out


def test_check_connection_sends_and_reports_success(monkeypatch, capsys):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", -100123)
    monkeypatch.setattr(telegram.config, "TELEGRAM_THREAD_ID", 42)

    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append(json)
        return _FakeResponse()

    monkeypatch.setattr(telegram.requests, "post", fake_post)

    assert telegram.check_connection() is True
    assert len(calls) == 1
    assert calls[0]["chat_id"] == -100123
    assert calls[0]["message_thread_id"] == 42
    out = capsys.readouterr().out
    assert "Sent OK" in out
    assert "-100123" in out
    assert "42" in out


def test_check_connection_reports_a_send_failure_without_raising(monkeypatch, capsys):
    monkeypatch.setattr(telegram.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(telegram.config, "TELEGRAM_CHAT_ID", 123)

    def fake_post(url, json, timeout):
        raise ConnectionError("network down")

    monkeypatch.setattr(telegram.requests, "post", fake_post)

    assert telegram.check_connection() is False  # not swallowed like send_digest
    assert "Failed to send" in capsys.readouterr().out
