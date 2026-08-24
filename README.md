# Employment Lookup (BLS LAUS)

Type a place, get its not-seasonally-adjusted employment numbers, formatted
to match your HUD concept package tables, ready to copy straight into Word
or Excel. Built on BLS's own official bulk data files (the same source
behind data.bls.gov) — no scraping, no rate limits, no API key.

**Covers every city, county, and metro area (MSA) in Texas, Arkansas,
Oklahoma, and Tennessee automatically** — no manual list to maintain. The
site has three separate search boxes (Cities / Counties / Metros), each
scoped to just that type of place, so you never see a county result mixed
in with a city one.

**Each area type shows exactly what your workbook needs, no more:**
- **City** — most recent month's employment, the same month 3 years prior,
  and the 3-year average annual growth rate (matches "Austin Employment Data").
- **County** — 11 years of annual averages, the most-recent-month YTD figure,
  and 10-yr / 5-yr average annual growth (matches "Job Estimates Summary Table").
- **Metro (MSA)** — 6 years of annual averages with year-over-year growth
  and $ / % totals (matches "MSA Employment Data").

Verified against the exact numbers in your Travis County / Austin worksheet
(1.31% city growth, 3.32%/4.70% county averages, 5.97% MSA average all
reproduce correctly).

## Important: one-time setup for automatic monthly updates

This tool needs a **free BLS API key** to run fully automatically. Here's why:
GitHub's automated servers are blocked by BLS's bulk-download server (a
security measure on BLS's end, not something we can configure around), so
this tool instead uses BLS's official **Public API** to fetch the actual
numbers — that's a modern, documented service meant for exactly this kind
of scheduled access, and isn't subject to the same block. Structural info
(which counties/cities/metros exist) still comes from the bulk files, since
that part doesn't go stale even on a slower mirror.

**Get your free key (takes under a minute, no approval wait):**
1. Go to https://www.bls.gov/developers/ and click "Register for an API key"
2. Enter your email — BLS emails you a key immediately
3. In your GitHub repo, go to **Settings → Secrets and variables → Actions**
4. Click **New repository secret**
5. Name it exactly `BLS_API_KEY`, paste in the key BLS emailed you, save

Without this key, the tool still works, but is capped at 25 requests/day —
not enough to refresh every place in one run, so some places would come back
empty. With the free key: 500 requests/day, easily enough for 700+ places.

## How it's structured

- `fetch_bls_data.py` — two-phase process:
  1. **Catalog phase**: downloads BLS's bulk files for TX/AR/OK/TN once, to
     build the list of every county/city/metro and its correct BLS series ID
     (this structural list doesn't go stale, unlike raw values from the same
     source when fetched from cloud servers).
  2. **Values phase**: queries the official BLS API for the actual current
     numbers for every place in the catalog.
- `bls_data.json` — the actual data (generated file — don't hand-edit).
- `index.html` — the search page: three boxes (Cities, Counties, Metros).
- `.github/workflows/refresh.yml` — has GitHub auto-run the fetch monthly.

## Adding another state

Open `fetch_bls_data.py` and find this line near the top:

```python
TARGET_STATES = ["TX", "AR", "OK", "TN"]
```

Add the 2-letter abbreviation for any other state, save, commit, and
re-run the fetch (or wait for the monthly auto-refresh). Every city,
county, and metro in that state will show up automatically — no per-place
setup needed.

## One-time setup

1. Register for a free BLS API key and add it as a GitHub secret named
   `BLS_API_KEY` (see above) — do this first.

2. **Host it on GitHub Pages** (free):
   - Create a new GitHub repo, push these files to it.
   - Repo Settings → Pages → Deploy from branch → `main` / root.
   - Your tool is now live at `https://<yourusername>.github.io/<reponame>/`.

3. The included GitHub Action (`.github/workflows/refresh.yml`) re-runs the
   fetch automatically on the 25th of each month and commits the refreshed
   `bls_data.json` — fully automatic, doesn't depend on anyone's computer.
   You can also trigger it manually anytime from the repo's "Actions" tab.

**Important:** the workflow file must end up at the exact path
`.github/workflows/refresh.yml` in your repo (not just `refresh.yml` sitting
at the root) — GitHub only recognizes automation files at that specific
nested path.

## Day-to-day use

Open the site. Each of the three boxes (Cities, Counties, Metros) searches
only its own category — type a few letters of the place name, click the
match, and its table appears below. Hit **Copy table** to copy it as a
tab-separated block that pastes cleanly into a Word table or an Excel range.

## A note on how this evolved

Earlier versions of this tool tried to get current data straight from BLS's
bulk-download files, which turned out to be unreliable from GitHub's
automated servers (BLS blocks that traffic on their main domain, and the
fallback mirror lags behind by months). Switching the actual number-fetching
to BLS's official API (while keeping the bulk files just for figuring out
which places exist) solved this cleanly and doesn't depend on anyone's
personal computer or network connection.

