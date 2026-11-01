# Citadel (SG/HK)

Citadel is registered as a provider but **disabled**: `company_toml/citadel_boards.toml` has `boards = []`, so nothing is fetched or written. Chromium is installed in workflows only when a Citadel board is selected, so nothing needs setting up while it is off.

## Why it is disabled

Citadel's listing sits behind a Cloudflare challenge that automated browsers do not clear.

- **2026-10-02:** Cloudflare blocked both the GitHub Actions probe and a local headed Chromium with manual clicks.
- **2026-10-12 to 2026-10-17:** the scheduled probe failed every day with "Cloudflare challenge did not complete automatically". It was removed on 2026-10-17.
- **Now:** deferred, with no activation planned. The client and the local probe remain for when this is revisited.

The HTML parser has never been checked against a real listing page. Its test fixtures are marked "representative structure, not captured live", so a passing live probe is the only proof it works.

## How the fetcher behaves

- **One headless Chromium context** (Singapore locale and timezone). It allows Cloudflare's automatic verification and fails if an interactive challenge is still showing after 45 seconds.
- **Slow on purpose.** Listing navigations are at least 10 seconds apart. The whole fetch has a five-minute deadline. One whole-snapshot retry is allowed for a navigation error or an incomplete page.
- **Fails closed.** Every page must match its "Viewing X of Y" counter, carry valid unique IDs and link to the next page in order. An unfamiliar office label or changed HTML fails the board instead of returning partial data. A failed fetch cannot prune Citadel rows, as with any other board.
- **SG/HK only.** The fetcher walks the whole global listing, then keeps postings with a Singapore or Hong Kong office. All of a kept posting's offices are preserved.
- **Thin data.** The ID is the slug of the detail-page URL, so a repost on the same slug is not new. Detail pages are not fetched, so published and updated dates, department, salary and description stay blank.

Citadel Securities is a separate source and is not supported. (From the earlier README; not verifiable from the code.)

## Run the probe

The probe needs no Sheets or Telegram credentials. It uses the production fetcher and writes a JSON report: status (`passed`, `empty` or `failed`), whether the snapshot was complete, global and SG/HK counts, how many match the tracked role categories, page count, elapsed time and the failure reason. It exits 0 only on `passed`. With `--diagnostics-dir` it also saves a failure screenshot. Cookies and browser profiles are never saved.

```bash
uv run playwright install chromium --with-deps --no-shell
uv run jobscraper-citadel-probe --output /tmp/citadel-probe.json --diagnostics-dir /tmp/citadel-probe
```

For a manual headed diagnostic, run `xvfb-run uv run jobscraper-citadel-probe --headed`. Headed mode is never an automatic fallback and does not guarantee Cloudflare lets it through.

## Enable it

The probe only reports readiness. It never edits configuration or writes to the sheet.

1. Get the probe to pass consistently, each pass with at least one SG/HK posting that matches a tracked role category.
2. Replace `boards = []` in `company_toml/citadel_boards.toml` with:

   ```toml
   [[boards]]
   token = "citadel"
   firm = "Citadel"
   company_type = "Prop Trading/Hedge Fund"
   ```

3. Run `uv run jobscraper-validate-boards`, then `uv run jobscraper-validate-boards --live --provider citadel --firm Citadel`.
4. Push. A TOML push triggers a Daily Scrape for the new firm.

How many consecutive passes count as "consistently" is not defined in the code. The old three-pass scheduled gate was removed with the scheduled probe.
