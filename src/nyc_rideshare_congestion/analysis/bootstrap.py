"""The plan's two bootstraps ("Inference").

Zone bootstrap: each round draws treated zones with replacement from the treated zones,
and control zones from the control zones. `zone_weights` makes every round's draw once
from the plan's seed, so every estimate that uses it shares the same draws.

Week bootstrap: each round draws whole calendar weeks (Monday to Sunday) with
replacement inside each stratum (a window, or a month for the pre-trend series),
keeping all zones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

from .calendar import week_start
from .panel import Array


def _counts(rng: np.random.Generator, n: int, rounds: int) -> Array:
    """How often each of n items is drawn, when each round draws n with replacement."""
    picks = rng.integers(0, n, size=(rounds, n))
    out = np.zeros((rounds, n))
    np.add.at(out, (np.repeat(np.arange(rounds), n), picks.ravel()), 1.0)
    return out


def zone_weights(n_treated: int, n_control: int, rounds: int, seed: int) -> Array:
    """A (rounds, zones) array of draw counts. Treated draws come first, then control."""
    rng = np.random.default_rng(seed)
    treated = _counts(rng, n_treated, rounds)
    control = _counts(rng, n_control, rounds)
    return np.concatenate([treated, control], axis=1)


def zone_group_sums(weights: Array, zone_sums: Array, n_treated: int) -> tuple[Array, Array]:
    """Group totals per round from (zones, ...) sums: returns (treated, control)."""
    flat = zone_sums.reshape(zone_sums.shape[0], -1)
    shape = (weights.shape[0], *zone_sums.shape[1:])
    treated = (weights[:, :n_treated] @ flat[:n_treated]).reshape(shape)
    control = (weights[:, n_treated:] @ flat[n_treated:]).reshape(shape)
    return treated, control


@dataclass(frozen=True)
class Stratum:
    """Days of one window or month, cut into calendar-week blocks."""

    blocks: list[npt.NDArray[np.intp]]


def make_stratum(days: list[date], index: npt.NDArray[np.intp]) -> Stratum:
    """Group day positions into blocks by calendar week. days and index line up."""
    weeks: dict[date, list[int]] = {}
    for d, i in zip(days, index, strict=True):
        weeks.setdefault(week_start(d), []).append(int(i))
    return Stratum([np.array(v, dtype=np.intp) for _, v in sorted(weeks.items())])


def week_draws(
    day_sums: Array, strata: list[Stratum], rounds: int, seed: int
) -> tuple[Array, Array]:
    """Resample weeks in each stratum. day_sums is (days, ...).

    Returns sums of shape (rounds, strata, ...) and day counts of shape (rounds, strata).
    """
    rng = np.random.default_rng(seed)
    sums = []
    n_days = []
    for s in strata:
        block_sums = np.stack([day_sums[b].sum(axis=0) for b in s.blocks])
        block_days = np.array([len(b) for b in s.blocks], dtype=float)
        counts = _counts(rng, len(s.blocks), rounds)
        flat = block_sums.reshape(len(s.blocks), -1)
        sums.append((counts @ flat).reshape(rounds, *day_sums.shape[1:]))
        n_days.append(counts @ block_days)
    return np.stack(sums, axis=1), np.stack(n_days, axis=1)


def interval(samples: Array, level: float) -> tuple[float, float]:
    """The equal-tailed percentile interval of the bootstrap values."""
    if not np.all(np.isfinite(samples)):
        raise ValueError("bootstrap values hold NaN or infinity; a resample had an empty group")
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(samples, [tail, 1.0 - tail])
    return float(lo), float(hi)


def excludes_zero(iv: tuple[float, float]) -> bool:
    return iv[0] > 0.0 or iv[1] < 0.0


def decide_excludes_zero(zone_iv: tuple[float, float], week_iv: tuple[float, float]) -> bool:
    """The plan: if the two intervals disagree on zero, the wider one decides."""
    zone_x, week_x = excludes_zero(zone_iv), excludes_zero(week_iv)
    if zone_x == week_x:
        return zone_x
    wider = zone_iv if zone_iv[1] - zone_iv[0] >= week_iv[1] - week_iv[0] else week_iv
    return excludes_zero(wider)
