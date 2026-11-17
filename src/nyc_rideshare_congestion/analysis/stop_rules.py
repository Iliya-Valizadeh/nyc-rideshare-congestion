"""The plan's stop rules and pipeline checks (docs/eval_plan.md, "Data-quality rules").

If any stop rule fails, no outcome is reported. Each rule returns its numbers, so a
pass is as visible as a fail.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.pipeline import checks

from .calendar import month_of, window_days
from .zones import ZoneGroups

UNKNOWN_DROPOFF = (264, 265)
WINDOWS = ("placebo_before", "placebo_after", "before", "after")


def _key(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def window_months(p: dict[str, Any]) -> list[str]:
    days = [d for name in WINDOWS for d in window_days(p, name)]
    return sorted({month_of(d) for d in days})


def rule_months_loaded(con: duckdb.DuckDBPyConnection, months: list[str]) -> dict[str, Any]:
    """Every month cast to the target schema and was loaded exactly once."""
    loaded = checks.loaded_months(con)
    bad = [m for m in months if len(loaded.get(m, [])) != 1]
    return {"months": len(months), "not_loaded_once": bad, "result": "fail" if bad else "pass"}


def rule_quarantine_share(
    con: duckdb.DuckDBPyConnection, months: list[str], limit: float
) -> dict[str, Any]:
    loaded = checks.loaded_months(con)
    shares = {}
    for m in months:
        rows = loaded.get(m, [])
        if rows:
            r = rows[0]
            in_scope = r.rows_kept + r.rows_quarantined
            shares[m] = r.rows_quarantined / in_scope if in_scope else 0.0
    worst = max(shares, key=lambda k: shares[k]) if shares else None
    over = [m for m, s in shares.items() if s > limit]
    return {
        "limit": limit,
        "max_share": shares[worst] if worst else None,
        "max_month": worst,
        "months_over": over,
        "result": "fail" if over or len(shares) != len(months) else "pass",
    }


def rule_quarantine_gap(
    con: duckdb.DuckDBPyConnection, groups: ZoneGroups, months: list[str], limit: float
) -> dict[str, Any]:
    """Per month, quarantine share for trips ending in treated vs control zones."""
    t, c = list(groups.treated), list(groups.control)
    keys = [int(m.replace("-", "")) for m in months]
    kept = {
        int(r[0]): (int(r[1] or 0), int(r[2] or 0))
        for r in con.execute(
            """
            SELECT date_key // 100,
                   sum(trip_count) FILTER (WHERE list_contains($t, do_zone_key)),
                   sum(trip_count) FILTER (WHERE list_contains($c, do_zone_key))
            FROM fact_trip_hourly
            WHERE list_contains($keys, date_key // 100)
            GROUP BY 1
            """,
            {"t": t, "c": c, "keys": keys},
        ).fetchall()
    }
    quarantined = {
        str(r[0]): (int(r[1] or 0), int(r[2] or 0))
        for r in con.execute(
            """
            SELECT source_month,
                   count(*) FILTER (WHERE list_contains($t, DOLocationID)),
                   count(*) FILTER (WHERE list_contains($c, DOLocationID))
            FROM quarantine_trip
            WHERE list_contains($months, source_month)
            GROUP BY 1
            """,
            {"t": t, "c": c, "months": months},
        ).fetchall()
    }
    rows: list[dict[str, Any]] = []
    for m, k in zip(months, keys, strict=True):
        kt, kc = kept.get(k, (0, 0))
        qt, qc = quarantined.get(m, (0, 0))
        st = qt / (qt + kt) if qt + kt else 0.0
        sc = qc / (qc + kc) if qc + kc else 0.0
        rows.append({"month": m, "treated": st, "control": sc, "gap": abs(st - sc)})
    worst = max(rows, key=lambda r: r["gap"])
    over = [r["month"] for r in rows if r["gap"] > limit]
    return {
        "limit": limit,
        "max_gap": worst["gap"],
        "max_month": worst["month"],
        "months_over": over,
        "by_month": rows,
        "result": "fail" if over else "pass",
    }


def _unknown_share(con: duckdb.DuckDBPyConnection, days: list[date]) -> float:
    row = con.execute(
        """
        SELECT sum(trip_count) FILTER (WHERE list_contains($unknown, do_zone_key)),
               sum(trip_count)
        FROM fact_trip_hourly
        WHERE list_contains($days, date_key)
        """,
        {"unknown": list(UNKNOWN_DROPOFF), "days": [_key(d) for d in days]},
    ).fetchone()
    if not row or not row[1]:
        return float("nan")
    return float(row[0] or 0) / float(row[1])


def rule_unknown_dropoff(
    con: duckdb.DuckDBPyConnection, p: dict[str, Any], limit: float
) -> dict[str, Any]:
    """Share of Uber and Lyft trips with an unknown drop-off, before vs after.

    The windows are used whole, as the plan defines them. The holiday list applies to
    the estimate, not to this data check.
    """
    before = _unknown_share(con, window_days(p, "before"))
    after = _unknown_share(con, window_days(p, "after"))
    change = abs(after - before)
    return {
        "limit": limit,
        "before": before,
        "after": after,
        "change": change,
        "result": "pass" if change <= limit else "fail",
    }


def rule_platform_days(
    con: duckdb.DuckDBPyConnection, p: dict[str, Any], min_trips: int
) -> dict[str, Any]:
    """Every day of the study and placebo windows has trips for both platforms."""
    days = sorted({d for name in WINDOWS for d in window_days(p, name)})
    rows = con.execute(
        """
        WITH want AS (
            SELECT unnest($days) AS date_key
        ),
        cells AS (
            SELECT w.date_key, pl.platform_key FROM want AS w CROSS JOIN dim_platform AS pl
        ),
        trips AS (
            SELECT date_key, platform_key, sum(trip_count) AS n
            FROM fact_trip_hourly WHERE list_contains($days, date_key) GROUP BY 1, 2
        )
        SELECT c.date_key, c.platform_key
        FROM cells AS c LEFT JOIN trips AS t USING (date_key, platform_key)
        WHERE coalesce(t.n, 0) < $min
        ORDER BY 1, 2
        """,
        {"days": [_key(d) for d in days], "min": min_trips},
    ).fetchall()
    short = [f"{r[0]} platform {r[1]}" for r in rows]
    return {
        "days": len(days),
        "min_trips": min_trips,
        "short_platform_days": short,
        "result": "fail" if short else "pass",
    }


def run(
    con: duckdb.DuckDBPyConnection,
    p: dict[str, Any],
    groups: ZoneGroups,
    paths: config.Paths,
    months: list[str] | None = None,
) -> dict[str, Any]:
    """All stop rules, then the pipeline checks the plan lists before analysis.

    `months` defaults to the months of the plan's study. The longer window (ADR 0009)
    passes every loaded month.
    """
    limits = p["stop_rules"]
    if months is None:
        months = config.months(config.FIRST_MONTH, config.PLAN_LAST_MONTH)
    rules = {
        "months_loaded": rule_months_loaded(con, months),
        "quarantine_share": rule_quarantine_share(
            con, months, float(limits["max_quarantine_share"])
        ),
        "quarantine_gap": rule_quarantine_gap(
            con, groups, window_months(p), float(limits["max_quarantine_gap"])
        ),
        "unknown_dropoff_change": rule_unknown_dropoff(
            con, p, float(limits["max_unknown_dropoff_change"])
        ),
        "platform_days": rule_platform_days(con, p, int(limits["min_rows_per_platform_per_day"])),
    }
    pipeline = [f.line() for f in checks.run_checks(con, paths, months, limits)]
    failed = [k for k, v in rules.items() if v["result"] == "fail"]
    return {
        "rules": rules,
        "failed_rules": failed,
        "pipeline_check_failures": pipeline,
        "result": "fail" if failed or pipeline else "pass",
    }
