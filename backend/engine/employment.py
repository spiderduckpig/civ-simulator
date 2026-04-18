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
from .improvements import imp_type, imp_level
from .helpers import cell_on_river
from .mapgen import cell_coastal


# Improvements that take workers. Forts are explicitly excluded — they
# consume metal upkeep but not population.
STAFFABLE_TYPES = frozenset({
    IMP.FARM, IMP.MINE, IMP.LUMBER, IMP.QUARRY, IMP.PASTURE,
    IMP.WINDMILL, IMP.PORT, IMP.SMITHERY, IMP.FISHERY,
})

# Types whose profit-per-worker can be priced directly from output × price.
# Windmill/pasture/port create value through neighbour bonuses or untracked
# flows; they keep their existing staffing during reallocation.
DIRECT_PRODUCER_TYPES = frozenset({
    IMP.FARM, IMP.FISHERY, IMP.MINE, IMP.QUARRY, IMP.LUMBER, IMP.SMITHERY,
})


def _focus_weight(imp_t: int, focus: int) -> float:
    """Hiring preference: focus-matched types get ~4× baseline."""
    if focus == FOCUS.FARMING:
        if imp_t in (IMP.FARM, IMP.WINDMILL, IMP.FISHERY, IMP.PASTURE):
            return 4.0
    elif focus == FOCUS.MINING:
        if imp_t in (IMP.MINE, IMP.QUARRY, IMP.SMITHERY):
            return 4.0
    elif focus == FOCUS.TRADE:
        if imp_t in (IMP.PORT, IMP.FISHERY, IMP.FARM):
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
    if not hasattr(city, "employee_level_count") or city.employee_level_count is None:
        city.employee_level_count = 0

    _cleanup_staffing(city, impr)

    pop = max(0.0, getattr(city, "population", 0.0))
    target = int(pop // N_EMPLOYEES_PER_LEVEL)
    current = int(city.employee_level_count)

    # Re-derive current from staffing sum (keeps things in sync if a
    # staffed building was destroyed out from under us).
    current = min(current, sum(city.staffing.values()))

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
    good. Smithery nets out ore consumption. Caller runs this before prices
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
        out = 1.5 * riv * coast_mult * eff("food")
        return out * p.get("food", BASE_PRICES["food"])
    if it == IMP.FISHERY:
        out = 1.2 * coast_mult * eff("food")
        return out * p.get("food", BASE_PRICES["food"])
    if it == IMP.MINE:
        out = (1.0 if r == "iron" else 0.25) * eff("ore")
        return out * p.get("ore", BASE_PRICES["ore"])
    if it == IMP.QUARRY:
        out = 2.0 * eff("stone")
        return out * p.get("stone", BASE_PRICES["stone"])
    if it == IMP.LUMBER:
        out = 2.0 * eff("lumber")
        return out * p.get("lumber", BASE_PRICES["lumber"])
    if it == IMP.SMITHERY:
        out = 2.0 * eff("metal")
        return out * (
            p.get("metal", BASE_PRICES["metal"])
            - p.get("ore", BASE_PRICES["ore"])
        )
    return 0.0


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
    return best


def reallocate_workers_by_profit(
    city, impr: list, ter: list, res: dict, rivers, good_efficiency,
) -> None:
    """Greedy sort-and-refill reallocation among direct-producer buildings.

    For each staffable producer cell, compute profit/level from current
    efficiency and prices; sort descending; give the top cells as many
    staffed levels as their building allows until the worker budget runs
    out or the next cell's profit ≤ 0 (leaving workers unemployed beats
    staffing a losing building).

    Non-producer staffables (windmill/pasture/port) keep their current
    staffing — their value model isn't priced. Their workers count against
    the budget so we don't over-assign.

    O(B log B) where B = staffable cells in the city (typically < 30).
    Safe to call every ~10 ticks."""
    if not hasattr(city, "staffing") or city.staffing is None:
        city.staffing = {}
    staffing = city.staffing

    producer_entries: list[tuple[float, int, int]] = []
    fixed_workers = 0
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
            producer_entries.append((ppl, cell, bldg_lvl))
        else:
            fixed_workers += staffing.get(cell, 0)

    budget = max(0, int(city.employee_level_count) - fixed_workers)

    for _, cell, _ in producer_entries:
        staffing.pop(cell, None)

    producer_entries.sort(key=lambda x: -x[0])

    remaining = budget
    for ppl, cell, bldg_lvl in producer_entries:
        if remaining <= 0 or ppl <= 0:
            break
        take = min(bldg_lvl, remaining)
        staffing[cell] = take
        remaining -= take

    city.employee_level_count = sum(staffing.values())


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
