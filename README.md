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

## How it's structured

- `fetch_bls_data.py` — downloads BLS's raw data for TX/AR/OK/TN and
  automatically sorts every area into city/county/metro based on BLS's own
  naming conventions (e.g. "Travis County, TX" → county, "Austin city, TX" →
  city, "...Metropolitan Statistical Area" → metro). Writes `bls_data.json`.
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

1. **Run the fetch script once**, locally, to generate real data:
   ```bash
   pip install -r requirements.txt
   python fetch_bls_data.py
   ```
   This downloads BLS's full data files for TX, AR, OK, and TN, which takes
   a few minutes, and classifies every area it finds.

2. **Host it on GitHub Pages** (free):
   - Create a new GitHub repo, push these files to it.
   - Repo Settings → Pages → Deploy from branch → `main` / root.
   - Your tool is now live at `https://<yourusername>.github.io/<reponame>/`.

3. The included GitHub Action (`.github/workflows/refresh.yml`) re-runs the
   fetch automatically on the 25th of each month and commits the refreshed
   `bls_data.json`, so the site stays current without you doing anything.
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

## A note on BLS blocking automated requests

BLS's servers reject requests that don't look like they're coming from a
real browser (a 403 error) — the script already sends browser-style
headers and tries two different BLS mirror domains to work around this.
If you ever see 403 errors in the GitHub Actions log again, it may mean
BLS has changed something on their end; running the script from your own
computer (a normal home/office connection, not a data-center one) is the
reliable fallback — just upload the resulting `bls_data.json` to GitHub by
hand afterward.

