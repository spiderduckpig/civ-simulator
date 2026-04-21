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
        outputs={"clothes": 3.0},
    ),
    "tailor_guild": BuildingType(
        key="tailor_guild",
        name="Tailor Guild",
        max_level=5,
        staffable=True,
        cost_resources={"lumber", "fabric"},
        # Smaller workshop, better conversion than heavy factory.
        inputs={"fabric": 2.0},
        outputs={"clothes": 1.5},
    ),
    "foundry": BuildingType(
        key="foundry",
        name="Copper Foundry",
        max_level=20,
        staffable=True,
        cost_resources={"stone", "copper"},
        inputs={"copper_ore": 2.0, "stone": 0.25},
        outputs={"copper": 3.0},
    ),
    "grain_mill": BuildingType(
        key="grain_mill",
        name="Grain Mill",
        max_level=100,
        staffable=True,
        cost_resources={"lumber", "stone", "copper"},
        inputs={"grain": 5.0},
        outputs={"bread": 10},
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
    "paper_mill": BuildingType(
        key="paper_mill",
        name="Paper Mill",
        max_level=10,
        staffable=True,
        cost_resources={"lumber", "stone", "fabric"},
        inputs={"lumber": 2.5, "fabric": 0.2},
        outputs={"paper": 3.0}
    ),
    "housing": BuildingType(
        key="housing",
        name="Housing Construction",
        max_level=100,
        staffable=False,
        cost_resources={"lumber"},
        inputs={"lumber": 6.0, "stone": 2.5, "fabric": 1.5},
        outputs={"housing": 10.0}
    ),
    "shipyard": BuildingType(
        key="shipyard",
        name="Shipyard",
        max_level=10,
        staffable=False,
        cost_resources={"lumber"},
        inputs={"lumber": 10.0, "fabric": 4.0},
        outputs={"ships": 2.0}
    ),
}


def get_building_type(key: str) -> BuildingType | None:
    return BUILDING_TYPES.get(key)
