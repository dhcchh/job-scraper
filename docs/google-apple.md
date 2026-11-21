# Google and Apple

Probe notes for the two careers sites. Neither company has a documented jobs API.

- **Google** (probed 2026-11-08) is not built. It embeds search results in its careers page, so a client has to parse HTML and breaks when the site changes. Check the endpoint again before building.
- **Apple** (probed 2026-11-15) is built: [`job_boards/apple/client.py`](../src/jobscraper/job_boards/apple/client.py). It uses an undocumented JSON endpoint that its own search page calls, as Amazon does. These notes record what the client relies on.

Every probe ran from a home connection, not GitHub Actions. Before merging a client, confirm that Actions can fetch from the site. If either site answers with a Cloudflare challenge, stop: Citadel was removed for that reason ([cloudflare-challenges.md](cloudflare-challenges.md)).

## Google

**Blocker:** Google's `robots.txt` disallows paginated results: `/about/careers/applications/jobs/results?page=` and `...?*&page=`. A complete snapshot needs every page, so decide whether to proceed before doing any other work.

- **URL:** `https://www.google.com/about/careers/applications/jobs/results?page=N`. The same listing includes Google DeepMind roles.
- **Data:** the page holds an `AF_initDataCallback({key: 'ds:1', ... data: [...]})` block. `data[0]` is that page's jobs, `data[2]` is the total and `data[3]` is the page size (20).
- **Job shape:** each job is a positional array with no field names. `[0]` is the numeric ID and `[1]` the title. A location entry looks like `['Singapore', ['Singapore'], city, postcode, state, 'SG']`, ending in an ISO alpha-2 code. Check the shape strictly and fail the board on a mismatch, because a silent change would put data in the wrong columns.
- **Filters:**
  - `category=` can be repeated, and repeated values combine as OR.
  - The main tech category is `SOFTWARE_ENGINEERING`. `NETWORK_ENGINEERING`, `TECHNICAL_INFRASTRUCTURE_ENGINEERING` and `INFORMATION_TECHNOLOGY` also exist.
  - `PROGRAM_MANAGEMENT`, `PRODUCT_MANAGEMENT`, `TECHNICAL_SOLUTIONS`, `DATA_CENTER_OPERATIONS`, `SALES` and other non-tech categories are separate and can be left out.
  - A posting can belong to more than one category.
  - An unknown category value returns zero results, so a zero total must fail the board. Otherwise every tracked row would be pruned.
  - `location=Singapore` filters by location.
- **Research roles** are spread across several categories, so `SOFTWARE_ENGINEERING` alone misses some research scientists.
- **Classifier gaps:** titles like "Tech Lead Manager", "Test Engineer", "Privacy Engineer" and "Student Researcher" are currently dropped as noise.

## Apple

Built on 2026-11-16. The decisions are under [Decisions](#decisions).

### Endpoint

- `POST https://jobs.apple.com/api/v1/search` with a JSON body. It is the
  search page's own endpoint: internal, undocumented and not a third-party ATS.
- No authentication is needed.
  - The site's script sends an `X-Apple-CSRF-Token` header, fetched from
    `GET /api/v1/CSRFToken`. The search answers the same without it, with a
    made-up token, and with no cookies.
  - A custom User-Agent, the `requests` default and a browser one all worked.
- There is no Cloudflare challenge. The response header is `server: Apple`.
- Apple serves no `robots.txt`; the request redirects to a not-found page.

### Request

```json
{
  "query": "",
  "filters": {"teams": [{"team": "teamsAndSubTeams-SFTWR"}]},
  "page": 1,
  "locale": "en-us",
  "sort": "newest",
  "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}
}
```

- `format` is required. Without it the response is HTTP 200 with no results.
- `filters` is required, even when empty. Without it the response is HTTP 436.
- `query`, `locale` and `sort` can be left out. Every `locale` value tried
  returned the same total.
- `page` starts at 1. The page size is fixed at 20: `pageSize`, `size`,
  `limit`, `recordsPerPage`, `perPage`, `count`, `rows` and `resultsPerPage`
  were all ignored.

### Response

`res.totalRecords` is the number of rows and `res.searchResults` holds one
page of them. A real row, with the summary shortened:

```json
{
  "id": "200694136-3337",
  "positionId": "200694136",
  "reqId": "200694136-3337",
  "jobPositionId": "REQ-200694136",
  "type": "REQ",
  "postingTitle": "GenAI Research Engineer",
  "transformedPostingTitle": "genai-research-engineer",
  "jobSummary": "Do you believe that generative models can ...",
  "postingDate": "Nov 13, 2026",
  "postDateInGMT": "2026-11-13T23:59:15.107Z",
  "team": {
    "teamName": "Software and Services",
    "teamID": "teamsAndSubTeams-SFTWR",
    "teamCode": "SFTWR"
  },
  "locations": [
    {
      "postLocationId": "postLocation-SEA",
      "name": "Seattle",
      "city": "",
      "stateProvince": "",
      "metro": "",
      "region": "",
      "countryName": "United States of America",
      "countryID": "iso-country-USA",
      "level": 5
    }
  ],
  "isMultiLocation": true,
  "managedPipelineRole": false,
  "homeOffice": false,
  "postExternal": true,
  "standardWeeklyHours": 40,
  "localeInfo": {
    "languageCode": "en_US",
    "dateFormat": "MMM D, YYYY",
    "defaultLocaleCode": "en-us"
  }
}
```

### One row per office

- A posting open in several offices is returned once per office.
  - The rows share `positionId`, which is the posting's ID.
  - They differ only in `id` (`<positionId>-<office code>`), `reqId` and
    `locations`.
  - `locations` always holds exactly one entry.
- `totalRecords` therefore counts rows, not postings. On 2026-11-15 the
  software and machine-learning teams were 2,245 rows for 1,821 postings.
- A client must group rows by `positionId` and join the offices with ` | `.

### Fields

- **ID:** `positionId`, nine digits.
- **Title:** `postingTitle`.
- **Department:** `team.teamName`. It was set on every row of the four
  teams fetched and null on retail pipeline rows.
- **Posted date:** `postDateInGMT`, an ISO timestamp. Retail pipeline rows
  carry the time of the request instead, so they always sort first.
- **Office:** `locations[0].name`, and its meaning depends on `level`.
  - 5 is a city, 4 a metro area ("San Francisco Bay Area"), 1 a country and
    6 a retail store.
  - Software and machine-learning rows had only levels 4 and 5.
  - `city`, `stateProvince`, `metro` and `region` were always empty.
- **Country:** `countryID` is `iso-country-` followed by an ISO alpha-3 code.
  `countryName` is Apple's own form ("United States of America").
  - `normalize_office(name, alpha-3 code)` resolved every office in the
    software and machine-learning teams, with none left unresolved.
- **URL:** `https://jobs.apple.com/en-us/details/<positionId>/<transformedPostingTitle>`.
  - Without the title slug the page redirects (301) to the full URL.
  - An unknown ID still returns HTTP 200, so the status says nothing about
    whether a posting exists.
- **Type:** `type` is `PIPE` or `REQ`, and both are ordinary corporate
  postings. It does not mark retail: the software team is a mix of the two.
  - Apple Retail pipeline rows have `managedPipelineRole: true`, an `id` of
    `PIPE-<positionId>` and team code `APPST`.
- **Description:** `GET /api/v1/jobDetails/<positionId>?locale=en-us` returns
  `description`, `responsibilities`, `minimumQualifications` and
  `preferredQualifications` without authentication. It is one request per
  posting. Apple is not in `FIRMS_NEEDING_DESCRIPTION`, so it isn't needed.

### Filters

- **Whole team:** `{"team": "teamsAndSubTeams-<CODE>"}` in `filters.teams`.
- **One sub-team:** add `"subTeam": "subTeam-<CODE>"` to the same object.
- **Several entries combine as OR**, and a row listed under two of them is
  returned once. One request for software and machine learning returned
  2,097 rows; the two teams requested separately share 147 rows.
- **A posting can be listed under several teams.** A row's own `team` is its
  main team: the machine-learning listing includes rows whose `team` is
  software or hardware.
- **Rows don't name their sub-team.** To leave a sub-team out, list the wanted
  sub-teams in the request.
- **An unknown team or sub-team code returns zero results**, not an error.
- **Team list:** `GET /api/v1/refData/teamsofinterest` needs no
  authentication and returns all eleven teams with their sub-teams.
  - Relevant teams: `SFTWR` (Software and Services), `MLAI` (Machine Learning
    and AI), `STDNT` (students) and `HRDWR` (hardware).
  - Program-manager sub-teams: `subTeam-EPM` under `SFTWR` and
    `subTeam-EPPM` under `MLAI`.
  - `STDNT` has no tech sub-team. `subTeam-INTRN` holds the internships,
    tech and non-tech alike. `subTeam-CORP` holds graduate roles, mostly in
    hardware. The rest are retail student roles.
- **Location:** `filters.locations` takes IDs such as `postLocation-SGP`, and
  several combine as OR. An unknown ID returns zero results.
- The page-style slugs (`apps-and-frameworks-SFTWR-AF`, `singapore-SGP`) do
  not work here. They belong to the HTML page's query string.

### Pagination

- Request `page` 1, 2, 3 and so on until the rows collected reach
  `totalRecords`.
- A page past the end returns `totalRecords: 0` and no rows. `page: 0`
  returns page 1.
- **Use only `sort: "newest"`.** One full pass of the machine-learning team
  with each of the others:
  - `relevance` returned 320 distinct rows of 455 and lost 98 postings.
  - `teamAsc` returned 441 of 455 and lost 13 postings.
  - `locationAsc` returned 453 of 455.
- **`newest` is unstable at one kind of page boundary.**
  - Rows with the same `postDateInGMT` come back in a different order on
    each request, and the offices of one posting share a timestamp.
  - When those rows straddle a page boundary, one office can appear on both
    pages and another on neither. The posting is still there; one of its
    offices is missing.
  - The row count still equals `totalRecords`, so only a repeated `id`
    shows it.
  - Seen on all 4 passes of the hardware team (one posting with seven
    offices), and never on software (3 passes) or machine learning (4).
- **Completeness test:** the number of distinct row `id`s equals
  `totalRecords`.
  - When it doesn't, refetch the pages holding a repeated `id` and merge the
    rows. That reached the full count within two rounds.
  - Fail the board if a few rounds don't.

### Failure modes to guard

- **A zero total must fail the board.** A request body missing `format` and
  an unknown team code both return HTTP 200 with no results, which would
  otherwise look like every posting closed.
- **Every team code is checked** against the team list endpoint before the
  search. An unknown sub-team in a combined search would otherwise drop its
  rows silently while the total stayed above zero.
- **Three of about 1,700 requests hung** until a 30-second read timeout. The
  shared retry policy already retries timeouts.
- **Load:** a page took about 0.33 seconds. Software and machine learning
  together are about 113 pages, or 40 seconds one page at a time. Four
  concurrent streams ran without errors, and nothing returned HTTP 429.

### Pagination helper

The offset helpers didn't fit, so `paginate_numbered_pages` in
`pagination.py` was added for Apple.

- `paginate_with_retries` and `paginate_with_overlap` send an offset and a
  page size. Apple takes a page number in a JSON body and no size.
- Both reject any repeated ID as an incomplete snapshot. Apple needs the
  page-boundary repair above first.
- Pages can't overlap, because the page size is fixed and there is no offset.
  - A posting added at the top mid-fetch would push a row off the end of a
    page already read.
  - So, after the repair, the first page is fetched again. A row on it that
    wasn't seen fails the snapshot.
- Everything else fits: `build_job`, `normalize_office`, `normalize_locations`
  and the retry policy.

### Classifier fit

A dry run on 2026-11-15 routed the software and machine-learning postings
with the shared rules alone and `company_type = "Consumer/Marketplace"`.

- 1,821 postings: 1,206 routed to a tab (192 of them PAC), 81 were excluded
  as India-only, and 534 were dropped as noise.
- The 534 dropped titles, bucketed by keyword and not reviewed one by one:
  - 144 program, project or product managers.
  - 82 QA, test or quality.
  - 168 other "engineer" titles.
  - 17 researchers or scientists.
  - 17 internships with a generic title ("2027 Apple Internship -
    Information Systems and Technology").
  - 106 others: managers, leads, editorial and operations.
- The real misses are in the engineer and researcher buckets: "Camera
  Software Frameworks Engineer", "RealityKit Spatial UI Rendering Engineer",
  "Senior GenAI Engineer, Apple Cloud AI", "Cryptography Engineer, SEAR",
  "AIML - Lead Researcher, Foundation Models", "Offensive Security
  Researcher".
- Some titles in those buckets are rightly dropped: "Language Engineer
  (Finnish), Global Siri", "Software Application Support Engineer".
- The Apple-only rules in [rules.md](rules.md#apple-only) fix these misses.
  They are gated on `source`, as Amazon's are, and kept out of the shared
  keyword lists.
- They also drop titles the shared rules kept: engineering program managers
  whose titles mention ML or software engineering, SDETs (as for Amazon),
  and hardware and chip-design internships.
- Apple's internships are named by type, tech and non-tech alike, so an
  Apple-only internship guard drops the non-tech types. With it in place,
  the software team's fallback also routes its internships.

### Decisions

- **Teams:** `SFTWR,MLAI,STDNT/INTRN`. That is software, machine learning
  and the internships; the classifier keeps the tech internships.
  - `HRDWR` is left out: it is mostly non-software, and it is the team that
    showed the page-boundary problem.
  - `STDNT/CORP` is left out: its graduate roles are mostly hardware.
- **One board.** One request naming several teams returns shared rows once.
  Separate boards per team would return the same posting twice, under one
  `(firm, job_id)`.
- **`company_type`:** `Consumer/Marketplace`, as for Amazon.
- **Reachable from GitHub Actions** (2026-11-16). `Board Health Check`,
  dispatched on a branch pushed without a pull request, fetched all 1,915
  postings from an Actions address. The page-boundary repair took two
  rounds there.

### HTML page

The search page also embeds the same rows, as escaped JSON inside
`__staticRouterHydrationData`. Prefer the JSON endpoint; these notes are for
reference.

- `https://jobs.apple.com/en-us/search?page=N` returns 20 rows per page.
- `team=` works only with sub-team slugs such as
  `apps-and-frameworks-SFTWR-AF`. A top-level or unknown code is ignored and
  the page returns the unfiltered listing.
- A repeated `team=` keeps only the last value.
- `location=singapore-SGP` filters by location.
