"""Job board API clients. Each board is a sibling package here exposing its
own fetch_<board>_jobs(token, firm, company_type) -> list[Job] -- e.g.
job_boards/greenhouse/client.py's fetch_greenhouse_jobs.
"""
