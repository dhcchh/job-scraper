"""Lightweight sheet checks: block broken identity, report missing metadata."""

from collections import Counter

from jobscraper.models import FIELDS, _validate_identity


def inspect_rows(rows: list[list[str]]) -> dict:
    """Inspect rows in FIELDS order; row references include the header offset."""
    errors = []
    blanks = {field: [] for field in FIELDS}
    seen = {}
    count = 0
    for number, row in enumerate(rows, start=2):
        if not any(str(value).strip() for value in row):
            continue
        count += 1
        values = {field: row[i] if i < len(row) else "" for i, field in enumerate(FIELDS)}
        for field, value in values.items():
            if value is None or not str(value).strip():
                blanks[field].append(number)
        valid = True
        for field in ("board", "job_id"):
            value = values[field]
            try:
                _validate_identity(value, field)
                if value != value.strip():
                    raise ValueError(f"{field} has leading/trailing whitespace")
            except ValueError as exc:
                errors.append({"row": number, "field": field, "message": str(exc)})
                valid = False
        if valid:
            key = (values["board"], values["job_id"])
            if key in seen:
                errors.append({"row": number, "message": f"duplicate key; first seen at row {seen[key]}"})
            else:
                seen[key] = number
    return {"rows": count, "errors": errors, "blank_fields": {k: v for k, v in blanks.items() if v}}


def validate_rows(rows: list[list[str]]) -> None:
    """Reject invalid or repeated keys before a sheet write."""
    errors = inspect_rows(rows)["errors"]
    if errors:
        first = errors[0]
        raise ValueError(f"data quality: {len(errors)} error(s); row {first['row']}: {first['message']}")


def inspect_tabs(tabs: dict[str, list[list[str]]], expected: list[str]) -> dict:
    """Audit managed tabs. Cross-tab keys are advisory during rerouting."""
    reports = {}
    keys = {}
    cross_tab = []
    for name in expected:
        data = tabs.get(name)
        if not data or data[0][:len(FIELDS)] != list(FIELDS):
            reports[name] = {"rows": 0, "errors": [{"row": 1, "message": "missing tab or header/schema mismatch"}], "blank_fields": {}}
            continue
        reports[name] = inspect_rows(data[1:])
        for number, row in enumerate(data[1:], 2):
            values = dict(zip(FIELDS, row))
            key = (values.get("board"), values.get("job_id"))
            if not all(key):
                continue
            if key in keys and keys[key]["tab"] != name:
                cross_tab.append({"board": key[0], "job_id": key[1], "first": keys[key], "other": {"tab": name, "row": number}})
            else:
                keys[key] = {"tab": name, "row": number}
    return {"tabs": reports, "errors": sum(len(r["errors"]) for r in reports.values()), "cross_tab_duplicates": cross_tab}


def summary_markdown(report: dict) -> str:
    blanks = Counter()
    for result in report["tabs"].values():
        blanks.update({field: len(rows) for field, rows in result["blank_fields"].items()})
    lines = ["## Data quality", "", f"Checked {sum(r['rows'] for r in report['tabs'].values())} rows; {report['errors']} blocking error(s).",
             f"Cross-tab duplicate keys (advisory): {len(report['cross_tab_duplicates'])}.", "",
             "Blank fields are advisory; optional metadata may be unavailable.", "", "| Field | Blank rows |", "| --- | ---: |"]
    lines.extend(f"| {field} | {count} |" for field, count in blanks.items())
    for tab, result in report["tabs"].items():
        for error in result["errors"][:10]:
            lines.append(f"- {tab}, row {error['row']}: {error['message']}")
    return "\n".join(lines) + "\n"
