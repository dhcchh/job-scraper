import json
from types import SimpleNamespace

import pytest

from jobscraper import config
from jobscraper.models import FIELDS
from jobscraper.sheets import audit


@pytest.mark.parametrize('job_id, fails', [('123', False), ('', True)])
def test_audit_readonly_report_and_failure_exit(monkeypatch, tmp_path, job_id, fails):
    row = [''] * len(FIELDS)
    row[FIELDS.index('board')] = 'Acme'
    row[FIELDS.index('job_id')] = job_id
    ws = SimpleNamespace(title='SWE/DE - PAC', get_all_values=lambda: [list(FIELDS), row])
    sheet = SimpleNamespace(title='Test', worksheets=lambda: [ws])
    seen = {}

    def credentials(info, *, scopes):
        seen['scopes'] = scopes
        return object()

    monkeypatch.setattr(audit.Credentials, 'from_service_account_info', credentials)
    monkeypatch.setattr(audit.gspread, 'authorize', lambda *a, **k: SimpleNamespace(open_by_key=lambda key: sheet))
    monkeypatch.setattr(audit, 'CATEGORIES', [('SWE/DE', ())])
    monkeypatch.setattr(audit, 'REGIONS', ('PAC',))
    monkeypatch.setattr(config, 'GOOGLE_SHEET_TITLE', 'Test')
    output = tmp_path / 'report.json'
    summary = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setattr('sys.argv', ['audit', '--output', str(output)])
    if fails:
        with pytest.raises(SystemExit) as exc:
            audit.main()
        assert exc.value.code == 1
    else:
        audit.main()
    assert seen['scopes'] == ['https://www.googleapis.com/auth/spreadsheets.readonly']
    assert bool(json.loads(output.read_text())['errors']) == fails
    assert 'Data quality' in summary.read_text()
