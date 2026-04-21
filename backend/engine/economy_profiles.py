"""Centralized economy parameters for improvements, resources, and demand.

Simulation should read from this module rather than embedding per-good or
per-improvement constants in the tick loop.
"""

from __future__ import annotations

from .constants import IMP
from .models import ImprovementEconomyProfile


IMPROVEMENT_ECONOMY: dict[int, ImprovementEconomyProfile] = {
    IMP.FARM: ImprovementEconomyProfile(
        improvement_type=IMP.FARM,
        output_good="grain",
        output_eff_good="grain",
        output_base=2.5,
        output_per_level=1.5,
        use_river_mult=True,
        use_coast_mult=True,
        windmill_bonus_per_staffed_level=0.5,
        counts_as_worked_tile=True,
    ),
    IMP.FISHERY: ImprovementEconomyProfile(
        improvement_type=IMP.FISHERY,
        output_good="grain",
        output_eff_good="grain",
        output_base=2.0,
        output_per_level=1.2,
        use_coast_mult=True,
        counts_as_worked_tile=True,
    ),
    IMP.COTTON: ImprovementEconomyProfile(
        improvement_type=IMP.COTTON,
        output_good="fabric",
        output_eff_good="fabric",
        output_base=1.5,
        output_per_level=0.9,
        use_river_mult=True,
        use_coast_mult=True,
        counts_as_worked_tile=True,
    ),
    IMP.MINE: ImprovementEconomyProfile(
        improvement_type=IMP.MINE,
        output_good="copper_ore",
        output_eff_good="copper_ore",
        output_base=0.5,
        output_per_level=0.25,
        resource_output_multiplier={"iron": 2.0},
        counts_as_worked_tile=True,
    ),
    IMP.QUARRY: ImprovementEconomyProfile(
        improvement_type=IMP.QUARRY,
        output_good="stone",
        output_eff_good="stone",
        output_base=0.0,
        output_per_level=2.0,
        counts_as_worked_tile=True,
    ),
    IMP.LUMBER: ImprovementEconomyProfile(
        improvement_type=IMP.LUMBER,
        output_good="lumber",
        output_eff_good="lumber",
        output_base=0.0,
        output_per_level=2.0,
        counts_as_worked_tile=True,
    ),
    IMP.SMITHERY: ImprovementEconomyProfile(
        improvement_type=IMP.SMITHERY,
        output_good="copper",
        output_eff_good="copper",
        output_base=0.0,
        output_per_level=2.0,
        demand_good="copper_ore",
        demand_per_output=1.0,
        counts_as_worked_tile=True,
    ),
    # Non-output improvements still count as worked tiles for city stats.
    IMP.PASTURE: ImprovementEconomyProfile(improvement_type=IMP.PASTURE),
    IMP.WINDMILL: ImprovementEconomyProfile(improvement_type=IMP.WINDMILL),
    IMP.PORT: ImprovementEconomyProfile(improvement_type=IMP.PORT),
    IMP.FORT: ImprovementEconomyProfile(improvement_type=IMP.FORT),
}


# Per-resource flat tile effects applied when the city controls that tile.
# Values are added directly to city supply (or misc income).
RESOURCE_TILE_EFFECTS: dict[str, dict[str, float | bool]] = {
    "wheat": {"good": "grain", "amount": 2.0, "river_mult": True},
    "fish": {"good": "grain", "amount": 1.5, "coast_mult": True},
    "iron": {"good": "copper_ore", "amount": 1.0},
    "stone": {"good": "stone", "amount": 1.0},
    "gold": {"income_misc": 2.0},
}


CITY_BASE_DEMAND_PER_POP: dict[str, float] = {
    "grain": 0.08,
    "fabric": 0.002,
    "clothes": 0.005,
    "lumber": 0.015,
    "stone": 0.01,
    "paper": 0.002,
    "housing": 0.01
}

FORT_METAL_DEMAND_PER_TILE = 1.5


# Base-demand good and its substitutes. Demand for base_good is reallocated
# across members in simulation based on weighted local availability.
SUBSTITUTE_GROUPS: tuple[dict[str, object], ...] = (
    {
        "base_good": "grain",
        "members": {
            "grain": 1.0,
            "bread": 1.8,
        },
    },
)
