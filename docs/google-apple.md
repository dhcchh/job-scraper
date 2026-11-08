# Google and Apple (not built)

These are notes from a probe on 2026-11-08, kept for when the integrations are built. Neither company has a public jobs API. Both embed search results in their own careers pages, so a client has to parse HTML, and it breaks when the site changes. Check the endpoints again before building.

Every probe ran from a home connection, not GitHub Actions. Before writing a client, confirm that Actions can fetch these pages. Citadel shows what happens when a site blocks automated access ([citadel.md](citadel.md)).

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

- **URL:** `https://jobs.apple.com/en-us/search?page=N`. Results are escaped JSON inside `__staticRouterHydrationData`, 20 per page, with the total in `totalRecords`. Apple serves no `robots.txt`; the request redirects to a not-found page.
- **Fields:**
  - `positionId` is the ID.
  - `postingTitle` is the title, and `postDateInGMT` the posting date.
  - `team` has a `teamName` and a `teamCode`.
  - Each entry in `locations[]` has `countryName` and `countryID` (e.g. `iso-country-JPN`).
  - A `type` of `PIPE` (with a `PIPE-...` `reqId`) marks Apple Retail pipeline roles.
- **Filters:**
  - `team=` works only with sub-team codes such as `apps-and-frameworks-SFTWR-AF`.
  - A top-level code (`SFTWR`, `MLAI`) or an unknown code is ignored, and the page returns the unfiltered listing. Reject a filtered total that equals the unfiltered one.
  - A repeated `team=` keeps only the last value, so fetch each sub-team separately.
  - A posting can be listed under several sub-teams, so deduplicate by `positionId`.
  - `location=singapore-SGP` filters by location.
- **Sub-team codes that worked:**
  - Software: `apps-and-frameworks-SFTWR-AF`, `cloud-and-infrastructure-SFTWR-CLD`, `core-operating-systems-SFTWR-COS`, `devops-and-site-reliability-SFTWR-DSR`, `information-systems-and-technology-SFTWR-ISTECH`, `machine-learning-and-ai-SFTWR-MCHLN`, `security-and-privacy-SFTWR-SEC`, `software-quality-automation-and-tools-SFTWR-SQAT`, `wireless-software-SFTWR-WSFT`.
  - ML and AI: `machine-learning-infrastructure-MLAI-MLI`, `deep-learning-and-reinforcement-learning-MLAI-DLRL`, `natural-language-processing-and-speech-technologies-MLAI-NLP`, `computer-vision-MLAI-CV`.
  - Skip `engineering-project-management-SFTWR-EPM`: it is Apple's program-manager team.
  - `applied-research-MLAI-AR` is not a valid code.
- **Classifier gaps:**
  - Engineering program manager titles that mention "ML" or "software engineering" are kept, even outside the EPM sub-team.
  - Many plain "X Engineer" titles are dropped as noise ("Java Server Engineer", "iOS App Development Engineer", "Embedded SOC Drivers Engineer").
