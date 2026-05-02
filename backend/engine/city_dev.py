"""Per-city investment, focus HMM, and improvement placement."""

from __future__ import annotations

import math
import random
from typing import Callable, Optional

from .constants import (
    N, IMP, FOCUS, T, CAN_FARM,
    INVEST_PERIOD_TICKS, FOCUS_HMM_PERIOD,
    FORT_BUILD_METAL_COST, BASE_PRICES, N_EMPLOYEES_PER_LEVEL,
    TRADE_HOUSE_CAPACITY_PER_EMPLOYEE,
    PRICE_MULT_MIN, PRICE_CURVE_SPAN, PRICE_CURVE_ANCHOR, PRICE_CURVE_STEEPNESS,
)
from . import employment
from .employment import STAFFABLE_TYPES as _STAFFABLE
from .improvements import (
    imp_type, imp_level, upgrade_imp, make_imp,
    max_level, UPGRADABLE_TYPES,
    best_improvement, advanced_structure_for,
)
from .helpers import neighbors, cell_on_river
from .buildings import BUILDING_TYPES, get_building_type
from .government import ensure_government
from .economy_profiles import IMPROVEMENT_ECONOMY
from .capacity import PRODUCER_BUILDINGS
from .models import City, Civ


# Terrain types a fort is allowed to sit on.
_FORT_TERRAIN = (T.PLAINS, T.GRASS, T.FOREST, T.HILLS)

# Construction economics tuning.
NEW_TILE_BASE_COST = 500.0
NEW_BUILDING_BASE_COST = 3000.0
CAPEX_PAYBACK_TICKS = 2400.0
BUILDING_PAYBACK_TICKS = 1800.0

def _touch_city_production(city: City) -> None:
    city._production_version = getattr(city, "_production_version", 0) + 1


# ── Upgrade cost ─────────────────────────────────────────────────────────────

def _upgrade_cost(city: City, it: int, current_level: int) -> float:
    """Flat cost for tile improvements regardless of level.

    Tile-improvement levels mainly act as capacity markers — production
    happens via the fungible producer-building pool. Scaling cost with
    level didn't carry useful economic signal beyond "rich cities can
    afford more", so we keep it flat and let ROI drive build decisions.
    """
    return NEW_TILE_BASE_COST


def _try_buy(city: City, cost: float) -> bool:
    if city.gold < cost:
        return False
    city.gold -= cost
    return True


def _try_buy_fort(city: City, civ: Civ, cost: float) -> bool:
    gov = ensure_government(civ)
    if gov.treasury < cost:
        return False
    gov.treasury -= cost
    return True


def _building_upgrade_cost(city: City, bkey: str, current_level: int) -> float:
    """Gold cost for city-center building construction/upgrades.

    Building recipes store a *set* of resources; we price that set against
    the local market and combine it with a level-scaling base cost.
    """
    b = get_building_type(bkey)
    if b is None:
        return float("inf")
    level_mult = max(1.0, current_level + 1) ** 1.5
    base_gold = NEW_BUILDING_BASE_COST if current_level <= 0 else 2000.0 * level_mult
    #mat_cost = 0.0
    #for good in b.cost_resources:
    #    mat_cost += city.prices.get(good, BASE_PRICES.get(good, 1.0)) * 4.0 * level_mult
    return base_gold 


def _city_is_profitable_for_expansion(city: City) -> bool:
    """Expansion gate. Scale-relative: thresholds are fractions of population
    so a 1000-pop city isn't held to the same bar as a 40-pop hamlet."""
    pop = max(1.0, float(getattr(city, "population", 0.0)))
    income = float(getattr(city, "income_total", 0.0))
    satisfaction = float(getattr(city, "market_satisfaction", 0.0))
    quality_income = income * (0.25 + 0.75 * (satisfaction ** 2.0))
    # Liquid gold worth ~½ pop unlocks investment regardless of current-tick
    # quality signal. Keeps slack cities (lots of gold, weak income) from
    # deadlocking while the stimulus loop catches up.
    if float(getattr(city, "gold", 0.0)) >= pop * 0.5:
        return True
    return quality_income >= pop * 0.01


# Block new construction when more than this fraction of existing staffable
# levels are unstaffed. The per-element gates (≤1 unstaffed level per
# building/tile) pass independently, so without a city-wide check a
# gold-rich city happily stacks idle capacity across many factories at once.
_MAX_VACANCY_RATIO = 0.10





def _try_build_city_buildings(city: City, impr: list) -> bool:
    """Pure-ROI growth of city-center buildings up to closed-form targets.

    `_estimate_target_building_levels` projects how many levels each
    building should have at equilibrium prices. The loop greedily adds
    levels to the highest-ROI underbuilt building, projecting staffing
    forward so consecutive upgrades stack before real employment runs.
    """
    if not hasattr(city, "buildings") or city.buildings is None:
        city.buildings = {}
    if not hasattr(city, "building_staffing") or city.building_staffing is None:
        city.building_staffing = {}


    targets = _estimate_target_building_levels(city)

    staffing = city.building_staffing
    remaining_unemployed = int(getattr(city, "unemployed_pop", 0))
    filled: dict[str, int] = {}
    upgraded_any = False

    while True:
        best_key: Optional[str] = None
        best_score = 0.0
        for key, b in BUILDING_TYPES.items():
            cur_lvl = int(city.buildings.get(key, 0))
            if cur_lvl >= b.max_level:
                continue
            target = min(int(targets.get(key, cur_lvl)), b.max_level)
            if cur_lvl >= target:
                continue
            if remaining_unemployed >= N_EMPLOYEES_PER_LEVEL:
                virtual_staffed = int(staffing.get(key, 0)) + filled.get(key, 0)
                if cur_lvl - virtual_staffed > 0:
                    continue
            if key == "trading_house":
                trade_profit = employment._building_profit_per_level(city, key)
                trade_required = float(getattr(city, "trade_capacity_required", 0.0) or 0.0)
                trade_provided = float(getattr(city, "trade_capacity_provided", 0.0) or 0.0)

                # If we already have a measurable trade profit signal, use it.
                # Otherwise estimate a sensible per-level value from available
                # signals (volume, required capacity, or a population fallback)
                # so the first trading house levels can be justified by ROI.
                projected_profit = trade_profit

                if projected_profit <= 0.0:
                    trade_volume = float(getattr(city, "trade_export_volume", 0.0) or 0.0)
                    pop = max(1.0, float(getattr(city, "population", 0.0) or 0.0))
                    pop_fallback = pop * 0.15 + 4.0
                    est_volume = max(trade_volume, trade_required, pop_fallback)

                    # Estimate an average value-per-volume unit. Prefer the
                    # observed average if available; otherwise fall back to a
                    # conservative fraction of the base-price mean.
                    avg_val = 0.0
                    if trade_volume > 0.0:
                        avg_val = float(getattr(city, "trade_export_income", 0.0) or 0.0) / trade_volume
                    else:
                        avg_base = sum(BASE_PRICES.values()) / max(1, len(BASE_PRICES))
                        avg_val = avg_base * 0.25

                    # Per staffed-level capacity-worth profit estimate.
                    projected_profit = avg_val * TRADE_HOUSE_CAPACITY_PER_EMPLOYEE

                # If capacity is nearly saturated, additional levels are
                # worth more than the current average — apply the same
                # headroom multiplier used elsewhere.
                if trade_provided > 0.0 and trade_required > trade_provided * 0.9:
                    projected_profit *= min(2.0, trade_required / trade_provided)

                score = projected_profit - (_building_upgrade_cost(city, key, cur_lvl) / BUILDING_PAYBACK_TICKS)
                if score > best_score:
                    best_score = score
                    best_key = key
                continue
            out_v = 0.0
            for g, amt in b.outputs.items():
                out_v += amt * city.prices.get(g, BASE_PRICES.get(g, 1.0))
            in_v = 0.0
            for g, amt in b.inputs.items():
                in_v += amt * city.prices.get(g, BASE_PRICES.get(g, 1.0))
            margin = out_v - in_v
            cost = _building_upgrade_cost(city, key, cur_lvl)
            score = margin - (cost / BUILDING_PAYBACK_TICKS)
            if score > best_score:
                best_score = score
                best_key = key

        if best_key is None:
            break

        cur_lvl = int(city.buildings.get(best_key, 0))
        cost = _building_upgrade_cost(city, best_key, cur_lvl)

        # Labor opportunity cost: when no idle labor is available, the
        # marginal level must clear a friction threshold to justify pulling
        # a worker off a lower-margin existing job. Friction scales with
        # the upgrade's own cost so it stays meaningful as prices drift.
        friction_threshold = cost / BUILDING_PAYBACK_TICKS * 0.5
        if remaining_unemployed < N_EMPLOYEES_PER_LEVEL and best_score < friction_threshold:
            break

        if not _try_buy(city, cost):
            break

        city.buildings[best_key] = cur_lvl + 1
        if remaining_unemployed >= N_EMPLOYEES_PER_LEVEL:
            filled[best_key] = filled.get(best_key, 0) + 1
            remaining_unemployed -= N_EMPLOYEES_PER_LEVEL
        upgraded_any = True

    return upgraded_any


# ── Profitability hint ────────────────────────────────────────────────────
# Producer-good pairs derived from economy profiles so city development
# automatically follows newly added goods/improvements.
_PRODUCER_GOOD_PAIRS: tuple[tuple[int, str], ...] = tuple(
    (imp_t, profile.output_good)
    for imp_t, profile in IMPROVEMENT_ECONOMY.items()
    if profile.output_good
)

# Primary output-good for each producer type (if any). Drives per-call
# saturation and need weighting in the greedy development loop.
_IMP_TO_GOOD: dict[int, str] = {
    imp_t: good for imp_t, good in _PRODUCER_GOOD_PAIRS
}

# Minimum price-to-base ratio before we consider a good "scarce enough to
# chase". Anything under this stays at baseline and doesn't hijack the goal.
_PROFIT_THRESHOLD = 1.15

# Probability of substituting the profitable pick in for the goal-queue pick
# on a build-new action. Heavy weight per the design brief; the goal still
# wins the remainder of the time.
_PROFIT_BIAS = 0.7


def _most_profitable_imp(city: City) -> Optional[int]:
    """Return the producer imp with the highest expected profit per build
    at this city, where profit = local_price_ratio × regional_efficiency.

    Cities in a low-stone region see their quarry score stay low even if
    stone prices are high — which pushes them toward importing stone rather
    than pouring gold into a quarry that barely produces anything.
    """
    best_imp: Optional[int] = None
    best_score = _PROFIT_THRESHOLD
    local_eff = getattr(city, "local_efficiency", None) or {}
    for imp_t, good in _PRODUCER_GOOD_PAIRS:
        base = BASE_PRICES.get(good, 1.0)
        price = city.prices.get(good, base)
        price_ratio = price / max(base, 0.01)
        eff = local_eff.get(good, 1.0)
        score = price_ratio * eff
        if score > best_score:
            best_score = score
            best_imp = imp_t
    return best_imp


# ── Tile picking heuristics ────────────────────────────────────────────────

def _pick_upgrade_candidate(
    city: City, impr: list, *, preferred_types: set,
) -> Optional[int]:
    # Lowest-level upgradable tile, preferring the focus's types.
    tiles = city.tiles
    if not tiles:
        return None

    candidates = []
    for cell in tiles:
        if not (0 <= cell < N): continue
        raw = impr[cell]
        if not raw: continue
        it = imp_type(raw)
        lvl = imp_level(raw)
        if it not in UPGRADABLE_TYPES: continue
        if lvl >= max_level(it): continue
        
        # Priority: (is_not_preferred, level, random_tiebreaker)
        is_pref = 0 if it in preferred_types else 1
        candidates.append((is_pref, lvl, random.random(), cell))

    if not candidates:
        return None
        
    candidates.sort()
    return candidates[0][3]


def _focus_preferred_types(focus: int) -> set:
    if focus == FOCUS.FARMING:
        return {IMP.FARM, IMP.COTTON, IMP.WINDMILL, IMP.FISHERY}
    if focus == FOCUS.MINING:
        return {IMP.MINE, IMP.QUARRY}
    if focus == FOCUS.DEFENSE:
        return {IMP.FORT, IMP.FARM}
    if focus == FOCUS.TRADE:
        return {IMP.PORT, IMP.FISHERY, IMP.FARM, IMP.COTTON}
    return {IMP.FARM}


def _tile_profit(
    city: City, cell: int, imp_type_id: int, ter: list, res: dict, rivers: dict,
    good_efficiency: dict | None,
) -> float:
    """Gold/tick for one staffed level of ``imp_type_id`` at ``cell``."""
    t = ter[cell]

    if imp_type_id == IMP.FARM:
        if not (t in CAN_FARM or (
            cell_on_river(cell, rivers)
            and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT)
        )):
            return 0.0
    elif imp_type_id == IMP.COTTON:
        if not (t in CAN_FARM or (
            cell_on_river(cell, rivers)
            and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT)
        )):
            return 0.0
    elif imp_type_id == IMP.FISHERY:
        if not any(0 <= n < N and ter[n] <= T.COAST for n in neighbors(cell)):
            return 0.0
    elif imp_type_id == IMP.LUMBER:
        if t not in (T.FOREST, T.DFOREST, T.JUNGLE):
            return 0.0
    elif imp_type_id == IMP.QUARRY:
        if t not in (T.HILLS, T.MTN):
            return 0.0
    elif imp_type_id == IMP.MINE:
        if t <= T.COAST:
            return 0.0
    else:
        return 0.0

    return employment._profit_per_level(
        cell, make_imp(imp_type_id, 1), city, ter, res, rivers, good_efficiency or city.local_efficiency,
    )




# Max tile-level upgrades the development loop will allow stacked on a single
# tile in one call. Stops a greedy city from taking a farm L1 → L49 in one
# invest tick just because it has the gold; forces growth to spread across
# tiles and types. Across multiple ticks the tile can still climb to max.
_MAX_UPGRADES_PER_TILE_PER_CALL = 50

# Tolerance for unstaffed levels on a tile before we refuse to upgrade it.
# Tightened from 1 to 0: any unstaffed level blocks the upgrade. Combined
# with the city-wide _has_capacity_headroom gate, this stops cities from
# stacking idle capacity across many tiles.
_MAX_UNSTAFFED_FOR_UPGRADE = 10


_BUILD_CANDS = tuple(dict.fromkeys(_IMP_TO_GOOD.keys()))


# ── Focus HMM transitions ──────────────────────────────────────────────────

def _focus_transition(
    city: City, civ_is_at_war: bool, *, rand: Callable[[], float] = random.random,
) -> None:
    f = city.focus
    pop = max(1.0, city.population)
    
    # Use supply dict for signals
    grain = city.supply.get("grain", 0.0) + city.supply.get("bread", 0.0) + city.supply.get("meat", 0.0)
    copper_ore = city.supply.get("copper_ore", 0.0)
    iron_ore = city.supply.get("iron_ore", 0.0)
    stone = city.supply.get("stone", 0.0)
    
    # Trade potential is now more abstract but we can use gold income or 
    # specific trade-related stocks if needed. For now, use a baseline.
    trade = city.gold / 50.0 
    coastal = city.coastal

    # Scale-free evidence signals (all fractions of population, not raw numbers)
    food_deficit = grain < pop * 0.12
    food_surplus = grain > pop * 0.25
    ore_rich     = (copper_ore + iron_ore + stone) > pop * 0.05
    trade_rich   = trade > pop * 0.25

    # Build transition weights from current state f.
    weights: dict[int, float] = {}

    if f == FOCUS.FARMING:
        if ore_rich:       weights[FOCUS.MINING]  = 0.14
        if civ_is_at_war:  weights[FOCUS.DEFENSE] = 0.40
        if trade_rich or coastal:
            weights[FOCUS.TRADE]  = 0.16 if trade_rich else 0.08

    elif f == FOCUS.MINING:
        if food_deficit:   weights[FOCUS.FARMING] = 0.30
        if civ_is_at_war:  weights[FOCUS.DEFENSE] = 0.40
        if trade_rich:     weights[FOCUS.TRADE]   = 0.10

    elif f == FOCUS.DEFENSE:
        if not civ_is_at_war:
            weights[FOCUS.FARMING] = 0.30
            if ore_rich:   weights[FOCUS.MINING] = 0.15
            if trade_rich: weights[FOCUS.TRADE]  = 0.12

    elif f == FOCUS.TRADE:
        if food_deficit:    weights[FOCUS.FARMING] = 0.25
        if civ_is_at_war:   weights[FOCUS.DEFENSE] = 0.40
        if ore_rich and not trade_rich:
            weights[FOCUS.MINING] = 0.12

    total = sum(weights.values())
    if total <= 0:
        return

    roll = rand()
    if roll >= total:
        return  # stay put

    # Choose a target state weighted by its transition probability
    acc = 0.0
    pick = rand() * total
    for tgt, w in weights.items():
        acc += w
        if pick <= acc:
            city.focus = tgt
            return


# ── Advanced structure / new-improvement placement ─────────────────────────

def _place_new_improvement(
    city: City, ter: list, res: dict, rivers: dict, impr: list,
    *, rand: Callable[[], float] = random.random,
) -> bool:
    """Try to place a new improvement on one of the city's empty tiles.

    Uses ``best_improvement`` (focus-aware, HMM-style) for the pick. Returns
    True if an improvement was built (and paid for), False otherwise.
    """
    tiles = city.tiles
    if not tiles:
        return False

    # Filter for empty tiles within our assigned area
    empties = [c for c in tiles if 0 <= c < N and impr[c] == IMP.NONE]
    if not empties:
        return False
    
    # Increase search depth: look at up to 20 candidates
    random.shuffle(empties)

    focus = city.focus
    for cell in empties[:20]:
        pick = best_improvement(ter, res, cell, rivers, focus, rand=rand)
        if pick == IMP.NONE:
            continue
        it = pick
        costs = _upgrade_cost(city, it, 0)
        if not _try_buy(city, costs):
            continue # Try another tile, maybe it's cheaper or we have different stocks
        impr[cell] = make_imp(pick, 1)
        _touch_city_production(city)
        return True
    return False


def _place_advanced_structure(
    city: City, ter: list, res: dict, impr: list,
    *, rand: Callable[[], float] = random.random,
) -> bool:
    """Try to place an advanced structure (port / fishery / windmill /
    smithery) on one of the city's empty tiles.

    Adjacent structures get more benefit to the city by being near existing
    improvements. Costs are scaled vs. city gold, same as upgrades.
    """
    tiles = city.tiles
    if not tiles:
        return False

    empties = [c for c in tiles if 0 <= c < N and impr[c] == IMP.NONE]
    if not empties:
        return False
    random.shuffle(empties)

    # Smitheries are only useful if the city has copper ore coming in.
    has_ore_potential = any(
        0 <= t < N and (
            imp_type(impr[t]) == IMP.MINE or
            (res.get(t) == "iron")
        )
        for t in tiles
    )
    # Check current copper ore availability too
    current_production_ore = city.supply.get("copper_ore", 0.0) + city.supply.get("iron_ore", 0.0)
    current_imports_ore = city.net_imports.get("copper_ore", 0.0) + city.net_imports.get("iron_ore", 0.0)
    has_ore = current_production_ore > 0.1 or current_imports_ore > 0.1 or has_ore_potential

    focus = city.focus
    # Increase search depth
    for cell in empties[:20]:
        pick = advanced_structure_for(cell, ter, impr, focus, rand=rand)
        if pick == IMP.NONE:
            continue
        it = pick
        costs = _upgrade_cost(city, it, 0)
        if not _try_buy(city, costs):
            continue
        impr[cell] = make_imp(pick, 1)
        _touch_city_production(city)
        return True
    return False


# ── Fort placement (border-biased) ─────────────────────────────────────────

def _place_fort(
    city: City, civ: Civ, ter: list, impr: list, territory_set: set,
    enemy_ids: set, om: list, *, pay_cost: bool = True,
    require_metal: bool = True,
) -> bool:
    """Try to place a new IMP.FORT on one of the city's walkable empty tiles.

    Prefers tiles closest to an enemy border. Returns True on success.
    Gated by the caller — this function assumes the civ wants a fort.
    """
    tiles = city.tiles
    if not tiles:
        return False

    # Prefer empty fort-eligible tiles, then rebuild on occupied fort tiles,
    # then fall back to any city tile if no fort terrain exists at all.
    fort_empty = []
    fort_occupied = []
    any_occupied = []
    for c in tiles:
        if not (0 <= c < N):
            continue
        raw = impr[c]
        if ter[c] in _FORT_TERRAIN:
            if raw == IMP.NONE:
                fort_empty.append(c)
            elif imp_type(raw) != IMP.FORT:
                fort_occupied.append(c)
        if raw != IMP.FORT:
            any_occupied.append(c)

    # Border bias: score by Manhattan distance to the nearest enemy cell
    # reachable from a border tile of *this* civ. Cheaper than a full BFS —
    # we look up to 3 hops out from each candidate.
    def border_score(cell: int) -> int:
        # Lower score = closer to enemy = more preferred.
        # Walk up to 3 rings out until we find a neighbor owned by an enemy.
        frontier = {cell}
        seen = {cell}
        for d in range(1, 4):
            nxt = set()
            for fc in frontier:
                for nb in neighbors(fc):
                    if nb in seen or not (0 <= nb < N):
                        continue
                    seen.add(nb)
                    owner = om[nb] if 0 <= nb < len(om) else 0
                    if owner and owner in enemy_ids:
                        return d
                    if nb in territory_set:
                        nxt.add(nb)
            frontier = nxt
            if not frontier:
                break
        # No enemy nearby — prefer territory-edge tiles (touch a non-territory cell)
        for nb in neighbors(cell):
            if 0 <= nb < N and nb not in territory_set:
                return 8
        return 20

    # City AI forts still need metal stock. Government-driven forts can skip
    # this gate because their construction is paid through treasury spending
    # against the relevant local goods.
    if require_metal and getattr(civ, "metal_stock", 0.0) < FORT_BUILD_METAL_COST:
        return False

    # Build the candidate list in preference order, keeping the cheapest
    # disruption first so we only demolish when we really have to.
    rebuilds = []
    for cell in fort_empty:
        rebuilds.append((-20, border_score(cell), cell))
    for cell in fort_occupied:
        raw = impr[cell]
        disruption = 0
        if imp_type(raw) in _STAFFABLE:
            disruption += imp_level(raw) * 2
        if cell in territory_set:
            disruption -= 2
        rebuilds.append((disruption, border_score(cell), cell))
    if not rebuilds:
        for cell in any_occupied:
            raw = impr[cell]
            disruption = 8
            if raw == IMP.NONE:
                disruption -= 10
            elif imp_type(raw) in _STAFFABLE:
                disruption += imp_level(raw) * 2
            if cell in territory_set:
                disruption -= 2
            rebuilds.append((disruption, border_score(cell), cell))
    if not rebuilds:
        return False

    rebuilds.sort()
    target_cell = rebuilds[0][2]
    # Cheap-ish: forts are strategic so they cost a bit more than a farm.
    it = IMP.FORT
    costs = _upgrade_cost(city, it, 0)
    if pay_cost and not _try_buy_fort(city, civ, costs):
        return False

    if require_metal:
        civ.metal_stock -= FORT_BUILD_METAL_COST
    impr[target_cell] = make_imp(IMP.FORT, 1)
    _touch_city_production(city)
    return True


# ── Rare: rebuild a different improvement on an occupied tile ──────────────

def _rebuild_improvement(
    city: City, ter: list, res: dict, rivers: dict, impr: list,
    good_efficiency: dict | None,
) -> bool:
    """Demolish a low-value tile and rebuild a better level-1 producer.

    Triggered as a fallback when normal build/upgrade actions cannot proceed.
    Prefers replacing unstaffed low-profit tiles and only rebuilds when the
    alternative has a clear profitability gain.
    """
    tiles = city.tiles
    if not tiles:
        return False

    staffing = city.staffing
    best_choice: Optional[tuple[float, int, int]] = None
    best_gain = 0.0

    for cell in tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        if raw == IMP.NONE:
            continue
        cur_type = imp_type(raw)
        if cur_type in (IMP.FORT, IMP.PORT, IMP.WINDMILL):
            continue

        cur_profit = _tile_profit(city, cell, cur_type, ter, res, rivers, good_efficiency)
        cur_lvl = imp_level(raw)
        cur_staff = staffing.get(cell, 0)
        staffed_ratio = (cur_staff / cur_lvl) if cur_lvl > 0 else 0.0
        keep_value = cur_profit * max(0.1, staffed_ratio)

        best_alt_type: Optional[int] = None
        best_alt_profit = 0.0
        for cand in _BUILD_CANDS:
            if cand == cur_type:
                continue
            p = _tile_profit(city, cell, cand, ter, res, rivers, good_efficiency)
            if p > best_alt_profit:
                best_alt_profit = p
                best_alt_type = cand

        if best_alt_type is None or best_alt_profit <= 0.0:
            continue

        rebuild_cost = _upgrade_cost(city, best_alt_type, 0) * 1.6
        gain = best_alt_profit - keep_value - (rebuild_cost / CAPEX_PAYBACK_TICKS)
        if gain > best_gain and best_alt_profit >= keep_value * 1.35 + 0.2:
            best_gain = gain
            best_choice = (gain, cell, best_alt_type)

    if best_choice is None:
        return False

    _, cell, new_type = best_choice
    cost = _upgrade_cost(city, new_type, 0) * 1.6
    if not _try_buy(city, cost):
        return False

    impr[cell] = make_imp(new_type, 1)
    _touch_city_production(city)
    return True


# ── Public entry: per-civ per-tick city development ───────────────────────

def _pick_build_type(city: City, goal_producer: Optional[str], impr: list) -> Optional[str]:
    """Choose the producer building to prioritize this tick.

    Priority order:
      1. Profitability bias: pick producer with highest expected margin.
      2. Otherwise goal_producer if specified.
      3. Fallback: farm (always useful, always has capacity).
    """
    best_key: Optional[str] = None
    best_margin = 0.0
    local_eff = getattr(city, "local_efficiency", None) or {}

    for key, meta in PRODUCER_BUILDINGS.items():
        margin = _producer_margin(city, key)
        eff_good = meta.get("eff_good") or meta.get("good")
        eff = local_eff.get(eff_good, 1.0) if eff_good else 1.0

        if margin > best_margin:
            best_margin = margin
            best_key = key

    # Profitability bias (70% of the time choose profitable over goal).
    if best_key is not None and random.random() < 0.7:
        return best_key
    if goal_producer is not None:
        return goal_producer
    return best_key if best_key is not None else "farm"


def _develop_producer_buildings(
    city: City,
    civ: Civ,
    goal_producer: Optional[str] = None,
) -> bool:
    """Pure-ROI growth of producer buildings up to closed-form target levels.

    `_estimate_target_producer_levels` computes the equilibrium count per
    building (the level at which adding one more would yield zero ROI given
    price feedback). The greedy loop then adds levels to the highest-ROI
    underbuilt producer until targets, gold, or the action cap are reached.
    The ``goal_producer`` argument is preserved for caller compatibility but
    no longer biases scoring — decisions are purely ROI.
    """
    del goal_producer  # decisions are purely ROI now

    buildings = getattr(city, "buildings", None) or {}
    capacities = getattr(city, "capacities", None) or {}
    shared = getattr(city, "shared_capacities", None) or {}

    any_action = False
    unemployed = int(getattr(city, "unemployed_pop", 0) or 0)
    payback = _effective_capex_payback(city)

    while unemployed >= N_EMPLOYEES_PER_LEVEL:
        best_key: Optional[str] = None
        best_score = -float("inf")

        for key in PRODUCER_BUILDINGS.keys():
            cur_level = int(buildings.get(key, 0))
            cap = int(capacities.get(key, 0))
            if cur_level >= cap:
                continue

            if key in ("farm", "cotton_farm"):
                agri_cap = int(shared.get("agri", 0))
                agri_used = int(buildings.get("farm", 0)) + int(buildings.get("cotton_farm", 0))
                if agri_used >= agri_cap:
                    continue

            margin = _producer_margin(city, key)
            if margin <= 0.0:
                continue

            cost = _producer_upgrade_cost(city, key, cur_level)
            score = margin - (cost / payback)
            if score > best_score:
                best_score = score
                best_key = key

        if best_key is None:
            break

        cost = _producer_upgrade_cost(city, best_key, int(buildings.get(best_key, 0)))
        if not _try_buy(city, cost):
            break

        buildings[best_key] = int(buildings.get(best_key, 0)) + 1
        any_action = True
        unemployed -= N_EMPLOYEES_PER_LEVEL

    city.buildings = buildings
    return any_action


def _producer_upgrade_cost(city: City, key: str, current_level: int) -> float:
    # Producer levels are frequent, city-scale investments. Keep costs in the
    # same order as typical city gold flow so expansion doesn't deadlock.
    level_mult = max(1.0, current_level + 1) ** 1.18
    return 35.0 * level_mult


def _effective_capex_payback(city: City) -> float:
    """Capex payback hurdle, stretched when the city has idle labor.

    Idle labor has near-zero opportunity cost: putting a worker on a less
    profitable building still beats them sitting unemployed. We model that
    by accepting a longer capital payback when unemployment is high — the
    pure ROI gate stays the same at full employment.
    """
    pop = max(1.0, float(getattr(city, "population", 0.0) or 0.0))
    unemp_rate = float(getattr(city, "unemployed_pop", 0) or 0) / pop
    # 0% unemp → 1.0×, 20%+ → 2.5× (capped). Linear in between.
    return CAPEX_PAYBACK_TICKS * (1.0 + min(1.5, unemp_rate * 7.5))


def _producer_margin(city: City, key: str) -> float:
    meta = PRODUCER_BUILDINGS.get(key)
    if meta is None:
        return 0.0
    out_good = meta.get("good")
    in_good = meta.get("input_good")
    out = float(meta.get("base_output", 0.0))
    eff_good = meta.get("eff_good") or out_good
    out *= float((getattr(city, "local_efficiency", None) or {}).get(eff_good, 1.0))

    bonus = (getattr(city, "capacity_bonuses", None) or {}).get(key, {})
    out *= 1.0 + float(bonus.get("mult", 0.0)) * 0.5

    out_val = out * city.prices.get(out_good, BASE_PRICES.get(out_good, 1.0)) if out_good else 0.0
    in_val = float(meta.get("input_per_level", 0.0)) * city.prices.get(in_good, BASE_PRICES.get(in_good, 1.0)) if in_good else 0.0
    return out_val - in_val


# ── Inverse price function (target-supply estimator) ─────────────────────────
#
# These constants must mirror the price curve in simulation._get_price.
# Duplicated here to avoid a circular import (simulation imports city_dev).


def _supply_at_price(good: str, demand: float, target_price: float) -> float:
    """Inverse of the city price curve: supply level that yields ``target_price``.

    Returns ``inf`` when any supply keeps the price above the target (i.e.
    the building is unconditionally profitable), and ``0`` when the target
    is above the maximum achievable price (never profitable). Used by the
    ROI target estimator to project equilibrium output for each producer.
    """
    base = BASE_PRICES.get(good, 1.0)
    if base <= 0.0 or demand <= 0.0:
        return 0.0
    m = target_price / base
    if m >= PRICE_MULT_MIN + PRICE_CURVE_SPAN:
        return 0.0
    if m <= PRICE_MULT_MIN:
        return float("inf")
    f = (m - PRICE_MULT_MIN) / PRICE_CURVE_SPAN
    f = max(1e-9, min(1.0 - 1e-9, f))
    ratio = PRICE_CURVE_ANCHOR - math.log((1.0 - f) / f) / PRICE_CURVE_STEEPNESS
    if ratio <= 0.0:
        return float("inf")
    return demand / ratio


# `city.effective_demand` is populated by the simulation loop prior to
# calling the development estimator. If absent, fall back to `city.demand`.


def _estimate_target_producer_levels(city: City) -> dict[str, int]:
    """Closed-form target per producer building, based on pure ROI.

    Algorithm (per output good Y):
      1. For each producer X of Y, compute its break-even output price
         ``p* = (cost_per_level / payback + input_cost) / output_per_level``.
      2. Use the inverse price function to get the supply level ``s*`` at
         which that producer's marginal level just breaks even.
      3. Sort producers of Y by ROI per output unit (descending) and
         allocate target supply in order — best-ROI fills first, lower-ROI
         only fills the remaining gap. This avoids double-counting when
         multiple producers share an output good.

    O(P log P) per city, where P is the producer-building count (~12).
    """
    targets: dict[str, int] = {}
    levels = getattr(city, "buildings", None) or {}
    capacities = getattr(city, "capacities", None) or {}
    shared = getattr(city, "shared_capacities", None) or {}
    local_eff = getattr(city, "local_efficiency", None) or {}

    by_good: dict[str, list[str]] = {}
    for key, meta in PRODUCER_BUILDINGS.items():
        good = meta.get("good")
        if good:
            by_good.setdefault(good, []).append(key)

    payback = _effective_capex_payback(city)

    for good, keys in by_good.items():
        eff_dem = getattr(city, "effective_demand", None)
        if eff_dem is not None:
            demand_y = float(eff_dem.get(good, 0.0))
        else:
            demand_y = float(city.demand.get(good, 0.0))
        if demand_y <= 0.0:
            for k in keys:
                targets[k] = int(levels.get(k, 0))
            continue

        # Per-producer ROI metrics.
        info: list[tuple[str, float, float, float, int]] = []
        for key in keys:
            meta = PRODUCER_BUILDINGS[key]
            q = float(meta.get("base_output", 0.0))
            eff_key = meta.get("eff_good") or good
            eff = float(local_eff.get(eff_key, 1.0))
            q_eff = q * eff
            cur = int(levels.get(key, 0))
            if q_eff <= 0.0:
                targets[key] = cur
                continue
            cost = _producer_upgrade_cost(city, key, cur)
            in_good = meta.get("input_good")
            in_per_lvl = float(meta.get("input_per_level", 0.0))
            in_cost = in_per_lvl * float(BASE_PRICES.get(in_good, 1.0)) if in_good else 0.0
            p_break = (cost / payback + in_cost) / q_eff
            market_price = float(city.prices.get(good, BASE_PRICES.get(good, 1.0)))
            roi_per_unit = market_price - p_break
            info.append((key, q_eff, p_break, roi_per_unit, cur))

        # Best ROI fills first.
        info.sort(key=lambda x: -x[3])
        accumulated = sum(q_eff * cur for _, q_eff, _, _, cur in info)

        for key, q_eff, p_break, roi_per_unit, cur in info:
            cap = int(capacities.get(key, 0))
            if roi_per_unit <= 0.0:
                # Not viable even at base price.
                targets[key] = cur
                continue
            tgt_supply = _supply_at_price(good, demand_y, p_break)
            if not math.isfinite(tgt_supply):
                tgt = cap
            else:
                add = max(0.0, tgt_supply - accumulated) / q_eff
                tgt = min(cap, cur + int(math.ceil(add)) if add > 1e-9 else cur)
            targets[key] = max(cur, tgt)
            accumulated += q_eff * (targets[key] - cur)

    # Shared capacity pool: agri (farm + cotton_farm) compete for one cap.
    agri_cap = int(shared.get("agri", 0))
    if agri_cap > 0:
        for member, other in (("farm", "cotton_farm"), ("cotton_farm", "farm")):
            cur = int(levels.get(member, 0))
            other_used = int(levels.get(other, 0))
            allowed = max(0, agri_cap - other_used)
            tgt = targets.get(member, cur)
            targets[member] = min(tgt, allowed)
    return targets


def max_profitable_levels(
    city: City, key: str, *, max_add: int | None = None,
) -> int:
    """Return how many additional levels of producer `key` would be
    considered profitable at current market prices, capped by capacity,
    available gold, and available unemployed labour.

    This wraps the closed-form target estimator and then applies simple
    affordability (gold) and labour gates to give a practical buildable
    count that mirrors what the development loop would actually apply.
    """
    if key not in PRODUCER_BUILDINGS:
        return 0

    levels = getattr(city, "buildings", None) or {}
    capacities = getattr(city, "capacities", None) or {}
    shared = getattr(city, "shared_capacities", None) or {}

    cur = int(levels.get(key, 0))
    cap = int(capacities.get(key, 0))

    # Respect shared agricultural pool for farm/cotton_farm
    if key in ("farm", "cotton_farm"):
        agri_cap = int(shared.get("agri", 0))
        other = "cotton_farm" if key == "farm" else "farm"
        other_used = int(levels.get(other, 0))
        cap = min(cap, max(0, agri_cap - other_used))

    targets = _estimate_target_producer_levels(city)
    tgt = int(targets.get(key, cur))
    allowed = max(0, min(tgt, cap) - cur)
    if allowed <= 0:
        return 0

    # Affordability by gold: simulate buying sequential levels until funds exhausted
    gold = float(getattr(city, "gold", 0.0) or 0.0)
    affordable = 0
    lvl = cur
    while affordable < allowed:
        cost = _producer_upgrade_cost(city, key, lvl)
        if cost > gold:
            break
        gold -= cost
        lvl += 1
        affordable += 1

    # Labour cap
    unemployed = int(getattr(city, "unemployed_pop", 0) or 0)
    labour_limit = unemployed // N_EMPLOYEES_PER_LEVEL

    result = min(allowed, affordable, labour_limit)
    if max_add is not None:
        result = min(result, max_add)
    return int(max(0, result))


def _estimate_target_building_levels(city: City) -> dict[str, int]:
    """Closed-form target per city-center building, based on pure ROI.

    City buildings can have multiple inputs and outputs, so we attribute
    the ROI to the dominant output good (highest current revenue share)
    and use the inverse price function on that good. Other outputs are
    treated as fixed-value bonuses at current prices.
    """
    targets: dict[str, int] = {}
    blevels = getattr(city, "buildings", None) or {}
    prices = getattr(city, "prices", None) or {}

    for key, b in BUILDING_TYPES.items():
        cur = int(blevels.get(key, 0))
        if key == "trading_house":
            trade_volume = float(getattr(city, "trade_export_volume", 0.0) or 0.0)
            trade_required = float(getattr(city, "trade_capacity_required", 0.0) or 0.0)
            trade_provided = float(getattr(city, "trade_capacity_provided", 0.0) or 0.0)
            pop_fallback = float(getattr(city, "population", 0.0) or 0.0) * 0.15 + 4.0

            target_volume = max(trade_volume, trade_required, pop_fallback)
            if trade_provided > 0.0 and trade_required > trade_provided * 0.9:
                # Keep a little headroom if the house is already near
                # saturation so the build target doesn't flatten too early.
                target_volume = max(target_volume, trade_required * 1.1)

            targets[key] = min(
                b.max_level,
                max(cur, int(math.ceil(target_volume / TRADE_HOUSE_CAPACITY_PER_EMPLOYEE))),
            )
            continue
        if not b.outputs:
            targets[key] = cur
            continue
        out_v = sum(amt * prices.get(g, BASE_PRICES.get(g, 1.0)) for g, amt in b.outputs.items())
        in_v = sum(amt * prices.get(g, BASE_PRICES.get(g, 1.0)) for g, amt in b.inputs.items())
        margin = out_v - in_v
        cost = _building_upgrade_cost(city, key, cur)
        if margin <= 0.0 or margin * BUILDING_PAYBACK_TICKS <= cost:
            targets[key] = cur
            continue

        # Pick the dominant output (largest revenue contribution).
        primary, _ = max(
            (
                (g, amt * prices.get(g, BASE_PRICES.get(g, 1.0)))
                for g, amt in b.outputs.items()
            ),
            key=lambda x: x[1],
        )
        primary_q = float(b.outputs.get(primary, 0.0))
        eff_dem = getattr(city, "effective_demand", None)
        if eff_dem is not None:
            demand_y = float(eff_dem.get(primary, 0.0))
        else:
            demand_y = float(city.demand.get(primary, 0.0))
        if demand_y <= 0.0 or primary_q <= 0.0:
            targets[key] = min(b.max_level, cur + 1)  # ROI-positive but no demand signal
            continue

        other_out_v = sum(
            amt * prices.get(g, BASE_PRICES.get(g, 1.0))
            for g, amt in b.outputs.items() if g != primary
        )
        # Net per level = primary_q * primary_p + other_out_v - in_v ≥ cost / payback.
        p_break = (cost / BUILDING_PAYBACK_TICKS + in_v - other_out_v) / primary_q
        if p_break <= 0.0:
            targets[key] = b.max_level
            continue
        tgt_supply = _supply_at_price(primary, demand_y, p_break)
        if not math.isfinite(tgt_supply):
            targets[key] = b.max_level
        else:
            other_supply = max(0.0, float(city.supply.get(primary, 0.0)) - primary_q * cur)
            add = max(0.0, tgt_supply - other_supply) / primary_q
            targets[key] = min(b.max_level, cur + int(add))
    return targets


def _has_capacity_for_next_level(city: City, key: str) -> bool:
    levels = getattr(city, "buildings", None) or {}
    capacities = getattr(city, "capacities", None) or {}
    shared = getattr(city, "shared_capacities", None) or {}

    cur = int(levels.get(key, 0))
    cap = int(capacities.get(key, 0))
    if cur >= cap:
        return False

    if key in ("farm", "cotton_farm"):
        agri_cap = int(shared.get("agri", 0))
        agri_used = int(levels.get("farm", 0)) + int(levels.get("cotton_farm", 0))
        if agri_used >= agri_cap:
            return False

    return True


def _develop_tiles(
    city: City, civ: Civ, build_type: int, impr: list,
    ter: list, res: dict, rivers: dict, om: list,
    *, good_efficiency: dict | None = None,
) -> bool:
    """Greedy unified development loop, pure ROI.

    Prices/efficiencies/terrain are stable during one call, so we precompute
    the (profit, good, kind) tuple per (tile, candidate) once and re-score
    cheaply each iteration — dropping the hot ``_tile_profit`` path from
    O(K × T × 6) to O(T × 6) + O(K × T).

    Gates: unemployment for builds, unstaffed cap for upgrades, per-tile
    upgrade cap. ``build_type`` is preserved for caller compatibility but
    no longer biases scoring.
    """
    del build_type  # decisions are purely ROI now

    remaining_unemployed = int(getattr(city, "unemployed_pop", 0))
    built_by_good: dict[str, int] = {}
    upgrades_this_call: dict[int, int] = {}
    any_action = False
    staffing = getattr(city, "staffing", None) or {}

    # Precompute valid candidates per tile. Entry layout:
    # [imp_type, kind ('build'|'upgrade'), profit, good, lvl]
    tile_entries: dict[int, list[list]] = {}
    for cell in city.tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        entries: list[list] = []
        if raw == IMP.NONE:
            for cand in _BUILD_CANDS:
                profit = _tile_profit(city, cell, cand, ter, res, rivers, good_efficiency)
                if profit <= 0:
                    continue
                entries.append([cand, "build", profit, _IMP_TO_GOOD.get(cand), 0])
        else:
            it = imp_type(raw)
            if it in UPGRADABLE_TYPES:
                lvl = imp_level(raw)
                if lvl < max_level(it):
                    profit = _tile_profit(city, cell, it, ter, res, rivers, good_efficiency)
                    if profit > 0:
                        entries.append([it, "upgrade", profit, _IMP_TO_GOOD.get(it), lvl])
        if entries:
            tile_entries[cell] = entries

    while tile_entries:
        best_score = 0.0
        best_cell = -1
        best_entry: Optional[list] = None

        for cell, entries in tile_entries.items():
            tile_up_count = upgrades_this_call.get(cell, 0)
            staffed = int(staffing.get(cell, 0))
            for entry in entries:
                imp_t, kind, profit, good, lvl = entry
                if kind == "build":
                    if remaining_unemployed < N_EMPLOYEES_PER_LEVEL:
                        continue
                    capex = _upgrade_cost(city, imp_t, 0)
                    base = profit - capex / CAPEX_PAYBACK_TICKS
                else:
                    # Allow stacking multiple upgrades on a single tile in one
                    # call (no artificial per-tile cap). Still respect staffing
                    # and labour availability checks below when necessary.
                    unstaffed = lvl - staffed
                    if remaining_unemployed < N_EMPLOYEES_PER_LEVEL and unstaffed >= 1:
                        continue
                    cost = _upgrade_cost(city, imp_t, lvl)
                    base = profit - cost / CAPEX_PAYBACK_TICKS
                if base <= 0:
                    continue

                # Pure ROI: no need/saturation/goal multipliers.
                score = base
                if score > best_score:
                    best_score = score
                    best_cell = cell
                    best_entry = entry

        if best_entry is None:
            break

        imp_t, kind, profit, good, lvl = best_entry
        cost = _upgrade_cost(city, imp_t, 0 if kind == "build" else lvl)

        is_fort = (imp_t == IMP.FORT and civ is not None)
        if is_fort:
            if not _try_buy_fort(city, civ, cost):
                break
        elif not _try_buy(city, cost):
            break

        if kind == "build":
            impr[best_cell] = make_imp(imp_t, 1)
            if imp_t in UPGRADABLE_TYPES and max_level(imp_t) > 1:
                tile_entries[best_cell] = [[imp_t, "upgrade", profit, good, 1]]
            else:
                tile_entries.pop(best_cell, None)
        else:
            impr[best_cell] = upgrade_imp(impr[best_cell])
            new_lvl = lvl + 1
            upgrades_this_call[best_cell] = upgrades_this_call.get(best_cell, 0) + 1
            if new_lvl >= max_level(imp_t):
                tile_entries.pop(best_cell, None)
            else:
                best_entry[4] = new_lvl

        if good:
            built_by_good[good] = built_by_good.get(good, 0) + 1
        remaining_unemployed = max(0, remaining_unemployed - N_EMPLOYEES_PER_LEVEL)
        any_action = True

    if any_action:
        _touch_city_production(city)
    return any_action


def _try_one_action(
    city: City, civ: Civ, goal_imp: Optional[int], impr: list,
    ter: list, res: dict, rivers: dict, om: list,
    *, build_type: Optional[int] = None, good_efficiency: dict | None = None,
) -> bool:
    """Run one producer-building investment action for this city.

    Pure ROI: pick the highest-score underbuilt producer and add a level.
    The ``build_type`` argument is preserved for caller compatibility but
    no longer biases the score.
    """
    del build_type  # decisions are purely ROI now

    best_key: Optional[str] = None
    best_score = 0.0
    for key in PRODUCER_BUILDINGS.keys():
        if not _has_capacity_for_next_level(city, key):
            continue
        cur_lvl = int((city.buildings or {}).get(key, 0))
        margin = _producer_margin(city, key)
        cost = _producer_upgrade_cost(city, key, cur_lvl)
        score = margin - (cost / CAPEX_PAYBACK_TICKS)
        if score > best_score:
            best_score = score
            best_key = key

    if best_key is None or best_score <= 0.0:
        return False

    cur_lvl = int((city.buildings or {}).get(best_key, 0))
    cost = _producer_upgrade_cost(city, best_key, cur_lvl)
    if not _try_buy(city, cost):
        return False

    city.buildings[best_key] = cur_lvl + 1
    return True


def tick_city_development(
    civ: Civ, wars: dict, ter: list, res: dict, rivers: dict, impr: list,
    tick: int, om: Optional[list] = None,
    good_efficiency: Optional[dict] = None,
) -> None:
    """Per-tick building decisions for every city in the civ.

    Each city takes one tile-development action per investment tick: either
    a single new build/rebuild, or an upgrade action that may apply multiple
    levels while the city can still pay. This keeps expansion controlled
    without slowing vertical development.
    """
    if not civ.cities:
        return

    if tick % INVEST_PERIOD_TICKS != 0:
        return

    civ.goal_ticks += 1

    goal_map = {
        "FARM": "farm",
        "MINE": "copper_mine",
        "LUMBER": "lumber_camp",
        "QUARRY": "quarry",
        "FORT": None,
    }

    if civ.goal_index >= len(civ.goal_queue):
        civ.goal_index = 0
    current_goal = civ.goal_queue[civ.goal_index]

    # Goal expiration — nothing fulfilled in 300 ticks, move on.
    if civ.goal_ticks > 60:
        civ.goal_index = (civ.goal_index + 1) % len(civ.goal_queue)
        civ.goal_ticks = 0
        return

    # FOUND goals are handled in simulation.py — but unlike before we still
    # let cities build production. The old code froze the whole civ, which
    # left new cities stuck at 40 pop with no buildings while the civ saved
    # for another settle. Cities still need farms to grow.
    goal_producer = goal_map.get(current_goal)  # None for FOUND/FORT

    city_count = len(civ.cities)

    # Rotate city-development work across ticks so the per-tick cost scales
    # sublinearly with city count.
    stride = 1
    if city_count >= 240:
        stride = 1
    elif city_count >= 160:
        stride = 1
    elif city_count >= 96:
        stride = 1

    if stride > 1:
        cursor = getattr(civ, "_invest_cursor", 0) % stride
        city_iter = civ.cities[cursor::stride]
        civ._invest_cursor = (cursor + 1) % stride
    else:
        city_iter = civ.cities

    any_built = False
    changed_cities: dict[int, City] = {}
    for city in city_iter:
        # Producer building investment
        if _develop_producer_buildings(city, civ, goal_producer=goal_producer):
            any_built = True
            changed_cities[city.cell] = city

        # City-center buildings (factories etc.) invest off market signals.
        # Separate from producer buildings so we can grow urban industry.
        if _try_build_city_buildings(city, impr):
            any_built = True
            changed_cities[city.cell] = city

    # Re-run employment so workers move into buildings placed this tick.
    # Without this, new cells wait a whole tick before producing.
    for city in changed_cities.values():
        employment.update_city_employment(city, impr)
        pop = max(1.0, float(getattr(city, "population", 0.0)))
        city.workforce = int(pop)
        city.employed_pop = int(getattr(city, "employee_level_count", 0)) * N_EMPLOYEES_PER_LEVEL
        city.unemployed_pop = max(0, city.workforce - city.employed_pop)

    # Advance the civ goal when SOMEONE built this tick (keeps the queue
    # from stalling but doesn't gate per-city work on it).
    if any_built and goal_producer is not None:
        civ.goal_index = (civ.goal_index + 1) % len(civ.goal_queue)
        civ.goal_ticks = 0
