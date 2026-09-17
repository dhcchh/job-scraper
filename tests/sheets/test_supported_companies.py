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


def test_slugify_turns_mixed_case_and_punctuation_into_snake_case():
    assert supported_companies._slugify("AI Lab") == "ai_lab"
    assert supported_companies._slugify("Prop Trading/Hedge Fund") == "prop_trading_hedge_fund"
    assert (
        supported_companies._slugify("Consumer/Media & Entertainment")
        == "consumer_media_entertainment"
    )


def test_build_columns_dedupes_a_multi_token_firm(monkeypatch):
    _set_boards(
        monkeypatch,
        greenhouse=[
            _board("optiverneurips", "Optiver", "Prop Trading/Hedge Fund"),
            _board("optiverprivate", "Optiver", "Prop Trading/Hedge Fund"),
        ],
    )

    assert supported_companies.build_columns() == {"prop_trading_hedge_fund": ["Optiver"]}


def test_build_columns_groups_and_sorts_within_and_across_categories(monkeypatch):
    _set_boards(
        monkeypatch,
        greenhouse=[_board("anthropic", "Anthropic", "AI Lab")],
        lever=[_board("spotify", "Spotify", "Consumer/Media & Entertainment")],
        ashby=[_board("cohere", "Cohere", "AI Lab")],
        smartrecruiters=[_board("Grab", "Grab", "Consumer/Marketplace")],
        eightfold=[_board("mlp", "Millennium", "Prop Trading/Hedge Fund")],
    )

    assert supported_companies.build_columns() == {
        "ai_lab": ["Anthropic", "Cohere"],
        "consumer_marketplace": ["Grab"],
        "consumer_media_entertainment": ["Spotify"],
        "prop_trading_hedge_fund": ["Millennium"],
    }


def test_build_columns_empty_config_yields_no_columns(monkeypatch):
    _set_boards(monkeypatch)

    assert supported_companies.build_columns() == {}


def test_grid_pads_shorter_columns_to_the_tallest():
    columns = {
        "ai_lab": ["Anthropic", "Cohere", "OpenAI"],
        "gaming": ["Roblox"],
    }

    grid = supported_companies._grid(columns)

    assert grid == [
        ["ai_lab", "gaming"],
        ["Anthropic", "Roblox"],
        ["Cohere", ""],
        ["OpenAI", ""],
    ]


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


def test_sync_supported_companies_writes_wide_grid_to_existing_tab(monkeypatch):
    _set_boards(
        monkeypatch,
        greenhouse=[
            _board("anthropic", "Anthropic", "AI Lab"),
            _board("cohere-gh", "Cohere", "AI Lab"),
        ],
        lever=[_board("spotify", "Spotify", "Consumer/Media & Entertainment")],
    )
    worksheet = _FakeWorksheet("Supported companies")
    spreadsheet = _FakeSpreadsheet([worksheet])

    total = supported_companies.sync_supported_companies(spreadsheet)

    assert total == 3
    assert worksheet.cleared is True
    assert worksheet.updated_with == [
        ["ai_lab", "consumer_media_entertainment"],
        ["Anthropic", "Spotify"],
        ["Cohere", ""],
    ]
    assert worksheet.update_kwargs == {
        "range_name": "A1",
        "value_input_option": "USER_ENTERED",
    }
    assert spreadsheet.added is None  # reused the existing tab, didn't create a new one


def test_sync_supported_companies_matches_tab_name_case_and_whitespace_insensitively(monkeypatch):
    _set_boards(monkeypatch, greenhouse=[_board("anthropic", "Anthropic", "AI Lab")])
    worksheet = _FakeWorksheet(" supported companies ")
    spreadsheet = _FakeSpreadsheet([worksheet])

    supported_companies.sync_supported_companies(spreadsheet)

    assert worksheet.cleared is True
    assert spreadsheet.added is None


def test_sync_supported_companies_creates_the_tab_when_missing(monkeypatch):
    _set_boards(monkeypatch, greenhouse=[_board("anthropic", "Anthropic", "AI Lab")])
    spreadsheet = _FakeSpreadsheet([])

    supported_companies.sync_supported_companies(spreadsheet)

    assert spreadsheet.added is not None
    assert spreadsheet.added.title == "Supported companies"
    assert spreadsheet.added.cleared is True


def test_sync_supported_companies_handles_no_configured_firms(monkeypatch):
    _set_boards(monkeypatch)
    worksheet = _FakeWorksheet("Supported companies")
    spreadsheet = _FakeSpreadsheet([worksheet])

    total = supported_companies.sync_supported_companies(spreadsheet)

    assert total == 0
    assert worksheet.cleared is True
    assert worksheet.updated_with is None  # nothing to write -- clear() alone is enough
