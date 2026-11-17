"""Charts for the checks before the estimate, saved to docs/img/.

Colors come from the portfolio palette (the same values as plots.py). Treated zones are
series 1 (blue) and control zones series 2 (orange). Lines are also named on the chart,
so color is never the only way to tell them apart.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # draw to files only

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES_1 = "#2a78d6"
SERIES_1_LIGHT = "#b7d3f6"
SERIES_2 = "#eb6834"


def _frame(title: str) -> tuple[Figure, Axes]:
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)
    return fig, ax


def _save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE, metadata={"Software": None})
    plt.close(fig)
    return path


def _num(days: list[date]) -> Any:
    """Dates as matplotlib numbers, so the axis code stays typed."""
    return mdates.date2num(days)  # type: ignore[no-untyped-call]


def _month_dates(months: list[str]) -> Any:
    return _num([date(int(m[:4]), int(m[5:7]), 15) for m in months])


def _date_axis(ax: Axes) -> None:
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))  # type: ignore[no-untyped-call]
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))  # type: ignore[no-untyped-call]


def plot_trips_indexed(pretrend: dict[str, Any], path: Path) -> Path:
    """Mean trips per day by month for each group, each indexed to its first-year mean."""
    months = pretrend["months"]
    row = pretrend["outcomes"]["trips_per_day_both_platforms"]
    x = _month_dates(months)
    fig, ax = _frame("Trips per day into each group of zones, by month (first-year mean = 100)")
    first_year = [m[:4] == months[0][:4] for m in months]
    for key, color, label in (
        ("treated_by_month", SERIES_1, "Into the fee zone"),
        ("control_by_month", SERIES_2, "Into control zones"),
    ):
        v = np.array(row[key])
        idx = v / v[first_year].mean() * 100
        ax.plot(x, idx, color=color, linewidth=2, marker="o", markersize=4, label=label)
        ax.annotate(
            label,
            (x[-1], idx[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            color=INK_SECONDARY,
            fontsize=9,
        )
    ax.set_ylabel("Index", color=INK_SECONDARY, fontsize=9)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.set_xlim(x[0], x[-1] + 75)
    _date_axis(ax)
    return _save(fig, path)


def plot_log_gap(pretrend: dict[str, Any], path: Path) -> Path:
    """The monthly log ratio with the pre-trend model's fitted values."""
    months = pretrend["months"]
    row = pretrend["outcomes"]["trips_per_day_both_platforms"]
    y = np.array(row["gap_by_month"])
    moy = [int(m[5:7]) for m in months]
    cols = sorted(set(moy))
    design = np.zeros((len(months), len(cols) + 1))
    for i, m in enumerate(moy):
        design[i, cols.index(m)] = 1.0
    design[:, -1] = np.arange(len(months))
    fitted = design @ np.linalg.lstsq(design, y, rcond=None)[0]
    x = _month_dates(months)
    fig, ax = _frame("Log of trips into the fee zone over trips into control zones, by month")
    ax.plot(x, y, color=SERIES_1, linewidth=2, marker="o", markersize=4, label="Data")
    ax.plot(
        x,
        fitted,
        color=MUTED,
        linewidth=1.5,
        linestyle="--",
        label="Model: month-of-year effect plus a straight-line trend",
    )
    ax.set_ylabel("log(treated / control)", color=INK_SECONDARY, fontsize=9)
    _date_axis(ax)
    ax.legend(frameon=False, fontsize=9, loc="best")
    return _save(fig, path)


def plot_weekly_gap(weekly: dict[str, Any], start: date, title: str, path: Path) -> Path:
    """Weekly gap against the before-window mean, with the zone bootstrap band."""
    x = _num([date.fromisoformat(w) for w in weekly["week_start"]])
    fig, ax = _frame(title)
    ax.fill_between(
        x,
        weekly["band_low"],
        weekly["band_high"],
        color=SERIES_1_LIGHT,
        linewidth=0,
        label="95% band, zone bootstrap",
    )
    ax.plot(x, weekly["gap"], color=SERIES_1, linewidth=2, marker="o", markersize=4, label="Week")
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.axvline(float(_num([start])[0]), color=MUTED, linewidth=1, linestyle="--")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %Y"))  # type: ignore[no-untyped-call]
    ax.annotate(
        "after window starts",
        (float(_num([start])[0]), 1.0),
        xycoords=("data", "axes fraction"),
        xytext=(4, -12),
        textcoords="offset points",
        color=INK_SECONDARY,
        fontsize=9,
    )
    ax.set_ylabel("log ratio minus its before-window mean", color=INK_SECONDARY, fontsize=9)
    ax.set_xlabel("Week starting (Monday)", color=INK_SECONDARY, fontsize=9)
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    return _save(fig, path)


SERIES_2_LIGHT = "#f6c9b5"


def _weeks_from_start(weekly: dict[str, Any]) -> list[float]:
    start = date.fromisoformat(weekly["after_starts"])
    origin = start.toordinal() - start.weekday()
    return [(date.fromisoformat(w).toordinal() - origin) / 7 for w in weekly["week_start"]]


def plot_event_study(event: dict[str, Any], path: Path) -> Path:
    """The weekly gap around the start, next to the same weeks one year earlier."""
    fig, ax = _frame("Trips into the fee zone against control zones, week by week")
    for key, color, light, label in (
        ("one_year_earlier", SERIES_2, SERIES_2_LIGHT, "One year earlier (no fee)"),
        ("study", SERIES_1, SERIES_1_LIGHT, "2024 to 2025 (fee starts at week 0)"),
    ):
        weekly = event[key]
        x = _weeks_from_start(weekly)
        ax.fill_between(x, weekly["band_low"], weekly["band_high"], color=light, alpha=0.6, lw=0)
        ax.plot(x, weekly["gap"], color=color, linewidth=2, marker="o", markersize=4, label=label)
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.axvline(-0.5, color=MUTED, linewidth=1, linestyle="--")
    ax.set_ylabel("log ratio minus its before-window mean", color=INK_SECONDARY, fontsize=9)
    ax.set_xlabel("Weeks from the week the after window starts", color=INK_SECONDARY, fontsize=9)
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    return _save(fig, path)


def plot_event_study_monthly(event: dict[str, Any], path: Path) -> Path:
    """ADR 0009: the monthly log ratio minus its value twelve months earlier.

    Three stretches are marked: no fee in either year, a fee year against a year with
    no fee, and two fee years. Each is also named in the legend, so color is never the
    only way to tell them apart.
    """
    months = event["months"]
    x = _month_dates(months)
    y = np.array(event["change_from_a_year_earlier"])
    lo, hi = np.array(event["band_low"]), np.array(event["band_high"])
    fee = np.array(event["fee_in_month"])
    fee_before = np.array(event["fee_in_month_a_year_earlier"])
    parts = [
        (~fee, MUTED, "o", "No fee in either year"),
        (fee & ~fee_before, SERIES_2, "s", "Fee year against a year with no fee"),
        (fee & fee_before, SERIES_1, "^", "Fee in both years"),
    ]
    fig, ax = _frame(
        "Trips into the fee zone against control zones: change from the same month a year earlier"
    )
    ax.axhline(0.0, color=AXIS, linewidth=1)
    ax.fill_between(x, lo, hi, color=GRID, linewidth=0, label="95% interval, zones resampled")
    ax.plot(x, y, color=INK_SECONDARY, linewidth=1)
    for mask, color, marker, label in parts:
        if mask.any():
            ax.plot(
                x[mask],
                y[mask],
                linestyle="none",
                marker=marker,
                markersize=6,
                color=color,
                label=label,
            )
    ax.axvline(_num([date(2025, 1, 5)])[0], color=INK_SECONDARY, linewidth=1, linestyle="--")
    ax.set_ylabel("change in log(treated / control)", color=INK_SECONDARY, fontsize=9)
    _date_axis(ax)
    ax.legend(frameon=False, fontsize=9, loc="best")
    return _save(fig, path)
