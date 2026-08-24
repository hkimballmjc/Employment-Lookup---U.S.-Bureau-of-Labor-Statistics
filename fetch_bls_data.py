#!/usr/bin/env python3
"""
fetch_bls_data.py

Pulls NOT-seasonally-adjusted EMPLOYMENT data (LAUS measure code 05) for the
places listed in places_config.csv, straight from BLS's official bulk data
files (the same source data.bls.gov itself is built on), and writes the
result to bls_data.json for the index.html lookup tool to read.

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

We don't hardcode which numbered file belongs to which state (BLS's internal
numbering isn't alphabetical or FIPS-based) -- instead we fetch the directory
listing and match state names, so this keeps working even if BLS renumbers
files.
"""

import csv
import io
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests

BASE = "https://download.bls.gov/pub/time.series/la/"
HEADERS = {
    # BLS blocks requests with no / generic User-Agent. Put a real contact
    # in here if you're running this on a schedule (courtesy to BLS, not
    # strictly required for occasional manual runs).
    "User-Agent": "Mozilla/5.0 (compatible; HUD-market-research-tool/1.0)"
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


def get_text(url):
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.text


def discover_state_files():
    """Fetch the la/ directory listing and map state name -> data filename."""
    log("Fetching BLS directory listing...")
    listing = get_text(BASE)
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
    text = get_text(BASE + "la.area")
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


def load_places_config(path="places_config.csv"):
    places = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            places.append({
                "label": row["label"].strip(),
                "state": row["state"].strip().upper(),
                "area_type": row["area_type"].strip().lower(),
                "match_text": row["match_text"].strip(),
            })
    return places


def fetch_state_employment_rows(state_file):
    """Stream a state's data file, keep only employment-measure rows.
    Returns list of (area_code, year, period, value, footnote)."""
    url = BASE + state_file
    log(f"Downloading {state_file} ...")
    r = requests.get(url, headers=HEADERS, timeout=300, stream=True)
    r.raise_for_status()

    rows = []
    buffer = ""
    for chunk in r.iter_content(chunk_size=1024 * 1024, decode_unicode=True):
        if chunk is None:
            continue
        buffer += chunk
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
    log(f"  kept {len(rows)} employment rows from {state_file}")
    return rows


def main():
    places = load_places_config()
    needed_states = sorted({p["state"] for p in places})
    log(f"Places configured: {len(places)}, states needed: {needed_states}")

    state_files = discover_state_files()
    area_names = load_area_names()

    # area_code -> area_text, restricted to states we actually need (still
    # need the full la.area file since it isn't split by state, but we only
    # do this lookup once).

    result_places = {p["label"]: {
        "state": p["state"],
        "area_type": p["area_type"],
        "match_text": p["match_text"],
        "matched_area_text": None,
        "series_id": None,
        "data": [],
    } for p in places}

    for abbr in needed_states:
        state_name = STATE_NAME_BY_ABBR.get(abbr)
        if not state_name or state_name not in state_files:
            log(f"WARNING: could not find a BLS data file for state '{abbr}' "
                f"(looked for '{state_name}'). Skipping.")
            continue
        rows = fetch_state_employment_rows(state_files[state_name])

        # Build area_code -> matched place labels, for just this state's places
        state_places = [p for p in places if p["state"] == abbr]

        for area_code, year, period, value, footnote in rows:
            area_text = area_names.get(area_code)
            if not area_text:
                continue
            for p in state_places:
                if p["match_text"].lower() in area_text.lower():
                    entry = result_places[p["label"]]
                    entry["matched_area_text"] = area_text
                    entry["series_id"] = f"LAU..{area_code}05"
                    entry["data"].append({
                        "year": int(year),
                        "period": period,
                        "periodName": MONTH_LABELS.get(period, period),
                        "value": None if value in ("", "-") else float(value),
                        "footnote": footnote,
                    })

    # Sort each place's data chronologically, most recent first
    for label, entry in result_places.items():
        entry["data"].sort(key=lambda d: (d["year"], d["period"]), reverse=True)
        if not entry["matched_area_text"]:
            log(f"WARNING: no match found for '{label}' "
                f"(match_text='{entry['match_text']}'). Check places_config.csv.")
            continue
        entry["view"] = build_view(entry["area_type"], entry["data"])

    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "measure": "Employment (not seasonally adjusted), BLS LAUS",
        "places": result_places,
    }

    with open("bls_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    matched = sum(1 for e in result_places.values() if e["matched_area_text"])
    log(f"Done. Matched {matched}/{len(places)} places. Wrote bls_data.json.")


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
