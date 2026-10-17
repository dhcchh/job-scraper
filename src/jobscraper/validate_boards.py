"""Validate board configuration, with optional live endpoint checks."""

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Iterable

from jobscraper.board_config import (
    DEFAULT_BOARD_DIR,
    PROVIDERS,
    BoardConfig,
    load_all_boards,
)


SelectedBoard = tuple[str, BoardConfig]


def select_boards(
    boards_by_provider: dict[str, list[BoardConfig]],
    *,
    providers: Iterable[str] = (),
    firms: Iterable[str] = (),
    tokens: Iterable[str] = (),
) -> list[SelectedBoard]:
    """Return boards matching every supplied filter."""
    provider_filter = set(providers)
    firm_filter = {firm.casefold() for firm in firms}
    token_filter = set(tokens)
    return [
        (provider, board)
        for provider, boards in boards_by_provider.items()
        for board in boards
        if (not provider_filter or provider in provider_filter)
        and (not firm_filter or board["firm"].casefold() in firm_filter)
        and (not token_filter or board["token"] in token_filter)
    ]


def validate_live(
    boards: Iterable[SelectedBoard],
    *,
    allow_empty: bool = False,
    output: Callable[[str], None] = print,
) -> int:
    """Fetch selected boards concurrently; return the number that fail validation."""
    # Imported here: importing config loads the board files, and main()
    # reports an invalid file itself rather than failing at import time.
    from jobscraper.job_boards import fetchers

    results = fetchers.fetch_boards([
        (provider, [board], fetchers.fetcher_for(provider)) for provider, board in boards
    ])
    failures = 0
    for result in results:
        board = result.board
        label = f"{result.source}[{board['firm']}] ({board['token']})"
        if result.error is not None:
            failures += 1
            output(f"FAIL {label}: {type(result.error).__name__}: {result.error}")
            continue

        jobs = result.jobs
        if not jobs:
            message = f"{label}: returned no jobs, so IDs could not be verified"
            if allow_empty:
                output(f"WARN {message}")
            else:
                failures += 1
                output(f"FAIL {message}; pass --allow-empty if this is expected")
            continue

        blank_ids = sum(not str(job.job_id).strip() for job in jobs)
        id_counts = Counter(str(job.job_id) for job in jobs)
        duplicate_ids = sorted(
            job_id for job_id, count in id_counts.items() if count > 1
        )
        wrong_firms = sum(job.board != board["firm"] for job in jobs)
        problems = []
        if blank_ids:
            problems.append(f"{blank_ids} blank job ID(s)")
        if duplicate_ids:
            sample = ", ".join(duplicate_ids[:3])
            problems.append(f"duplicate job ID(s): {sample}")
        if wrong_firms:
            problems.append(f"{wrong_firms} job(s) have the wrong firm name")

        if problems:
            failures += 1
            output(f"FAIL {label}: {'; '.join(problems)}")
        else:
            output(f"OK   {label}: {len(jobs)} job(s)")

    output(f"Live validation: {len(results) - failures}/{len(results)} board(s) passed")
    return failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate checked-in board configuration. Static validation is "
            "always performed; --live also fetches selected endpoints."
        )
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="fetch selected boards and verify that they return unique job IDs",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=PROVIDERS,
        default=[],
        help="limit live checks to a provider; may be repeated",
    )
    parser.add_argument(
        "--firm",
        action="append",
        default=[],
        help="limit live checks to an exact firm name; may be repeated",
    )
    parser.add_argument(
        "--token",
        action="append",
        default=[],
        help="limit live checks to an exact board token; may be repeated",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="warn instead of failing when a live board has no open jobs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.allow_empty and not args.live:
        print("--allow-empty requires --live", file=sys.stderr)
        return 2

    try:
        boards_by_provider = load_all_boards(DEFAULT_BOARD_DIR)
    except (OSError, ValueError) as exc:
        print(f"Board configuration invalid: {exc}", file=sys.stderr)
        return 1

    board_count = sum(len(boards) for boards in boards_by_provider.values())
    firm_count = len(
        {
            board["firm"].casefold()
            for boards in boards_by_provider.values()
            for board in boards
        }
    )
    print(
        f"Static validation passed: {board_count} board(s), {firm_count} firm(s), "
        f"{len(boards_by_provider)} provider(s)"
    )

    selected = select_boards(
        boards_by_provider,
        providers=args.provider,
        firms=args.firm,
        tokens=args.token,
    )
    if not selected:
        print("No boards matched the supplied filters", file=sys.stderr)
        return 1
    if not args.live:
        return 0
    return 1 if validate_live(selected, allow_empty=args.allow_empty) else 0


if __name__ == "__main__":
    raise SystemExit(main())
