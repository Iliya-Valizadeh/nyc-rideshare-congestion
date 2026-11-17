"""`nrc site`: the static page is built from fake reports and a fake shapes file.

No warehouse and no real data are needed, so CI runs this test as it is.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import shapefile

from nyc_rideshare_congestion import cli, config, site

PRIMARY = site.PRIMARY


def _shapes(raw: Path) -> None:
    folder = raw / "shp"
    folder.mkdir(parents=True)
    cells = {4: ("Manhattan", 0, 0), 12: ("Manhattan", 0, 1), 24: ("Manhattan", 1, 1)}
    cells |= {11: ("Brooklyn", 2, 0), 7: ("Queens", 5, 5)}
    with shapefile.Writer(str(folder / "taxi_zones"), shapeType=shapefile.POLYGON) as w:
        w.field("LocationID", "N", size=5)
        w.field("borough", "C", size=20)
        for zone, (borough, x, y) in cells.items():
            w.poly([[(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1), (x, y)]])
            w.record(zone, borough)
    with zipfile.ZipFile(raw / "taxi_zones.zip", "w") as zf:
        for f in folder.iterdir():
            zf.write(f, f"taxi_zones/{f.name}")


def _row(effect: float, lo: float, hi: float) -> dict[str, Any]:
    return {"effect": effect, "interval_zone": [lo / 2, hi / 2], "interval_week": [lo, hi]}


def _reports(reports: Path, table_row: str = "holds_zero_inside_smallest_effect") -> None:
    reports.mkdir(parents=True, exist_ok=True)
    ext = {
        "last_day_loaded": "2026-09-30",
        "verdict": {"table_row": table_row, "interval_used_as_percent": [-0.021, 0.018]},
        "triple_difference": {PRIMARY: {"as_percent": -0.0012}},
        "year_over_year": {"outcomes": {PRIMARY: _row(-0.071, -0.09, -0.05)}},
        "check_one_year_earlier": {"outcomes": {PRIMARY: _row(-0.07, -0.088, -0.051)}},
        "zone_changes": [
            {"zone": 4, "group": "treated", "change_vs_control_log": -0.08},
            {"zone": 12, "group": "treated", "change_vs_control_log": None},
            {"zone": 24, "group": "control", "change_vs_control_log": 0.02},
            {"zone": 11, "group": "control", "change_vs_control_log": -0.01},
        ],
    }
    plan = {
        "triple_difference": {PRIMARY: _row(0.0007, -0.0476, 0.0431)},
        "verdict": {"interval_used": [-0.1, -0.05]},
    }
    (reports / "extended.json").write_text(json.dumps(ext), encoding="utf-8")
    (reports / "estimate.json").write_text(json.dumps(plan), encoding="utf-8")


@pytest.fixture
def paths(tmp_path: Path) -> config.Paths:
    p = config.Paths(tmp_path)
    _shapes(p.raw)
    img = tmp_path / "docs" / "img"
    img.mkdir(parents=True)
    (img / site.EVENT_FILE).write_bytes(b"\x89PNG fake")
    return p


def test_cli_builds_the_page_and_both_images(paths: config.Paths) -> None:
    _reports(paths.reports)
    assert cli.main(["--root", str(paths.root), "site"]) == config.EXIT_OK
    out = paths.root / site.SITE_DIR
    page = (out / "index.html").read_text(encoding="utf-8")
    assert (out / "img" / site.MAP_FILE).stat().st_size > 1000
    assert (out / "img" / site.EVENT_FILE).exists()
    assert "-0.1%" in page and "-2.1% to +1.8%" in page
    assert "We find no change larger than 3%" in page
    assert "2026-09-30" in page
    # the table shows the wider interval of each row: here the week interval
    assert "-9.0% to -5.0%" in page
    # the first study's corrected result, with the wider of its two intervals
    assert "+0.1% after the same correction" in page
    assert "-4.6% to +4.4%" in page


@pytest.mark.parametrize(
    ("row", "words"),
    [
        ("leaves_out_zero", "The data are consistent with congestion pricing changing"),
        ("holds_zero_inside_smallest_effect", "We find no change larger than 3%"),
        ("holds_zero_wider_than_smallest_effect", "The result is not clear."),
    ],
)
def test_headline_follows_the_plans_wording(row: str, words: str) -> None:
    text = site.headline_sentence({"table_row": row}, -0.05, [-0.08, -0.02])
    assert text.startswith(words)
    assert "proves" not in text and "no effect" not in text


def test_unknown_verdict_row_is_refused() -> None:
    with pytest.raises(site.SiteError, match="no wording"):
        site.headline_sentence({"table_row": "stop_rule_fails"}, 0.0, [0.0, 0.0])


def test_missing_reports_stop_the_build(paths: config.Paths) -> None:
    with pytest.raises(site.SiteError, match="nrc extended"):
        site.build_site(paths, paths.root / "docs" / "img")


def test_stopped_estimate_stops_the_build(paths: config.Paths) -> None:
    _reports(paths.reports)
    stopped = {"verdict": {"table_rows": ["stop_rule_fails"]}}
    (paths.reports / "extended.json").write_text(json.dumps(stopped), encoding="utf-8")
    with pytest.raises(site.SiteError, match="stop rule"):
        site.build_site(paths, paths.root / "docs" / "img")


def test_wider_picks_the_wider_interval() -> None:
    assert site.wider([0.0, 1.0], [0.0, 2.0]) == [0.0, 2.0]
    assert site.wider([0.0, 2.0], [0.0, 1.0]) == [0.0, 2.0]
