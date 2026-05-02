"""Procedural tile -> city production capacity model.

Capacities are generated from terrain, resources, river/coast access, and
regional efficiency fields. Production buildings are fungible city-level
levels constrained by these capacities.
"""

from __future__ import annotations

from typing import Dict, Tuple

from .constants import T
from .helpers import cell_on_river
from .mapgen import cell_coastal


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# Former tile improvements now represented as fungible building pools.
PRODUCER_BUILDINGS: dict[str, dict] = {
    "farm": {
        "label": "Farm",
        "icon": "🌾",
        "good": "grain",
        "eff_good": "grain",
        "base_output": 2.4,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "FARM",
        "professions": {"farmer": 9, "aristocrat": 1},
    },
    "cotton_farm": {
        "label": "Cotton Farm",
        "icon": "🧵",
        "good": "fabric",
        "eff_good": "fabric",
        "base_output": 1.35,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "FARM",
        "professions": {"farmer": 9, "aristocrat": 1},
    },
    "fishery": {
        "label": "Fishery",
        "icon": "🐟",
        "good": "meat",
        # Terrain efficiency for meat falls back to neutral; fishery placement
        # already requires coastal access via tile capacity.
        "eff_good": "grain",
        "base_output": 1.5,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "FARM",
        "professions": {"fisherman": 9, "aristocrat": 1},
    },
    "lumber_camp": {
        "label": "Lumber Camp",
        "icon": "🪵",
        "good": "lumber",
        "eff_good": "lumber",
        "base_output": 2.0,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "LUMBER",
        "professions": {"lumberjack": 9, "aristocrat": 1},
    },
    "quarry": {
        "label": "Quarry",
        "icon": "🧱",
        "good": "stone",
        "eff_good": "stone",
        "base_output": 2.0,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "QUARRY",
        "professions": {"miner": 9, "aristocrat": 1},
    },
    "copper_mine": {
        "label": "Copper Mine",
        "icon": "⛏",
        "good": "copper_ore",
        "eff_good": "copper_ore",
        "base_output": 1.2,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "MINE",
        "professions": {"miner": 9, "aristocrat": 1},
    },
    "iron_mine": {
        "label": "Iron Mine",
        "icon": "⛓",
        "good": "iron_ore",
        "eff_good": "iron_ore",
        "base_output": 1.1,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "MINE",
        "professions": {"miner": 9, "aristocrat": 1},
    },
    "sapphire_mine": {
        "label": "Sapphire Mine",
        "icon": "⛓",
        "good": "sapphires",
        "eff_good": "sapphires",
        "base_output": 0.5,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "MINE",
        "professions": {"miner": 9, "aristocrat": 1},
    },
    "port": {
        "label": "Port",
        "icon": "⚓",
        "good": "ships",
        "eff_good": "ships",
        "base_output": 0.7,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "TRADE",
        "professions": {"sailor": 9, "aristocrat": 1},
    },
    "windmill": {
        "label": "Windmill",
        "icon": "🌀",
        "good": "grain",
        "eff_good": "grain",
        "base_output": 0.45,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "FARM",
        "professions": {"miller": 9, "aristocrat": 1},
    },
    "pasture": {
        "label": "Pasture",
        "icon": "🐄",
        "good": "meat",
        # Pasture benefits from grain-fertile terrain (grassland, plains).
        "eff_good": "grain",
        "base_output": 0.9,
        "input_good": None,
        "input_per_level": 0.0,
        "focus": "FARM",
        "professions": {"rancher": 9, "aristocrat": 1},
    },
}


# Shared capacity pools: farms and cotton farms consume a single agricultural
# pool so they are fungible and compete against one another.
SHARED_CAPACITY_GROUPS: dict[str, tuple[str, ...]] = {
    "agri": ("farm", "cotton_farm"),
}


def _eff(good_efficiency: dict | None, good: str, cell: int) -> float:
    if not good_efficiency:
        return 1.0
    field = good_efficiency.get(good)
    if field is None or not (0 <= cell < len(field)):
        return 1.0
    return _clamp(float(field[cell]), 0.2, 2.8)


def compute_tile_capacity(
    cell: int,
    ter: list,
    res: dict,
    rivers,
    good_efficiency: dict | None,
) -> tuple[dict[str, int], dict[str, dict[str, float]]]:
    """Return (capacity, bonus) contributed by one tile.

    Bonus format: {key: {"slots": int, "mult": float}}
    where the first N staffed levels on that building key get +mult output.
    """
    t = ter[cell]
    r = res.get(cell)
    on_river = cell_on_river(cell, rivers)
    coastal = cell_coastal(cell, ter)

    land = 1.0 if t > T.COAST else 0.0
    if land <= 0.0:
        return {}, {}

    grain_eff = _eff(good_efficiency, "grain", cell)
    fabric_eff = _eff(good_efficiency, "fabric", cell)
    lumber_eff = _eff(good_efficiency, "lumber", cell)
    stone_eff = _eff(good_efficiency, "stone", cell)
    copper_eff = _eff(good_efficiency, "copper_ore", cell)
    iron_eff = _eff(good_efficiency, "iron_ore", cell)
    sapphire_eff = _eff(good_efficiency, "sapphires", cell)

    fertile = 0.0
    openness = 0.0
    if t in (T.PLAINS, T.GRASS):
        fertile += 1.1
        openness += 1.0
    elif t in (T.HILLS,):
        fertile += 0.6
    elif t in (T.JUNGLE,):
        fertile += 0.12
    elif t in (T.SWAMP,):
        fertile += 0.15
    elif t in (T.FOREST, T.DFOREST):
        fertile += 0.19
    elif t in (T.TUNDRA,):
        fertile += 0.03
    if on_river:
        fertile *= 2.0
    if coastal:
        fertile *= 1.5
    fertile += 0.55 * (grain_eff - 1.0)
    fertile = _clamp(fertile, 0.0, 12.8)

    rugged = 0.0
    if t in (T.HILLS, T.MTN):
        rugged += 1.2
    elif t in (T.FOREST, T.DFOREST, T.TUNDRA):
        rugged += 0.45
    rugged += 0.45 * (stone_eff - 1.0)
    rugged += 0.40 * (copper_eff - 1.0)
    rugged += 0.35 * (iron_eff - 1.0)
    rugged = _clamp(rugged, 0.0, 12.8)

    woodland = 0.0
    if t in (T.FOREST, T.DFOREST, T.JUNGLE):
        woodland += 1.35
    elif t in (T.PLAINS, T.GRASS):
        woodland += 0.10
    woodland += 0.60 * (lumber_eff - 1.0)
    woodland = _clamp(woodland, 0.0, 2.8)

    coastal_factor = 1.0 if coastal else 0.0

    caps: dict[str, int] = {
        "farm": max(0, int(round(0 + 10 * fertile + (16 if r == "wheat" else 0)))),
        "cotton_farm": max(0, int(round(0 + 7 * (0.03 * fertile + 0.5 * max(0.0, fabric_eff - 0.2))))),
        "fishery": max(0, int(round(coastal_factor * (6 + 10 * grain_eff + (12 if r == "fish" else 0))))),
        "lumber_camp": max(0, int(round(0 + 6 * woodland + (10 if r == "wood" else 0)))),
        "quarry": max(0, int(round(0 + 4 * rugged + (12 if r == "stone" else 0)))),
        "copper_mine": max(0, int(round(0 + 0 * rugged + (8 if r == "gold" else 0) + 3 * copper_eff))),
        "iron_mine": max(0, int(round((8 + 0.0 * rugged + 12 * iron_eff) if r == "iron" else (1 + 2.5 * rugged * max(0.0, iron_eff - 0.9))))),
        "sapphire_mine": max(0, rugged * sapphire_eff),
        "port": max(0, int(round(coastal_factor * (5 + 9 * (0.5 + 0.5 * grain_eff))))),
        "pasture": max(0, int(round((0 + 2 * fertile + 4 * openness)))),
    }

    bonus: dict[str, dict[str, float]] = {}

    def _add_bonus(key: str, slots: int, mult: float) -> None:
        if slots <= 0 or mult <= 0.0:
            return
        prev = bonus.get(key)
        if prev is None:
            bonus[key] = {"slots": float(slots), "mass": float(slots) * mult}
        else:
            prev["slots"] += float(slots)
            prev["mass"] += float(slots) * mult

    if on_river:
        _add_bonus("farm", int(round(caps["farm"] * 0.55)), 0.22)
        _add_bonus("cotton_farm", int(round(caps["cotton_farm"] * 0.55)), 0.15)
    if coastal:
        _add_bonus("fishery", int(round(caps["fishery"] * 0.65)), 0.20)
        _add_bonus("port", int(round(caps["port"] * 0.60)), 0.18)
    if r == "wheat":
        _add_bonus("farm", int(round(caps["farm"] * 0.35)), 0.18)
    if r == "fish":
        _add_bonus("fishery", int(round(caps["fishery"] * 0.35)), 0.20)
    if r == "iron":
        _add_bonus("iron_mine", int(round(caps["iron_mine"] * 0.45)), 0.22)
    if t in (T.FOREST, T.DFOREST):
        _add_bonus("lumber_camp", int(round(caps["lumber_camp"] * 0.35)), 0.15)

    out_bonus: dict[str, dict[str, float]] = {}
    for key, item in bonus.items():
        slots = int(round(item["slots"]))
        mass = float(item.get("mass", 0.0))
        mult = (mass / slots) if slots > 0 else 0.0
        out_bonus[key] = {"slots": slots, "mult": mult}

    return caps, out_bonus


def compute_city_capacities(
    city,
    ter: list,
    res: dict,
    rivers,
    good_efficiency: dict | None,
) -> None:
    """Compute aggregate capacities and bonuses for a city from its tiles."""
    cap_total: dict[str, int] = {k: 0 for k in PRODUCER_BUILDINGS.keys()}
    bonus_slots: dict[str, int] = {}
    bonus_mass: dict[str, float] = {}
    tile_caps: dict[int, dict[str, int]] = {}
    tile_bonus: dict[int, dict[str, dict[str, float]]] = {}

    for cell in getattr(city, "tiles", []) or []:
        if not (0 <= cell < len(ter)):
            continue
        caps, bonus = compute_tile_capacity(cell, ter, res, rivers, good_efficiency)
        if caps:
            tile_caps[cell] = caps
            for key, v in caps.items():
                cap_total[key] = cap_total.get(key, 0) + int(v)
        if bonus:
            tile_bonus[cell] = bonus
            for key, b in bonus.items():
                slots = int(b.get("slots", 0))
                mult = float(b.get("mult", 0.0))
                if slots <= 0:
                    continue
                bonus_slots[key] = bonus_slots.get(key, 0) + slots
                bonus_mass[key] = bonus_mass.get(key, 0.0) + slots * mult

    city.capacities = cap_total
    city.shared_capacities = {
        "agri": int(cap_total.get("farm", 0) + cap_total.get("cotton_farm", 0)),
    }
    city.capacity_bonuses = {
        key: {
            "slots": int(bonus_slots.get(key, 0)),
            "mult": (bonus_mass[key] / bonus_slots[key]) if bonus_slots.get(key, 0) > 0 else 0.0,
        }
        for key in PRODUCER_BUILDINGS.keys()
    }
    city.tile_capacities = tile_caps
    city.tile_capacity_bonuses = tile_bonus


def clamp_city_buildings_to_capacity(city) -> bool:
    """Clamp producer building levels/staffing to current city capacities.

    Returns True when any level/staffing was reduced.
    """
    changed = False
    buildings = getattr(city, "buildings", None) or {}
    bstaff = getattr(city, "building_staffing", None) or {}
    caps = getattr(city, "capacities", None) or {}

    for key in PRODUCER_BUILDINGS.keys():
        cur = int(buildings.get(key, 0))
        cap = int(caps.get(key, 0))
        if cur > cap:
            buildings[key] = cap
            changed = True

    # Shared agricultural cap: farm + cotton_farm compete for one pool.
    shared = int((getattr(city, "shared_capacities", None) or {}).get("agri", 0))
    farm = int(buildings.get("farm", 0))
    cotton = int(buildings.get("cotton_farm", 0))
    total_agri = farm + cotton
    if total_agri > shared:
        overflow = total_agri - shared
        drop_cotton = min(cotton, overflow)
        if drop_cotton > 0:
            buildings["cotton_farm"] = cotton - drop_cotton
            overflow -= drop_cotton
            changed = True
        if overflow > 0:
            buildings["farm"] = max(0, int(buildings.get("farm", 0)) - overflow)
            changed = True

    for key in list(bstaff.keys()):
        lvl = int(buildings.get(key, 0))
        cur = int(bstaff.get(key, 0))
        if cur > lvl:
            bstaff[key] = lvl
            changed = True
        if int(bstaff.get(key, 0)) <= 0:
            bstaff.pop(key, None)

    city.buildings = buildings
    city.building_staffing = bstaff
    return changed
