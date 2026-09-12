"""Tests for sheets.backfill -- no live gspread/Google call.

Same fake-worksheet approach as test_writer.py: a tiny in-file fake with
just the methods backfill.py actually calls, kept self-contained rather
than shared, matching the existing pattern (see CLAUDE.md's Architecture
section on why sheets/writer.py's tests do the same).
"""

from jobscraper.sheets import backfill


class _FakeWorksheet:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.cleared = False
        self.updated_with = None

    def get_all_values(self):
        return self._rows

    def clear(self):
        self.cleared = True

    def update(self, values, value_input_option=None):
        self.updated_with = values


# Deliberately NOT in backfill's current column order -- exercises that a
# row is located by column name, not position, regardless of what order a
# tab's own header happens to be in.
_OLD_HEADER = [
    "job_id",
    "board",
    "seniority",
    "title",
    "company_type",
    "department",
    "location",
    "updated_at",
    "url",
    "scraped_date",
]


def _old_row(
    job_id="101",
    board="Acme",
    seniority="Unspecified",
    title="Junior Engineer",
    company_type="Big Tech",
    department="Eng",
    location="Remote",
    updated_at="2026-09-01T00:00:00Z",
    url="https://x/101",
    scraped_date="2026-09-01",
):
    return [
        job_id,
        board,
        seniority,
        title,
        company_type,
        department,
        location,
        updated_at,
        url,
        scraped_date,
    ]


def test_refresh_noop_on_empty_sheet():
    worksheet = _FakeWorksheet([])
    backfill.refresh_worksheet(worksheet)
    assert worksheet.cleared is False


def test_refresh_noop_on_header_only():
    worksheet = _FakeWorksheet([_OLD_HEADER])
    backfill.refresh_worksheet(worksheet)
    assert worksheet.cleared is False


def test_refresh_reorders_into_current_fields_order_and_recomputes_seniority():
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row()])

    backfill.refresh_worksheet(worksheet)

    assert worksheet.cleared is True
    header, row = worksheet.updated_with
    assert header == backfill.HEADER
    assert header == [
        "board",
        "title",
        "location",
        "updated_at",
        "company_type",
        "url",
        "seniority",
        "department",
        "job_id",
        "scraped_date",
    ]
    assert row == [
        "Acme",
        "Junior Engineer",
        "Remote",
        "2026-09-01T00:00:00Z",
        "Big Tech",
        "https://x/101",
        "Junior",  # recomputed from "Junior Engineer", was stale "Unspecified"
        "Eng",
        "101",
        "2026-09-01",
    ]


def test_refresh_recomputes_company_type_from_current_config(monkeypatch):
    monkeypatch.setattr(
        backfill.config,
        "GREENHOUSE_BOARDS",
        [{"token": "acme", "firm": "Acme", "company_type": "Prop Trading/Hedge Fund"}],
    )
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(company_type="Big Tech")])

    backfill.refresh_worksheet(worksheet)

    company_type_index = backfill.HEADER.index("company_type")
    _, row = worksheet.updated_with
    assert row[company_type_index] == "Prop Trading/Hedge Fund"


def test_refresh_leaves_company_type_alone_when_firm_not_in_config(monkeypatch):
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", [])
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(company_type="Big Tech")])

    backfill.refresh_worksheet(worksheet)

    company_type_index = backfill.HEADER.index("company_type")
    _, row = worksheet.updated_with
    assert row[company_type_index] == "Big Tech"  # unchanged, not blanked


def test_refresh_defaults_missing_trailing_cells_to_empty_string():
    # A row shorter than the header -- a normal shape (gspread/Sheets trims
    # trailing blank cells), not a malformed one.
    short_row = _old_row()[:-2]  # missing url, scraped_date
    worksheet = _FakeWorksheet([_OLD_HEADER, short_row])

    backfill.refresh_worksheet(worksheet)

    _, row = worksheet.updated_with
    url_index = backfill.HEADER.index("url")
    scraped_date_index = backfill.HEADER.index("scraped_date")
    assert row[url_index] == ""
    assert row[scraped_date_index] == ""


def test_refresh_normalizes_a_legacy_unsplit_single_location():
    # Written before location normalization existed -- one plain city name,
    # never split (no "|").
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(location="Bangalore")])

    backfill.refresh_worksheet(worksheet)

    location_index = backfill.HEADER.index("location")
    _, row = worksheet.updated_with
    assert row[location_index] == "Bangalore, India"


def test_refresh_renormalizes_an_already_split_location_cell():
    # Split by an earlier run of this script -- re-normalizing picks up any
    # change to normalize_location.py's table since, and de-dupes.
    worksheet = _FakeWorksheet(
        [_OLD_HEADER, _old_row(location="Amsterdam office | NYC | New York City")]
    )

    backfill.refresh_worksheet(worksheet)

    location_index = backfill.HEADER.index("location")
    _, row = worksheet.updated_with
    assert row[location_index] == "Amsterdam, Netherlands | New York, NY"


def test_refresh_leaves_legacy_multi_location_string_alone():
    # Written before location normalization existed -- a multi-location
    # string with no "|" to split on and no offices data left to recover
    # it from. Must not be mangled by a naive comma-split.
    legacy = "Jersey, Montreal, Warsaw"
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(location=legacy)])

    backfill.refresh_worksheet(worksheet)

    location_index = backfill.HEADER.index("location")
    _, row = worksheet.updated_with
    assert row[location_index] == legacy  # unchanged, not shredded


def test_refresh_uses_user_entered_input_option():
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row()])

    calls = {}
    original_update = worksheet.update

    def spy_update(values, value_input_option=None):
        calls["value_input_option"] = value_input_option
        return original_update(values, value_input_option=value_input_option)

    worksheet.update = spy_update

    backfill.refresh_worksheet(worksheet)

    assert calls["value_input_option"] == "USER_ENTERED"


def test_refresh_all_refreshes_every_category_region_tab(monkeypatch):
    from jobscraper.classifications.classify_region import REGIONS, tab_name
    from jobscraper.classifications.classify_roles import CATEGORIES

    # No boards to re-fetch for location overrides -- keeps this test off
    # the network (see module docstring: tests never hit it).
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", [])
    monkeypatch.setattr(backfill, "get_spreadsheet", lambda: "SPREADSHEET")
    monkeypatch.setattr(backfill, "get_worksheet", lambda spreadsheet, name: name)

    refreshed = []
    monkeypatch.setattr(
        backfill,
        "refresh_worksheet",
        lambda worksheet, location_overrides=None: refreshed.append(worksheet),
    )

    backfill.refresh_all()

    assert refreshed == [
        tab_name(category, region)
        for category, _keywords in CATEGORIES
        for region in REGIONS
    ]


def test_location_overrides_from_greenhouse_keys_by_board_and_job_id(monkeypatch):
    boards = [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}]
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", boards)

    fetched = [_job_stub("Acme", "1", "Singapore"), _job_stub("Acme", "2", "London, United Kingdom")]
    monkeypatch.setattr(
        backfill, "fetch_jobs", lambda token, firm, company_type: fetched
    )

    overrides = backfill._location_overrides_from_greenhouse()

    assert overrides == {
        ("Acme", "1"): "Singapore",
        ("Acme", "2"): "London, United Kingdom",
    }


def test_location_overrides_from_greenhouse_skips_a_board_that_fails(monkeypatch):
    boards = [
        {"token": "good", "firm": "GoodCo", "company_type": "Big Tech"},
        {"token": "bad", "firm": "BadCo", "company_type": "Big Tech"},
    ]
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", boards)

    def fake_fetch_jobs(token, firm, company_type):
        if firm == "BadCo":
            raise RuntimeError("greenhouse down")
        return [_job_stub("GoodCo", "1", "Singapore")]

    monkeypatch.setattr(backfill, "fetch_jobs", fake_fetch_jobs)

    overrides = backfill._location_overrides_from_greenhouse()  # must not raise

    assert overrides == {("GoodCo", "1"): "Singapore"}


def test_refresh_prefers_a_location_override_over_best_effort_renormalization():
    # "Chicago, New York City" is a legacy, never-split multi-location cell
    # best-effort re-normalization can't safely fix (see
    # test_refresh_leaves_legacy_multi_location_string_alone) -- a fresh
    # override for this exact (board, job_id) fixes it directly.
    worksheet = _FakeWorksheet(
        [_OLD_HEADER, _old_row(board="Acme", job_id="1", location="Chicago, New York City")]
    )

    backfill.refresh_worksheet(
        worksheet,
        location_overrides={("Acme", "1"): "Chicago, IL | New York, NY"},
    )

    location_index = backfill.HEADER.index("location")
    _, row = worksheet.updated_with
    assert row[location_index] == "Chicago, IL | New York, NY"


def test_refresh_falls_back_to_best_effort_when_no_override_for_that_row():
    worksheet = _FakeWorksheet(
        [_OLD_HEADER, _old_row(board="Acme", job_id="1", location="Bangalore")]
    )

    # Override map has entries, just none for ("Acme", "1") -- e.g. that
    # posting has since closed and dropped out of the fresh fetch.
    backfill.refresh_worksheet(
        worksheet, location_overrides={("OtherCo", "9"): "Singapore"}
    )

    location_index = backfill.HEADER.index("location")
    _, row = worksheet.updated_with
    assert row[location_index] == "Bangalore, India"


def _job_stub(board, job_id, location):
    from datetime import date

    from jobscraper.models import Job

    return Job(board, job_id, "Title", "Dept", location, "https://x", "", date(2026, 9, 12))
