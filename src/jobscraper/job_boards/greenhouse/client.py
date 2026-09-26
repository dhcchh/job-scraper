"""Greenhouse Job Board API client.

One function: give it a board token, get back this run's Job objects.

`firm` (not `board_token`) is what ends up in Job.board -- the token is only
needed to hit the right Greenhouse URL. This keeps the sheet's board column
readable ("HRT", "Optiver") instead of the raw career-page slug
("wehrtyou", "optiverneurips") -- see company_toml/greenhouse_boards.toml
for the token->firm mapping. Because Job.board (not the token) is also half
of the (board, job_id) dedup/prune key in sheets.writer, changing what firm
maps to for an existing token would make every already-tracked job under
that token look new again next run -- rename firms there with care, and
re-key any already-written rows to match if you do. Every Job returned here
is stamped `source="Greenhouse"` -- a fixed literal, not anything read from
the response -- see models.Job's docstring.

Transient failures (connection errors, timeouts, 5xx, 429) are retried with
exponential backoff -- see job_boards._http.get_with_retries, the shared
retry helper every board client uses. Anything else (a 4xx other than 429)
raises immediately: retrying a bad request/board token can't possibly
succeed. Exhausting all retries still raises, same as before this existed --
main.py is what decides a failed board shouldn't abort the whole run (it
catches per-board and skips), not this module.
"""

from __future__ import annotations

import datetime
import logging

# Not called directly here anymore (job_boards._http.get_with_retries does
# the actual GET) -- kept as the target
# tests/greenhouse/test_client.py's monkeypatch.setattr(client.requests,
# "get", ...) patches, since `requests` is the same module object wherever
# it's imported, so patching it here still reaches _http's call.
import requests

from jobscraper.job_boards._http import get_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_date, strip_html
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

# Board tokens whose `offices[]` holds teams rather than places, so _location
# reads `location.name` instead. GovTech lists its teams and client agencies
# there ("MOE HQ", "SingPass", "BCA") with the city ("Singapore") only in
# `location.name`, which sent every GovTech posting to RoW. Opt-in per
# board, not a general "offices look unrecognized" fallback: dry-run over
# every configured board, that fallback also moved 656 other postings, many
# wrongly (Block "US-CA - Bay Area - Remote" -> Melbourne, Elastic
# "Indonesia" -> Singapore), since `location.name` is free text that often
# disagrees with the office list.
_OFFICES_ARE_TEAMS: frozenset[str] = frozenset({"govtech"})


def fetch_greenhouse_jobs(
    board_token: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Greenhouse board.

    `board_token` is the Greenhouse career-page slug used to hit the API;
    `firm` and `company_type` (both from company_toml/greenhouse_boards.toml)
    are stored as-is on every returned Job -- `firm` instead of the token
    (see the module docstring), `company_type` because it's a fact about the
    firm, not something derivable from a posting's title.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Greenhouse.

    We ask for content=true (full HTML job descriptions) -- `_to_job` runs
    that through models.strip_html into Job.description, but only for a
    firm in models.FIRMS_NEEDING_DESCRIPTION (confirmed to have an apply
    page that can't be trusted to actually show the JD -- see that
    constant's comment); every other firm's Job.description stays "".
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_token}/jobs"
    response = get_with_retries("greenhouse", board_token, url, params={"content": "true"})

    payload = response.json()
    raw_jobs = payload.get("jobs", [])

    # `id` is the one field _to_job doesn't already default via .get() --
    # every other field degrades to "" on a malformed record, so this
    # matches that same defensive posture rather than letting one bad
    # record (no id at all) raise and cost this whole board's ~hundreds of
    # otherwise-good postings for the day (main.py treats any exception
    # from fetch_greenhouse_jobs as the entire board failing, not per-job).
    jobs = []
    skipped = 0
    for raw in raw_jobs:
        if "id" not in raw:
            skipped += 1
            continue
        jobs.append(
            _to_job(
                raw,
                firm,
                company_type,
                scraped_date,
                use_location_name=board_token in _OFFICES_ARE_TEAMS,
            )
        )
    if skipped:
        logger.warning(
            "greenhouse[%s]: skipped %d job(s) missing an id", board_token, skipped
        )
    logger.info("greenhouse[%s]: fetched %d jobs", board_token, len(jobs))

    return jobs


def _to_job(
    raw: dict,
    firm: str,
    company_type: str,
    scraped_date: datetime.date,
    *,
    use_location_name: bool = False,
) -> Job:
    title = raw.get("title", "")
    department = _first_name(raw.get("departments"))
    return Job(
        board=firm,
        job_id=str(raw["id"]),
        title=title,
        department=department,
        location=_location(raw, use_location_name=use_location_name),
        url=raw.get("absolute_url", ""),
        updated_at=normalize_date(raw.get("updated_at", "")),
        first_published=normalize_date(raw.get("first_published", "")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department, _employment_type(raw)),
        company_type=company_type,
        source="Greenhouse",
        description=(
            strip_html(raw.get("content", ""))
            if firm in FIRMS_NEEDING_DESCRIPTION
            else ""
        ),
        # No structured pay field exists in this API at all -- confirmed
        # across Stripe/Airbnb/Affirm/GitLab/Robinhood's live boards. A pay
        # range, when a posting discloses one, is only ever freeform text
        # buried in `content` (e.g. Robinhood: "the salary range for this
        # role is ... aligned to one of 3 compensation zones"), in no
        # consistent shape worth regexing out -- see models.Job.salary.
        salary=None,
    )


# The metadata field name to read an employment type out of, lowercased for
# a case/whitespace-insensitive match. `metadata` is a board-defined custom
# field list, not a fixed schema, so this matches on the field's NAME rather
# than a position, and a board without that field simply yields "".
#
# Deliberately this one name only. Surveyed across all 54 configured
# Greenhouse boards' live postings, it's both the most widely used
# ("Employment Type" appears on 13 boards) and the only candidate whose
# values are purely employment-type vocabulary ("Full-time", "Summer
# Internship", "Winter Co-Op", "Full-Time: New Grad", "Intern",
# "Contractor", ...). The other plausible-looking names are either
# redundant (Jane Street's "Workday Job Profile" -- "Technology Intern" and
# friends, all on postings this already catches) or actively wrong to feed
# a seniority classifier: Elastic's "Seniority" is "Mid-Senior Level" on
# 300 postings (it would relabel 142 real Principal/Manager/Lead/Director
# titles as plain "Senior"), and MongoDB's "Job Level" is "People Manager"
# on reqs whose titles say Director. classify_seniority only consults this
# as a fallback anyway (see its docstring), which contains the damage of a
# single miscategorized value but is not a reason to widen the field list.
_EMPLOYMENT_TYPE_FIELD = "employment type"


def _employment_type(raw: dict) -> str:
    """This posting's "Employment Type" metadata value, or "".

    Passed to classify_seniority as its fallback signal, for the many real
    internships whose title never says so -- Jane Street's 46 summer
    internships are all posted as plain "Data Engineer"/"Software
    Engineer"/... with "Summer Internship" only in this field (see that
    module's docstring).

    A metadata entry's `value` is null for an unset field (a date/text
    field nobody filled in) and a list for a multi-select one, so anything
    that isn't a string degrades to "" -- the same defensive posture every
    other field in _to_job takes.

    Greenhouse-only on purpose: Lever's `categories.commitment`, Ashby's
    `employmentType`, and SmartRecruiters' `typeOfEmployment.label` are the
    same kind of field, but dry-running this exact comparison across every
    configured board of those three types changed nothing at all -- every
    intern posting on them already says "Intern"/"Internship" in its own
    title. (Eightfold has no such field; its `type` is the string "ATS" on
    every posting.) Wiring it through those clients is a one-liner each if
    a future board needs it.
    """
    for item in raw.get("metadata") or []:
        if str(item.get("name") or "").strip().lower() == _EMPLOYMENT_TYPE_FIELD:
            value = item.get("value")
            return value if isinstance(value, str) else ""
    return ""


def _first_name(items: list[dict] | None) -> str:
    """First department name, or "" -- a posting can list several or none.

    TODO(filtering extension): if you need every department rather than
    just the first, this is the spot to change -- e.g. join them with a
    delimiter.
    """
    if not items:
        return ""
    return items[0].get("name", "")


def _location(raw: dict, *, use_location_name: bool = False) -> str:
    """Every office this posting is open in, normalized and " | "-joined.

    `raw["offices"]` (present because fetch_greenhouse_jobs always requests
    content=true) is Greenhouse's own *pre-split* per-office list -- each
    entry's `name` is one office, with none of the comma-inside-a-city-vs-
    comma-between-cities ambiguity `location.name` has (that field is one
    free-text string a recruiter typed, e.g. "London, United Kingdom; New
    York, NY, United States" -- not safely splittable by this code, only
    by Greenhouse's own admin UI). Falls back to `location.name`
    (stripped) only if `offices` is ever missing/empty, or for a board in
    `_OFFICES_ARE_TEAMS` (`use_location_name`), whose offices aren't places.

    " | " (not ",") is the join delimiter deliberately: a comma can appear
    inside a single normalized name (none do today, but nothing guarantees
    that for every future city), so it can't double as the between-offices
    separator without reintroducing the exact ambiguity this function
    exists to avoid.
    """
    if use_location_name:
        return normalize_location((raw.get("location") or {}).get("name", ""))
    offices = raw.get("offices") or []
    names: list[str] = []
    for office in offices:
        name = normalize_location(office.get("name", ""))
        if name and name not in names:
            names.append(name)
    if names:
        return " | ".join(names)
    return normalize_location((raw.get("location") or {}).get("name", ""))
