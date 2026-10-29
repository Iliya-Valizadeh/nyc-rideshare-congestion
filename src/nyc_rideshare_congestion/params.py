"""Read the JSON parameter block in docs/eval_plan.md, so code and plan cannot drift."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nyc_rideshare_congestion import config

_BLOCK = re.compile(r"## Parameter block.*?```json\n(.*?)\n```", re.DOTALL)


def load_params(plan: Path = config.EVAL_PLAN) -> dict[str, Any]:
    """Return the plan's parameter block as a dict. Fails if the block is missing."""
    match = _BLOCK.search(plan.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"{plan}: no JSON block under '## Parameter block'")
    params: dict[str, Any] = json.loads(match.group(1))
    return params
