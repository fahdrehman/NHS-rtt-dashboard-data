#!/usr/bin/env python3
"""
Fetch the latest NHS England Referral to Treatment (RTT) full data extract,
compute headline metrics for England's acute NHS trusts, and write a compact
summary JSON (plus an appended history file) that a dashboard can consume.

This script is designed to run inside a GitHub Actions runner (which has
normal internet access) on a monthly schedule. It is intentionally
dependency-light: requests + beautifulsoup4 + pandas + openpyxl.

In addition to the latest month, it will backfill a handful of the most
recent prior months into "history" (national_acute-level metrics only) the
first few times it runs, so trend charts have more than one data point.
Once history has TARGET_HISTORY_MONTHS distinct months, backfilling stops
automatically and each run only processes the latest month.
"""
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (rtt-dashboard-bot; +https://github.com)"}
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# How many distinct months we want sitting in history. Backfilling only
# happens while history has fewer months than this - once we reach it,
# each run just adds the latest month going forward (cheap, one download).
TARGET_HISTORY_MONTHS = 4
# Safety cap on how many extra full-extract downloads a single run will do
# to backfill, so a fresh repo doesn't try to download every month NHS has
# ever published in one go.
MAX_BACKFILL_DOWNLOADS_PER_RUN = 4


def financial_year_slugs(today=None):
    """Return candidate NHS 'rtt-data-YYYY-YY' page slugs to try, most likely first."""
    today = today or datetime.utcnow()
    fy_start = today.year if today.month >= 4 else today.year - 1
    current = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    previous = f"{fy_start - 1}-{str(fy_start)[-2:]}"
    return [current, previous]


def find_all_month_links(slug):
    """Scrape an NHS England RTT data page for every month's full-extract link found.

    Returns a list of {"period_date": datetime, "csv_zip_url": str}, most
    recent first. Used both to find the latest month and to backfill prior
    months.
    """
    url = f"https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/rtt-data-{slug}/"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")

    candidates = []  # (date, href, text)
    month_pat = re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[\-_ ]?(\d{2})", re.I)

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        haystack = f"{text} {href}"
        if not re.search(r"full[\-_]?csv|full[\-_]?extract", haystack, re.I):
            continue
        m = month_pat.search(haystack)
        if not m:
            continue
        mon = MONTH_ABBR.index(m.group(1).title()) + 1
        yr = 2000 + int(m.group(2))
        if not href.startswith("http"):
            href = "https://www.england.nhs.uk" + href
        candidates.append((datetime(yr, mon, 1), href, text))

    if not candidates:
        return []

    candidates.sort(key=lambda c: c[0], reverse=True)
    return [{"period_date": d, "csv_zip_url": href} for d, href, _ in candidates]


def find_latest_links(slug):
    """Return just the most recent month's link for this financial-year page."""
    all_links = find_all_month_links(slug)
    return all_links[0] if all_links else None


def download_full_extract(csv_zip_url):
    resp = requests.get(csv_zip_url, headers=HEADERS, timeout=180)
    resp.raise_for_status()
    frames = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        print(f"DEBUG: zip contains {len(names)} csv file(s): {names}", file=sys.stderr)
        for name in names:
            with zf.open(name) as f:
                frame = pd.read_csv(f, low_memory=False)
            total_col = next((c for c in frame.columns if c.strip().lower() == "total"), None)
            print(f"DEBUG: {name} -> {len(frame)} rows"
                  + (f", Total column sum = {frame[total_col].sum():,.0f}" if total_col else ", no Total column"),
                  file=sys.stderr)
            frames.append(frame)
    if not frames:
        raise RuntimeError("No CSV found inside the RTT full extract zip")
    combined = pd.concat(frames, ignore_index=True)
    print(f"DEBUG: combined shape = {combined.shape}, columns = {list(combined.columns)}", file=sys.stderr)
    return combined


def normalise_columns(df):
    df = df.copy()
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    return df


def band_upper_bound(col_name):
    """'Gt 17 To 18 Weeks SUM 1' -> 18. 'Gt 104 Weeks SUM 1' -> 9999 (open-ended)."""
    m = re.search(r"Gt\s*(\d+)\s*To\s*(\d+)\s*Weeks", col_name, re.I)
    if m:
        return int(m.group(2))
    m = re.search(r"Gt\s*(\d+)\s*Weeks", col_name, re.I)
    if m:
        return 9999
    return None


def band_lower_bound(col_name):
    m = re.search(r"Gt\s*(\d+)\s*To\s*(\d+)\s*Weeks", col_name, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"Gt\s*(\d+)\s*Weeks", col_name, re.I)
    if m:
        return int(m.group(1))
    return None


def get_band_columns(df):
    cols = [c for c in df.columns if re.match(r"Gt\s*\d", c, re.I)]
    cols = [c for c in cols if band_upper_bound(c) is not None]
    cols.sort(key=band_lower_bound)
    return cols


def estimate_median_weeks(row, band_cols, total):
    """Linear interpolation of the median from banded (1-week wide) counts."""
    if total <= 0:
        return None
    half = total / 2.0
    cum = 0.0
    for c in band_cols:
        lo = band_lower_bound(c)
        hi = band_upper_bound(c)
        width = 1 if hi == 9999 else (hi - lo)
        n = row[c] if not pd.isna(row[c]) else 0.0
        if cum + n >= half:
            if n == 0:
                return float(lo)
            frac = (half - cum) / n
            return float(round(lo + frac * width, 1))
        cum += n
    return None


def normalise_name(name):
    name = name.upper()
    name = re.sub(r"[^A-Z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def load_region_map():
    """trust_name (normalised) -> NHS England region, or {} if the reference file is missing."""
    path = DATA_DIR / "trust_regions.csv"
    if not path.exists():
        print("DEBUG: no data/trust_regions.csv found - regions will be omitted", file=sys.stderr)
        return {}
    regions_df = pd.read_csv(path)
    return {
        normalise_name(row["trust_name"]): row["region"]
        for _, row in regions_df.iterrows()
    }


def compute_metrics(df, band_cols, acute_names_norm, region_map=None):
    region_map = region_map or {}

    df = df[df["RTT Part Description"].astype(str).str.strip()
            .str.lower() == "incomplete pathways"].copy()

    # NHS's RTT extract includes a "Treatment Function Code" 999 row per
    # provider+commissioner, which is a pre-computed rollup ("Total") across
    # that provider+commissioner's actual specialty rows - not a real
    # specialty. Left in, it silently doubles every total. Drop it here so
    # we only sum genuine per-specialty rows.
    is_rollup = (
        df["Treatment Function Code"].astype(str).str.contains(r"999", na=False)
        | df["Treatment Function Name"].astype(str).str.strip().str.lower().eq("total")
    )
    print(f"DEBUG: dropping {int(is_rollup.sum())} Treatment Function 'Total' rollup rows "
          f"(of {len(df)}) to avoid double-counting", file=sys.stderr)
    df = df[~is_rollup].copy()

    print(f"DEBUG: after filtering to Incomplete Pathways (excl. rollup rows): {len(df)} rows", file=sys.stderr)

    for c in band_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    within18_cols = [c for c in band_cols if band_upper_bound(c) <= 18]
    over52_cols = [c for c in band_cols if band_lower_bound(c) >= 52]

    df["_total"] = df[band_cols].sum(axis=1)
    df["_within18"] = df[within18_cols].sum(axis=1)
    df["_over52"] = df[over52_cols].sum(axis=1)

    grouped = df.groupby(["Provider Org Code", "Provider Org Name"], as_index=False)[
        band_cols + ["_total", "_within18", "_over52"]
    ].sum()

    grouped["_name_norm"] = grouped["Provider Org Name"].map(normalise_name)
    grouped["is_acute"] = grouped["_name_norm"].isin(acute_names_norm)
    grouped["_region"] = grouped["_name_norm"].map(region_map)

    def build(sub):
        total = float(sub["_total"].sum())
        within18 = float(sub["_within18"].sum())
        over52 = float(sub["_over52"].sum())
        band_sums = sub[band_cols].sum()
        median = estimate_median_weeks(band_sums, band_cols, total)
        return {
            "waiting_list": int(round(total)),
            "pct_within_18wk": round(100.0 * within18 / total, 1) if total else None,
            "over_52wk": int(round(over52)),
            "median_weeks": median,
        }

    national_all = build(grouped)
    acute_rows = grouped[grouped["is_acute"]]
    national_acute = build(acute_rows)

    trusts = []
    for _, r in acute_rows.iterrows():
        m = build(pd.DataFrame([r]))
        trusts.append({
            "code": r["Provider Org Code"],
            "name": r["Provider Org Name"],
            "region": r["_region"] if pd.notna(r["_region"]) else None,
            **m,
        })
    trusts.sort(key=lambda t: t["waiting_list"], reverse=True)

    regions = []
    mapped_acute_rows = acute_rows[acute_rows["_region"].notna()]
    for region_name, sub in mapped_acute_rows.groupby("_region"):
        m = build(sub)
        regions.append({"region": region_name, "trust_count": int(len(sub)), **m})
    regions.sort(key=lambda r: r["region"])

    unmatched = sorted(
        grouped[(~grouped["is_acute"]) & grouped["Provider Org Name"].str.contains(
            "NHS TRUST|NHS FOUNDATION TRUST", case=False, na=False)]["Provider Org Name"].unique().tolist()
    )

    return national_all, national_acute, trusts, unmatched, regions


def process_period(csv_zip_url, band_cols_cache, acute_names_norm, region_map):
    """Download + compute for one period. Returns (national_all, national_acute, trusts, unmatched, regions, band_cols)."""
    df = download_full_extract(csv_zip_url)
    df = normalise_columns(df)
    band_cols = get_band_columns(df)
    if not band_cols:
        raise RuntimeError("Could not find weeks-waited band columns in the extract")
    national_all, national_acute, trusts, unmatched, regions = compute_metrics(
        df, band_cols, acute_names_norm, region_map)
    return national_all, national_acute, trusts, unmatched, regions


def main():
    slugs = financial_year_slugs()

    all_candidates = []
    for slug in slugs:
        all_candidates.extend(find_all_month_links(slug))

    if not all_candidates:
        print("Could not find any RTT data links on any candidate page.", file=sys.stderr)
        sys.exit(1)

    # De-duplicate by period (a month can theoretically appear on more than
    # one FY page near a financial-year boundary) and sort most recent first.
    seen = set()
    candidates = []
    for c in sorted(all_candidates, key=lambda c: c["period_date"], reverse=True):
        lbl = c["period_date"].strftime("%Y-%m")
        if lbl in seen:
            continue
        seen.add(lbl)
        candidates.append(c)

    found = candidates[0]
    period_date = found["period_date"]
    period_label = period_date.strftime("%Y-%m")
    print(f"Latest period found: {period_label} -> {found['csv_zip_url']}")

    summary_path = DATA_DIR / "rtt_summary.json"
    existing = json.loads(summary_path.read_text()) if summary_path.exists() else {"history": []}
    history = existing.get("history", [])

    acute_names = pd.read_csv(DATA_DIR / "acute_trusts.csv")["trust_name"].tolist()
    acute_names_norm = set(normalise_name(n) for n in acute_names)
    region_map = load_region_map()

    national_all, national_acute, trusts, unmatched, regions = process_period(
        found["csv_zip_url"], None, acute_names_norm, region_map)

    history = [h for h in history if h.get("period") != period_label]
    history.append({"period": period_label, **national_acute})

    # Backfill a handful of the most recent prior months (national_acute
    # metrics only - the full trust table is only kept for the latest
    # month) until history has TARGET_HISTORY_MONTHS distinct entries, or
    # we run out of candidates, or we hit the per-run download cap.
    have_periods = {h["period"] for h in history}
    downloads_used = 0
    for c in candidates[1:]:
        if len(have_periods) >= TARGET_HISTORY_MONTHS:
            break
        if downloads_used >= MAX_BACKFILL_DOWNLOADS_PER_RUN:
            print(f"DEBUG: hit backfill download cap ({MAX_BACKFILL_DOWNLOADS_PER_RUN}) for this run, "
                  f"will continue backfilling on future runs", file=sys.stderr)
            break
        lbl = c["period_date"].strftime("%Y-%m")
        if lbl in have_periods:
            continue
        print(f"DEBUG: backfilling {lbl} -> {c['csv_zip_url']}", file=sys.stderr)
        try:
            _, backfill_acute, _, _, _ = process_period(
                c["csv_zip_url"], None, acute_names_norm, region_map)
        except Exception as exc:  # noqa: BLE001 - a bad backfill month shouldn't fail the whole run
            print(f"DEBUG: backfill of {lbl} failed, skipping: {exc}", file=sys.stderr)
            continue
        history.append({"period": lbl, **backfill_acute})
        have_periods.add(lbl)
        downloads_used += 1

    history.sort(key=lambda h: h["period"])

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": period_label,
        "period_display": period_date.strftime("%B %Y"),
        "source_url": found["csv_zip_url"],
        "national_all_providers": national_all,
        "national_acute": national_acute,
        "trusts": trusts,
        "regions": regions,
        "history": history,
    }
    summary_path.write_text(json.dumps(out, indent=2))
    (DATA_DIR / "unmatched_nhs_trust_providers.json").write_text(json.dumps(unmatched, indent=2))
    print(f"Wrote {summary_path} with {len(trusts)} acute trusts across {len(regions)} regions, "
          f"{len(history)} months of history. "
          f"{len(unmatched)} non-acute NHS Trust providers seen (not included).")


if __name__ == "__main__":
    main()
