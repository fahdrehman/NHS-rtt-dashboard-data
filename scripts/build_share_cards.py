#!/usr/bin/env python3
"""
Build link-preview cards for the dashboard.

For every acute trust (and England as a whole) this writes:
  docs/og/<CODE>-<YYYY-MM>.png  a 1200x630 image in the dashboard's colours
  docs/t/<CODE>.html            a tiny page carrying Open Graph tags that point
                                at the image, then sends people on to the
                                trust's one-page summary

Messaging apps (WhatsApp, Teams, Outlook, LinkedIn) read those tags when a
link is pasted, so a shared trust link shows that trust's figures.
The month is in the image name so apps don't keep showing last month's
picture; older images are removed each run.
"""
import html
import json
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "data" / "rtt_summary.json").read_text())
OG_DIR = ROOT / "docs" / "og"
T_DIR = ROOT / "docs" / "t"
BASE_URL = "https://fahdrehman.github.io/NHS-rtt-dashboard-data/"

# Dashboard light theme
PLANE = "#e9e7f6"
CARD = "#ffffff"
BORDER = (79 / 255, 70 / 255, 229 / 255, 0.28)
INDIGO = "#4f46e5"
INDIGO_WASH = (79 / 255, 70 / 255, 229 / 255, 0.10)
TEXT = "#14141f"
TEXT_2 = "#55536b"
MUTED = "#92909f"
GRID = "#e6e5f0"
GOOD, GOOD_WASH = "#16a34a", (22 / 255, 163 / 255, 74 / 255, 0.12)
BAD, BAD_WASH = "#dc2626", (220 / 255, 38 / 255, 38 / 255, 0.12)

plt.rcParams["font.family"] = "DejaVu Sans"

W, H = 1200, 630


def proper_case(name):
    words = []
    for w in name.split():
        low = w.lower()
        if low in ("nhs", "ucl"):
            words.append(w.upper())
        elif low in ("and", "of", "the", "for") and words:
            words.append(low)
        else:
            words.append(w[:1].upper() + w[1:].lower())
    return " ".join(words)


def month_label(period):
    import datetime as dt
    y, m = period.split("-")
    return dt.date(int(y), int(m), 1).strftime("%b %y")


def fmt_int(n):
    return "—" if n is None else f"{round(n):,}"


def draw_card(path, title, subtitle, curr, prev, series):
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    fig.patch.set_facecolor(PLANE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    ax.add_patch(FancyBboxPatch((36, 36), W - 72, H - 72,
                                boxstyle="round,pad=0,rounding_size=22",
                                facecolor=CARD, edgecolor=BORDER, linewidth=1.5))

    # Heading: kicker, trust name (one or two lines) and region, with the
    # dashboard's indigo brand mark running down the left of the block.
    period = DATA.get("period_display") or DATA["period"]
    ax.text(104, 548, f"NHS ENGLAND RTT WAITING TIMES  ·  {period.upper()}",
            fontsize=13, color=MUTED, fontweight="bold", va="center")
    lines = textwrap.wrap(title, 42)[:2]
    size = 27 if len(lines) == 1 else 23
    ax.text(104, 526, "\n".join(lines), fontsize=size, color=TEXT, fontweight="bold", va="top",
            linespacing=1.12)
    name_h = len(lines) * size * 1.39 * 1.12
    region_y = 526 - name_h - 16
    ax.text(104, region_y, subtitle, fontsize=14, color=TEXT_2, va="center")
    ax.add_patch(FancyBboxPatch((78, region_y - 12), 6, 572 - region_y, boxstyle="round,pad=0,rounding_size=3",
                                facecolor=INDIGO, edgecolor="none"))

    # Headline: % waiting under 18 weeks
    pct = curr.get("pct_within_18wk")
    ax.text(100, 345, "—" if pct is None else f"{pct:.1f}%", fontsize=66, fontweight="bold",
            color=TEXT, va="center")
    ax.text(104, 282, "waiting under 18 weeks  ·  92% standard", fontsize=14, color=TEXT_2, va="center")
    if prev and prev.get("pct_within_18wk") is not None and pct is not None:
        d = pct - prev["pct_within_18wk"]
        good = d >= 0
        label = f"{'▲' if d > 0 else ('▼' if d < 0 else '•')} {'+' if d > 0 else ''}{d:.1f}pp vs {month_label(prev['period'])}"
        ax.text(104 + 9, 236, label, fontsize=13, fontweight="bold", color=GOOD if good else BAD, va="center",
                bbox=dict(boxstyle="round,pad=0.45,rounding_size=0.9", facecolor=GOOD_WASH if good else BAD_WASH,
                          edgecolor="none"))

    # Trend: % under 18 weeks over the months tracked
    pts = [(h["period"], h["pct_within_18wk"]) for h in series if h.get("pct_within_18wk") is not None]
    if len(pts) >= 2:
        tx = fig.add_axes([600 / W, 225 / H, 500 / W, 205 / H])
        ys = [v for _, v in pts]
        xs = list(range(len(ys)))
        lo, hi = min(ys), max(ys)
        span = max(hi - lo, 3)
        mid = (hi + lo) / 2
        tx.set_ylim(mid - span * 0.62, mid + span * 0.62)
        tx.set_xlim(-0.3, len(ys) - 0.7)
        tx.fill_between(xs, ys, tx.get_ylim()[0], color=INDIGO_WASH, linewidth=0)
        tx.plot(xs, ys, color=INDIGO, linewidth=3, solid_capstyle="round")
        tx.scatter([xs[-1]], [ys[-1]], s=70, color=INDIGO, edgecolor=CARD, linewidth=2.5, zorder=3)
        for sp in tx.spines.values():
            sp.set_visible(False)
        tx.set_xticks([0, len(xs) - 1])
        tx.set_xticklabels([month_label(pts[0][0]), month_label(pts[-1][0])], fontsize=12, color=MUTED)
        tx.tick_params(axis="x", length=0, pad=8)
        tx.set_yticks([])
        tx.grid(axis="y", color=GRID, linewidth=1)
        tx.set_facecolor("none")
        ax.text(850, 205, "% waiting under 18 weeks, monthly", fontsize=12, color=MUTED, fontweight="bold",
                ha="center", va="center")

    # Stats row
    ax.plot([78, W - 78], [175, 175], color=GRID, linewidth=1.2)
    stats = [
        ("Waiting list", fmt_int(curr.get("waiting_list"))),
        ("Median wait", "—" if curr.get("median_weeks") is None else f"{curr['median_weeks']:.1f} wks"),
        ("Over 52 weeks", fmt_int(curr.get("over_52wk"))),
        ("Over 65 weeks", fmt_int(curr.get("over_65wk"))),
    ]
    for i, (lab, val) in enumerate(stats):
        x = 104 + i * 255
        ax.text(x, 140, lab, fontsize=13, color=TEXT_2, va="center")
        ax.text(x, 103, val, fontsize=24, fontweight="bold", color=TEXT, va="center")

    ax.text(W - 78, 62, "Dashboard by Fahd Rehman  ·  www.fahd.uk", fontsize=12, color=MUTED,
            ha="right", va="center")

    fig.savefig(path, dpi=100, facecolor=PLANE)
    plt.close(fig)
    # Flat colours compress well as a palette image (~100KB down to ~30KB).
    Image.open(path).convert("RGB").quantize(colors=96, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)


def write_share_page(code, title, description, image_name, target):
    page_url = f"{BASE_URL}t/{code}.html"
    img_url = f"{BASE_URL}og/{image_name}"
    e = html.escape
    (T_DIR / f"{code}.html").write_text(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="NHS England RTT dashboard">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:url" content="{e(page_url)}">
<meta property="og:image" content="{e(img_url)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{e(title)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{e(img_url)}">
<meta http-equiv="refresh" content="0; url={e(target)}">
<style>body{{font-family:system-ui,sans-serif;background:{PLANE};color:{TEXT};padding:40px}}a{{color:{INDIGO}}}</style>
</head><body>
<script>location.replace({json.dumps(target)});</script>
<p><a href="{e(target)}">Open the summary for {e(title)}</a></p>
</body></html>
""")


def describe(curr):
    bits = []
    if curr.get("pct_within_18wk") is not None:
        bits.append(f"{curr['pct_within_18wk']:.1f}% waiting under 18 weeks")
    bits.append(f"waiting list {fmt_int(curr.get('waiting_list'))}")
    if curr.get("median_weeks") is not None:
        bits.append(f"median wait {curr['median_weeks']:.1f} weeks")
    bits.append(f"{fmt_int(curr.get('over_52wk'))} over 52 weeks")
    return "; ".join(bits) + "."


def main():
    OG_DIR.mkdir(parents=True, exist_ok=True)
    T_DIR.mkdir(parents=True, exist_ok=True)
    period = DATA["period"]
    period_display = DATA.get("period_display") or period
    keep = set()

    # England
    hist = sorted(DATA.get("history", []), key=lambda h: h["period"])
    prev = next((h for h in reversed(hist) if h["period"] < period), None)
    draw_card(OG_DIR / "england.png", "England: all acute trusts",
              f"{len(DATA['trusts'])} acute NHS trusts", DATA["national_acute"], prev, hist)
    keep.add("england.png")

    for t in DATA["trusts"]:
        code = t["code"]
        series = sorted(DATA.get("trust_history", {}).get(code, []), key=lambda h: h["period"])
        prev = next((h for h in reversed(series) if h["period"] < period), None)
        image_name = f"{code}-{period}.png"
        name = proper_case(t["name"])
        draw_card(OG_DIR / image_name, name, t.get("region") or "", t, prev, series)
        keep.add(image_name)
        write_share_page(code, f"{name}: RTT waiting times, {period_display}", describe(t),
                         image_name, f"../?trust={code}&view=summary")

    # Remove images from earlier months, and pages for trusts no longer listed.
    for f in OG_DIR.glob("*.png"):
        if f.name not in keep:
            f.unlink()
    codes = {t["code"] for t in DATA["trusts"]}
    for f in T_DIR.glob("*.html"):
        if f.stem not in codes:
            f.unlink()
    print(f"Built {len(keep)} preview images and {len(codes)} share pages for {period_display}.")


if __name__ == "__main__":
    main()
