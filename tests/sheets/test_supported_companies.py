"""Tests for sheets.supported_companies -- no live gspread/Google call.

Same self-contained fake approach as test_writer.py/test_backfill.py (see
CLAUDE.md's Architecture section on why sheets tests do this instead of
sharing one fixture). Board lists are monkeypatched onto
supported_companies.config directly (the same object jobscraper.config
already is -- see config.py's own docstring on why callers read through the
module rather than copying its values) so these tests don't depend on
whatever firms happen to be in the real company_toml/*_boards.toml files.
"""

from jobscraper.sheets import supported_companies


def _set_boards(monkeypatch, *, greenhouse=(), lever=(), ashby=(), smartrecruiters=(), eightfold=()):
    monkeypatch.setattr(supported_companies.config, "GREENHOUSE_BOARDS", list(greenhouse))
    monkeypatch.setattr(supported_companies.config, "LEVER_BOARDS", list(lever))
    monkeypatch.setattr(supported_companies.config, "ASHBY_BOARDS", list(ashby))
    monkeypatch.setattr(supported_companies.config, "SMARTRECRUITERS_BOARDS", list(smartrecruiters))
    monkeypatch.setattr(supported_companies.config, "EIGHTFOLD_BOARDS", list(eightfold))


def _board(token, firm, company_type):
    return {"token": token, "firm": firm, "company_type": company_type}


def test_build_rows_dedupes_a_multi_token_firm(monkeypatch):
    _set_boards(
        monkeypatch,
        greenhouse=[
            _board("optiverneurips", "Optiver", "Prop Trading/Hedge Fund"),
            _board("optiverprivate", "Optiver", "Prop Trading/Hedge Fund"),
        ],
    )

    rows = supported_companies.build_rows()

    assert rows == [["Prop Trading/Hedge Fund", "Optiver"]]


def test_build_rows_sorts_by_category_then_company_across_board_types(monkeypatch):
    _set_boards(
        monkeypatch,
        greenhouse=[_board("anthropic", "Anthropic", "AI Lab")],
        lever=[_board("spotify", "Spotify", "Consumer/Media & Entertainment")],
        ashby=[_board("cohere", "Cohere", "AI Lab")],
        smartrecruiters=[_board("Grab", "Grab", "Consumer/Marketplace")],
        eightfold=[_board("mlp", "Millennium", "Prop Trading/Hedge Fund")],
    )

    rows = supported_companies.build_rows()

    assert rows == [
        ["AI Lab", "Anthropic"],
        ["AI Lab", "Cohere"],
        ["Consumer/Marketplace", "Grab"],
        ["Consumer/Media & Entertainment", "Spotify"],
        ["Prop Trading/Hedge Fund", "Millennium"],
    ]


def test_build_rows_empty_config_yields_no_rows(monkeypatch):
    _set_boards(monkeypatch)

    assert supported_companies.build_rows() == []


class _FakeWorksheet:
    def __init__(self, title):
        self.title = title
        self.cleared = False
        self.updated_with = None
        self.update_kwargs = None

    def clear(self):
        self.cleared = True

    def update(self, range_name=None, values=None, value_input_option=None):
        self.updated_with = values
        self.update_kwargs = {
            "range_name": range_name,
            "value_input_option": value_input_option,
        }


class _FakeSpreadsheet:
    def __init__(self, worksheets=()):
        self._worksheets = list(worksheets)
        self.added = None

    def worksheets(self):
        return self._worksheets

    def add_worksheet(self, title, rows, cols):
        new = _FakeWorksheet(title)
        self._worksheets.append(new)
        self.added = new
        return new


def test_sync_supported_companies_writes_header_and_rows_to_existing_tab(monkeypatch):
    _set_boards(
        monkeypatch,
        greenhouse=[_board("anthropic", "Anthropic", "AI Lab")],
    )
    worksheet = _FakeWorksheet("Supported companies")
    spreadsheet = _FakeSpreadsheet([worksheet])

    count = supported_companies.sync_supported_companies(spreadsheet)

    assert count == 1
    assert worksheet.cleared is True
    assert worksheet.updated_with == [["Category", "Company"], ["AI Lab", "Anthropic"]]
    assert worksheet.update_kwargs == {
        "range_name": "A1",
        "value_input_option": "USER_ENTERED",
    }
    assert spreadsheet.added is None  # reused the existing tab, didn't create a new one


def test_sync_supported_companies_matches_tab_name_case_and_whitespace_insensitively(monkeypatch):
    _set_boards(monkeypatch)
    worksheet = _FakeWorksheet(" supported companies ")
    spreadsheet = _FakeSpreadsheet([worksheet])

    supported_companies.sync_supported_companies(spreadsheet)

    assert worksheet.cleared is True
    assert spreadsheet.added is None


def test_sync_supported_companies_creates_the_tab_when_missing(monkeypatch):
    _set_boards(monkeypatch)
    spreadsheet = _FakeSpreadsheet([])

    supported_companies.sync_supported_companies(spreadsheet)

    assert spreadsheet.added is not None
    assert spreadsheet.added.title == "Supported companies"
    assert spreadsheet.added.cleared is True
