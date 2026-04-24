"""City-center building definitions and helpers."""

from __future__ import annotations

from .models import BuildingType


# Buildings are built in-city (not on map tiles). For staffable buildings,
# ``professions`` values must sum to N_EMPLOYEES_PER_LEVEL; non-staffable
# buildings (housing, shipyard) omit the field since they don't consume pop.
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
        professions={"worker": 9, "owner": 1},
    ),
    "tailor_guild": BuildingType(
        key="tailor_guild",
        name="Tailor Guild",
        max_level=5,
        staffable=True,
        cost_resources={"lumber", "fabric"},
        # Smaller workshop, better conversion than heavy factory.
        inputs={"fabric": 2.0},
        outputs={"clothes": 2.0},
        professions={"artisan": 10},
    ),
    "foundry": BuildingType(
        key="foundry",
        name="Copper Foundry",
        max_level=20,
        staffable=True,
        cost_resources={"stone", "copper"},
        inputs={"copper_ore": 2.0, "stone": 0.25},
        outputs={"copper": 3.0},
        professions={"worker": 9, "owner": 1},
    ),
    "grain_mill": BuildingType(
        key="grain_mill",
        name="Grain Mill",
        max_level=100,
        staffable=True,
        cost_resources={"lumber", "stone", "copper"},
        inputs={"grain": 5.0},
        outputs={"bread": 10},
        professions={"worker": 9, "owner": 1},
    ),
    "artisan_works": BuildingType(
        key="artisan_works",
        name="Artisan Works",
        max_level=15,
        staffable=True,
        cost_resources={"lumber", "stone", "fabric"},
        inputs={"lumber": 0.9, "fabric": 0.8},
        outputs={"clothes": 0.75},
        professions={"worker": 9, "owner": 1},
    ),
    "paper_mill": BuildingType(
        key="paper_mill",
        name="Paper Mill",
        max_level=10,
        staffable=True,
        cost_resources={"lumber", "stone", "fabric"},
        inputs={"lumber": 2.5, "fabric": 0.2},
        outputs={"paper": 3.0},
        professions={"worker": 9, "owner": 1},
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
    "jeweler": BuildingType(
        key="jeweler",
        name="Jeweler",
        max_level=10,
        staffable=True,
        cost_resources={"lumber", "stone", "copper"},
        inputs={"sapphires": 0.4, "copper": 2.0},
        outputs={"jewelry": 0.2},
        professions={"artisan": 7, "owner": 2, "aristocrat": 1},
    ),
    "arisanry": BuildingType(
        key="aristanry",
        name="Artisanry",
        max_level=5,
        staffable=True,
        cost_resources={"lumber"},
        inputs={"fabric": 1.0, "lumber": 0.5, "grain": 1.0},
        outputs={"clothes": 1.2, "paper": 1.2, "bread": 2.5},
        professions={"artisan": 10},
    ),
}


def get_building_type(key: str) -> BuildingType | None:
    return BUILDING_TYPES.get(key)
