import datetime

from jobscraper.models import FIELDS, Job


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
        "1",
        "Engineer",
        "Eng",
        "Remote",
        "https://example.com/1",
        "2026-09-01T00:00:00Z",
        "2026-09-12",
        "Unspecified",  # seniority defaults when not passed explicitly
        "",  # company_type defaults when not passed explicitly
    ]
