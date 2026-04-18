"""City-center building definitions and helpers."""

from __future__ import annotations

from .models import BuildingType


# Buildings are built in-city (not on map tiles).
BUILDING_TYPES: dict[str, BuildingType] = {
    "textile_factory": BuildingType(
        key="textile_factory",
        name="Textile Factory",
        max_level=30,
        staffable=True,
        # The investment code values these resources at market prices.
        cost_resources={"lumber", "stone", "metal"},
        # Per staffed level per tick.
        inputs={"fabric": 5.0},
        outputs={"clothes": 1.0},
    ),
    "tailor_guild": BuildingType(
        key="tailor_guild",
        name="Tailor Guild",
        max_level=20,
        staffable=True,
        cost_resources={"lumber", "fabric"},
        # Smaller workshop, better conversion than heavy factory.
        inputs={"fabric": 2.0},
        outputs={"clothes": 0.85},
    ),
    "foundry": BuildingType(
        key="foundry",
        name="Copper Foundry",
        max_level=20,
        staffable=True,
        cost_resources={"stone", "copper"},
        inputs={"copper_ore": 2.4, "stone": 0.25},
        outputs={"copper": 1.25},
    ),
    "grain_mill": BuildingType(
        key="grain_mill",
        name="Grain Mill",
        max_level=24,
        staffable=True,
        cost_resources={"lumber", "stone", "copper"},
        inputs={"grain": 3.2},
        outputs={"bread": 1.35},
    ),
    "artisan_works": BuildingType(
        key="artisan_works",
        name="Artisan Works",
        max_level=15,
        staffable=True,
        cost_resources={"lumber", "stone", "fabric"},
        inputs={"lumber": 0.9, "fabric": 0.8},
        outputs={"clothes": 0.75},
    ),
}


def get_building_type(key: str) -> BuildingType | None:
    return BUILDING_TYPES.get(key)
