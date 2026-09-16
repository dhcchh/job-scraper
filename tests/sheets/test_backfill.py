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
        self.updated_with = None

    def get_all_values(self):
        return self._rows

    def get(self, range_name, value_render_option=None, pad_values=None, maintain_size=None):
        # Real Sheets FORMULA-rendered read of just the url column (see
        # backfill._url_formula_cells) -- a fake row's url cell already
        # holds either a plain legacy url or a literal "=HYPERLINK(...)"
        # string, so returning it as-is is exactly what a real formula-mode
        # read of either shape would return.
        header, *data_rows = self._rows
        url_index = header.index("url")
        return [[row[url_index]] if url_index < len(row) and row[url_index] else [] for row in data_rows]

    def update(self, values, range_name=None, value_input_option=None):
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
    assert worksheet.updated_with is None


def test_refresh_noop_on_header_only():
    worksheet = _FakeWorksheet([_OLD_HEADER])
    backfill.refresh_worksheet(worksheet)
    assert worksheet.updated_with is None


def test_refresh_reorders_into_current_fields_order_and_recomputes_seniority():
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row()])

    backfill.refresh_worksheet(worksheet)

    header, row = worksheet.updated_with
    assert header == backfill.HEADER
    assert header == [
        "first_published",
        "updated_at",
        "board",
        "title",
        "location",
        "seniority",
        "company_type",
        "url",
        "department",
        "job_id",
        "scraped_date",
        "source",
        "description",
    ]
    assert row == [
        "",  # first_published: no such column in _OLD_HEADER, no override given
        "2026-09-01",
        "Acme",
        "Junior Engineer",
        "Remote",
        "Junior",  # recomputed from "Junior Engineer", was stale "Unspecified"
        "Big Tech",
        '=HYPERLINK("https://x/101", "Apply")',
        "Eng",
        "101",
        "2026-09-01",
        "",  # source: "Acme" isn't a real configured firm, no override given
        "",  # description: no such column in _OLD_HEADER, no override given
    ]


def test_refresh_recomputes_company_type_from_current_config(monkeypatch):
    monkeypatch.setattr(
        backfill.config,
        "GREENHOUSE_BOARDS",
        [{"token": "acme", "firm": "Acme", "company_type": "Prop Trading/Hedge Fund"}],
    )
    monkeypatch.setattr(backfill.config, "LEVER_BOARDS", [])
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(company_type="Big Tech")])

    backfill.refresh_worksheet(worksheet)

    company_type_index = backfill.HEADER.index("company_type")
    _, row = worksheet.updated_with
    assert row[company_type_index] == "Prop Trading/Hedge Fund"


def test_refresh_recomputes_company_type_from_a_lever_or_ashby_board(monkeypatch):
    # backfill.py predates Lever/Ashby support and only ever looked up
    # company_type in GREENHOUSE_BOARDS -- a Lever- or Ashby-only firm's
    # company_type edit must be picked up too.
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", [])
    monkeypatch.setattr(
        backfill.config,
        "LEVER_BOARDS",
        [{"token": "acme", "firm": "Acme", "company_type": "Prop Trading/Hedge Fund"}],
    )
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(company_type="Big Tech")])

    backfill.refresh_worksheet(worksheet)

    company_type_index = backfill.HEADER.index("company_type")
    _, row = worksheet.updated_with
    assert row[company_type_index] == "Prop Trading/Hedge Fund"


def test_refresh_leaves_company_type_alone_when_firm_not_in_config(monkeypatch):
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", [])
    monkeypatch.setattr(backfill.config, "LEVER_BOARDS", [])
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(company_type="Big Tech")])

    backfill.refresh_worksheet(worksheet)

    company_type_index = backfill.HEADER.index("company_type")
    _, row = worksheet.updated_with
    assert row[company_type_index] == "Big Tech"  # unchanged, not blanked


def test_refresh_recomputes_source_from_current_config(monkeypatch):
    # "Acme" moved from Greenhouse to Lever -- its existing rows' stale
    # "Greenhouse" source cell must be corrected to "Lever".
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", [])
    monkeypatch.setattr(
        backfill.config,
        "LEVER_BOARDS",
        [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}],
    )
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    header_with_source = _OLD_HEADER + ["source"]
    row_with_source = _old_row() + ["Greenhouse"]
    worksheet = _FakeWorksheet([header_with_source, row_with_source])

    backfill.refresh_worksheet(worksheet)

    source_index = backfill.HEADER.index("source")
    _, row = worksheet.updated_with
    assert row[source_index] == "Lever"


def test_refresh_leaves_source_alone_when_firm_not_in_config(monkeypatch):
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", [])
    monkeypatch.setattr(backfill.config, "LEVER_BOARDS", [])
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    header_with_source = _OLD_HEADER + ["source"]
    row_with_source = _old_row() + ["Greenhouse"]
    worksheet = _FakeWorksheet([header_with_source, row_with_source])

    backfill.refresh_worksheet(worksheet)

    source_index = backfill.HEADER.index("source")
    _, row = worksheet.updated_with
    assert row[source_index] == "Greenhouse"  # unchanged, not blanked


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


def test_refresh_wraps_a_legacy_plain_url_in_a_hyperlink_formula():
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(url="https://x/101")])

    backfill.refresh_worksheet(worksheet)

    url_index = backfill.HEADER.index("url")
    _, row = worksheet.updated_with
    assert row[url_index] == '=HYPERLINK("https://x/101", "Apply")'


def test_refresh_preserves_the_real_url_behind_an_already_migrated_hyperlink_cell():
    # get_all_values() (FORMATTED) would return just the label "Apply" for
    # this cell -- only the FORMULA-rendered read backfill does specifically
    # for the url column recovers the real url it points to.
    already_migrated = '=HYPERLINK("https://x/101", "Apply")'
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(url=already_migrated)])

    backfill.refresh_worksheet(worksheet)

    url_index = backfill.HEADER.index("url")
    _, row = worksheet.updated_with
    assert row[url_index] == already_migrated


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

    def spy_update(values, range_name=None, value_input_option=None):
        calls["range_name"] = range_name
        calls["value_input_option"] = value_input_option
        return original_update(
            values, range_name=range_name, value_input_option=value_input_option
        )

    worksheet.update = spy_update

    backfill.refresh_worksheet(worksheet)

    assert calls["range_name"] == "A1"
    assert calls["value_input_option"] == "USER_ENTERED"


def test_refresh_all_refreshes_every_category_region_tab(monkeypatch):
    from jobscraper.enrichment.classify_region import REGIONS, tab_name
    from jobscraper.enrichment.classify_roles import CATEGORIES

    # No boards to re-fetch for location overrides -- keeps this test off
    # the network (see module docstring: tests never hit it).
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", [])
    monkeypatch.setattr(backfill.config, "LEVER_BOARDS", [])
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    monkeypatch.setattr(backfill.config, "SMARTRECRUITERS_BOARDS", [])
    monkeypatch.setattr(backfill.config, "EIGHTFOLD_BOARDS", [])
    monkeypatch.setattr(backfill, "get_spreadsheet", lambda: "SPREADSHEET")
    monkeypatch.setattr(backfill, "get_worksheet", lambda spreadsheet, name: name)

    refreshed = []
    monkeypatch.setattr(
        backfill,
        "refresh_worksheet",
        lambda worksheet, overrides=None: refreshed.append(worksheet),
    )

    backfill.refresh_all()

    assert refreshed == [
        tab_name(category, region)
        for category, _keywords in CATEGORIES
        for region in REGIONS
    ]


def test_fetch_overrides_keys_by_board_and_job_id(monkeypatch):
    boards = [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}]
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", boards)
    monkeypatch.setattr(backfill.config, "LEVER_BOARDS", [])
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    monkeypatch.setattr(backfill.config, "SMARTRECRUITERS_BOARDS", [])
    monkeypatch.setattr(backfill.config, "EIGHTFOLD_BOARDS", [])

    fetched = [_job_stub("Acme", "1", "Singapore"), _job_stub("Acme", "2", "London, United Kingdom")]
    monkeypatch.setattr(
        backfill, "fetch_greenhouse_jobs", lambda token, firm, company_type: fetched
    )

    overrides = backfill._fetch_overrides()

    assert overrides == {
        ("Acme", "1"): fetched[0],
        ("Acme", "2"): fetched[1],
    }


def test_fetch_overrides_skips_a_board_that_fails(monkeypatch):
    boards = [
        {"token": "good", "firm": "GoodCo", "company_type": "Big Tech"},
        {"token": "bad", "firm": "BadCo", "company_type": "Big Tech"},
    ]
    monkeypatch.setattr(backfill.config, "GREENHOUSE_BOARDS", boards)
    monkeypatch.setattr(backfill.config, "LEVER_BOARDS", [])
    monkeypatch.setattr(backfill.config, "ASHBY_BOARDS", [])
    monkeypatch.setattr(backfill.config, "SMARTRECRUITERS_BOARDS", [])
    monkeypatch.setattr(backfill.config, "EIGHTFOLD_BOARDS", [])

    good_job = _job_stub("GoodCo", "1", "Singapore")

    def fake_fetch_greenhouse_jobs(token, firm, company_type):
        if firm == "BadCo":
            raise RuntimeError("greenhouse down")
        return [good_job]

    monkeypatch.setattr(backfill, "fetch_greenhouse_jobs", fake_fetch_greenhouse_jobs)

    overrides = backfill._fetch_overrides()  # must not raise

    assert overrides == {("GoodCo", "1"): good_job}


def test_fetch_overrides_merges_greenhouse_lever_ashby_smartrecruiters_and_eightfold(
    monkeypatch,
):
    # backfill.py predates Lever/Ashby/SmartRecruiters/Eightfold support and
    # only ever re-fetched GREENHOUSE_BOARDS -- a row sourced from any of
    # the other four must get the same fresh-data override a
    # Greenhouse-sourced row already did.
    monkeypatch.setattr(
        backfill.config,
        "GREENHOUSE_BOARDS",
        [{"token": "gh", "firm": "GreenhouseCo", "company_type": "Big Tech"}],
    )
    monkeypatch.setattr(
        backfill.config,
        "LEVER_BOARDS",
        [{"token": "lv", "firm": "LeverCo", "company_type": "Big Tech"}],
    )
    monkeypatch.setattr(
        backfill.config,
        "ASHBY_BOARDS",
        [{"token": "as", "firm": "AshbyCo", "company_type": "Big Tech"}],
    )
    monkeypatch.setattr(
        backfill.config,
        "SMARTRECRUITERS_BOARDS",
        [{"token": "sr", "firm": "SmartRecruitersCo", "company_type": "Big Tech"}],
    )
    monkeypatch.setattr(
        backfill.config,
        "EIGHTFOLD_BOARDS",
        [{"token": "ef", "firm": "EightfoldCo", "company_type": "Big Tech"}],
    )

    gh_job = _job_stub("GreenhouseCo", "1", "Singapore")
    lever_job = _job_stub("LeverCo", "2", "London, United Kingdom")
    ashby_job = _job_stub("AshbyCo", "3", "Sydney, Australia")
    smartrecruiters_job = _job_stub("SmartRecruitersCo", "4", "Beijing, China")
    eightfold_job = _job_stub("EightfoldCo", "5", "Tokyo, Japan")

    monkeypatch.setattr(backfill, "fetch_greenhouse_jobs", lambda token, firm, company_type: [gh_job])
    monkeypatch.setattr(
        backfill, "fetch_lever_jobs", lambda token, firm, company_type: [lever_job]
    )
    monkeypatch.setattr(
        backfill, "fetch_ashby_jobs", lambda token, firm, company_type: [ashby_job]
    )
    monkeypatch.setattr(
        backfill,
        "fetch_smartrecruiters_jobs",
        lambda token, firm, company_type: [smartrecruiters_job],
    )
    monkeypatch.setattr(
        backfill,
        "fetch_eightfold_jobs",
        lambda token, firm, company_type: [eightfold_job],
    )

    overrides = backfill._fetch_overrides()

    assert overrides == {
        ("GreenhouseCo", "1"): gh_job,
        ("LeverCo", "2"): lever_job,
        ("AshbyCo", "3"): ashby_job,
        ("SmartRecruitersCo", "4"): smartrecruiters_job,
        ("EightfoldCo", "5"): eightfold_job,
    }


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
        overrides={("Acme", "1"): _job_stub("Acme", "1", "Chicago, IL | New York, NY")},
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
        worksheet, overrides={("OtherCo", "9"): _job_stub("OtherCo", "9", "Singapore")}
    )

    location_index = backfill.HEADER.index("location")
    _, row = worksheet.updated_with
    assert row[location_index] == "Bangalore, India"


def test_refresh_fills_in_first_published_for_a_header_that_predates_the_column():
    # _OLD_HEADER/_old_row have no first_published column at all -- the
    # "column added after rows already existed" shape, not just a reorder.
    worksheet = _FakeWorksheet(
        [_OLD_HEADER, _old_row(board="Acme", job_id="1")]
    )

    backfill.refresh_worksheet(
        worksheet,
        overrides={
            ("Acme", "1"): _job_stub(
                "Acme", "1", "Remote", first_published="2026-06-01T00:00:00Z"
            )
        },
    )

    header, row = worksheet.updated_with
    assert "first_published" in header
    assert row[header.index("first_published")] == "2026-06-01"


def test_refresh_leaves_first_published_unchanged_when_no_override_for_that_row():
    # Already-migrated row (has a real first_published value) whose posting
    # has since closed -- no override available, so it must not be blanked.
    header_with_fp = _OLD_HEADER + ["first_published"]
    row_with_fp = _old_row(board="Acme", job_id="1") + ["2026-05-01T00:00:00Z"]
    worksheet = _FakeWorksheet([header_with_fp, row_with_fp])

    backfill.refresh_worksheet(worksheet, overrides={})

    header, row = worksheet.updated_with
    assert row[header.index("first_published")] == "2026-05-01"


_A_FIRM_NEEDING_DESCRIPTION = "QRT"  # in FIRMS_NEEDING_DESCRIPTION


def test_refresh_fills_in_description_for_a_header_that_predates_the_column():
    # Same "column added after rows already existed" shape as
    # first_published above -- _OLD_HEADER/_old_row have no description
    # column at all. Firm must be in FIRMS_NEEDING_DESCRIPTION, or this
    # would just get blanked instead (see
    # test_refresh_blanks_description_for_a_firm_not_needing_it).
    worksheet = _FakeWorksheet(
        [_OLD_HEADER, _old_row(board=_A_FIRM_NEEDING_DESCRIPTION, job_id="1")]
    )

    backfill.refresh_worksheet(
        worksheet,
        overrides={
            (_A_FIRM_NEEDING_DESCRIPTION, "1"): _job_stub(
                _A_FIRM_NEEDING_DESCRIPTION, "1", "Remote", description="Do the thing."
            )
        },
    )

    header, row = worksheet.updated_with
    assert "description" in header
    assert row[header.index("description")] == "Do the thing."


def test_refresh_leaves_description_unchanged_when_no_override_for_that_row():
    # Already-migrated row (has real description text) whose posting has
    # since closed -- no override available, so it must not be blanked.
    header_with_desc = _OLD_HEADER + ["description"]
    row_with_desc = (
        _old_row(board=_A_FIRM_NEEDING_DESCRIPTION, job_id="1") + ["Existing JD text."]
    )
    worksheet = _FakeWorksheet([header_with_desc, row_with_desc])

    backfill.refresh_worksheet(worksheet, overrides={})

    header, row = worksheet.updated_with
    assert row[header.index("description")] == "Existing JD text."


def test_refresh_overwrites_stale_description_when_fresh_differs():
    # Unlike first_published, description overwrites a non-empty existing
    # value too -- a JD can be legitimately edited after this project first
    # saw it, and the fresh re-fetch is the only way to pick that up.
    header_with_desc = _OLD_HEADER + ["description"]
    row_with_desc = (
        _old_row(board=_A_FIRM_NEEDING_DESCRIPTION, job_id="1") + ["Stale JD text."]
    )
    worksheet = _FakeWorksheet([header_with_desc, row_with_desc])

    backfill.refresh_worksheet(
        worksheet,
        overrides={
            (_A_FIRM_NEEDING_DESCRIPTION, "1"): _job_stub(
                _A_FIRM_NEEDING_DESCRIPTION, "1", "Remote", description="Updated JD text."
            )
        },
    )

    header, row = worksheet.updated_with
    assert row[header.index("description")] == "Updated JD text."


def test_refresh_blanks_description_for_a_firm_not_needing_it():
    # "Acme" isn't in FIRMS_NEEDING_DESCRIPTION -- its rows must have
    # description cleared unconditionally, even with no overrides at all
    # (e.g. a firm dropped from FIRMS_NEEDING_DESCRIPTION after this
    # project's earlier blanket rollout of the column).
    header_with_desc = _OLD_HEADER + ["description"]
    row_with_desc = _old_row(board="Acme", job_id="1") + ["Stale JD text."]
    worksheet = _FakeWorksheet([header_with_desc, row_with_desc])

    backfill.refresh_worksheet(worksheet, overrides={})

    header, row = worksheet.updated_with
    assert row[header.index("description")] == ""


def test_refresh_leaves_description_empty_for_a_firm_not_needing_it():
    worksheet = _FakeWorksheet([_OLD_HEADER, _old_row(board="Acme", job_id="1")])

    backfill.refresh_worksheet(worksheet)

    header, row = worksheet.updated_with
    assert row[header.index("description")] == ""


def _job_stub(board, job_id, location, first_published="", description=""):
    from datetime import date

    from jobscraper.models import Job

    return Job(
        board,
        job_id,
        "Title",
        "Dept",
        location,
        "https://x",
        "",
        date(2026, 9, 12),
        first_published=first_published,
        description=description,
    )
