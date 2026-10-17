import json
from types import SimpleNamespace

import pytest

from jobscraper import config
from jobscraper.job_boards.citadel import client, install, probe
from jobscraper.job_boards.citadel.parser import parse_listing_page
from tests.citadel.test_parser import fixture


def snapshot():
    import datetime

    raw = parse_listing_page(fixture()).jobs[0]
    return client.CitadelSnapshot((client._to_job(raw, "Citadel", "Prop Trading/Hedge Fund", datetime.date.today()),), 3, 2)


def test_probe_report_and_summary_need_no_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "fetch_citadel_snapshot", lambda **kwargs: snapshot())
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_SHA", "tested-sha")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    output = tmp_path / "report.json"
    assert probe.main(["--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "passed" and report["snapshot_complete"]
    assert report["global_count"] == 3 and report["sg_hk_count"] == report["tracked_role_count"] == 1
    assert report["event"] == "schedule" and report["commit"] == "tested-sha"
    assert "scoped live validation" in summary.read_text()


def test_probe_failure_does_not_expose_raw_browser_tokens(monkeypatch, tmp_path):
    def fail(**kwargs):
        raise RuntimeError("https://example.test/?__cf_chl_tk=secret")

    monkeypatch.setattr(probe, "fetch_citadel_snapshot", fail)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    output = tmp_path / "report.json"
    assert probe.main(["--output", str(output)]) == 1
    assert "secret" not in output.read_text()
    assert not json.loads(output.read_text())["snapshot_complete"]


def test_empty_snapshot_cannot_pass_usefulness_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "fetch_citadel_snapshot", lambda **kwargs: client.CitadelSnapshot((), 0, 1))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    output = tmp_path / "report.json"
    assert probe.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "empty" and report["snapshot_complete"] and not report["useful"]


@pytest.mark.parametrize("selected,all_firms,installs", [
    (frozenset(), False, True), (frozenset({"Other"}), False, False),
    (frozenset({"Citadel"}), False, True), (frozenset({"Other"}), True, True),
])
def test_chromium_setup_respects_selection(monkeypatch, selected, all_firms, installs):
    monkeypatch.setattr(config, "CITADEL_BOARDS", [{"firm": "Citadel"}])
    monkeypatch.setattr(config, "ONLY_FIRMS", selected)
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0))
    assert install.main(["--all-firms"] if all_firms else []) == 0
    assert bool(calls) == installs


def test_disabled_citadel_needs_no_browser(monkeypatch):
    monkeypatch.setattr(config, "CITADEL_BOARDS", [])
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: pytest.fail("Browser installed while disabled"))
    assert install.main([]) == 0
