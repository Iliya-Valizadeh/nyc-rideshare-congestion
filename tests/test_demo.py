"""The demo runs the whole pipeline offline (ADR 0004 test 17). conftest blocks sockets."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyc_rideshare_congestion import cli, config, demo


def test_demo_runs_end_to_end_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.run(tmp_path) == config.EXIT_OK
    out = capsys.readouterr().out
    assert out.count("loaded 2024-12: kept") == 1 and "DUPLICATE_TRIP 2" in out
    assert "skip 2024-12: already loaded" in out and "skip 2025-01: already loaded" in out
    assert "check passed: 2 months, 2024-12 to 2025-01" in out
    assert "FAIL freshness 2025-02" in out
    assert "tf_fare_per_mile_by_zone, first 3 rows:" in out
    assert out.rstrip().endswith("demo finished: every step did what it should")


def test_demo_writes_nothing_outside_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["demo"]) == config.EXIT_OK
    assert list(tmp_path.iterdir()) == []
    assert "Fake data only" in capsys.readouterr().out


def test_demo_stops_when_a_step_goes_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo, "MONTHS", ("2024-12", "2025-01", "2025-02"))
    monkeypatch.setattr(demo, "write_fixtures", lambda paths: None)
    assert demo.run(tmp_path) == config.EXIT_FAILED
    assert "FAIL demo: `nrc init` exited 1, expected 0" in capsys.readouterr().out
