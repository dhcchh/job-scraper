"""jobscraper -- daily job-board scraper that writes results into a Google Sheet.

Greenhouse, Lever, and Ashby are the boards fetched today; see
job_boards/greenhouse, job_boards/lever, and job_boards/ashby. Adding
another board means adding a sibling package with its own
fetch_<board>_jobs() returning list[Job] -- see models.py and main.py.
"""
