import pytest

from jobscraper.models import FIELDS
from jobscraper.quality import inspect_rows, inspect_tabs, summary_markdown, validate_rows


def row(**overrides):
    values = dict(board="Acme", job_id="001", title="Engineer")
    values.update(overrides)
    return [values.get(field, "") for field in FIELDS]


@pytest.mark.parametrize("value", [None, "", " ", "None", "null", " 123", "123 ", 123])
@pytest.mark.parametrize("field", ["board", "job_id"])
def test_invalid_keys_block_writes(field, value):
    with pytest.raises(ValueError, match="data quality"):
        validate_rows([row(**{field: value})])


def test_optional_blanks_are_reported_without_blocking():
    validate_rows([row()])
    report = inspect_rows([row()])
    assert report['errors'] == []
    assert report['blank_fields']['salary'] == [2]
    assert report['blank_fields']['url'] == [2]
    assert 'job_id' not in report['blank_fields']


def test_duplicates_block_but_same_id_at_other_board_is_valid():
    with pytest.raises(ValueError, match='duplicate key'):
        validate_rows([row(), row()])
    validate_rows([row(), row(board='Other')])


def test_short_rows_are_missing_keys_and_empty_grid_rows_are_ignored():
    report = inspect_rows([[], [''], ['2026-09-28']])
    assert report['rows'] == 1
    assert len(report['errors']) == 2
    assert report['errors'][0]['row'] == 4


def test_schema_and_missing_tabs_fail_cross_tab_duplicates_are_advisory():
    report = inspect_tabs({'A': [list(FIELDS), row()], 'B': [list(FIELDS), row()], 'C': [['job_id']]}, ['A', 'B', 'C', 'D'])
    assert report['errors'] == 2
    assert len(report['cross_tab_duplicates']) == 1
    assert report['cross_tab_duplicates'][0]['other'] == {'tab': 'B', 'row': 2}
    assert '| salary | 2 |' in summary_markdown(report)
