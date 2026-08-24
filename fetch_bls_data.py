#!/usr/bin/env python3
"""
fetch_bls_data.py

Pulls NOT-seasonally-adjusted EMPLOYMENT data (LAUS measure code 05) for
EVERY county, city, and metro area in the states listed in TARGET_STATES
below, straight from BLS's official bulk data files (the same source
data.bls.gov itself is built on), and writes the result to bls_data.json
for the index.html lookup tool to read.

Usage:
    pip install requests
    python fetch_bls_data.py

Re-run this monthly (or set up the included GitHub Action to do it for you)
to keep bls_data.json current. BLS usually posts new LAUS numbers about
3 weeks after the reference month ends.

How it works
------------
BLS's Local Area Unemployment Statistics (LAUS) program publishes:
  - la.area          : area_code -> human-readable area name
  - la.data.N.<State> : every LAUS series for that state (all areas, all
                        measures, full history), one row per series/period

A LAUS series ID is structured as:
    LAU + <2-char area type> + <13-digit area code> + <2-digit measure code>
e.g. LAUCN040010000000005
     LAU | CN (county) | 0400100000000 (area code) | 05 (employment)

Measure codes: 03 = unemployment rate, 04 = unemployed, 05 = employed,
06 = labor force. We only keep 05.

Rather than requiring a hand-typed list of places, every area within a
target state is automatically bucketed into county / city / metro based on
BLS's own consistent naming conventions in la.area (e.g. "Travis County,
TX", "Austin city, TX", "Austin-Round Rock-San Marcos, TX Metropolitan
Statistical Area") -- see classify_area() below.

We don't hardcode which numbered file belongs to which state (BLS's internal
numbering isn't alphabetical or FIPS-based) -- instead we fetch the directory
listing and match state names, so this keeps working even if BLS renumbers
files.
"""

import codecs
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone

import requests

# Edit this list to add/remove states. Use the 2-letter abbreviation.
TARGET_STATES = ["TX", "AR", "OK", "TN"]

BASE_CANDIDATES = [
    "https://downloadt.bls.gov/pub/time.series/la/",
    "https://download.bls.gov/pub/time.series/la/",
]
HEADERS = {
    # BLS's servers return 403 Forbidden to generic/script-like User-Agent
    # strings (including the default "python-requests/x.x"). A normal
    # browser-style User-Agent gets through fine for a manual/local run.
    # From cloud CI (GitHub Actions, etc.) BLS may still 403 based on IP
    # range regardless of headers -- that's what BASE_CANDIDATES above is
    # for: BLS mirrors this same data at a second hostname that isn't
    # always subject to the same block.
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MEASURE_EMPLOYMENT = "05"

MONTH_LABELS = {
    "M01": "Jan", "M02": "Feb", "M03": "Mar", "M04": "Apr",
    "M05": "May", "M06": "Jun", "M07": "Jul", "M08": "Aug",
    "M09": "Sep", "M10": "Oct", "M11": "Nov", "M12": "Dec",
    "M13": "Annual Avg",
}


def log(msg):
    print(f"[fetch_bls_data] {msg}", file=sys.stderr)


def classify_area(area_text):
    """Classify a BLS area name into 'county', 'city', or 'metro' (or None
    to skip it -- combined areas, metro divisions, regions, state totals,
    etc. are all intentionally excluded).

    Based on BLS's own consistent la.area naming conventions:
      county : "Travis County, TX"        (also Parish/Borough/Census Area
                                            for LA/AK, included for safety)
      city   : "Austin city, TX"          (lowercase "city," is BLS's marker
                                            for incorporated cities/towns)
      metro  : "Austin-Round Rock-San Marcos, TX Metropolitan Statistical Area"

    Returns (category, clean_label) or (None, None).
    """
    if area_text.endswith("Metropolitan Statistical Area"):
        label = area_text[: -len(" Metropolitan Statistical Area")]
        return "metro", label
    if re.search(r"\b(County|Parish|Borough|Census Area), [A-Z]{2}$", area_text):
        return "county", area_text
    if re.search(r"\bcity, [A-Z]{2}$", area_text):
        return "city", area_text
    if re.search(r"\btown, [A-Z]{2}$", area_text):
        return "city", area_text  # New England towns function like cities here
    return None, None


def get_text(path):
    """Fetch a path (relative to the la/ directory) trying each BLS mirror
    domain in turn. Returns text from the first one that succeeds."""
    last_err = None
    for base in BASE_CANDIDATES:
        url = base + path
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            return r.text
        except requests.exceptions.HTTPError as e:
            log(f"  {base} -> {e}")
            last_err = e
            continue
    raise last_err


def discover_state_files():
    """Fetch the la/ directory listing and map state name -> data filename."""
    log("Fetching BLS directory listing...")
    listing = get_text("")
    files = re.findall(r"la\.data\.\d+\.[A-Za-z]+", listing)
    state_to_file = {}
    for fname in files:
        m = re.match(r"la\.data\.\d+\.([A-Za-z]+)$", fname)
        if m:
            state_to_file[m.group(1)] = fname
    return state_to_file


STATE_NAME_BY_ABBR = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "DC", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "NewHampshire", "NJ": "NewJersey", "NM": "NewMexico",
    "NY": "NewYork", "NC": "NorthCarolina", "ND": "NorthDakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "RhodeIsland",
    "SC": "SouthCarolina", "SD": "SouthDakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "WestVirginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def load_area_names():
    """area_code -> area_text, from la.area."""
    log("Fetching la.area (area code -> name lookup)...")
    text = get_text("la.area")
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    header = next(reader)
    header = [h.strip() for h in header]
    idx_code = header.index("area_code")
    idx_text = header.index("area_text")
    area_names = {}
    for row in reader:
        if len(row) <= max(idx_code, idx_text):
            continue
        area_names[row[idx_code].strip()] = row[idx_text].strip()
    log(f"Loaded {len(area_names)} area names.")
    return area_names


def fetch_state_employment_rows(state_file):
    """Stream a state's data file, keep only employment-measure rows.
    Returns list of (area_code, year, period, value, footnote)."""
    log(f"Downloading {state_file} ...")
    r = None
    last_err = None
    for base in BASE_CANDIDATES:
        url = base + state_file
        try:
            candidate = requests.get(url, headers=HEADERS, timeout=300, stream=True)
            candidate.raise_for_status()
            r = candidate
            break
        except requests.exceptions.HTTPError as e:
            log(f"  {base} -> {e}")
            last_err = e
            continue
    if r is None:
        raise last_err

    rows = []
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    buffer = ""
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        buffer += decoder.decode(chunk)
        *lines, buffer = buffer.split("\n")
        for line in lines:
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            series_id = parts[0].strip()
            if len(series_id) != 20:
                continue
            measure_code = series_id[18:20]
            if measure_code != MEASURE_EMPLOYMENT:
                continue
            area_code = series_id[5:18]
            year = parts[1].strip()
            period = parts[2].strip()
            value = parts[3].strip()
            footnote = parts[4].strip() if len(parts) > 4 else ""
            rows.append((area_code, year, period, value, footnote))
    # flush any trailing partial bytes / last line without a newline
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        parts = buffer.split("\t")
        if len(parts) >= 4:
            series_id = parts[0].strip()
            if len(series_id) == 20 and series_id[18:20] == MEASURE_EMPLOYMENT:
                rows.append((series_id[5:18], parts[1].strip(), parts[2].strip(),
                             parts[3].strip(), parts[4].strip() if len(parts) > 4 else ""))
    log(f"  kept {len(rows)} employment rows from {state_file}")
    return rows


def main():
    log(f"Target states: {TARGET_STATES}")
    state_files = discover_state_files()
    area_names = load_area_names()

    categories = {"counties": {}, "cities": {}, "metros": {}}
    CATEGORY_KEY = {"county": "counties", "city": "cities", "metro": "metros"}

    for abbr in TARGET_STATES:
        state_name = STATE_NAME_BY_ABBR.get(abbr)
        if not state_name or state_name not in state_files:
            log(f"WARNING: could not find a BLS data file for state '{abbr}' "
                f"(looked for '{state_name}'). Skipping.")
            continue
        rows = fetch_state_employment_rows(state_files[state_name])

        # Group by area_code first so each area's history accumulates together
        by_area = {}
        for area_code, year, period, value, footnote in rows:
            by_area.setdefault(area_code, []).append({
                "year": int(year),
                "period": period,
                "periodName": MONTH_LABELS.get(period, period),
                "value": None if value in ("", "-") else float(value),
                "footnote": footnote,
            })

        kept = 0
        for area_code, data in by_area.items():
            area_text = area_names.get(area_code)
            if not area_text:
                continue
            category, label = classify_area(area_text)
            if not category:
                continue  # combined areas, divisions, regions, etc. -- skip
            data_desc = sorted(data, key=lambda d: (d["year"], d["period"]), reverse=True)
            categories[CATEGORY_KEY[category]][label] = {
                "state": abbr,
                "area_type": category,
                "matched_area_text": area_text,
                "view": build_view(category, data_desc),
            }
            kept += 1
        log(f"  classified {kept} areas for {abbr} "
            f"(counties/cities/metros combined)")

    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "measure": "Employment (not seasonally adjusted), BLS LAUS",
        "states": TARGET_STATES,
        "counties": categories["counties"],
        "cities": categories["cities"],
        "metros": categories["metros"],
    }

    with open("bls_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    log(f"Done. counties={len(categories['counties'])} "
        f"cities={len(categories['cities'])} metros={len(categories['metros'])}. "
        f"Wrote bls_data.json.")


def build_view(area_type, data_desc):
    """data_desc is sorted most-recent-first. Build the exact table shape
    Hannah's HUD workbook uses for each area type."""
    monthly = [d for d in data_desc if d["period"] != "M13" and d["value"] is not None]
    annual = [d for d in data_desc if d["period"] == "M13" and d["value"] is not None]
    annual_asc = sorted(annual, key=lambda d: d["year"])  # oldest -> newest

    if area_type == "city":
        if not monthly:
            return {"error": "no monthly data found"}
        most_recent = monthly[0]
        prior = next((d for d in monthly
                      if d["year"] == most_recent["year"] - 3
                      and d["period"] == most_recent["period"]), None)
        view = {"most_recent": most_recent, "three_years_prior": prior}
        if prior and prior["value"]:
            growth = (most_recent["value"] - prior["value"]) / prior["value"] / 3
            view["three_year_avg_annual_growth_rate"] = round(growth, 4)
        return view

    if area_type == "metro":
        last6 = annual_asc[-6:]
        rows = []
        for i, d in enumerate(last6):
            row = {"year": d["year"], "jobs": d["value"], "growth": None, "growth_pct": None}
            if i > 0:
                prev = last6[i - 1]["value"]
                row["growth"] = d["value"] - prev
                row["growth_pct"] = round((d["value"] - prev) / prev, 4)
            rows.append(row)
        total_growth = None
        avg_annual_growth = None
        if len(last6) >= 2 and last6[0]["value"]:
            total_growth = last6[-1]["value"] - last6[0]["value"]
            avg_annual_growth = round(total_growth / last6[0]["value"] / (len(last6) - 1), 4)
        return {"annual_6yr": rows, "total_growth": total_growth,
                "avg_annual_growth": avg_annual_growth}

    if area_type == "county":
        last11 = annual_asc[-11:]
        rows = []
        for i, d in enumerate(last11):
            row = {"year": d["year"], "jobs": d["value"], "growth_pct": None}
            if i > 0:
                prev = last11[i - 1]["value"]
                row["growth_pct"] = round((d["value"] - prev) / prev, 4)
            rows.append(row)
        ytd = monthly[0] if monthly else None

        def span_avg(n):
            if len(last11) >= n + 1 and last11[-(n + 1)]["value"]:
                start = last11[-(n + 1)]["value"]
                end = last11[-1]["value"]
                return round((end - start) / start / n, 4)
            return None

        return {
            "ytd": ytd,
            "annual_11yr": rows,
            "avg_annual_growth_10yr": span_avg(10),
            "avg_annual_growth_5yr": span_avg(5),
        }

    return {"error": f"unknown area_type '{area_type}'"}


if __name__ == "__main__":
    main()
