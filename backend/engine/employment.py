"""City employment: population allocates workforce to improvements.

Each staffable improvement has a *staffed level* (0..building-level). A
level-K building fully staffed (l == K) produces at 100%; partly staffed
(l < K) produces at ``l / K`` of full. Windmills are the exception and
scale linearly — their neighbour-farm bonus uses the staffed level
directly in place of the building level.

Instead of re-distributing every tick we track an ``employee_level_count``
per city and only add/remove staff levels when the city population crosses
a multiple of ``N_EMPLOYEES_PER_LEVEL``. Allocation and removal are
biased by the city's current focus.
"""

from __future__ import annotations

import random
from typing import Callable

from .constants import N, IMP, FOCUS, N_EMPLOYEES_PER_LEVEL, BASE_PRICES
from .buildings import BUILDING_TYPES
from .improvements import imp_type, imp_level
from .helpers import cell_on_river
from .mapgen import cell_coastal


# Improvements that take workers. Forts are explicitly excluded — they
# consume metal upkeep but not population.
STAFFABLE_TYPES = frozenset({
    IMP.FARM, IMP.MINE, IMP.LUMBER, IMP.QUARRY, IMP.PASTURE,
    IMP.WINDMILL, IMP.PORT, IMP.SMITHERY, IMP.FISHERY, IMP.COTTON,
})

# Types whose profit-per-worker can be priced directly from output × price.
# Windmill/pasture/port create value through neighbour bonuses or untracked
# flows; they keep their existing staffing during reallocation.
DIRECT_PRODUCER_TYPES = frozenset({
    IMP.FARM, IMP.FISHERY, IMP.COTTON, IMP.MINE, IMP.QUARRY, IMP.LUMBER, IMP.SMITHERY,
})


def _focus_weight(imp_t: int, focus: int) -> float:
    """Hiring preference: focus-matched types get ~4× baseline."""
    if focus == FOCUS.FARMING:
        if imp_t in (IMP.FARM, IMP.COTTON, IMP.WINDMILL, IMP.FISHERY, IMP.PASTURE):
            return 4.0
    elif focus == FOCUS.MINING:
        if imp_t in (IMP.MINE, IMP.QUARRY, IMP.SMITHERY):
            return 4.0
    elif focus == FOCUS.TRADE:
        if imp_t in (IMP.PORT, IMP.FISHERY, IMP.FARM, IMP.COTTON):
            return 4.0
    elif focus == FOCUS.DEFENSE:
        # Defense doesn't produce anything staffable, so feed the soldiers.
        if imp_t in (IMP.FARM, IMP.PASTURE, IMP.SMITHERY):
            return 3.0
    return 1.0


def _employable_cells(city, impr: list) -> list[int]:
    out: list[int] = []
    for c in city.tiles:
        if not (0 <= c < N):
            continue
        raw = impr[c]
        if raw and imp_type(raw) in STAFFABLE_TYPES:
            out.append(c)
    return out


def _weighted_pick(
    items: list[tuple[int, float]], rand: Callable[[], float],
) -> int | None:
    total = sum(w for _, w in items)
    if total <= 0:
        return None
    pick = rand() * total
    acc = 0.0
    for cell, w in items:
        acc += w
        if pick <= acc:
            return cell
    return items[-1][0]


def _add_staff_level(
    city, impr: list, rand: Callable[[], float],
) -> bool:
    staffing = city.staffing
    candidates: list[tuple[int, float]] = []
    for cell in _employable_cells(city, impr):
        lvl = imp_level(impr[cell])
        cur = staffing.get(cell, 0)
        if cur >= lvl:
            continue  # already maxed
        candidates.append((cell, _focus_weight(imp_type(impr[cell]), city.focus)))
    pick = _weighted_pick(candidates, rand)
    if pick is None:
        return False
    staffing[pick] = staffing.get(pick, 0) + 1
    return True


def _remove_staff_level(
    city, impr: list, rand: Callable[[], float],
) -> bool:
    staffing = city.staffing
    if not staffing:
        return False
    candidates: list[tuple[int, float]] = []
    for cell in _employable_cells(city, impr):
        cur = staffing.get(cell, 0)
        if cur <= 0:
            continue
        # Invert focus weight: fire non-focus workers first.
        fw = _focus_weight(imp_type(impr[cell]), city.focus)
        candidates.append((cell, 1.0 / fw))
    pick = _weighted_pick(candidates, rand)
    if pick is None:
        return False
    staffing[pick] -= 1
    if staffing[pick] <= 0:
        staffing.pop(pick, None)
    return True


def _cleanup_staffing(city, impr: list) -> None:
    """Drop entries for cells whose improvement was removed/downgraded."""
    staffing = city.staffing
    tiles_set = set(city.tiles)
    for cell in list(staffing.keys()):
        if cell not in tiles_set or not (0 <= cell < N):
            staffing.pop(cell, None)
            continue
        raw = impr[cell]
        if not raw or imp_type(raw) not in STAFFABLE_TYPES:
            staffing.pop(cell, None)
            continue
        lvl = imp_level(raw)
        if staffing[cell] > lvl:
            staffing[cell] = lvl
        if staffing[cell] <= 0:
            staffing.pop(cell, None)

    # Keep building staffing in bounds as city-building levels change.
    if not hasattr(city, "building_staffing") or city.building_staffing is None:
        city.building_staffing = {}
    bstaff = city.building_staffing
    blevels = getattr(city, "buildings", None) or {}
    for bkey in list(bstaff.keys()):
        lvl = int(blevels.get(bkey, 0))
        if lvl <= 0:
            bstaff.pop(bkey, None)
            continue
        if bstaff[bkey] > lvl:
            bstaff[bkey] = lvl
        if bstaff[bkey] <= 0:
            bstaff.pop(bkey, None)


def update_city_employment(
    city, impr: list, *, rand: Callable[[], float] = random.random,
) -> None:
    """Reconcile ``city.employee_level_count`` with current population.

    Called once per city per tick (after pop growth & city development).
    Staffing only changes when population crosses a multiple of
    ``N_EMPLOYEES_PER_LEVEL`` — otherwise this is nearly free.
    """
    if not hasattr(city, "staffing") or city.staffing is None:
        city.staffing = {}
    if not hasattr(city, "building_staffing") or city.building_staffing is None:
        city.building_staffing = {}
    if not hasattr(city, "employee_level_count") or city.employee_level_count is None:
        city.employee_level_count = 0

    _cleanup_staffing(city, impr)

    pop = max(0.0, getattr(city, "population", 0.0))
    target = int(pop // N_EMPLOYEES_PER_LEVEL)
    current = int(city.employee_level_count)

    # Re-derive current from staffing sum (keeps things in sync if a
    # staffed building was destroyed out from under us).
    current = min(current, sum(city.staffing.values()) + sum(city.building_staffing.values()))

    if target > current:
        for _ in range(target - current):
            if not _add_staff_level(city, impr, rand):
                break
            current += 1
    elif target < current:
        for _ in range(current - target):
            if not _remove_staff_level(city, impr, rand):
                break
            current -= 1

    city.employee_level_count = current


def _profit_per_level(
    cell: int, raw: int, city, ter: list, res: dict, rivers, good_eff,
) -> float:
    """Gold/tick produced by one staffed level at this cell.

    Multiplies the per-level marginal output (mirrors the formulas in
    simulation.py's production pass) by the city's current price for that
    good. Smithery nets out copper-ore consumption. Caller runs this before prices
    are recomputed for the new tick, so it reflects last tick's post-trade
    equilibrium."""
    it = imp_type(raw)
    riv = 2.0 if cell_on_river(cell, rivers) else 1.0
    coast_mult = 1.5 if cell_coastal(cell, ter) else 1.0
    r = res.get(cell)

    def eff(good: str) -> float:
        if not good_eff:
            return 1.0
        field = good_eff.get(good)
        return field[cell] if field is not None else 1.0

    p = city.prices

    if it == IMP.FARM:
        # Windmill coupling is ignored — all farms in a city roughly share
        # the same bonus structure so it doesn't shift the ordering.
        out = 1.5 * riv * coast_mult * eff("grain")
        return out * p.get("grain", BASE_PRICES["grain"])
    if it == IMP.FISHERY:
        out = 1.2 * coast_mult * eff("grain")
        return out * p.get("grain", BASE_PRICES["grain"])
    if it == IMP.COTTON:
        out = 1.0 * riv * coast_mult * eff("fabric")
        return out * p.get("fabric", BASE_PRICES["fabric"])
    if it == IMP.MINE:
        out = (1.0 if r == "iron" else 0.25) * eff("copper_ore")
        return out * p.get("copper_ore", BASE_PRICES["copper_ore"])
    if it == IMP.QUARRY:
        out = 2.0 * eff("stone")
        return out * p.get("stone", BASE_PRICES["stone"])
    if it == IMP.LUMBER:
        out = 2.0 * eff("lumber")
        return out * p.get("lumber", BASE_PRICES["lumber"])
    if it == IMP.SMITHERY:
        out = 2.0 * eff("copper")
        return out * (
            p.get("copper", BASE_PRICES["copper"])
            - p.get("copper_ore", BASE_PRICES["copper_ore"])
        )
    return 0.0


def _building_profit_per_level(city, bkey: str) -> float:
    """Gold/tick for one staffed building level."""
    b = BUILDING_TYPES.get(bkey)
    if b is None:
        return 0.0
    p = city.prices
    out_v = sum(
        amount * p.get(good, BASE_PRICES.get(good, 1.0))
        for good, amount in b.outputs.items()
    )
    in_v = sum(
        amount * p.get(good, BASE_PRICES.get(good, 1.0))
        for good, amount in b.inputs.items()
    )
    return out_v - in_v


def best_vacancy_profit(
    city, impr: list, ter: list, res: dict, rivers, good_efficiency,
) -> float:
    """Highest profit/level among unstaffed slots in this city.

    Represents the best job a migrant could walk into. Ignores producer
    cells that are already fully staffed and ignores slots whose
    profit/level is non-positive (an unprofitable vacancy shouldn't
    attract anyone). Returns 0.0 if no profitable vacancy exists.

    O(B) with B = staffable producer cells in the city."""
    staffing = getattr(city, "staffing", None) or {}
    best = 0.0
    for cell in city.tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        if not raw:
            continue
        it = imp_type(raw)
        if it not in DIRECT_PRODUCER_TYPES:
            continue
        bldg_lvl = imp_level(raw)
        if bldg_lvl <= 0:
            continue
        if staffing.get(cell, 0) >= bldg_lvl:
            continue
        ppl = _profit_per_level(cell, raw, city, ter, res, rivers, good_efficiency)
        if ppl > best:
            best = ppl

    # Include city-building vacancies in migration pull calculations.
    if hasattr(city, "buildings") and city.buildings:
        bstaff = getattr(city, "building_staffing", None) or {}
        for bkey, lvl in city.buildings.items():
            if lvl <= 0:
                continue
            if bstaff.get(bkey, 0) >= lvl:
                continue
            ppl = _building_profit_per_level(city, bkey)
            if ppl > best:
                best = ppl
    return best


def reallocate_workers_by_profit(
    city, impr: list, ter: list, res: dict, rivers, good_efficiency,
) -> None:
    """Market-impact-aware reallocation among producer improvements/buildings.

    Rebalancing is intentionally one-step per reshuffle: at most one worker
    is fired from the weakest slot, and at most one worker is moved into a
    clearly better slot. This keeps staffing changes gradual and avoids the
    0 -> 2 -> 0 style swings that came from full reset reallocations.

    Non-producer staffables (windmill/pasture/port) keep their staffing and
    are left alone here.
    """
    if not hasattr(city, "staffing") or city.staffing is None:
        city.staffing = {}
    if not hasattr(city, "building_staffing") or city.building_staffing is None:
        city.building_staffing = {}
    staffing = city.staffing
    bstaff = city.building_staffing

    # Entries: (base_profit_per_level, kind, key, capacity_levels)
    producer_entries: list[tuple[float, str, object, int]] = []
    for cell in city.tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        if not raw:
            continue
        it = imp_type(raw)
        if it not in STAFFABLE_TYPES:
            continue
        bldg_lvl = imp_level(raw)
        if bldg_lvl <= 0:
            continue
        if it in DIRECT_PRODUCER_TYPES:
            ppl = _profit_per_level(cell, raw, city, ter, res, rivers, good_efficiency)
            producer_entries.append((ppl, "imp", cell, bldg_lvl))

    # City buildings compete for the same workforce budget.
    blevels = getattr(city, "buildings", None) or {}
    for bkey, lvl in blevels.items():
        if lvl <= 0:
            continue
        ppl = _building_profit_per_level(city, bkey)
        producer_entries.append((ppl, "bld", bkey, int(lvl)))

    # Build slot table keyed by (kind, key).
    slots: dict[tuple[str, object], dict] = {}
    for base_ppl, kind, key, cap in producer_entries:
        cur = staffing.get(key, 0) if kind == "imp" else bstaff.get(key, 0)
        slots[(kind, key)] = {
            "base": base_ppl,
            "cap": max(0, int(cap)),
            "alloc": max(0, int(cur)),
        }

    # Cap current allocations to each slot's capacity.
    for s in slots.values():
        if s["alloc"] > s["cap"]:
            s["alloc"] = s["cap"]

    # Market-impact proxy and anti-oscillation knobs.
    MARKET_IMPACT_BETA = 0.35
    SWITCH_MARGIN = 0.15

    def _marginal_value(slot: dict, current_alloc: int) -> float:
        """Value of adding one more staffed level at current_alloc.

        Diminishes with occupancy to approximate own impact on prices.
        """
        base = float(slot["base"])
        if base <= 0:
            return base
        return base / (1.0 + MARKET_IMPACT_BETA * max(0, current_alloc))

    donor_id = None
    donor_val = float("inf")
    recv_id = None
    recv_val = 0.0

    for sid, s in slots.items():
        if s["alloc"] > 0:
            val = _marginal_value(s, s["alloc"] - 1)
            if val < donor_val:
                donor_val = val
                donor_id = sid
        if s["alloc"] < s["cap"]:
            val = _marginal_value(s, s["alloc"])
            if val > recv_val:
                recv_val = val
                recv_id = sid

    if donor_id is not None:
        fire_one = donor_val <= 0.0
        move_one = (
            recv_id is not None
            and recv_id != donor_id
            and recv_val > donor_val * (1.0 + SWITCH_MARGIN)
        )

        if fire_one:
            slots[donor_id]["alloc"] -= 1
            if move_one:
                slots[recv_id]["alloc"] += 1
        elif move_one:
            slots[donor_id]["alloc"] -= 1
            slots[recv_id]["alloc"] += 1

    # Rewrite only producer slots; keep non-producer staffing untouched.
    for _, kind, key, _ in producer_entries:
        if kind == "imp":
            staffing.pop(key, None)
        else:
            bstaff.pop(key, None)

    for (kind, key), s in slots.items():
        if s["alloc"] <= 0:
            continue
        if kind == "imp":
            staffing[key] = s["alloc"]
        else:
            bstaff[key] = s["alloc"]

    city.employee_level_count = sum(staffing.values()) + sum(bstaff.values())


def staffed_level(city, cell: int, building_level: int) -> int:
    """Return staffed levels for ``cell``, clamped to ``building_level``."""
    staffing = getattr(city, "staffing", None)
    if not staffing:
        return 0
    v = staffing.get(cell, 0)
    if v > building_level:
        v = building_level
    if v < 0:
        v = 0
    return v
