"""`nrc site`: a static one-page site in site/, built from the committed reports.

The page shows the headline number with its interval, the monthly event study and a
zone map of the change. It reads reports/estimate.json, reports/extended.json and the
TLC zone shapes. It needs no warehouse and no network, and it publishes nothing: the
output is plain files to open in a browser.

The words on the page come from the plan's "What the memo may say" table. Each verdict
row maps to one sentence pattern, so the page cannot say more than the plan allows.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # draw to files only

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from nyc_rideshare_congestion import config  # noqa: E402
from nyc_rideshare_congestion.analysis import figures, zones  # noqa: E402

PRIMARY = "trips_per_day_both_platforms"
SITE_DIR = "site"
MAP_FILE = "zone_map_change.png"
EVENT_FILE = "event_study_monthly.png"
DIVERGING = "RdBu"  # red: fewer trips than control zones, blue: more
NO_DATA = "#ecebe6"


class SiteError(RuntimeError):
    """A report the page needs is missing or stopped."""


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _interval(lo: float, hi: float) -> str:
    return f"{lo * 100:+.1f}% to {hi * 100:+.1f}%"


def headline_sentence(verdict: dict[str, Any], effect: float, iv: list[float]) -> str:
    """One sentence for the triple difference, following the plan's wording table."""
    row = verdict["table_row"]
    span = _interval(iv[0], iv[1])
    if row == "leaves_out_zero":
        return (
            f"The data are consistent with congestion pricing changing Uber and Lyft trips "
            f"into the zone by {_pct(effect)} (95% interval {span}), against other Manhattan "
            f"and Brooklyn zones and after taking out the change seen a year earlier."
        )
    if row == "holds_zero_inside_smallest_effect":
        return (
            f"We find no change larger than 3% in either direction: the estimate is "
            f"{_pct(effect)} (95% interval {span})."
        )
    if row == "holds_zero_wider_than_smallest_effect":
        return (
            f"The result is not clear. The estimate is {_pct(effect)}, and the data cannot "
            f"rule out a change anywhere from {span}."
        )
    raise SiteError(f"no wording for verdict row {row!r}")


def plot_zone_map(changes: list[dict[str, Any]], shapes_zip: Path, path: Path) -> Path:
    """Each panel zone colored by its change against the control zones, in percent.

    Treated zones get a dark outline. Zones outside the panel are drawn in light gray
    with no color, so the map shows where the comparison sits.
    """
    geoms, boroughs = zones.read_shapes(shapes_zip)
    value = {r["zone"]: r["change_vs_control_log"] for r in changes}
    treated = {r["zone"] for r in changes if r["group"] == "treated"}
    shown = [z for z, b in boroughs.items() if b in ("Manhattan", "Brooklyn")]
    vals = np.array([np.expm1(v) for v in value.values() if v is not None])
    limit = float(np.quantile(np.abs(vals), 0.95)) if len(vals) else 0.1
    norm = TwoSlopeNorm(vcenter=0.0, vmin=-limit, vmax=limit)
    cmap = plt.get_cmap(DIVERGING)

    fig, ax = plt.subplots(figsize=(6.4, 7.6), dpi=150)
    fig.patch.set_facecolor(figures.SURFACE)
    ax.set_facecolor(figures.SURFACE)
    for z in shown:
        v = value.get(z)
        face = NO_DATA if v is None else cmap(norm(float(np.expm1(v))))
        edge, width = (figures.INK, 1.1) if z in treated else ("#ffffff", 0.4)
        polys = getattr(geoms[z], "geoms", [geoms[z]])
        for poly in polys:
            x, y = poly.exterior.xy
            ax.fill(x, y, facecolor=face, edgecolor=edge, linewidth=width)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(
        "Change in trips per day, 2025 against 2024,\nminus the change in all control zones",
        loc="left",
        color=figures.INK,
        fontsize=10,
    )
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    bar = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
    bar.ax.yaxis.set_major_formatter(lambda x, _: f"{x * 100:+.0f}%")
    bar.ax.tick_params(labelsize=8, colors=figures.INK_SECONDARY)
    ax.legend(
        handles=[
            Patch(facecolor="none", edgecolor=figures.INK, label="Fee zone (treated)"),
            Patch(facecolor=NO_DATA, edgecolor="#ffffff", label="Not in the comparison"),
        ],
        frameon=False,
        fontsize=8,
        loc="lower right",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, facecolor=figures.SURFACE, metadata={"Software": None})
    plt.close(fig)
    return path


def wider(a: list[float], b: list[float]) -> list[float]:
    """The wider of two intervals: the plan says the wider one decides."""
    return a if a[1] - a[0] >= b[1] - b[0] else b


def page_html(ext: dict[str, Any], plan: dict[str, Any]) -> str:
    """The page as one HTML string. Every number comes from the two reports."""
    triple = ext["triple_difference"][PRIMARY]
    verdict = ext["verdict"]
    iv = verdict["interval_used_as_percent"]
    yoy = ext["year_over_year"]["outcomes"][PRIMARY]
    plc = ext["check_one_year_earlier"]["outcomes"][PRIMARY]
    first = plan["triple_difference"][PRIMARY]
    first_iv = [float(np.expm1(v)) for v in wider(first["interval_zone"], first["interval_week"])]
    esc = html.escape
    rows = [
        ("Year over year, 2025 against 2024", yoy),
        ("The same check a year earlier, 2024 against 2023", plc),
    ]
    table = "\n".join(
        f"<tr><td>{esc(label)}</td><td>{_pct(r['effect'])}</td>"
        f"<td>{_interval(*wider(r['interval_zone'], r['interval_week']))}</td></tr>"
        for label, r in rows
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Congestion pricing and rideshare trips</title>
<style>
:root {{ --bg: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --rule: #e1e0d9; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #151514; --ink: #f2f1ec; --ink2: #b9b8b1; --rule: #33322f; }}
  img {{ background: #fcfcfb; border-radius: 6px; }}
}}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 760px; margin: 0 auto; padding: 32px 16px 64px; }}
h1 {{ font-size: 1.6rem; line-height: 1.25; margin: 0 0 8px; }}
.big {{ font-size: 2.4rem; font-weight: 650; margin: 24px 0 0; }}
.sub {{ color: var(--ink2); margin: 4px 0 24px; }}
img {{ width: 100%; height: auto; display: block; margin: 16px 0 4px; }}
figcaption, .note {{ color: var(--ink2); font-size: 0.9rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.95rem; }}
td, th {{ border-bottom: 1px solid var(--rule); padding: 6px 4px; text-align: left; }}
</style>
</head>
<body>
<main>
<h1>Did congestion pricing change Uber and Lyft trips into the zone?</h1>
<p class="sub">New York started its congestion fee on January 5, 2025. This page compares
trips into the fee zone with trips into other Manhattan and Brooklyn zones, month by
month, through {esc(ext["last_day_loaded"])}.</p>

<p class="big">{_pct(triple["as_percent"])}</p>
<p class="sub">95% interval {_interval(iv[0], iv[1])}, after taking out the change seen
in a year with no fee.</p>
<p>{esc(headline_sentence(verdict, triple["as_percent"], iv))}</p>

<figure>
<img src="img/{EVENT_FILE}" alt="Monthly change from the same month a year earlier,
with a shaded 95% interval. Points are marked by whether each year had the fee.">
<figcaption>Each point compares a month with the same month a year earlier. In 2024
neither year had the fee. In 2025 the fee year is set against a year without it. In
2026 both years have the fee. The band comes from resampling zones.</figcaption>
</figure>

<figure>
<img src="img/{MAP_FILE}" alt="Map of Manhattan and Brooklyn zones colored by their
change in trips per day from 2024 to 2025, against the control zones.">
<figcaption>Where the change sits. This is a description: no single zone has an
interval or a test.</figcaption>
</figure>

<h2>The numbers behind the headline</h2>
<table>
<tr><th>Comparison</th><th>Change</th><th>95% interval (the wider of the two)</th></tr>
{table}
</table>
<p class="note">The headline is the first row minus the second, on the log scale. It
assumes the gap between the two groups would have moved by the same amount from 2024 to
2025 as it did from 2023 to 2024. Only one earlier pair of years is in the data.</p>

<h2>How this fits the first study</h2>
<p>The study plan was fixed before any result. Its own window, fall 2024 against winter
2025, gave {_pct(float(np.expm1(first["effect"])))} after the same correction (95%
interval {_interval(first_iv[0], first_iv[1])}). That result stays the primary one.
The longer window on this page is a dated amendment, written
before any month after March 2025 was loaded.</p>
<p class="note">Data: NYC TLC High Volume For-Hire Vehicle trip records. Built by
<code>nrc site</code> from the reports in the repo. Not published.</p>
</main>
</body>
</html>
"""


def build_site(paths: config.Paths, img_dir: Path) -> Path:
    """Write site/index.html and its two images. Returns the page path."""
    ext_file = paths.reports / "extended.json"
    plan_file = paths.reports / "estimate.json"
    for f in (ext_file, plan_file):
        if not f.exists():
            raise SiteError(f"{f} is missing; run `nrc estimate` and `nrc extended` first")
    ext = json.loads(ext_file.read_text(encoding="utf-8"))
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    if "triple_difference" not in ext or "triple_difference" not in plan:
        raise SiteError("a stop rule failed, so there is no estimate to show")
    out = paths.root / SITE_DIR
    (out / "img").mkdir(parents=True, exist_ok=True)
    plot_zone_map(ext["zone_changes"], paths.raw / "taxi_zones.zip", img_dir / MAP_FILE)
    for name in (EVENT_FILE, MAP_FILE):
        shutil.copyfile(img_dir / name, out / "img" / name)
    page = out / "index.html"
    page.write_text(page_html(ext, plan), encoding="utf-8")
    return page
