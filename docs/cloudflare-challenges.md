# Boards behind a Cloudflare challenge (not supported)

Don't build an integration for a careers site that answers automated
requests with a Cloudflare challenge. Citadel was tried and removed. The
same goes for any other board that does this: it is not worth the time.

- Pruning needs a complete snapshot every day. A challenged site gives one
  rarely or never, so its rows would sit stale or held.
- Getting through means defeating a check the site put there to keep
  automation out. This project doesn't do that.
- A board on a supported provider's public API takes minutes to add. Spend
  the time there.

## How to recognise one

Request the listing page once. A challenged site returns HTTP 403 with a
`cf-mitigated: challenge` header and a "Just a moment..." page.

```bash
curl -s -o /dev/null -D - https://www.citadel.com/careers/open-opportunities/ \
  | grep -i -E "^HTTP|cf-mitigated"
```

Check from GitHub Actions as well as from home. A page that loads on a home
connection can still be challenged on Actions addresses.

## Citadel: what was tried

A browser-based client for Citadel's Singapore and Hong Kong postings was
added on 2026-10-02 and removed on 2026-11-15. It never fetched a job.

- **2026-10-02:** Cloudflare blocked both the GitHub Actions probe and a
  local headed Chromium with manual clicks.
- **2026-10-02 to 2026-10-17:** the daily Actions probe failed all 16 runs
  with "Cloudflare challenge did not complete automatically".
- **2026-11-15:** from a home connection, the probe stopped at an
  interactive "Verify you are human" box. The listing, the homepage and the
  careers sitemap all returned the challenge. Only `robots.txt` and the
  sitemap index were served.
- **No other source:** Greenhouse, Lever and Ashby have no board under
  `citadel`, `citadelsecurities` or `citadelllc`. Citadel Securities' site
  is behind the same challenge.

The client, its parser and the probe are in Git history. `a18e8be` is the
last commit that has them.

Revisit only if Citadel drops the challenge or moves its postings to a
provider with a public API.
