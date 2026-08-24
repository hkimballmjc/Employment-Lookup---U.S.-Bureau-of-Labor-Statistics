# Employment Lookup (BLS LAUS)

Type a place, get its not-seasonally-adjusted employment numbers, formatted
to match your HUD concept package tables, ready to copy straight into Word
or Excel. Built on BLS's own official bulk data files (the same source
behind data.bls.gov) — no scraping, no rate limits, no API key.

**Each area type shows exactly what your workbook needs, no more:**
- **City** — most recent month's employment, the same month 3 years prior,
  and the 3-year average annual growth rate (matches "Austin Employment Data").
- **County** — 11 years of annual averages, the most-recent-month YTD figure,
  and 10-yr / 5-yr average annual growth (matches "Job Estimates Summary Table").
- **Metro (MSA)** — 6 years of annual averages with year-over-year growth
  and $ / % totals (matches "MSA Employment Data").

Verified against the exact numbers in your Travis County / Austin worksheet
(1.31% city growth, 3.32%/4.70% county averages, 5.97% MSA average all
reproduce correctly) — see fetch_bls_data.py's build_view() function if you
ever want to tweak the math.

## How it's structured

- `places_config.csv` — the list of places you want. Edit this to add/remove places.
- `fetch_bls_data.py` — downloads real numbers from BLS and writes `bls_data.json`.
- `bls_data.json` — the actual data (generated file — don't hand-edit).
- `index.html` — the search page you actually use day to day.
- `.github/workflows/refresh.yml` — has GitHub auto-run the fetch monthly.

## One-time setup

1. **Run the fetch script once**, locally, to generate real data:
   ```bash
   pip install -r requirements.txt
   python fetch_bls_data.py
   ```
   This takes a few minutes — it downloads BLS's full state data files for
   TX, AR, and OK and filters them down to just employment figures for your
   configured places.

2. Check the terminal output. If a place shows a `WARNING: no match found`,
   open `places_config.csv` and adjust that row's `match_text` — see
   "Adding a new place" below.

3. **Host it on GitHub Pages** (free):
   - Create a new GitHub repo, push these files to it.
   - Repo Settings → Pages → Deploy from branch → `main` / root.
   - Your tool is now live at `https://<yourusername>.github.io/<reponame>/`.

4. The included GitHub Action (`.github/workflows/refresh.yml`) re-runs the
   fetch automatically on the 25th of each month and commits the refreshed
   `bls_data.json`, so the site stays current without you doing anything.
   You can also trigger it manually anytime from the repo's "Actions" tab.

## Day-to-day use

Open the site, start typing a place name (e.g. "League City"), click the
match, pick a date range (12mo / 36mo / 5yr / full history / annual
averages), and hit **Copy table** — it copies as a tab-separated table that
pastes cleanly into a Word table or an Excel range.

## Adding a new place

Open `places_config.csv` and add a row:

```
label,state,area_type,match_text
Waco - McLennan County,TX,county,"McLennan County, TX"
```

- `label` — whatever you want to see in search (your own naming).
- `state` — 2-letter abbreviation.
- `area_type` — county / city / metro (informational only, doesn't affect matching).
- `match_text` — must be a **substring of BLS's own official area name**. To find
  the exact wording BLS uses:
  - Counties: almost always `"<County Name> County, <ST>"`.
  - Cities: almost always `"<City Name> city, <ST>"` (note the lowercase "city").
  - Metro areas (MSAs): the full official CBSA title, e.g.
    `"Dallas-Fort Worth-Arlington, TX"` — check the exact wording at
    https://www.bls.gov/lau/lausmsa.htm if you're not sure of a metro's full name.

Then re-run `python fetch_bls_data.py` (or wait for the monthly auto-refresh,
or run it manually from the Actions tab so you don't have to wait).

## A note on accuracy

I built and syntax-checked this script, but I don't have network access to
bls.gov from where I'm running, so I haven't been able to execute it
end-to-end against live data myself. Run it once and skim the warnings —
if any of your starter places (Austin, League City, Little Rock, Oklahoma
City, Abilene, Conway, Moore) don't match, send me the warning text and
I'll fix the matching logic.
