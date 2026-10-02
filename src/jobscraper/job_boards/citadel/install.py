"""Install Chromium in CI only when the selected boards include Citadel."""

import argparse
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    from jobscraper import config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-firms", action="store_true", help="refresh commands ignore ONLY_FIRMS")
    args = parser.parse_args(argv)
    selected = [
        board for board in config.CITADEL_BOARDS
        if args.all_firms or not config.ONLY_FIRMS or board["firm"] in config.ONLY_FIRMS
    ]
    if not selected:
        print("Citadel is not selected; no Chromium installation needed.")
        return 0
    return subprocess.run([
        sys.executable, "-m", "playwright", "install", "chromium", "--with-deps", "--no-shell",
    ], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
