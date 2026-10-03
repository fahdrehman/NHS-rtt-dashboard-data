# NHS England waiting times: acute trusts

A public dashboard of NHS England's referral to treatment (RTT) waiting times for England's acute NHS trusts. It shows how long people are waiting, how that is changing, and how each trust compares with its region and with similar trusts.

**Live dashboard:** https://fahdrehman.github.io/NHS-rtt-dashboard-data/

This is an independent project by Fahd Rehman. It is not linked to any NHS body and is not an official source of NHS statistics.

## What it shows

**At a glance**
- The share of people waiting under 18 weeks, against the 70% milestone for March 2027 and the 92% constitutional standard due by 2029.
- People waiting, the median wait, and the number waiting over 52, 65 and 78 weeks.
- A plain-English summary of the month, which follows the region selected.
- Monthly trends since April 2024.

**Find a trust**
- Search for any acute trust to see its figures, its change on the previous month and its own trend.
- A comparison with its region, with the 10 most similar-sized trusts of the same type, and with England.
- A share button that copies a link to a one-page summary of that trust. When the link is pasted into WhatsApp, Teams, LinkedIn or email, it shows a preview card with the trust's figures.

**Explore the detail** (collapsed by default)
- **Regions:** median wait and long waits across NHS England's seven regions.
- **Waits compared with trust size:** each trust's median wait against the wait expected for a trust with that many people waiting.
- **All trusts:** a sortable, filterable table of every acute trust.

The size comparison and the full table are designed for a PC, Mac or tablet. On phones these two sections show a note with buttons to share or copy the link.

## Data source

NHS England's monthly RTT "Full CSV data file", published on the [RTT waiting times statistics pages](https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/), usually on the second Thursday of each month for the month two months earlier.

## Method

**What is counted**
- Only "incomplete pathways", meaning people still waiting to start treatment. This is the basis of NHS England's own 18-week figure.
- NHS England's extract includes a total row for each provider (treatment function code 999) that repeats the sum of its specialty rows. These are removed before adding up, otherwise every total would double.
- The median wait is estimated by linear interpolation across the published one-week waiting bands, as NHS England does not publish an exact median.

**Which trusts are included**
- NHS England's data does not label provider type, so `data/acute_trusts.csv` lists the acute trusts to include, matched on NHS organisation code so that renamed trusts still match. It also holds each trust's region and whether it is a general or specialist trust.
- Merged trusts are recorded with the code they merged into (for example, North Bristol into Bristol NHS Foundation Trust), so their figures stay comparable across the merger.
- Trusts on the list that did not submit data for the latest month are named on the dashboard rather than silently dropped.
- `data/providers_seen.json` lists every provider in the latest extract, so the list can be checked when trusts merge or new ones appear.

**Month-on-month changes**
- Changes compare only trusts that reported in both months, with merged trusts combined. Without this, a trust that stops sending data makes the totals fall even though nothing has changed. In July 2026, for example, the reported England waiting list fell by about 7,600, but at the same trusts it rose by about 39,700.
- Small movements are shown as "no real change" rather than as a rise or fall: under 0.3 percentage points for the 18-week figure, 0.2 weeks for the median wait, and 0.5% for counts.
- Months when a trust stopped or started reporting are noted in the chart tooltips. Months with no data for a trust appear as a red point and dashed line rather than a break in its trend.

**Comparing trusts of different sizes**
- Bigger waiting lists tend to mean longer waits, so the median wait is fitted against the log of the waiting list across general acute trusts.
- Specialist trusts (children's, eye, orthopaedic, cancer, heart, neurosciences, women's and plastics; 16 in total) treat a different mix of patients, so they are shown but not compared with the line. In the "Find a trust" comparison they are only compared with other specialist trusts.
- A trust is flagged only if it falls outside the normal range, where about 95 in 100 trusts would be expected to sit. This replaces a top-five and bottom-five league table, which would always name ten trusts whether or not they really stood out.

## How it is updated

NHS England's website now shows a browser check to automated requests, so the workflow cannot find each month's download link by itself. The update is split in two:

1. **Finding the link.** A scheduled task runs on my own computer four times a day from the 8th to the 20th of each month. It checks whether the dashboard already has the latest month and, if not, opens the NHS England page in a normal browser, finds the new "Full CSV data file" link and starts the GitHub workflow with it. If the new month has not appeared by the 20th, it sends a notification instead.
2. **Processing the data.** The [GitHub Actions workflow](.github/workflows/update-rtt.yml) downloads the file, runs `scripts/fetch_and_process_rtt.py`, rebuilds the preview cards with `scripts/build_share_cards.py`, and commits the results. GitHub Pages then republishes the site.

The dashboard page fetches `data/rtt_summary.json` each time it is opened, so it always shows the latest figures without being rebuilt.

**Running an update by hand:** on GitHub, open Actions, choose "Update NHS RTT data", then "Run workflow", and paste the month's "Full CSV data file" link from the NHS England page. Several links separated by spaces can be pasted at once to rebuild earlier months.

**Running locally:**

```
pip install -r requirements.txt
RTT_ZIP_URL="<full CSV zip link>" python scripts/fetch_and_process_rtt.py
python scripts/build_share_cards.py
```

## Repository structure

```
docs/index.html                     the dashboard (served by GitHub Pages)
docs/og/                            link-preview images, one per trust plus England
docs/t/                             share pages that carry each preview and open the trust summary
data/rtt_summary.json               figures used by the dashboard (generated)
data/bands_latest.json              latest month's waiting bands per trust, kept for next month's comparison (generated)
data/acute_trusts.csv               acute trust list: code, name, region, merger, general or specialist
data/providers_seen.json            every provider in the latest extract (generated)
data/unmatched_nhs_trust_providers.json   NHS trusts in the extract that are not on the acute list (generated)
scripts/fetch_and_process_rtt.py    downloads and processes the NHS England extract
scripts/build_share_cards.py        builds the preview images and share pages
scripts/fonts/                      Fraunces and Manrope, with their licences
.github/workflows/update-rtt.yml    the update workflow
```

## Limitations

- England only, and acute trusts only. Mental health, community and independent-sector providers are excluded.
- The median wait is an estimate from banded data.
- Trusts that stop submitting data are missing for those months. Changes are adjusted for this, but the reported totals in the trend charts are not.
- The size comparison uses a single month and a simple model. Waiting-list size explains only part of the difference between trusts, so a flag is a prompt for questions rather than a verdict.
- The acute trust list is maintained by hand and may need updating after mergers.

## Design

The dashboard shares its look with [fahd.uk](https://www.fahd.uk): Fraunces for headings and figures, Manrope for text, and the same light and dark colour schemes. Both fonts are from Google Fonts under the SIL Open Font Licence.

## Credit

Created by Fahd Rehman · [fahd.uk](https://www.fahd.uk)
