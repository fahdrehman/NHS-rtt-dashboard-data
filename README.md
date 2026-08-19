# NHS England RTT Dashboard — Acute Trusts

A live dashboard tracking NHS England's Referral to Treatment (RTT) waiting
times for England's acute NHS trusts, refreshed automatically every month.

**Live dashboard:** https://fahdrehman.github.io/NHS-rtt-dashboard-data/

## What it shows

- **Headline figures** — % of patients waiting within 18 weeks, total waiting
  list size, estimated median wait, and the number of patients waiting over
  52 weeks, all for England's acute trusts combined.
- **National trend** — how those figures have moved over the last few months.
- **Average wait time by region** — acute trusts grouped into NHS England's
  7 administrative regions (London, Midlands, North West, North East and
  Yorkshire, East of England, South East, South West), compared by median
  wait.
- **Trust-by-trust comparison** — a searchable, sortable, filterable table
  of every acute trust, with the same metrics broken out individually.

## Data source

Figures are built from NHS England's monthly Referral to Treatment (RTT)
["Full CSV" data extract](https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/),
published roughly two weeks after the end of each reporting month.

## Methodology

- Filtered to **Incomplete Pathways** rows only — this is the current
  waiting list (as opposed to completed pathways or new referrals), and is
  what NHS England's own headline "% within 18 weeks" figure is based on.
- NHS's extract includes a pre-computed rollup row per provider
  (`Treatment Function Code = 999`) that duplicates the sum of that
  provider's real specialty rows. These rollup rows are explicitly excluded
  before aggregating — left in, they silently double every total.
- **Median wait** is estimated by linear interpolation across NHS's
  published weekly waiting-time bands (NHS doesn't publish an exact median,
  only counts within 1-week bands).
- **Acute trust list** — NHS's raw data doesn't tag provider type, so this
  dashboard uses a maintained reference list of ~135 acute NHS trusts
  (`data/acute_trusts.csv`) to separate acute trusts from mental health,
  community, and independent-sector providers. Trust mergers or renames may
  occasionally need a manual update to this list.
- **Region mapping** — each acute trust is matched by name to one of NHS
  England's 7 administrative regions (`data/trust_regions.csv`). This is a
  best-effort mapping based on each trust's real headquarters location.
- **Trend history** — the dashboard keeps a rolling window of the most
  recent months' figures to chart trends over time; older months are
  backfilled automatically the first few times the pipeline runs.

## How it stays up to date

1. A [GitHub Actions workflow](.github/workflows/update-rtt.yml) runs on the
   15th, 20th, and 25th of each month, checks for a new NHS RTT release, and
   commits the refreshed figures to `data/rtt_summary.json`.
2. The dashboard page (`docs/index.html`) is a static page that fetches
   `data/rtt_summary.json` live, in the visitor's browser, every time it's
   opened — so it's always showing the latest committed figures without
   needing to be rebuilt or redeployed.

## Repository structure

```
scripts/fetch_and_process_rtt.py   the data pipeline (download, clean, aggregate)
data/acute_trusts.csv              reference list of acute NHS trusts
data/trust_regions.csv             trust → NHS England region mapping
data/rtt_summary.json              latest computed figures (auto-generated)
docs/index.html                    the dashboard page (served via GitHub Pages)
.github/workflows/update-rtt.yml   the monthly automation
```

## Limitations

- Figures are England-only, acute trusts only — mental health, community,
  ambulance, and independent-sector providers are excluded from trust- and
  region-level figures (though still counted in NHS's raw national totals).
- Median wait is an estimate, not an exact figure, since NHS publishes
  banded (not individual) wait times.
- The acute trust list and region mapping are maintained manually and may
  lag behind trust mergers, renames, or reorganisations.

## Credit

Dashboard created by Fahd Rehman.
