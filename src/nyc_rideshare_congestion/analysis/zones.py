"""Zone groups from the plan, checked against the warehouse and the TLC zone shapes.

The plan lists every zone group by number. This module rebuilds the lists that can be
rebuilt (buffer zones from the shapes, control zones from the zone table) and fails if
they differ from the plan, so the code and the plan cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import shapefile
import shapely
from shapely.geometry import shape

SHAPES_MEMBER = "taxi_zones/taxi_zones.shp"

# The plan's cross-zone rule names its sides in words. These are the boroughs they mean.
# "Manhattan outside treated zones" needs no zone filter here: panel trips never start
# in a treated zone, and control drop-offs are never in one.
_SIDE_BOROUGHS = {
    "Brooklyn": "Brooklyn",
    "Staten Island": "Staten Island",
    "Manhattan outside treated zones": "Manhattan",
    "Bronx": "Bronx",
}


class ZoneListError(ValueError):
    """A zone list rebuilt from data differs from the plan's parameter block."""


@dataclass(frozen=True)
class ZoneGroups:
    treated: tuple[int, ...]
    control_manhattan: tuple[int, ...]
    control_brooklyn: tuple[int, ...]
    buffer: tuple[int, ...]
    island: tuple[int, ...]
    excluded_pickup: tuple[int, ...]
    side_a: tuple[str, ...]
    side_b: tuple[str, ...]

    @property
    def control(self) -> tuple[int, ...]:
        return tuple(sorted(self.control_manhattan + self.control_brooklyn))

    @property
    def panel_zones(self) -> tuple[int, ...]:
        """Treated zones, then control zones, each in number order."""
        return self.treated + self.control


def groups_from_params(p: dict[str, Any]) -> ZoneGroups:
    rule = p["cross_zone_rule"]
    if not rule.get("drop_control_trip_if_ends_on_opposite_sides"):
        raise ZoneListError("the plan's cross-zone rule is switched off; the code expects it on")
    try:
        side_a = tuple(_SIDE_BOROUGHS[s] for s in rule["far_side_a"])
        side_b = tuple(_SIDE_BOROUGHS[s] for s in rule["far_side_b"])
    except KeyError as err:
        raise ZoneListError(f"unknown side in the cross-zone rule: {err}") from err
    groups = ZoneGroups(
        treated=tuple(sorted(p["treated_zones"])),
        control_manhattan=tuple(sorted(p["control_zones_manhattan"])),
        control_brooklyn=tuple(sorted(p["control_zones_brooklyn"])),
        buffer=tuple(sorted(p["buffer_zones"])),
        island=tuple(sorted(p["island_zones"])),
        excluded_pickup=tuple(sorted(p["excluded_pickup_zones"])),
        side_a=side_a,
        side_b=side_b,
    )
    lists = [groups.treated, groups.control, groups.buffer, groups.island]
    flat = [z for zl in lists for z in zl]
    if len(flat) != len(set(flat)):
        raise ZoneListError("a zone is in more than one group in the plan")
    return groups


def read_shapes(shapes_zip: Path) -> tuple[dict[int, Any], dict[int, str]]:
    """Return each zone's shape (parts joined) and borough from the TLC shapes file."""
    parts: dict[int, list[Any]] = {}
    boroughs: dict[int, str] = {}
    with shapefile.Reader(f"{shapes_zip}/{SHAPES_MEMBER}") as reader:
        for item in reader.iterShapeRecords():
            rec = item.record.as_dict()
            zone = int(rec["LocationID"])
            parts.setdefault(zone, []).append(shape(item.shape.__geo_interface__))
            boroughs[zone] = str(rec["borough"])
    return {z: shapely.union_all(g) for z, g in parts.items()}, boroughs


def derive_buffer_zones(
    shapes_zip: Path, treated: tuple[int, ...], island: tuple[int, ...]
) -> list[int]:
    """Manhattan zones outside the treated and island lists that touch a treated zone."""
    geoms, boroughs = read_shapes(shapes_zip)
    treated_area = shapely.union_all([geoms[z] for z in treated])
    return sorted(
        z
        for z, g in geoms.items()
        if boroughs[z] == "Manhattan"
        and z not in treated
        and z not in island
        and g.intersects(treated_area)
    )


def brooklyn_touching_treated(shapes_zip: Path, treated: tuple[int, ...]) -> list[int]:
    geoms, boroughs = read_shapes(shapes_zip)
    treated_area = shapely.union_all([geoms[z] for z in treated])
    return sorted(
        z for z, g in geoms.items() if boroughs[z] == "Brooklyn" and g.intersects(treated_area)
    )


def verify(con: duckdb.DuckDBPyConnection, groups: ZoneGroups, shapes_zip: Path) -> dict[str, Any]:
    """Rebuild the zone lists and fail loudly if any differs from the plan."""
    buffer = derive_buffer_zones(shapes_zip, groups.treated, groups.island)
    if buffer != list(groups.buffer):
        raise ZoneListError(f"buffer zones from the shapes {buffer} != plan {list(groups.buffer)}")
    touching = brooklyn_touching_treated(shapes_zip, groups.treated)
    if touching:
        raise ZoneListError(f"Brooklyn zones {touching} share a border with a treated zone")
    rows = con.execute("SELECT zone_key, borough FROM dim_zone").fetchall()
    manhattan = {int(z) for z, b in rows if b == "Manhattan"}
    brooklyn = sorted(int(z) for z, b in rows if b == "Brooklyn")
    others = set(groups.treated) | set(groups.buffer) | set(groups.island)
    control_m = sorted(manhattan - others)
    if control_m != list(groups.control_manhattan):
        raise ZoneListError(
            f"Manhattan control zones from dim_zone {control_m}"
            f" != plan {list(groups.control_manhattan)}"
        )
    if brooklyn != list(groups.control_brooklyn):
        raise ZoneListError(
            f"Brooklyn zones from dim_zone {brooklyn} != plan {list(groups.control_brooklyn)}"
        )
    if not set(groups.treated) <= manhattan:
        raise ZoneListError("a treated zone is not in Manhattan in dim_zone")
    return {
        "treated": len(groups.treated),
        "control_manhattan": len(groups.control_manhattan),
        "control_brooklyn": len(groups.control_brooklyn),
        "buffer": len(groups.buffer),
        "island": len(groups.island),
        "buffer_rebuilt_from_shapes": buffer,
    }
