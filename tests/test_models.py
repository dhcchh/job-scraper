import datetime

from jobscraper.models import FIELDS, Job, hyperlink_formula, normalize_date, strip_html


def test_normalize_date():
    assert normalize_date("2026-09-09T05:54:53-04:00") == "2026-09-09"
    assert normalize_date("2026-06-01T00:00:00Z") == "2026-06-01"
    assert normalize_date("2026-09-12") == "2026-09-12"
    assert normalize_date(datetime.date(2026, 9, 12)) == "2026-09-12"
    assert normalize_date(datetime.datetime(2026, 9, 12, 14, 30)) == "2026-09-12"
    assert normalize_date("") == ""
    assert normalize_date(None) == ""


def test_job_to_row_matches_fields_order():
    job = Job(
        board="acme",
        job_id="1",
        title="Engineer",
        department="Eng",
        location="Remote",
        url="https://example.com/1",
        updated_at="2026-09-01T00:00:00Z",
        scraped_date=datetime.date(2026, 9, 12),
    )
    row = job.to_row()
    assert job.salary is None
    assert len(row) == len(FIELDS)
    assert row == [
        "",  # first_published defaults when not passed explicitly
        "2026-09-01",
        "acme",
        "Engineer",
        "Remote",
        "Unspecified",  # seniority defaults when not passed explicitly
        "",  # company_type defaults when not passed explicitly
        '=HYPERLINK("https://example.com/1", "Apply")',
        "Eng",
        "1",
        "2026-09-12",
        "",  # source defaults when not passed explicitly
        "",  # description defaults when not passed explicitly
        "",  # null salary renders as an empty Google Sheets cell
    ]


def test_hyperlink_formula_wraps_url_with_default_label():
    assert (
        hyperlink_formula("https://example.com/1")
        == '=HYPERLINK("https://example.com/1", "Apply")'
    )


def test_hyperlink_formula_empty_url_is_empty_string():
    assert hyperlink_formula("") == ""


def test_hyperlink_formula_escapes_embedded_quotes():
    assert (
        hyperlink_formula('https://example.com/"1"', label='say "hi"')
        == '=HYPERLINK("https://example.com/""1""", "say ""hi""")'
    )


def test_strip_html_empty_is_empty_string():
    assert strip_html("") == ""


def test_strip_html_undoes_greenhouse_double_encoding():
    # Greenhouse's `content` field is HTML-entity-double-encoded -- the raw
    # JSON value literally contains "&lt;p&gt;", not "<p>" -- confirmed
    # against every board this project fetches.
    raw = "&lt;p&gt;Qube Research &amp;amp; Technologies is hiring.&lt;/p&gt;"
    assert strip_html(raw) == "Qube Research & Technologies is hiring."


def test_strip_html_converts_list_items_to_bullet_lines():
    raw = "<p>Responsibilities:</p><ul><li>Write code</li><li>Ship it</li></ul>"
    assert strip_html(raw) == "Responsibilities:\n\n- Write code\n- Ship it"


def test_strip_html_collapses_excess_blank_lines():
    raw = "<p>First.</p><p></p><p></p><p>Second.</p>"
    assert strip_html(raw) == "First.\n\nSecond."
