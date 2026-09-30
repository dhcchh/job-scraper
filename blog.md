# How the job scraper works

The scraper fetches configured boards from seven providers, assigns role
and region categories, and tracks open postings in Google Sheets. Optional
Telegram messages report newly added jobs and companies.

Each posting has a `(firm, job_id)` identity. Daily runs append unseen
identities and remove postings absent from a complete, successful board
fetch. Existing rows retain their original `scraped_date` and metadata until
a refresh. Multiple tokens for one firm must all succeed before pruning.

Boards run sequentially. Workday fetches pages and required posting details
with up to eight workers. Requests retry transient failures; malformed
responses and missing or repeated identities fail a board. Failed boards
keep their existing rows. An unusually large drop holds both appends and
prunes for that firm until the feed recovers or an operator confirms it.

Role keywords route jobs to five categories. Locations are normalized per
office and retain provider country information. PAC covers Singapore, Hong
Kong, Australia, Japan, South Korea, China, Taiwan, New Zealand and Thailand.
Jobs located only in Vietnam, Malaysia, Indonesia, India, Armenia or Uruguay
are excluded. Other jobs go to RoW. Category and region determine one of ten
tabs; the source code owns the exact rules.

Daily writes validate the header and use the current column order. Backfill
migrates existing rows in place. Full Refresh also moves rows between tabs,
committing all row data and grid sizes atomically before applying formatting.
It preserves first-seen dates during routing changes.

The Telegram digest groups new PAC postings by category, firm and location,
with a link to the sheet. Optional DMs contain individual SG/HK data-engineering
roles. Full descriptions are retained only for the firms listed in
`FIRMS_NEEDING_DESCRIPTION`. Notifications are best effort and are not queued
for later retries.

See [README.md](README.md) for setup, commands and operational limitations.
