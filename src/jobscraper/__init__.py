"""jobscraper -- daily job-board scraper that writes results into a Google Sheet.

Greenhouse is the first (and today, only) board; see greenhouse/client.py.
Adding a second board (Lever, Workday, ...) means adding a sibling package
with its own fetch_jobs() returning list[Job] -- see models.py and main.py.
"""
