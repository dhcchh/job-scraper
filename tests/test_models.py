import datetime

from jobscraper.models import FIELDS, Job, normalize_date


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
    assert len(row) == len(FIELDS)
    assert row == [
        "acme",
        "Engineer",
        "Remote",
        "",  # first_published defaults when not passed explicitly
        "2026-09-01",
        "",  # company_type defaults when not passed explicitly
        "https://example.com/1",
        "Unspecified",  # seniority defaults when not passed explicitly
        "Eng",
        "1",
        "2026-09-12",
    ]
