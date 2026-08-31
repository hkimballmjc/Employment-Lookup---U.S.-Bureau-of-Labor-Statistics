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
import os
import re
import sys
from datetime import datetime, timezone

import requests

# Edit this list to add/remove states. Use the 2-letter abbreviation.
TARGET_STATES = ["TX", "AR", "OK", "TN"]

BASE_CANDIDATES = [
    "https://download.bls.gov/pub/time.series/la/",
    "https://downloadt.bls.gov/pub/time.series/la/",
]
HEADERS = {
    # BLS's servers return 403 Forbidden to generic/script-like User-Agent
    # strings (including the default "python-requests/x.x"). A normal
    # browser-style User-Agent gets through fine for a manual/local run.
    # From cloud CI (GitHub Actions, etc.) BLS may still 403 based on IP
    # range regardless of headers -- that's what BASE_CANDIDATES above is
    # for: BLS mirrors this same data at a second hostname. NOTE: that
    # second hostname (downloadt.bls.gov) has been observed to lag behind
    # on recent months/years (missing data past a certain cutoff), so it's
    # listed second and only used as a fallback if the primary domain is
    # blocked -- always prefer download.bls.gov when it's reachable.
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
    to skip it -- combined statistical areas, metro divisions, regions,
    state totals, etc. are all intentionally excluded).

    Based on BLS's own consistent la.area naming conventions:
      county : "Travis County, TX"
      city   : "Austin city, TX"            -> displayed as "Austin, TX"
      metro  : "Austin-Round Rock-San Marcos, TX Metropolitan Statistical Area"

    BLS always appends a lowercase "city" (or "town" for New England) after
    every place name, even when the place's own proper name already ends in
    "City" (e.g. the raw name is "Oklahoma City city, OK" -- the first
    "City" is part of the real name, the second lowercase "city" is BLS's
    marker). We strip only that trailing marker for a clean display label.

    Returns (category, clean_label) or (None, None).
    """
    # Defensive normalization: BLS's raw file has occasionally been observed
    # with non-breaking spaces (\xa0) or doubled whitespace instead of plain
    # ASCII spaces, which would silently break naive regex matching.
    area_text = area_text.replace("\xa0", " ")
    area_text = re.sub(r"\s+", " ", area_text).strip()

    if area_text.endswith("Metropolitan Statistical Area"):
        label = area_text[: -len(" Metropolitan Statistical Area")].strip()
        return "metro", label
    if re.search(r"\b(County|Parish|Borough|Census Area),\s*[A-Za-z]{2}$", area_text):
        return "county", area_text
    if re.search(r"\bcity,\s*[A-Za-z]{2}$", area_text):
        label = re.sub(r"\s+city(?=,\s*[A-Za-z]{2}$)", "", area_text)
        label = _strip_parenthetical_annotation(label)
        return "city", label
    if re.search(r"\btown,\s*[A-Za-z]{2}$", area_text):
        label = re.sub(r"\s+town(?=,\s*[A-Za-z]{2}$)", "", area_text)
        label = _strip_parenthetical_annotation(label)
        return "city", label  # New England towns function like cities here
    if re.search(r"\(balance\),\s*[A-Za-z]{2}$", area_text):
        # Fallback for consolidated city-county governments named WITHOUT a
        # trailing "city" marker (e.g. "...metropolitan government
        # (balance), ST") -- Nashville itself turned out to actually use
        # "Nashville-Davidson (consolidated) city, TN", which the plain
        # city branch above already handles, but this stays as a safety
        # net in case another state's consolidated government is named
        # this way instead.
        label = re.sub(r"\s*(metropolitan government|urban county|unified government)?"
                        r"\s*\(balance\)", "", area_text, flags=re.IGNORECASE)
        label = re.sub(r"\s+", " ", label).strip()
        return "city", label
    return None, None


def _strip_parenthetical_annotation(label):
    """Remove BLS/Census annotations like '(consolidated)' or '(balance)'
    that sometimes appear inside an otherwise-normal city label (e.g.
    "Nashville-Davidson (consolidated), TN" -> "Nashville-Davidson, TN").
    Nobody searches for a city by including this kind of qualifier, and it
    also needs to be gone for coordinate-lookup matching against Census's
    own (unqualified) place names to work."""
    cleaned = re.sub(r"\s*\([^)]*\)", "", label)
    return re.sub(r"\s+", " ", cleaned).strip()


# BLS DOES publish a combined employment series for large "split" MSAs
# like Dallas-Fort Worth (confirmed directly against BLS's own published
# data: series LAUMT481910000000005, "Employed Persons in Dallas-Fort
# Worth-Arlington, TX (MSA)"). The gap is narrower than that: the la.area
# *index* file we're forced to pull from a secondary/lagging BLS mirror
# (since the primary domain blocks GitHub's servers) is simply missing a
# handful of area entries, even though their real series data exists fine.
# Rather than guess at every possible gap, we manually list any known-good
# series IDs here (verified against BLS's own regional press releases or
# FRED, which mirrors BLS's real published data) and add them to the
# catalog directly, bypassing the incomplete area index for just these.
KNOWN_METRO_SERIES_OVERRIDES = {
    "Dallas-Fort Worth-Arlington, TX": "LAUMT481910000000005",
}


def get_text(path):
    """Fetch a path (relative to the la/ directory) trying each BLS mirror
    domain in turn. Returns text from the first one that succeeds."""
    last_err = None
    for base in BASE_CANDIDATES:
        url = base + path
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            log(f"  fetched {path!r} from {base}")
            return r.text
        except requests.exceptions.HTTPError as e:
            log(f"  {base} -> {e}")
            last_err = e
            continue
    raise last_err


def discover_state_files():
    """Fetch the la/ directory listing and map state name -> data filename.
    Also returns the name of the most recent 'CurrentU' file, which BLS
    updates continuously with the newest months/years -- the per-state
    files (la.data.N.<State>) are apparently frozen as of some cutoff and
    do NOT get new years appended, so recent data (2025+ at time of
    writing) only exists in this separate file."""
    log("Fetching BLS directory listing...")
    listing = get_text("")

    files = re.findall(r"la\.data\.\d+\.[A-Za-z]+", listing)
    state_to_file = {}
    for fname in files:
        m = re.match(r"la\.data\.\d+\.([A-Za-z]+)$", fname)
        if m:
            state_to_file[m.group(1)] = fname

    # Find "la.data.0.CurrentUxx-yy" files and pick the one with the highest
    # (most recent) starting year. Two-digit years are resolved the normal
    # way: 00-49 -> 2000s, 50-99 -> 1900s, so "25-29" (2025) correctly beats
    # "90-94" (1990).
    current_files = re.findall(r"la\.data\.0\.CurrentU\d{2}-\d{2}", listing)
    current_file = None
    best_year = -1
    for fname in current_files:
        yy = int(fname[-5:-3])
        year = 2000 + yy if yy < 50 else 1900 + yy
        if year > best_year:
            best_year = year
            current_file = fname
    if current_file:
        log(f"  most recent 'Current' file: {current_file} (covers {best_year}+)")
    else:
        log("  WARNING: no 'la.data.0.CurrentUxx-yy' file found in listing "
            "-- recent-year data may be missing.")

    return state_to_file, current_file


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
    log(f"  la.area header columns: {header}")
    idx_code = header.index("area_code")
    idx_text = header.index("area_text")
    area_names = {}
    for row in reader:
        if len(row) <= max(idx_code, idx_text):
            continue
        # la.area's area_code column includes a leading 2-letter area-type
        # prefix (e.g. "ST0100000000000" = ST + 13-digit code), but the
        # area_code we extract from a series_id (series_id[5:18]) is just
        # the bare 13 digits with no prefix. Strip any leading letters here
        # so both sides use the same key.
        raw_code = row[idx_code].strip()
        code = re.sub(r"^[A-Za-z]+", "", raw_code)
        area_names[code] = row[idx_text].strip()
    log(f"Loaded {len(area_names)} area names.")
    sample_pairs = list(area_names.items())[:5]
    log(f"  sample (area_code, area_text) pairs: {sample_pairs}")
    return area_names


def fetch_state_employment_rows(state_file):
    """Stream a state's data file, keep only employment-measure rows.
    Returns list of (series_id, year, period, value, footnote). We keep
    the full series_id (not just the area_code portion) because we need
    it later to query the same exact series via the official BLS API."""
    log(f"Downloading {state_file} ...")
    r = None
    last_err = None
    for base in BASE_CANDIDATES:
        url = base + state_file
        try:
            candidate = requests.get(url, headers=HEADERS, timeout=300, stream=True)
            candidate.raise_for_status()
            r = candidate
            log(f"  fetched {state_file!r} from {base}")
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
            year = parts[1].strip()
            period = parts[2].strip()
            value = parts[3].strip()
            footnote = parts[4].strip() if len(parts) > 4 else ""
            rows.append((series_id, year, period, value, footnote))
    # flush any trailing partial bytes / last line without a newline
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        parts = buffer.split("\t")
        if len(parts) >= 4:
            series_id = parts[0].strip()
            if len(series_id) == 20 and series_id[18:20] == MEASURE_EMPLOYMENT:
                rows.append((series_id, parts[1].strip(), parts[2].strip(),
                             parts[3].strip(), parts[4].strip() if len(parts) > 4 else ""))
    log(f"  kept {len(rows)} employment rows from {state_file}")
    return rows


API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def fetch_values_via_api(series_ids, start_year, end_year, api_key=None):
    """Fetch actual current data for a list of series IDs via BLS's official
    Public API (not the bulk-download server) -- this is the piece that
    should always be current, since the bulk-file mirror reachable from
    cloud CI has been observed to lag behind by months.

    Returns: dict of series_id -> list of {year, period, periodName, value, footnote}
    """
    batch_size = 50 if api_key else 25
    all_results = {}
    batches = [series_ids[i:i + batch_size] for i in range(0, len(series_ids), batch_size)]
    log(f"Querying BLS API for {len(series_ids)} series in {len(batches)} "
        f"batch(es) of up to {batch_size}...")

    for i, batch in enumerate(batches):
        payload = {
            "seriesid": batch,
            "startyear": str(start_year),
            "endyear": str(end_year),
            "annualaverage": True,
        }
        if api_key:
            payload["registrationkey"] = api_key
        r = requests.post(API_URL, json=payload, headers=HEADERS, timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "REQUEST_SUCCEEDED":
            log(f"  WARNING: API batch {i+1}/{len(batches)} returned "
                f"status={body.get('status')} message={body.get('message')}")
            continue
        for series in body.get("Results", {}).get("series", []):
            sid = series.get("seriesID")
            data_points = []
            for d in series.get("data", []):
                value_str = d.get("value", "")
                data_points.append({
                    "year": int(d["year"]),
                    "period": d["period"],
                    "periodName": MONTH_LABELS.get(d["period"], d.get("periodName", d["period"])),
                    "value": None if value_str in ("", "-") else float(value_str),
                    "footnote": ",".join(fn.get("text", "") for fn in d.get("footnotes", []) if fn),
                })
            all_results[sid] = data_points
        log(f"  batch {i+1}/{len(batches)}: got data for "
            f"{len(body.get('Results', {}).get('series', []))} series")
    return all_results


def main():
    log(f"Target states: {TARGET_STATES}")

    api_key = os.environ.get("BLS_API_KEY", "").strip() or None
    if api_key:
        log("BLS_API_KEY found -- using registered API access (500 queries/day, 50 series/query)")
    else:
        log("No BLS_API_KEY set -- using unregistered API access (25 queries/day, "
            "25 series/query, 10yr max history). Set the BLS_API_KEY environment "
            "variable / GitHub secret for full access.")

    state_files, _current_file = discover_state_files()
    area_names = load_area_names()

    # --- Phase 1: build the CATALOG (which places exist, and their correct
    # BLS series ID) from the bulk per-state files. We deliberately ignore
    # the VALUES in these files -- only the structural info (which areas
    # exist, and each one's series_id) is used, since that doesn't go stale
    # even though the values reachable from cloud CI apparently do.
    categories = {"counties": {}, "cities": {}, "metros": {}}
    CATEGORY_KEY = {"county": "counties", "city": "cities", "metro": "metros"}

    for abbr in TARGET_STATES:
        state_name = STATE_NAME_BY_ABBR.get(abbr)
        if not state_name or state_name not in state_files:
            log(f"WARNING: could not find a BLS data file for state '{abbr}' "
                f"(looked for '{state_name}'). Skipping.")
            continue
        rows = fetch_state_employment_rows(state_files[state_name])

        # One representative series_id per area_code is all we need
        area_series_id = {}
        for series_id, year, period, value, footnote in rows:
            area_code = series_id[5:18]
            area_series_id.setdefault(area_code, series_id)

        kept = 0
        for area_code, series_id in area_series_id.items():
            area_text = area_names.get(area_code)
            if not area_text:
                continue
            category, label = classify_area(area_text)
            if not category:
                continue  # combined areas, divisions, regions, etc. -- skip
            categories[CATEGORY_KEY[category]][label] = {
                "state": abbr,
                "area_type": category,
                "matched_area_text": area_text,
                "series_id": series_id,
            }
            kept += 1
        log(f"  catalog: {kept} areas identified for {abbr}")

    # Add any known-good metros that were missing from the (incomplete)
    # secondary-mirror area index -- see KNOWN_METRO_SERIES_OVERRIDES above.
    for msa_label, series_id in KNOWN_METRO_SERIES_OVERRIDES.items():
        if msa_label in categories["metros"]:
            continue  # already found normally, no override needed
        categories["metros"][msa_label] = {
            "state": TARGET_STATES[0],  # best-effort; not used for lookups
            "area_type": "metro",
            "matched_area_text": (f"{msa_label} Metropolitan Statistical Area "
                                   f"(added directly -- missing from the mirror's area index, "
                                   f"but this is BLS's real published series ID)"),
            "series_id": series_id,
        }
        log(f"  added known override for '{msa_label}' (series {series_id}) -- "
            f"was missing from the area index")

    total_places = sum(len(c) for c in categories.values())
    log(f"Catalog complete: {total_places} total places across "
        f"{len(categories['counties'])} counties, {len(categories['cities'])} cities, "
        f"{len(categories['metros'])} metros")

    # --- Phase 2: fetch actual current VALUES for every cataloged series,
    # via the official API (not the bulk-download server).
    all_series_ids = [
        place["series_id"]
        for cat in categories.values()
        for place in cat.values()
        if place.get("series_id")
    ]
    current_year = datetime.now(timezone.utc).year
    start_year = current_year - 15  # comfortably covers the 11-year county view
    api_data = fetch_values_via_api(all_series_ids, start_year, current_year, api_key)

    # --- Phase 3: attach data + build views
    missing = 0
    for cat in categories.values():
        for label, place in cat.items():
            data = api_data.get(place["series_id"])
            if not data:
                missing += 1
                place["view"] = {"error": "no data returned from BLS API for this series"}
                continue
            data_desc = sorted(data, key=lambda d: (d["year"], d["period"]), reverse=True)
            data_desc = impute_missing_months(data_desc)
            data_desc.sort(key=lambda d: (d["year"], d["period"]), reverse=True)
            place["view"] = build_view(place["area_type"], data_desc)
    if missing:
        log(f"  WARNING: {missing}/{total_places} places got no data back from the API")

    # DIAGNOSTIC -- check how many cities are missing a 5-year-back
    # comparison, to distinguish "a few small/newer cities lack that much
    # history" from "something is systematically broken."
    city_places = list(categories["cities"].items())
    missing_5yr = [label for label, p in city_places
                   if p.get("view") and not p["view"].get("error")
                   and not p["view"].get("five_years_prior")]
    missing_3yr = [label for label, p in city_places
                   if p.get("view") and not p["view"].get("error")
                   and not p["view"].get("three_years_prior")]
    log(f"  DIAGNOSTIC -- cities missing a 3yr comparison: {len(missing_3yr)}/{len(city_places)}")
    log(f"  DIAGNOSTIC -- cities missing a 5yr comparison: {len(missing_5yr)}/{len(city_places)}")
    if missing_5yr:
        log(f"  DIAGNOSTIC -- sample cities missing 5yr data: {missing_5yr[:5]}")
    sample_label, sample_place = city_places[0] if city_places else (None, None)
    if sample_place and sample_place.get("view") and not sample_place["view"].get("error"):
        v = sample_place["view"]
        log(f"  DIAGNOSTIC -- sample city {sample_label!r}: "
            f"most_recent={v.get('most_recent')}, "
            f"three_years_prior={v.get('three_years_prior')}, "
            f"five_years_prior={v.get('five_years_prior')}")

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


def impute_missing_months(data_desc):
    """Detect years with exactly one missing month (like October 2025 during
    the government shutdown, when BLS did not publish that month's LAUS
    data) and fill the gap as the average of its immediate neighbors, then
    recompute that year's annual average using the imputed value so it
    reflects a true 12-month mean rather than BLS's own 11-month figure.
    Returns a new data_desc list with the fix applied; years with complete
    data or more than one gap are left untouched."""
    monthly = [d for d in data_desc if d["period"] != "M13" and d["value"] is not None]
    others = [d for d in data_desc if d["period"] == "M13" or d["value"] is None]

    by_year = {}
    for d in monthly:
        by_year.setdefault(d["year"], {})[d["period"]] = d

    all_months = [f"M{m:02d}" for m in range(1, 13)]
    imputed_entries = []
    recomputed_annuals = {}

    for year, months_present in by_year.items():
        missing = [m for m in all_months if m not in months_present]
        if len(missing) != 1:
            continue  # only handle a single-gap year
        gap = missing[0]
        gap_idx = all_months.index(gap)
        prev_month = all_months[gap_idx - 1] if gap_idx > 0 else None
        next_month = all_months[gap_idx + 1] if gap_idx < 11 else None
        if prev_month not in months_present or next_month not in months_present:
            continue  # can't impute without both neighbors present
        prev_val = months_present[prev_month]["value"]
        next_val = months_present[next_month]["value"]
        imputed_val = (prev_val + next_val) / 2

        imputed_entries.append({
            "year": year, "period": gap, "periodName": MONTH_LABELS[gap],
            "value": imputed_val,
            "footnote": (f"IMPUTED: {MONTH_LABELS[gap]} {year} was not published "
                         f"by BLS; estimated as the average of "
                         f"{MONTH_LABELS[prev_month]} and {MONTH_LABELS[next_month]} {year}."),
        })

        all_vals = [months_present[m]["value"] for m in all_months if m != gap]
        all_vals.append(imputed_val)
        recomputed_annuals[year] = {
            "year": year, "period": "M13", "periodName": "Annual Avg",
            "value": round(sum(all_vals) / 12, 1),
            "footnote": (f"RECOMPUTED: BLS's own annual average for {year} was "
                         f"based on only 11 months (missing {MONTH_LABELS[gap]}); "
                         f"this figure includes the imputed month above."),
        }

    result = list(monthly) + imputed_entries
    for d in others:
        if d["period"] == "M13" and d["year"] in recomputed_annuals:
            continue  # drop BLS's 11-month-based figure, we're replacing it
        result.append(d)
    result.extend(recomputed_annuals.values())
    return result


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

        def find_prior(years_back):
            return next((d for d in monthly
                         if d["year"] == most_recent["year"] - years_back
                         and d["period"] == most_recent["period"]), None)

        prior_3yr = find_prior(3)
        prior_5yr = find_prior(5)
        view = {
            "most_recent": most_recent,
            "three_years_prior": prior_3yr,
            "five_years_prior": prior_5yr,
        }
        if prior_3yr and prior_3yr["value"]:
            growth = (most_recent["value"] - prior_3yr["value"]) / prior_3yr["value"] / 3
            view["three_year_avg_annual_growth_rate"] = round(growth, 4)
        if prior_5yr and prior_5yr["value"]:
            growth = (most_recent["value"] - prior_5yr["value"]) / prior_5yr["value"] / 5
            view["five_year_avg_annual_growth_rate"] = round(growth, 4)
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
