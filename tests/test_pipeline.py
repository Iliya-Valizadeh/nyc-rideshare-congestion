"""Tests for `make eval`: reports/metrics.json must match the committed analysis reports."""

from __future__ import annotations

import json
from pathlib import Path

from nyc_rideshare_congestion import __version__, evaluate

REPO = Path(__file__).resolve().parent.parent


def test_version_is_set() -> None:
    assert __version__


def test_committed_metrics_match_the_committed_reports() -> None:
    """reports/metrics.json must be what `make eval` builds from the analysis reports."""
    committed = json.loads((REPO / "reports" / "metrics.json").read_text(encoding="utf-8"))
    fresh = evaluate._round(evaluate.build_metrics(REPO / "reports"))
    flat_committed, flat_fresh = _flatten(committed), _flatten(fresh)
    assert flat_committed.keys() == flat_fresh.keys()
    for key, value in flat_fresh.items():
        assert flat_committed[key] == value, key


def test_metrics_hold_no_numbers_when_a_stop_rule_fails(tmp_path: Path) -> None:
    stops = {"result": "fail", "failed_rules": ["platform_days"]}
    est = {"verdict": {"table_rows": ["stop_rule_fails"]}}
    (tmp_path / "stop_rules.json").write_text(json.dumps(stops), encoding="utf-8")
    (tmp_path / "estimate.json").write_text(json.dumps(est), encoding="utf-8")
    assert evaluate.main(["--reports-dir", str(tmp_path)]) == 0
    written = json.loads((tmp_path / evaluate.METRICS_FILE).read_text(encoding="utf-8"))
    assert written["failed_rules"] == ["platform_days"]
    assert "main" not in written


def _flatten(node: object, prefix: str = "") -> dict[str, object]:
    if isinstance(node, dict):
        out: dict[str, object] = {}
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}{key}."))
        return out
    return {prefix.rstrip("."): node}
