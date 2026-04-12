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

from .constants import N, IMP, FOCUS, N_EMPLOYEES_PER_LEVEL
from .improvements import imp_type, imp_level


# Improvements that take workers. Forts are explicitly excluded — they
# consume metal upkeep but not population.
STAFFABLE_TYPES = frozenset({
    IMP.FARM, IMP.MINE, IMP.LUMBER, IMP.QUARRY, IMP.PASTURE,
    IMP.WINDMILL, IMP.PORT, IMP.SMITHERY, IMP.FISHERY,
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
