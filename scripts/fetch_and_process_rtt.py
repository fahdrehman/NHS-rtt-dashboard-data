#!/usr/bin/env python3
"""
Fetch the latest NHS England Referral to Treatment (RTT) full data extract,
compute headline metrics for England's acute NHS trusts, and write a compact
summary JSON (plus an appended history file) that a dashboard can consume.

This script is designed to run inside a GitHub Actions runner (which has
normal internet access) on a monthly schedule. It is intentionally
dependency-light: requests + beautifulsoup4 + pandas + openpyxl.

The zip link(s) are normally supplied by the workflow (RTT_ZIP_URL), because
NHS England's pages now block scripts with a browser check. Each run keeps a
month-by-month history nationally and for every acute trust.
"""
import io
import json
import os
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

import time

# NHS England's site started refusing requests that identify as a bot (Sept
# 2026), so send ordinary browser headers instead.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def http_get(url, timeout, attempts=4):
    """GET with retries on blocks/rate limits/server errors, logging every failure."""
    resp = None
    for i in range(attempts):
        try:
            resp = SESSION.get(url, timeout=timeout)
        except requests.RequestException as exc:
            print(f"WARN: attempt {i + 1} for {url} raised {exc!r}", file=sys.stderr)
        else:
            if resp.status_code == 200:
                return resp
            print(f"WARN: attempt {i + 1} for {url} returned HTTP {resp.status_code}; "
                  f"body starts: {resp.text[:200]!r}", file=sys.stderr)
            if resp.status_code not in (403, 429, 500, 502, 503, 504):
                return resp
        if i < attempts - 1:
            time.sleep(10 * (i + 1))
    return resp
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Long-wait thresholds reported (weeks).
LONG_WAIT_TIERS = (52, 65, 78)


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
    resp = http_get(url, timeout=60)
    if resp is None or resp.status_code != 200:
        print(f"WARN: could not load {url} "
              f"(HTTP {getattr(resp, 'status_code', 'no response')})", file=sys.stderr)
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
    resp = http_get(csv_zip_url, timeout=180)
    if resp is None:
        raise RuntimeError(f"No response downloading {csv_zip_url}")
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


def load_acute_reference():
    """Read data/acute_trusts.csv (code, trust_name, region, merged_into, trust_type).

    Matching is on provider code, so renamed trusts keep matching. Rows with
    merged_into are predecessor codes: they count in months before the merger
    and are grouped with their successor when months are compared.
    """
    acute = pd.read_csv(DATA_DIR / "acute_trusts.csv", dtype=str).fillna("")
    ref = {"codes": set(), "names": set(), "region": {}, "region_by_name": {}, "group": {},
           "type": {}, "trust_name": {}, "merged": set()}
    for _, row in acute.iterrows():
        code = row.get("code", "").strip().upper()
        name = normalise_name(row.get("trust_name", ""))
        region = row.get("region", "").strip()
        if code:
            ref["codes"].add(code)
            ref["region"][code] = region or None
            ref["type"][code] = row.get("trust_type", "").strip() or "general"
            ref["trust_name"][code] = row.get("trust_name", "").strip()
            merged = row.get("merged_into", "").strip().upper()
            ref["group"][code] = merged or code
            if merged:
                ref["merged"].add(code)
        elif name:
            ref["names"].add(name)
        if name and region:
            ref["region_by_name"][name] = region
    return ref


def metrics_from_bands(bands, band_cols):
    """Headline metrics from one set of weekly band counts (a pandas Series)."""
    total = float(bands.sum())
    out = {
        "waiting_list": int(round(total)),
        "pct_within_18wk": round(100.0 * float(bands[[c for c in band_cols if band_upper_bound(c) <= 18]].sum())
                                 / total, 1) if total else None,
    }
    # "Gt 52 To 53 Weeks" onward = waiting more than 52 weeks, and so on.
    for wk in LONG_WAIT_TIERS:
        out[f"over_{wk}wk"] = int(round(float(bands[[c for c in band_cols if band_lower_bound(c) >= wk]].sum())))
    out["median_weeks"] = estimate_median_weeks(bands, band_cols, total)
    return out


def compute_metrics(df, band_cols, ref):
    """Returns (national_all, national_acute, trusts, regions, providers, acute_bands).

    acute_bands maps each acute trust code to its summed weekly band counts,
    kept so months can be compared like for like.
    """
    df = df[df["RTT Part Description"].astype(str).str.strip()
            .str.lower() == "incomplete pathways"].copy()

    # NHS's RTT extract includes a "Treatment Function Code" 999 row per
    # provider+commissioner, which is a pre-computed rollup ("Total") across
    # that provider+commissioner's actual specialty rows - not a real
    # specialty. Left in, it silently doubles every total.
    is_rollup = (
        df["Treatment Function Code"].astype(str).str.contains(r"999", na=False)
        | df["Treatment Function Name"].astype(str).str.strip().str.lower().eq("total")
    )
    df = df[~is_rollup].copy()
    df[band_cols] = df[band_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)

    grouped = df.groupby(["Provider Org Code", "Provider Org Name"], as_index=False)[band_cols].sum()
    grouped["_code"] = grouped["Provider Org Code"].astype(str).str.strip().str.upper()
    grouped["_name_norm"] = grouped["Provider Org Name"].map(normalise_name)
    grouped["is_acute"] = grouped["_code"].isin(ref["codes"]) | grouped["_name_norm"].isin(ref["names"])
    grouped["_region"] = [ref["region"].get(c) or ref["region_by_name"].get(n)
                          for c, n in zip(grouped["_code"], grouped["_name_norm"])]

    national_all = metrics_from_bands(grouped[band_cols].sum(), band_cols)
    acute_rows = grouped[grouped["is_acute"]]
    national_acute = metrics_from_bands(acute_rows[band_cols].sum(), band_cols)

    trusts, acute_bands = [], {}
    for _, r in acute_rows.iterrows():
        bands = r[band_cols].astype(float)
        acute_bands[r["_code"]] = bands
        trusts.append({
            "code": r["_code"],
            "name": r["Provider Org Name"],
            "region": r["_region"] or None,
            "type": ref["type"].get(r["_code"], "general"),
            **metrics_from_bands(bands, band_cols),
        })
    trusts.sort(key=lambda t: t["waiting_list"], reverse=True)

    regions = []
    mapped = acute_rows[acute_rows["_region"].notna()]
    for region_name, sub in mapped.groupby("_region"):
        regions.append({"region": region_name, "trust_count": int(len(sub)),
                        **metrics_from_bands(sub[band_cols].sum(), band_cols)})
    regions.sort(key=lambda r: r["region"])

    providers = sorted(
        ({"code": r["_code"], "name": r["Provider Org Name"],
          "waiting_list": int(round(float(r[band_cols].sum()))), "is_acute": bool(r["is_acute"])}
         for _, r in grouped.iterrows()),
        key=lambda p: p["waiting_list"], reverse=True)

    return national_all, national_acute, trusts, regions, providers, acute_bands


def process_period(csv_zip_url, ref):
    df = normalise_columns(download_full_extract(csv_zip_url))
    band_cols = get_band_columns(df)
    if not band_cols:
        raise RuntimeError("Could not find weeks-waited band columns in the extract")
    return (*compute_metrics(df, band_cols, ref), band_cols)


def group_bands(acute_bands, ref):
    """Sum trust band counts by merger group (a predecessor joins its successor)."""
    groups = {}
    for code, bands in acute_bands.items():
        g = ref["group"].get(code, code)
        groups[g] = bands if g not in groups else groups[g].add(bands, fill_value=0.0)
    return groups


def like_for_like(curr_bands, prev_bands, band_cols, ref, curr_period, prev_period):
    """Compare two months using only trusts (merger groups) reporting in both.

    A trust that stops or starts reporting would otherwise move the totals on
    its own, which looks like a real change in waiting times when it isn't.
    """
    gc, gp = group_bands(curr_bands, ref), group_bands(prev_bands, ref)
    common = sorted(set(gc) & set(gp))
    cols = sorted(set(band_cols), key=band_lower_bound)

    def total(groups, keys):
        if not keys:
            return None
        s = pd.concat([groups[k] for k in keys], axis=1).fillna(0.0).sum(axis=1)
        return s.reindex(cols, fill_value=0.0)

    def compare(keys):
        if not keys:
            return None
        return {"prev_period": prev_period, "trusts": len(keys),
                "curr": metrics_from_bands(total(gc, keys), cols),
                "prev": metrics_from_bands(total(gp, keys), cols)}

    national = compare(common)
    regions = {}
    for region in sorted({ref["region"].get(k) for k in common if ref["region"].get(k)}):
        regions[region] = compare([k for k in common if ref["region"].get(k) == region])
    trusts = {}
    for code in curr_bands:
        g = ref["group"].get(code, code)
        if g in gp:
            trusts[code] = {"prev_period": prev_period, **metrics_from_bands(gp[g].reindex(cols, fill_value=0.0), cols)}

    def label(k):
        return ref["trust_name"].get(k, k)
    reporting = {"stopped": sorted(label(k) for k in set(gp) - set(gc)),
                 "started": sorted(label(k) for k in set(gc) - set(gp))}
    return national, regions, trusts, reporting


def previous_month(period):
    y, m = map(int, period.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def load_bands_cache(path):
    if not path.exists():
        return None, {}
    raw = json.loads(path.read_text())
    cols = raw["band_cols"]
    return raw["period"], {code: pd.Series(vals, index=cols, dtype=float) for code, vals in raw["trusts"].items()}


def save_bands_cache(path, period, bands):
    cols = list(next(iter(bands.values())).index)
    path.write_text(json.dumps({"period": period, "band_cols": cols,
                                "trusts": {c: [int(round(v)) for v in b.tolist()] for c, b in bands.items()}},
                               separators=(",", ":")))


def candidate_from_url(zip_url):
    """Build a candidate from a full-CSV zip link (e.g. '...Full-CSV-data-file-Aug26-ZIP...')."""
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[\-_ ]?(\d{2})",
                  zip_url.rsplit("/", 1)[-1], re.I)
    if not m:
        print(f"Could not read a month (e.g. Aug26) from the link: {zip_url}", file=sys.stderr)
        sys.exit(1)
    mon = MONTH_ABBR.index(m.group(1).title()) + 1
    return {"period_date": datetime(2000 + int(m.group(2)), mon, 1), "csv_zip_url": zip_url}


def collect_candidates():
    """Links to process, most recent first. RTT_ZIP_URL may hold several
    links (space, comma or newline separated) to fill in older months too."""
    raw = os.environ.get("RTT_ZIP_URL", "").strip()
    links = [u for u in re.split(r"[\s,]+", raw) if u]
    if links:
        print(f"Using {len(links)} zip link(s) supplied to the workflow")
        found = [candidate_from_url(u) for u in links]
    else:
        # NHS England's pages now sit behind a browser check that blocks this
        # script, so this fallback normally finds nothing.
        found = []
        for slug in financial_year_slugs():
            found.extend(find_all_month_links(slug))
    seen, out = set(), []
    for c in sorted(found, key=lambda c: c["period_date"], reverse=True):
        lbl = c["period_date"].strftime("%Y-%m")
        if lbl not in seen:
            seen.add(lbl)
            out.append(c)
    return out


def not_reported(ref, trusts):
    """Listed trusts (not merged predecessors) with no rows in this month's extract."""
    codes = {t["code"] for t in trusts}
    return sorted(ref["trust_name"][c] for c in ref["codes"] if c not in ref["merged"] and c not in codes)


def main():
    candidates = collect_candidates()
    if not candidates:
        print("Could not find any RTT data links on any candidate page.", file=sys.stderr)
        sys.exit(1)

    summary_path = DATA_DIR / "rtt_summary.json"
    cache_path = DATA_DIR / "bands_latest.json"
    existing = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    history = {h["period"]: h for h in existing.get("history", [])}
    region_history = {h["period"]: h for h in existing.get("region_history", [])}
    trust_history = existing.get("trust_history", {})
    ref = load_acute_reference()

    # Bands for like-for-like comparisons: the cached latest month, plus every
    # month processed in this run. Oldest first so each month can look back.
    cache_period, cache_bands = load_bands_cache(cache_path)
    bands_by_period = {cache_period: cache_bands} if cache_period else {}
    results = {}
    for idx, c in enumerate(sorted(candidates, key=lambda c: c["period_date"])):
        lbl = c["period_date"].strftime("%Y-%m")
        print(f"Processing {lbl} -> {c['csv_zip_url']}")
        try:
            national_all, national_acute, trusts, regions, providers, acute_bands, band_cols = \
                process_period(c["csv_zip_url"], ref)
        except Exception as exc:  # noqa: BLE001
            if c is candidates[0]:
                raise
            print(f"WARN: {lbl} failed, skipping: {exc}", file=sys.stderr)
            continue
        bands_by_period[lbl] = acute_bands

        prev_lbl = previous_month(lbl)
        lfl = None
        if prev_lbl in bands_by_period:
            lfl = like_for_like(acute_bands, bands_by_period[prev_lbl], band_cols, ref, lbl, prev_lbl)

        entry = {"period": lbl, **national_acute}
        old = history.get(lbl, {})
        if lfl:
            entry["like_for_like"], entry["reporting"] = lfl[0], lfl[3]
        else:  # keep what an earlier run worked out, if this run can't look back
            for k in ("like_for_like", "reporting"):
                if k in old:
                    entry[k] = old[k]
        history[lbl] = entry

        rentry = {"period": lbl, "regions": regions}
        if lfl:
            rentry["like_for_like"] = lfl[1]
        elif "like_for_like" in region_history.get(lbl, {}):
            rentry["like_for_like"] = region_history[lbl]["like_for_like"]
        region_history[lbl] = rentry

        for t in trusts:
            rows = [h for h in trust_history.get(t["code"], []) if h["period"] != lbl]
            rows.append({"period": lbl, **{k: v for k, v in t.items() if k not in ("code", "name", "region", "type")}})
            trust_history[t["code"]] = sorted(rows, key=lambda h: h["period"])
            if lfl and t["code"] in lfl[2]:
                t["prev"] = lfl[2][t["code"]]
        results[lbl] = (c, national_all, national_acute, trusts, regions, providers)

    if not results:
        sys.exit(1)
    lbl = max(results)
    c, national_all, national_acute, trusts, regions, providers = results[lbl]
    if existing.get("period") and existing["period"] > lbl:
        print(f"Supplied month {lbl} is older than the dashboard's {existing['period']}; "
              f"history updated but headline left as it was.")
        out = {**existing}
    else:
        if "prev" not in (trusts[0] if trusts else {}) and existing.get("period") == lbl:
            # Re-run of the same month without last month's bands: keep earlier comparisons.
            old_prev = {t["code"]: t.get("prev") for t in existing.get("trusts", []) if t.get("prev")}
            for t in trusts:
                if t["code"] in old_prev:
                    t["prev"] = old_prev[t["code"]]
        period_date = c["period_date"]
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": lbl,
            "period_display": period_date.strftime("%B %Y"),
            "source_url": c["csv_zip_url"],
            "national_all_providers": national_all,
            "national_acute": national_acute,
            "trusts": trusts,
            "regions": regions,
            "acute_not_reported": not_reported(ref, trusts),
        }
        (DATA_DIR / "providers_seen.json").write_text(json.dumps(providers, indent=1))
        unmatched = [p["name"] for p in providers if not p["is_acute"]
                     and re.search("NHS TRUST|NHS FOUNDATION TRUST", p["name"], re.I)]
        (DATA_DIR / "unmatched_nhs_trust_providers.json").write_text(json.dumps(sorted(unmatched), indent=2))
        save_bands_cache(cache_path, lbl, bands_by_period[lbl])
    out["history"] = sorted(history.values(), key=lambda h: h["period"])
    out["region_history"] = sorted(region_history.values(), key=lambda h: h["period"])
    out["trust_history"] = trust_history
    summary_path.write_text(json.dumps(out, indent=1))
    print(f"Wrote {summary_path}: {len(out['trusts'])} acute trusts, {len(out['history'])} months of history.")


if __name__ == "__main__":
    main()
