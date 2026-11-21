# Classification rules

How a posting gets a role, seniority, region, location, date, description and
salary. The code is the source of truth; this page describes what it does.

- Role keywords: [`classify_roles.py`](../src/jobscraper/enrichment/classify_roles.py)
- Seniority keywords: [`classify_seniority.py`](../src/jobscraper/enrichment/classify_seniority.py)
- Region and country sets: [`classify_region.py`](../src/jobscraper/enrichment/classify_region.py)
- Location cleanup: [`normalize_location.py`](../src/jobscraper/enrichment/normalize_location.py)
- Description allowlist: `FIRMS_NEEDING_DESCRIPTION` in [`models.py`](../src/jobscraper/models.py)

A posting's role and region together name its tab, such as `SWE/DE - PAC`.
After changing any rule here, see "Which refresh to run" in the
[README](../README.md#which-refresh-to-run).

## Roles

The first matching title keyword wins, in this order:

1. DS/ML/Algos
2. DA/BI
3. SWE/DE
4. QT/QR
5. SRE/DevOps

Anything unmatched is noise and is dropped, apart from the fallbacks below.

**Fallbacks**

- Generic senior engineering titles go to SWE/DE.
- Bare "research" goes to QT/QR at `Prop Trading/Hedge Fund` firms.
- Bare "research" goes to DS/ML/Algos at `AI Lab` firms.
- Bare "analytics" goes to DA/BI.
- Amazon's job family and Apple's teams, below.

**Noise**

- Recruiter titles.
- Solutions-architect titles.
- Legal-engineer titles.
- Developer-relations and developer-advocacy titles.
- Leads of customer-facing engineering teams ("Sales Engineering Manager").
- Traders who are not systematic: discretionary, sales, OTC, floor and
  financing traders.

**Team names don't make a role.** A data-engineering keyword on a product,
program, project, technical-writer or service-delivery title doesn't make it
an engineering role ("Product Manager, Data Engineering").

### Amazon only

These rules apply only when the posting's `source` is Amazon. Other firms
never see them.

- **Extra keywords** for Amazon's own titles:
  - SDE
  - SDM
  - Software Dev Engineer
  - Software Development
  - System Development Engineer
  - BIE
  - Applied Science
  - Research Science
- **Extra noise:**
  - Every data-centre role
  - Infrastructure Reliability & Quality
  - QA and SDET
  - Technicians
  - Delivery consultants
  - Physical security
  - Program, project and product managers
- **Job-family fallback:** an otherwise unmatched posting in the "Software
  Development" job family is SWE/DE. It runs last and never overrides a noise
  rule. It also skips a title that names a job outside software engineering:
  - A noise title from the list above
  - A product owner
  - A customer-facing engineer
  - A user researcher
  - A designer
  - A writer

### Apple only

These rules apply only when the posting's `source` is Apple. Other firms
never see them.

- **Extra keywords:**
  - Security researcher, security research and vulnerability researcher
    (SWE/DE)
  - Information Systems and Technology, which is all the IS&T internships
    name (SWE/DE)
  - Tools engineer (SWE/DE). At Apple "Developer Experience" is the Xcode
    team, so "Profiling Tools Engineer - Developer Experience" isn't
    developer relations.
  - AI research, AIML research, applied researcher and algorithms
    (DS/ML/Algos)
  - Data and reporting roles the shared analyst keywords miss (DA/BI):
    - Insights specialist
    - Technical analyst
    - Solutions analyst
    - Analytics developer, which beats the shared "developer" keyword
    - Applied Data Solutions, which is all its programme's internships name
  - Apple's other analysts stay noise: business and financial operations,
    policy, compliance and SAP functional analysts.
- **Extra noise:**
  - QA and SDET
  - Program, project and product managers, including EPMs and TPMs
  - Siri's language engineers and language experts
  - Electrical (EE), RF, mechanical product-design, tooling and chip
    physical-design roles. "EE&P" is an IS&T team, not electrical
    engineering.
  - Non-tech internships, below.
- **Internship guard:** Apple names each internship by its type ("Software
  PhD Internships", "Legal Internships"). An internship is noise when its
  title names a non-tech type, even if a keyword matches:
  - Hardware, chip, product-design and manufacturing types ("CAD Virtuoso
    Engineering Intern", "Home EE Design Intern")
  - Validation, verification and field-test types
  - Business, marketing, creative, finance, legal, MBA, sales, media, music,
    travel, sourcing, operations, program-management and Apple Services
    types
  - The guard doesn't apply when the title also names software, ML/AI, data
    science, computer vision or security work ("Software Engineering Intern –
    AI Tools for Hardware Engineering").
- **Team fallbacks:** Apple files each posting under its main team, kept in
  `department`. An otherwise unmatched posting is:
  - DS/ML/Algos in "Software and Services" when its title names a scientist
    ("Imaging Scientist").
  - SWE/DE in "Software and Services" when its title names an engineer, an
    architect, software or an internship.
  - SWE/DE in "Students" when its title names an engineer or software.
  - DS/ML/Algos in "Machine Learning and AI" when its title names an
    engineer, an architect, research, a scientist or an internship.
- The team fallbacks run last and skip the same titles as Amazon's
  job-family fallback. They also skip a title that names any of these:
  - Test, quality, validation or verification
  - Hardware, analog, circuits, silicon, SoC, manufacturing, mechanical or
    molding
  - A data centre
  - A lab, broadcast, regulatory, human-factors or technical-content
    engineer
  - A solution architect, who is customer-facing like the shared
    solutions-architect noise

## Seniority

Seniority comes from the title and department. For Greenhouse, the
employment type is the fallback, so a metadata-only internship keeps its
level. A posting with no match is `Unspecified`.

The levels, checked in order: Director/VP/Head, Manager, Principal, Staff,
Lead, Senior, Mid, Junior, Intern, Graduate.

## Regions

- **PAC** (Asia-Pacific) is any of these:
  - Singapore
  - Hong Kong
  - Australia
  - Japan
  - South Korea
  - China
  - Taiwan
  - New Zealand
  - Thailand
  - A role listed for APAC or APJ
- One PAC office is enough to make a posting PAC.
- **Excluded:** a posting is dropped only if every office is in Vietnam,
  Malaysia, Indonesia, India, Armenia or Uruguay.
- **RoW** (rest of world) is everything else.

## Locations

- Each office is normalized separately.
- Offices are joined with ` | `.
- Provider country data is kept.
- A label splits on `/`, `;` or ` or ` only when every part names a known
  place.
- Free text is never split on commas.
- Unresolved offices in new rows appear in the run summary.
- A few firms use the posting's location label instead of its office list:
  - GovTech: its office list names teams.
  - Twitch: every posting lists the same placeholder office.
  - Scale AI and Lyft: their office tags are placeholders or workspace names
    that disagree with the posting's real location.
- Amazon builds offices from its structured `locations` entries, because
  `normalized_location` ends in an ISO alpha-3 code. Each office uses the
  shared alpha-3 table first, then Amazon's country name.
- Apple lists each office as its own row. Rows are grouped by posting, and
  each office uses the alpha-3 code in `countryID` first, then Apple's
  country name.
- TikTok names one city per posting. Its country is the top of the city's
  parent chain (city, state, country).

## Dates

- Dates are written as `YYYY-MM-DD`.
- A date is blank when the provider gives none. TikTok gives none.

## Descriptions

- Only firms in `FIRMS_NEEDING_DESCRIPTION` get one: QRT and Squarepoint.
- Never from SmartRecruiters.
- Apple's is the search result's summary, not the full posting.
- TikTok's is the posting's description followed by its requirements.

## Salary

- Lever and Ashby only.
- TikTok's listing has salary fields, but they were empty on every Singapore
  posting checked (2026-11-21), so they aren't read.
- Greenhouse offers structured ranges through its
  [job-detail endpoint](https://docs.greenhouse.io/job-board.html#retrieve-a-job)
  (`pay_transparency=true`). This project doesn't call it. Per the Greenhouse
  docs; not verified.

## Text safety

- External text stays literal. Text starting with `=`, `+`, `-` or `@` is
  escaped, and Full Refresh writes typed values.
- Only the `url` column is a formula: a generated `HYPERLINK`.
