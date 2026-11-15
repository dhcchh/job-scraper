"""Tests for the helpers shared by the command entry points."""

from jobscraper import cli


def test_write_step_summary_appends_to_existing_file(monkeypatch, tmp_path):
    path = tmp_path / "summary.md"
    path.write_text("existing\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))

    cli.write_step_summary("new content\n")

    assert path.read_text() == "existing\nnew content\n"


def test_write_step_summary_noop_without_env_var(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    cli.write_step_summary("content")  # must not raise
