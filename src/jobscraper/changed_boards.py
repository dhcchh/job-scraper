"""Select firms with added or modified TOML entries between two Git revisions."""

import argparse
import subprocess
import tomllib
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def _boards_at(ref: str) -> dict[str, list[dict]]:
    paths = _git("ls-tree", "-r", "--name-only", ref, "--", "company_toml").splitlines()
    return {
        path: tomllib.loads(_git("show", f"{ref}:{path}"))["boards"]
        for path in paths if path.endswith("_boards.toml")
    }


def changed_firms(before: dict[str, list[dict]], after: dict[str, list[dict]]) -> list[str]:
    """Include token, scope, category, and name edits; ignore formatting changes."""
    return sorted({
        board["firm"]
        for path, boards in after.items()
        for board in boards
        if board not in before.get(path, [])
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--merge-base", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    base = _git("merge-base", args.base, args.head).strip() if args.merge_base else args.base
    # GitHub uses an all-zero base for the first push to a branch.
    before = {} if set(base) == {"0"} else _boards_at(base)
    firms = changed_firms(before, _boards_at(args.head))
    if any(any(char in firm for char in ",\r\n") for firm in firms):
        raise ValueError("Firm names used by ONLY_FIRMS cannot contain commas or newlines")
    output = ",".join(firms)
    if args.github_output:
        with args.github_output.open("a") as handle:
            handle.write(f"firms={output}\n")
    print(output)


if __name__ == "__main__":
    main()
