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

import math
import random
from typing import Callable

from .constants import N, IMP, FOCUS, N_EMPLOYEES_PER_LEVEL, BASE_PRICES, TRADE_HOUSE_CAPACITY_PER_EMPLOYEE
from .buildings import BUILDING_TYPES
from .improvements import imp_type, imp_level
from .helpers import cell_on_river
from .mapgen import cell_coastal
from .registry import IMPROVEMENTS
from .capacity import PRODUCER_BUILDINGS
from .economy_profiles import (
    PROFESSION_CONSUMPTION_PROFILES,
    PROFESSION_WAGE_POOL_SHARE,
    STIMULUS_UNEMP_FLOOR,
    STIMULUS_RESERVE_TICKS,
    STIMULUS_DRAWDOWN_CAP,
    STIMULUS_INCOME_MULT,
    CONSUMPTION_INCREASE_MULT,
    CONSUMPTION_DECREASE_MULT,
    profession_consumption_cost,
)


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


def _staffable_building_keys(city) -> list[str]:
    out: list[str] = []
    blevels = getattr(city, "buildings", None) or {}
    for key, lvl in blevels.items():
        if int(lvl) <= 0:
            continue
        if key in PRODUCER_BUILDINGS:
            out.append(key)
            continue
        b = BUILDING_TYPES.get(key)
        if b is not None and b.staffable:
            out.append(key)
    return out


def _building_focus_weight(key: str, focus: int) -> float:
    prod = PRODUCER_BUILDINGS.get(key)
    if prod is not None:
        fk = str(prod.get("focus", ""))
        if focus == FOCUS.FARMING and fk == "FARM":
            return 4.0
        if focus == FOCUS.MINING and fk in ("MINE", "QUARRY"): 
            return 4.0
        if focus == FOCUS.TRADE and fk == "TRADE":
            return 4.0
        if focus == FOCUS.DEFENSE and fk in ("FARM", "MINE"):
            return 2.5
        return 1.0

    # Generic city buildings: rough buckets from key names.
    key_l = key.lower()
    if focus == FOCUS.FARMING and ("grain" in key_l or "mill" in key_l or "housing" in key_l):
        return 2.2
    if focus == FOCUS.MINING and ("foundry" in key_l or "smith" in key_l):
        return 2.2
    if focus == FOCUS.TRADE and ("ship" in key_l or "tailor" in key_l or "factory" in key_l or "trade" in key_l or "trading" in key_l or "merchant" in key_l):
        return 2.0
    return 1.0


def _trade_house_target_staff(city) -> int:
    lvl = int((getattr(city, "buildings", None) or {}).get("trading_house", 0))
    if lvl <= 0:
        return 0
    volume = float(getattr(city, "trade_export_volume", 0.0) or 0.0)
    capacity = float(getattr(city, "trade_capacity_provided", 0.0) or 0.0)

    if volume <= 0.0:
        # No measured trade yet — staff a small bootstrap crew so the city
        # can demonstrate it can export. Without this, trade_export_volume
        # stays at 0 forever, since trade capacity is itself gated on staff.
        used_capacity = float(getattr(city, "trade_potential", 0.0) or 0.0)
        if used_capacity <= 0.0:
            used_capacity = float(getattr(city, "population", 0.0) or 0.0) * 0.15 + 4.0
    elif capacity > 0.0:
        # Volume is implicitly capped by current capacity. Hysteresis on the
        # saturation ratio decides whether to grow, hold, or shrink:
        #   ≥ 0.90 → saturated, leave one worker of headroom so unmet
        #            export demand can show up next tick
        #   ≥ 0.45 → comfortable load, keep the current crew
        #   < 0.45 → genuine slack, fall back to volume
        sat_ratio = volume / capacity
        if sat_ratio >= 0.90:
            used_capacity = volume + TRADE_HOUSE_CAPACITY_PER_EMPLOYEE
        elif sat_ratio >= 0.45:
            used_capacity = capacity
        else:
            used_capacity = volume
    else:
        used_capacity = volume

    return min(lvl, int(math.ceil(used_capacity / TRADE_HOUSE_CAPACITY_PER_EMPLOYEE)))


def _weighted_pick(
    items: list[tuple[object, float]], rand: Callable[[], float],
) -> object | None:
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
    bstaff = city.building_staffing
    blevels = city.buildings
    candidates: list[tuple[int, float]] = []
    for key in _staffable_building_keys(city):
        lvl = int(blevels.get(key, 0))
        cur = int(bstaff.get(key, 0))
        if cur >= lvl:
            continue
        if key == "trading_house" and cur >= _trade_house_target_staff(city):
            continue
        candidates.append((key, _building_focus_weight(key, city.focus)))
    pick = _weighted_pick(candidates, rand)
    if pick is None:
        return False
    bstaff[pick] = int(bstaff.get(pick, 0)) + 1
    return True


def _remove_staff_level(
    city, impr: list, rand: Callable[[], float],
) -> bool:
    bstaff = city.building_staffing
    if not bstaff:
        return False
    candidates: list[tuple[str, float]] = []
    for key in _staffable_building_keys(city):
        cur = int(bstaff.get(key, 0))
        if cur <= 0:
            continue
        fw = _building_focus_weight(key, city.focus)
        candidates.append((key, 1.0 / max(0.1, fw)))
    pick = _weighted_pick(candidates, rand)
    if pick is None:
        return False
    bstaff[pick] = int(bstaff.get(pick, 0)) - 1
    if int(bstaff.get(pick, 0)) <= 0:
        bstaff.pop(pick, None)
    return True


def _cleanup_staffing(city, impr: list) -> None:
    """Drop entries for cells whose improvement was removed/downgraded."""
    # Tile-based staffing is deprecated in the fungible capacity model.
    city.staffing = {}

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


def _vacant_producer_levels(city, impr: list) -> dict[int, int]:
    """Cache how many staffed producer levels are still vacant by type."""
    blevels = getattr(city, "buildings", None) or {}
    bstaff = getattr(city, "building_staffing", None) or {}
    vacant: dict[str, int] = {}
    for key in PRODUCER_BUILDINGS.keys():
        lvl = int(blevels.get(key, 0))
        if lvl <= 0:
            continue
        staffed = int(bstaff.get(key, 0))
        if staffed >= lvl:
            continue
        vacant[key] = lvl - staffed
    return vacant


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

    trade_target = _trade_house_target_staff(city)
    if trade_target <= 0:
        city.building_staffing.pop("trading_house", None)
    else:
        city.building_staffing["trading_house"] = trade_target

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

    # Final strict trade-house clamp: always fire excess merchants when
    # required trade capacity drops.
    trade_target = _trade_house_target_staff(city)
    if trade_target <= 0:
        city.building_staffing.pop("trading_house", None)
    else:
        city.building_staffing["trading_house"] = trade_target

    current = min(target, sum(city.staffing.values()) + sum(city.building_staffing.values()))

    city.employee_level_count = current
    city.professions = profession_breakdown(city, impr)
    city.vacant_producer_levels = _vacant_producer_levels(city, impr)


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
        value = out * p.get("copper_ore", BASE_PRICES["copper_ore"])
        if r == "iron":
            value += 0.45 * eff("iron_ore") * p.get("iron_ore", BASE_PRICES.get("iron_ore", 4.2))
        if r == "sapphires":
            value += 0.22 * eff("sapphires") * p.get("sapphires", BASE_PRICES.get("sapphires", 42.0))
        return value
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
    if bkey == "trading_house":
        return float(getattr(city, "trade_export_income", 0.0) or 0.0)
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


def _producer_profit_per_level(city, key: str) -> float:
    meta = PRODUCER_BUILDINGS.get(key)
    if meta is None:
        return 0.0
    p = city.prices
    out_good = meta.get("good")
    in_good = meta.get("input_good")
    out = float(meta.get("base_output", 0.0))
    eff_good = meta.get("eff_good") or out_good
    out *= float((getattr(city, "local_efficiency", None) or {}).get(eff_good, 1.0))

    bonus = (getattr(city, "capacity_bonuses", None) or {}).get(key, {})
    if float(bonus.get("mult", 0.0)) > 0.0:
        out *= 1.0 + float(bonus.get("mult", 0.0)) * 0.5

    out_val = out * p.get(out_good, BASE_PRICES.get(out_good, 1.0)) if out_good else 0.0
    in_val = float(meta.get("input_per_level", 0.0)) * p.get(in_good, BASE_PRICES.get(in_good, 1.0)) if in_good else 0.0
    return out_val - in_val


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
    city.staffing = {}
    bstaff = city.building_staffing

    # Entries: (base_profit_per_level, key, capacity_levels)
    # Trading house is managed entirely by update_city_employment via
    # _trade_house_target_staff — its profit signal (trade_export_income)
    # collapses to 0 on any tick without trade, which would have this
    # function fire the very staff that enables trade in the first place.
    entries: list[tuple[float, str, int]] = []
    blevels = getattr(city, "buildings", None) or {}
    for bkey, lvl in blevels.items():
        lvl_i = int(lvl)
        if lvl_i <= 0:
            continue
        if bkey == "trading_house":
            continue
        if bkey in PRODUCER_BUILDINGS:
            ppl = _producer_profit_per_level(city, bkey)
        else:
            b = BUILDING_TYPES.get(bkey)
            if b is None or not b.staffable:
                continue
            ppl = _building_profit_per_level(city, bkey)
        entries.append((ppl, bkey, lvl_i))

    slots: dict[str, dict] = {}
    for base_ppl, key, cap in entries:
        cur = bstaff.get(key, 0)
        slots[key] = {
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

    # Single-pass rebalance: at most one fire, at most one move per call.
    # Many small calls converge gradually and avoid the 0 → 2 → 0 swings
    # of full-reset reallocation.
    donor_id: str | None = None
    donor_val = float("inf")
    recv_id: str | None = None
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

    changed = False
    if donor_id is not None:
        fire_one = donor_val <= 0.0
        move_one = (
            recv_id is not None
            and recv_id != donor_id
            and recv_val > donor_val * (1.0 + SWITCH_MARGIN)
        )

        if fire_one:
            slots[donor_id]["alloc"] -= 1
            changed = True
            if move_one:
                slots[recv_id]["alloc"] += 1
        elif move_one:
            slots[donor_id]["alloc"] -= 1
            slots[recv_id]["alloc"] += 1
            changed = True

    if not changed:
        return

    # Rewrite staffing for tracked slots.
    for _, key, _ in entries:
        bstaff.pop(key, None)

    for key, s in slots.items():
        if s["alloc"] <= 0:
            continue
        bstaff[key] = s["alloc"]

    city.employee_level_count = sum(bstaff.values())
    city.professions = profession_breakdown(city, impr)
    city.vacant_producer_levels = _vacant_producer_levels(city, impr)


def profession_breakdown(city, impr: list) -> dict[str, int]:
    """Tally profession headcounts across every staffed improvement and
    building in the city. Pulls the per-level breakdown from the registered
    ImprovementType / BuildingType metadata — adding a new profession is a
    data-only change in constants.py + registry.py + buildings.py.
    """
    counts: dict[str, int] = {}

    bstaff = getattr(city, "building_staffing", None) or {}
    for bkey, level in bstaff.items():
        if level <= 0:
            continue
        if bkey in PRODUCER_BUILDINGS:
            profs = PRODUCER_BUILDINGS[bkey].get("professions", {})
        else:
            b = BUILDING_TYPES.get(bkey)
            if b is None:
                continue
            profs = b.professions
        for prof, per_level in profs.items():
            counts[prof] = counts.get(prof, 0) + per_level * int(level)

    unemployed = int(max(0, getattr(city, "unemployed_pop", 0) or 0))
    if unemployed > 0:
        counts["unemployed"] = counts.get("unemployed", 0) + unemployed

    return counts


def update_city_consumption_state(city) -> None:
    """Update per-profession wages and slow-moving consumption levels.

    This is intentionally cadence-based and cheap: a small number of
    professions are updated from a rolling income snapshot, then their
    consumption tiers are nudged up or down based on whether the current
    basket looks affordable.
    """
    if not hasattr(city, "profession_wages") or city.profession_wages is None:
        city.profession_wages = {}
    if not hasattr(city, "profession_income_shares") or city.profession_income_shares is None:
        city.profession_income_shares = {}
    if not hasattr(city, "consumption_levels") or city.consumption_levels is None:
        city.consumption_levels = {}

    prof_counts = getattr(city, "professions", None) or {}
    effective_counts = dict(prof_counts)
    unemployed_count = int(max(0, getattr(city, "unemployed_pop", 0) or 0))
    if unemployed_count > 0:
        effective_counts["unemployed"] = effective_counts.get("unemployed", 0) + unemployed_count

    if not effective_counts:
        for prof, profile in PROFESSION_CONSUMPTION_PROFILES.items():
            city.profession_wages[prof] = 0.0
            city.profession_income_shares[prof] = 0.0
            city.consumption_levels.setdefault(prof, profile.base_level)
        return

    pop = max(1.0, float(getattr(city, "population", 0.0)))
    income_per_person = float(getattr(city, "income_per_person", 0.0))
    total_income = max(0.0, income_per_person * pop)

    # Slack stimulus: idle labor + idle capital → spend accumulated savings
    # to supplement this tick's wage pool. Raises consumption tiers via the
    # hysteresis below, which raises demand, which reopens investment.
    unemp_rate = unemployed_count / pop
    gold_reserve = float(getattr(city, "gold", 0.0))
    reserve_floor = total_income * STIMULUS_RESERVE_TICKS
    stimulus = 0.0
    if unemp_rate > STIMULUS_UNEMP_FLOOR and gold_reserve > reserve_floor:
        slack = unemp_rate - STIMULUS_UNEMP_FLOOR
        target_draw = total_income * slack * STIMULUS_INCOME_MULT
        max_draw = (gold_reserve - reserve_floor) * STIMULUS_DRAWDOWN_CAP
        stimulus = max(0.0, min(target_draw, max_draw))
        city.gold = gold_reserve - stimulus

    wage_pool = (total_income + stimulus) * PROFESSION_WAGE_POOL_SHARE

    weights: dict[str, float] = {}
    for prof, count in effective_counts.items():
        if count <= 0:
            continue
        profile = PROFESSION_CONSUMPTION_PROFILES.get(prof)
        if profile is None:
            continue
        weights[prof] = count * profile.income_weight

    total_weight = sum(weights.values())
    if total_weight <= 0.0:
        for prof, profile in PROFESSION_CONSUMPTION_PROFILES.items():
            city.profession_wages[prof] = 0.0
            city.profession_income_shares[prof] = 0.0
            city.consumption_levels.setdefault(prof, profile.base_level)
        return

    prices = getattr(city, "prices", None) or {}
    for prof, count in effective_counts.items():
        if count <= 0:
            continue
        profile = PROFESSION_CONSUMPTION_PROFILES.get(prof)
        if profile is None:
            continue

        share = weights.get(prof, 0.0) / total_weight
        group_income = wage_pool * share
        wage = group_income / max(1, int(count))
        city.profession_income_shares[prof] = share
        city.profession_wages[prof] = wage

        current_level = float(city.consumption_levels.get(prof, profile.base_level))
        budget = wage * profile.spend_share
        basket_cost = profession_consumption_cost(prices, prof, current_level)

        # Hysteresis on the budget/basket ratio:
        #   budget < basket × lower_threshold  → can't afford basket, lower level
        #   budget > basket × raise_threshold  → plenty of headroom, raise level
        #   otherwise                          → hold (the "content" band between)
        # raise_threshold > 1 ensures we only climb when there's real slack, not
        # just whenever the decrease condition happens to miss.
        if budget < basket_cost * profile.lower_threshold:
            current_level -= profile.decrease_step * CONSUMPTION_DECREASE_MULT
        elif budget > basket_cost * profile.raise_threshold:
            current_level += profile.increase_step * CONSUMPTION_INCREASE_MULT

        if current_level < profile.min_level:
            current_level = profile.min_level

        city.consumption_levels[prof] = current_level


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
