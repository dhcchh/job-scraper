from pathlib import Path

import pytest

from jobscraper.job_boards._http import IncompleteBoardResponse
from jobscraper.job_boards.citadel.parser import parse_listing_page

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name="page-1.html"):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_listing_preserves_title_canonical_identity_and_separate_offices():
    page = parse_listing_page(fixture())
    assert (page.shown, page.total, page.next_page) == (2, 3, 2)
    first = page.jobs[0]
    assert first.job_id == "software-engineer"
    assert first.url == "https://www.citadel.com/careers/details/software-engineer/"
    assert first.offices == ("New York", "Greenwich", "Houston", "London", "Singapore")
    assert page.jobs[1].title == "Quantitative Researcher – Data Strategies Group"
    last = parse_listing_page(fixture("page-2.html"))
    assert last.next_page is None
    assert last.jobs[0].offices == ("Hong Kong", "Singapore")


@pytest.mark.parametrize("before,after", [
    ("Viewing 2 of 3", "Viewing jobs"),
    ("Viewing 2 of 3", "Viewing 4 of 3"),
    ("<h3><span>Software Engineer</span></h3>", "<h3></h3>"),
    ("New York, Greenwich, Houston, London, Singapore", "Unknown City, Singapore"),
    ("New York, Greenwich, Houston, London, Singapore", ""),
    ("/careers/details/software-engineer/", "https://elsewhere.example/careers/details/software-engineer/"),
    ("quantitative-researcher-data-strategies-group", "software-engineer"),
    ("/careers/open-opportunities/page/2/", "/careers/open-opportunities/page/2/?location=sg"),
])
def test_changed_or_malformed_markup_fails_closed(before, after):
    with pytest.raises(IncompleteBoardResponse):
        parse_listing_page(fixture().replace(before, after))


def test_unknown_separate_office_cannot_be_silently_dropped():
    html = fixture("page-2.html").replace("<span>Hong Kong</span>", "<span>Unknown City</span>")
    with pytest.raises(IncompleteBoardResponse):
        parse_listing_page(html)


def test_conflicting_counter_and_next_links_are_rejected():
    for addition in (
        "<p>Viewing 2 of 4</p>",
        '<a rel="next" href="/careers/open-opportunities/page/3/">Next</a>',
    ):
        with pytest.raises(IncompleteBoardResponse):
            parse_listing_page(fixture().replace("</main>", addition + "</main>"))


def test_detail_query_and_fragment_do_not_change_identity():
    page = parse_listing_page(fixture().replace("software-engineer/", "software-engineer/?source=careers#apply"))
    assert page.jobs[0].job_id == "software-engineer"
    assert page.jobs[0].url.endswith("/software-engineer/")


def test_explicit_empty_listing_is_parseable_but_missing_total_is_not():
    page = parse_listing_page("<main>Viewing 0 of 0</main>")
    assert page.total == 0 and page.jobs == ()
    with pytest.raises(IncompleteBoardResponse):
        parse_listing_page("<title>Just a moment...</title>")
