import subprocess
import sys

import pytest

from jobscraper.changed_boards import changed_firms


@pytest.mark.parametrize("changes", [
    {"token": "corrected-token"},
    {"location_scope": "Singapore"},
    {"company_type": "Fintech"},
    {"firm": "Renamed"},
])
def test_changed_firms_detects_edits_without_an_added_firm_line(changes):
    board = {"token": "old", "firm": "Acme", "company_type": "Tech"}
    updated = {**board, **changes}
    assert changed_firms({"workday": [board]}, {"workday": [updated]}) == [updated["firm"]]


def test_changed_firms_ignores_order_and_removals_and_dedupes_firms():
    a = {"token": "a", "firm": "Acme"}
    b = {"token": "b", "firm": "Beta"}
    assert changed_firms({"gh": [a, b]}, {"gh": [b, a]}) == []
    assert changed_firms({"gh": [a, b]}, {"gh": [a]}) == []
    assert changed_firms({}, {"gh": [a, {**a, "token": "c"}]}) == ["Acme"]


def test_changed_boards_cli_reads_committed_toml(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    boards = tmp_path / "company_toml"
    boards.mkdir()
    path = boards / "workday_boards.toml"
    path.write_text('[[boards]]\nfirm="Acme"\ntoken="old"\ncompany_type="Tech"\n')
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    path.write_text(path.read_text().replace('token="old"', "token = 'new'"))
    git("add", ".")
    git("commit", "-qm", "token change")
    output = tmp_path / "output"
    subprocess.run([
        sys.executable, "-m", "jobscraper.changed_boards", "--base", base,
        "--github-output", str(output),
    ], cwd=tmp_path, check=True, capture_output=True)
    assert output.read_text() == "firms=Acme\n"
