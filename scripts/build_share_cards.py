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
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "data" / "rtt_summary.json").read_text())
OG_DIR = ROOT / "docs" / "og"
T_DIR = ROOT / "docs" / "t"
BASE_URL = "https://fahdrehman.github.io/NHS-rtt-dashboard-data/"

# Dashboard light theme (matches fahd.uk): violet for the brand, blue for data
PLANE = "#f6f6fc"
CARD = "#ffffff"
BORDER = "#dddde7"
BRAND = "#8557c8"
DATA_BLUE = "#2d74ca"
DATA_WASH = (45 / 255, 116 / 255, 202 / 255, 0.10)
TEXT = "#131423"
TEXT_2 = "#585a66"
MUTED = "#848592"
GRID = "#e7e7ee"
NEUTRAL_PP = 0.3  # changes smaller than this are shown as "no real change"
GOOD, GOOD_WASH = "#16a34a", (22 / 255, 163 / 255, 74 / 255, 0.12)
BAD, BAD_WASH = "#dc2626", (220 / 255, 38 / 255, 38 / 255, 0.12)

# The portfolio's fonts (SIL Open Font Licence, see scripts/fonts): Space
# Grotesk for names and figures, Manrope for labels.
FONT_DIR = Path(__file__).resolve().parent / "fonts"


def font(face, size):
    return FontProperties(fname=str(FONT_DIR / f"{face}.ttf"), size=size)


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


def month_name(period):
    import datetime as dt
    y, m = period.split("-")
    return dt.date(int(y), int(m), 1).strftime("%B")


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
                                boxstyle="round,pad=0,rounding_size=26",
                                facecolor=CARD, edgecolor=BORDER, linewidth=1.5))

    # Heading: kicker, trust name (one or two lines) and region
    period = DATA.get("period_display") or DATA["period"]
    ax.text(84, 548, f"NHS ENGLAND WAITING TIMES  ·  {period.upper()}",
            fontproperties=font("Manrope-ExtraBold", 13), color=BRAND, va="center")
    lines = textwrap.wrap(title, 42)[:2]
    size = 30 if len(lines) == 1 else 25
    name = ax.text(84, 528, "\n".join(lines), fontproperties=font("SpaceGrotesk-SemiBold", size), color=TEXT,
                   va="top", linespacing=1.0)
    # Measure the drawn name so the region sits just below it, however many lines.
    renderer = fig.canvas.get_renderer()
    name_bottom = ax.transData.inverted().transform(name.get_window_extent(renderer))[0][1]
    ax.text(84, name_bottom - 20, subtitle, fontproperties=font("Manrope-SemiBold", 15), color=TEXT_2, va="center")

    # Headline: % waiting under 18 weeks
    pct = curr.get("pct_within_18wk")
    ax.text(80, 342, "—" if pct is None else f"{pct:.1f}%", fontproperties=font("SpaceGrotesk-Bold", 72),
            color=TEXT, va="center")
    ax.text(84, 278, "waiting under 18 weeks", fontproperties=font("Manrope-SemiBold", 15), color=TEXT_2, va="center")
    curr_cmp = (prev or {}).get("curr_pct", pct)
    if prev and prev.get("pct_within_18wk") is not None and pct is not None:
        d = curr_cmp - prev["pct_within_18wk"]
        when = month_label(prev["period"])
        when = month_name(prev["period"])
        if abs(d) < NEUTRAL_PP:  # month-to-month wobble, not a real change
            label, fg, bg = f"No real change on {when}", TEXT_2, GRID
        else:
            good = d > 0
            label = f"{'Up' if good else 'Down'} {abs(d):.1f} points on {when}"
            fg, bg = (GOOD, GOOD_WASH) if good else (BAD, BAD_WASH)
        ax.text(84 + 10, 232, label, fontproperties=font("Manrope-ExtraBold", 13.5), color=fg, va="center",
                bbox=dict(boxstyle="round,pad=0.55,rounding_size=1.0", facecolor=bg, edgecolor="none"))

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
        tx.fill_between(xs, ys, tx.get_ylim()[0], color=DATA_WASH, linewidth=0)
        tx.plot(xs, ys, color=DATA_BLUE, linewidth=3, solid_capstyle="round")
        tx.scatter([xs[-1]], [ys[-1]], s=70, color=DATA_BLUE, edgecolor=CARD, linewidth=2.5, zorder=3)
        for sp in tx.spines.values():
            sp.set_visible(False)
        tx.set_xticks([0, len(xs) - 1])
        tx.set_xticklabels([month_label(pts[0][0]), month_label(pts[-1][0])])
        for lbl in tx.get_xticklabels():
            lbl.set_fontproperties(font("Manrope-SemiBold", 12))
            lbl.set_color(MUTED)
        tx.tick_params(axis="x", length=0, pad=8)
        tx.set_yticks([])
        tx.grid(axis="y", color=GRID, linewidth=1)
        tx.set_facecolor("none")
        ax.text(850, 205, "% waiting under 18 weeks, monthly", fontproperties=font("Manrope-Bold", 12),
                color=MUTED, ha="center", va="center")

    # Stats row
    ax.plot([78, W - 78], [175, 175], color=GRID, linewidth=1.2)
    stats = [
        ("People waiting", fmt_int(curr.get("waiting_list"))),
        ("Median wait", "—" if curr.get("median_weeks") is None else f"{curr['median_weeks']:.1f} wks"),
        ("Over 52 weeks", fmt_int(curr.get("over_52wk"))),
        ("Over 65 weeks", fmt_int(curr.get("over_65wk"))),
    ]
    for i, (lab, val) in enumerate(stats):
        x = 84 + i * 262
        ax.text(x, 140, lab, fontproperties=font("Manrope-SemiBold", 14), color=TEXT_2, va="center")
        ax.text(x, 103, val, fontproperties=font("SpaceGrotesk-SemiBold", 26), color=TEXT, va="center")

    site = ax.text(W - 84, 62, "www.fahd.uk", fontproperties=font("Manrope-Bold", 12.5), color=BRAND,
                   ha="right", va="center")
    site_left = ax.transData.inverted().transform(site.get_window_extent(fig.canvas.get_renderer()))[0][0]
    ax.text(site_left, 62, "Dashboard by Fahd Rehman  ·  ", fontproperties=font("Manrope-SemiBold", 12.5),
            color=MUTED, ha="right", va="center")

    fig.savefig(path, dpi=100, facecolor=PLANE)
    plt.close(fig)
    # Flat colours compress well as a palette image (~100KB down to ~30KB).
    Image.open(path).convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)


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
<style>body{{font-family:system-ui,sans-serif;background:{PLANE};color:{TEXT};padding:40px}}a{{color:{BRAND}}}</style>
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
    lfl = (hist[-1] if hist else {}).get("like_for_like")
    prev = ({"period": lfl["prev_period"], **lfl["prev"], "curr_pct": lfl["curr"]["pct_within_18wk"]}
            if lfl else next((h for h in reversed(hist) if h["period"] < period), None))
    draw_card(OG_DIR / "england.png", "England: all acute trusts",
              f"{len(DATA['trusts'])} acute NHS trusts", DATA["national_acute"], prev, hist)
    keep.add("england.png")

    for t in DATA["trusts"]:
        code = t["code"]
        series = sorted(DATA.get("trust_history", {}).get(code, []), key=lambda h: h["period"])
        prev = ({"period": t["prev"]["prev_period"], **t["prev"]} if t.get("prev")
                else next((h for h in reversed(series) if h["period"] < period), None))
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
