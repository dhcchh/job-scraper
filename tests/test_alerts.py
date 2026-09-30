"""Tests for alerts.py: matching, JD retrieval, formatting, and sending.
No real network call: get_with_retries and requests.post are monkeypatched.
"""

import datetime

import pytest

from jobscraper import alerts, telegram
from jobscraper.models import Job

_SHEET_URL = "https://docs.google.com/spreadsheets/d/test-sheet-id"


def _job(title="Data Engineer", location="Singapore", **kwargs):
    fields = dict(
        board="Acme",
        job_id="1",
        title=title,
        department="Eng",
        location=location,
        url="https://example.com/1",
        updated_at="",
        scraped_date=datetime.date(2026, 9, 30),
    )
    fields.update(kwargs)
    return Job(**fields)


@pytest.mark.parametrize(
    "title",
    [
        "Senior Data Engineer",
        "Data Engineering Lead",
        "Big Data Engineer",
        "ETL Engineer",
        "Analytics Engineer",
        "Software Engineer (Data)",
        "Software Engineer, Data Platform",
        "Data Platform Engineer",
        "Data Infrastructure Engineer",
        "Backend Developer - Data",
    ],
)
def test_data_engineering_titles_match(title):
    assert alerts.is_alert_match(_job(title))


@pytest.mark.parametrize(
    "title",
    [
        "Product Manager, Data Engineering",
        "Data Scientist",
        "Data Analyst",
        "Software Engineer, Payments",
        "Data Center Engineer",
        "Technical Recruiter, Data Engineering",
    ],
)
def test_other_titles_do_not_match(title):
    assert not alerts.is_alert_match(_job(title))


@pytest.mark.parametrize(
    "location, expected",
    [
        ("Singapore", True),
        ("Hong Kong", True),
        ("London, United Kingdom | Singapore", True),
        ("Tokyo, Japan", False),
        ("Sydney, Australia", False),
        ("", False),
    ],
)
def test_location_must_include_singapore_or_hong_kong(location, expected):
    assert alerts.is_alert_match(_job(location=location)) is expected


def test_fetch_jd_strips_captured_html_without_a_request(monkeypatch):
    monkeypatch.setattr(alerts, "get_with_retries", _fail)
    job = _job(jd_raw="<p>Build <b>pipelines</b></p><ul><li>Spark</li></ul>")
    assert alerts.fetch_jd(job) == "Build pipelines\n\n- Spark"


def test_fetch_jd_reads_workday_detail_derived_from_the_url(monkeypatch):
    urls = []
    monkeypatch.setattr(
        alerts,
        "get_with_retries",
        _respond(urls, {"jobPostingInfo": {"jobDescription": "<p>About the role</p>"}}),
    )
    job = _job(
        source="Workday",
        url="https://visa.wd5.myworkdayjobs.com/Visa/job/Singapore/Data-Engineer_REF1",
    )
    assert alerts.fetch_jd(job) == "About the role"
    assert urls == [
        "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/job/Singapore/Data-Engineer_REF1"
    ]


def test_fetch_jd_joins_smartrecruiters_sections(monkeypatch):
    urls = []
    payload = {
        "jobAd": {
            "sections": {
                "companyDescription": {"title": "Company Description", "text": "<p>Grab</p>"},
                "jobDescription": {"title": "Job Description", "text": "<p>Pipelines</p>"},
                "additionalInformation": {"title": "Extra", "text": ""},
            }
        }
    }
    monkeypatch.setattr(alerts, "get_with_retries", _respond(urls, payload))
    job = _job(source="SmartRecruiters", url="https://jobs.smartrecruiters.com/Grab/744")
    assert alerts.fetch_jd(job) == (
        "Company Description\nGrab\n\nJob Description\nPipelines"
    )
    assert urls == ["https://api.smartrecruiters.com/v1/companies/Grab/postings/744"]


def test_fetch_jd_returns_empty_on_failure_or_unknown_board(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(alerts, "get_with_retries", boom)
    assert alerts.fetch_jd(_job(source="Workday", url="https://a.wd1.myworkdayjobs.com/S/job/x")) == ""
    assert alerts.fetch_jd(_job(source="Eightfold")) == ""


def test_build_alert_text_escapes_and_includes_details():
    job = _job(
        title="Data Engineer <Core>",
        board="A&B",
        salary="SGD 100,000-150,000/year",
        first_published="2026-09-29",
        seniority="Senior",
        url="https://example.com/?a=1&b=2",
    )
    text = alerts.build_alert_text(job, "Use <Spark> & SQL")
    assert "<b>Data Engineer &lt;Core&gt;</b>" in text
    assert "A&amp;B · Singapore" in text
    assert "Seniority: Senior · Salary: SGD 100,000-150,000/year · Posted: 2026-09-29" in text
    assert '<a href="https://example.com/?a=1&amp;b=2">Apply</a>' in text
    assert text.endswith("<b>Job description</b>\nUse &lt;Spark&gt; &amp; SQL")


def test_build_alert_text_without_jd_points_to_the_link():
    assert alerts.build_alert_text(_job(), "").endswith(
        "Not available from this board, see the link."
    )


def test_long_jd_is_truncated_and_chunks_fit_telegram():
    jd = "\n\n".join(["word " * 900] * 5)  # long paragraphs, > the JD cap
    text = alerts.build_alert_text(_job(), jd)
    assert "(truncated, see the link)" in text
    assert all(len(chunk) <= 4096 for chunk in telegram._chunk(text))


def test_send_alerts_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(alerts.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(alerts.config, "TELEGRAM_DM_CHAT_ID", None)
    monkeypatch.setattr(telegram.requests, "post", _fail)
    alerts.send_alerts([_job()], _SHEET_URL)


def test_send_alerts_dms_matches_only_and_caps_the_run(monkeypatch):
    monkeypatch.setattr(alerts.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(alerts.config, "TELEGRAM_DM_CHAT_ID", 42)
    monkeypatch.setattr(alerts, "_PAUSE_SECONDS", 0)
    monkeypatch.setattr(alerts, "MAX_ALERTS_PER_RUN", 2)
    sent = []

    class _Ok:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        sent.append(json)
        return _Ok()

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    jobs = [
        _job(job_id="1"),
        _job(job_id="2", title="Data Scientist"),
        _job(job_id="3", location="Tokyo, Japan"),
        _job(job_id="4", location="Hong Kong"),
        _job(job_id="5"),
    ]
    alerts.send_alerts(jobs, _SHEET_URL)

    assert [m["chat_id"] for m in sent] == [42, 42, 42]
    assert all("message_thread_id" not in m for m in sent)
    assert "+1 more matching role(s)" in sent[-1]["text"]


def test_send_alerts_swallows_send_failures(monkeypatch):
    monkeypatch.setattr(alerts.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(alerts.config, "TELEGRAM_DM_CHAT_ID", 42)
    monkeypatch.setattr(alerts, "_PAUSE_SECONDS", 0)

    def boom(url, json, timeout):
        raise ConnectionError("down")

    monkeypatch.setattr(telegram.requests, "post", boom)
    alerts.send_alerts([_job(), _job(job_id="2")], _SHEET_URL)  # must not raise


def _fail(*args, **kwargs):
    raise AssertionError("must not make a request")


def _respond(urls, payload):
    class _Response:
        def json(self):
            return payload

    def fake_get(source, identifier, url, **kwargs):
        urls.append(url)
        return _Response()

    return fake_get


def test_check_dm_reports_not_configured(monkeypatch, capsys):
    monkeypatch.setattr(alerts.config, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(telegram.requests, "post", _fail)
    assert alerts.check_dm() is False
    assert "Not configured" in capsys.readouterr().out


def test_check_dm_reports_a_send_failure(monkeypatch, capsys):
    monkeypatch.setattr(alerts.config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(alerts.config, "TELEGRAM_DM_CHAT_ID", 42)

    def boom(url, json, timeout):
        raise ConnectionError("chat not found")

    monkeypatch.setattr(telegram.requests, "post", boom)
    assert alerts.check_dm() is False
    assert "pressed Start" in capsys.readouterr().out
