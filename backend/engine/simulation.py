"""Main per-tick simulation loop. Army logic lives in engine.combat,
city development in engine.city_dev."""

import heapq
import math
import random
import logging
import time
from typing import List, Callable, Optional, Dict

from .constants import (
    W, H, N, T, IMP, CAN_FARM, FOCUS, BASE_PRICES,
    FORT_METAL_UPKEEP, CITY_HP_REGEN, N_EMPLOYEES_PER_LEVEL,
    TRADABLE_GOODS, GOV_CONSTRUCTION_PERIOD,
    MAX_TILES_PER_CITY_FOR_EXPANSION,
)
from .helpers import (
    neighbors, is_land, border_cells, dist,
    war_key, cell_on_river,
)
from .improvements import imp_type, imp_level, downgrade_imp
from .mapgen import cell_coastal, cell_river_mouth
from .models import City, Civ, War, Road, Rivers
from .government import (
    sync_fort_funding, fort_host_city, collect_tax, update_fort_funding,
    refresh_government_construction_queue,
    execute_government_construction,
)

from .civ import gen_city_name, build_road
from . import combat
from . import city_dev
from . import diplomacy
from . import employment
from .employment import staffed_level


log = logging.getLogger("civitas.simulation")

# Performance controls (tick-based cadence and adaptive solver limits).
PERF_LOG_PERIOD = 250
EMPLOYMENT_PERIOD = 11
WORKER_REALLOC_PERIOD = 41
MIGRATION_PERIOD = 23
LOCAL_EFFICIENCY_PERIOD = 53
BUILDING_PROFIT_PERIOD = 29
CONSUMPTION_REBALANCE_PERIOD = 25
from .buildings import BUILDING_TYPES
from .economy_profiles import (
    IMPROVEMENT_ECONOMY,
    RESOURCE_TILE_EFFECTS,
    CITY_BASE_DEMAND_PER_POP,
    GRAIN_DEMAND_FLOOR_MULTIPLIER,
    SUBSTITUTE_GROUPS,
    GOOD_CONSUMPTION_PROFILES,
    DEFAULT_CONSUMPTION_GOOD_PROFILE,
    PROFESSION_CONSUMPTION_PROFILES,
    good_consumption_multiplier,
)


# ── Settle scoring ─────────────────────────────────────────────────────────

def _settle_score(cell, ter, rivers, res, all_city_cells, params):
    """Score a cell as a potential city site. Returns a float or None."""
    t = ter[cell]
    if t in (T.MTN, T.SNOW, T.DESERT) or t <= T.COAST:
        return None

    score = 0.0

    if all_city_cells:
        min_d = min(dist(cell, oc) for oc in all_city_cells)
        if min_d <= 2:
            score -= 500
        elif min_d <= 4:
            score -= 80 / min_d
        elif min_d <= 7:
            score -= 30 / min_d
        score += min(min_d * 0.3, 5)

    if cell_river_mouth(cell, ter, rivers):
        score += 60
    elif cell_on_river(cell, rivers):
        score += params.get("river_pref", 10) * 1.5
    if cell_coastal(cell, ter):
        score += params.get("coast_pref", 5) * 1.5
    if t in CAN_FARM or (cell_on_river(cell, rivers) and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT)):
        score += 2
    else:
        for n in neighbors(cell):
            if cell_on_river(n, rivers) and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT):
                score += 2
                break
    if cell in res:
        score += 3

    return score


def _eval_settle_candidate(civ, cell, ter, rivers, res, all_city_cells, params):
    sc = _settle_score(cell, ter, rivers, res, all_city_cells, params)
    if sc is not None and sc > getattr(civ, "_settle_score", float("-inf")):
        civ._settle_candidate = cell
        civ._settle_score = sc


# ── Peace (restore pre-war borders, keep captured cities) ─────────────────

def _settle_peace(war, a, b, om, civs, tick, add_event):
    att_id, def_id = war.att, war.def_id
    att  = next((c for c in civs if c.id == att_id), None)
    defn = next((c for c in civs if c.id == def_id), None)
    if not att or not defn:
        return

    pre_a = getattr(war, "pre_ter_a", set())
    pre_d = getattr(war, "pre_ter_d", set())
    cap_by_a = set(getattr(war, "captured_cities_a", []))
    cap_by_d = set(getattr(war, "captured_cities_d", []))

    att_city_cells = [ci.cell for ci in att.cities]
    def_city_cells = [ci.cell for ci in defn.cities]

    perm_to_att = set()
    for t in pre_d:
        best_cap_d = min((dist(t, cc) for cc in cap_by_a), default=float("inf"))
        best_def_d = min((dist(t, dc) for dc in def_city_cells), default=float("inf"))
        if cap_by_a and best_cap_d < best_def_d:
            perm_to_att.add(t)

    perm_to_def = set()
    for t in pre_a:
        best_cap_d = min((dist(t, cc) for cc in cap_by_d), default=float("inf"))
        best_att_d = min((dist(t, ac) for ac in att_city_cells), default=float("inf"))
        if cap_by_d and best_cap_d < best_att_d:
            perm_to_def.add(t)

    disputed = (att.territory | defn.territory) & (pre_a | pre_d)

    for cell in list(disputed):
        if cell in perm_to_att:
            defn.territory.discard(cell)
            att.territory.add(cell)
            om[cell] = att_id
        elif cell in perm_to_def:
            att.territory.discard(cell)
            defn.territory.add(cell)
            om[cell] = def_id
        elif cell in pre_a:
            defn.territory.discard(cell)
            att.territory.add(cell)
            om[cell] = att_id
        elif cell in pre_d:
            att.territory.discard(cell)
            defn.territory.add(cell)
            om[cell] = def_id

    # Prune cities that ended up outside their owner's territory
    att.cities  = [c for c in att.cities  if c.cell in att.territory]
    defn.cities = [c for c in defn.cities if c.cell in defn.territory]

    _touch_city_layout(att)
    _touch_city_layout(defn)

    # Peace treaty disbands all armies
    war.armies_a = []
    war.armies_d = []

    add_event(f"🕊 Year {tick}: {a.name} & {b.name} made peace")
    a.events.append(f"Year {a.age}: Peace with {b.name}")
    b.events.append(f"Year {b.age}: Peace with {a.name}")


# ── Per-tick simulation driver ─────────────────────────────────────────────

from .constants import (
    W, H, N, T, IMP, CAN_FARM, FOCUS,
    FORT_METAL_UPKEEP, CITY_HP_REGEN,
    GOODS, BASE_PRICES, TRANSPORT_COST_PER_DIST,
)
from .helpers import (
    neighbors, is_land, border_cells, dist,
    war_key, cell_on_river,
)
from .improvements import imp_type, imp_level, downgrade_imp
from .mapgen import cell_coastal, cell_river_mouth
from .models import City, Civ, War, Road, Rivers

from .civ import gen_city_name, build_road
from . import combat
from . import city_dev
from . import diplomacy
from . import employment
from .employment import staffed_level


# Price curve controls. We keep 1.0x exactly at supply=demand while making
# scarcity respond much more aggressively than before.
PRICE_MULT_MIN = 0.05
PRICE_MULT_MAX = 20.0
PRICE_CURVE_STEEPNESS = 5.5
_PRICE_CURVE_SPAN = PRICE_MULT_MAX - PRICE_MULT_MIN
_PRICE_CURVE_ANCHOR = 1.0 + math.log((_PRICE_CURVE_SPAN / (1.0 - PRICE_MULT_MIN)) - 1.0) / PRICE_CURVE_STEEPNESS


def _get_price(good: str, supply: float, demand: float) -> float:
    base = BASE_PRICES.get(good, 1.0)
    ratio = demand / max(supply, 0.1)
    # Bounded logistic with much stronger scarcity response.
    # Anchor is derived so mult == 1.0 exactly when ratio == 1.0.
    mult = PRICE_MULT_MIN + _PRICE_CURVE_SPAN / (
        1.0 + math.exp(-PRICE_CURVE_STEEPNESS * (ratio - _PRICE_CURVE_ANCHOR))
    )
    return base * mult


def _apply_substitute_demands(city: City) -> None:
    """Reallocate base-good demand across substitute goods.

    Allocation is preference-first with a mild availability boost so
    substitutes with little current supply (e.g., newly introduced bread)
    still retain non-zero demand.
    """
    avail_weight = 0.35
    for group in SUBSTITUTE_GROUPS:
        base_good = str(group.get("base_good", ""))
        members = dict(group.get("members", {}))
        base_demand = city.demand.get(base_good, 0.0)
        if base_demand <= 0.0 or not members:
            continue

        scores: dict[str, float] = {}
        score_sum = 0.0
        for good, pref in members.items():
            avail = max(0.0, city.supply.get(good, 0.0))
            preference = max(0.01, float(pref))
            score = preference * (1.0 + avail_weight * math.sqrt(avail))
            scores[good] = score
            score_sum += score

        if score_sum <= 0.0:
            continue

        for good in members.keys():
            city.demand[good] = 0.0
        for good, score in scores.items():
            city.demand[good] += base_demand * (score / score_sum)


def _apply_consumption_demands(city: City) -> None:
    """Build demand from slow-moving profession consumption levels.

    This keeps early manufactured-goods demand tiny, then gradually raises it
    as profession wages and consumption levels climb.
    """
    for good in GOODS:
        city.demand[good] = 0.0

    prof_counts = dict(getattr(city, "professions", None) or {})
    unemployed_count = int(max(0, getattr(city, "unemployed_pop", 0) or 0))
    if unemployed_count > 0:
        prof_counts["unemployed"] = prof_counts.get("unemployed", 0) + unemployed_count

    consumption_levels = getattr(city, "consumption_levels", None) or {}
    for prof, count in prof_counts.items():
        if count <= 0:
            continue
        profile = PROFESSION_CONSUMPTION_PROFILES.get(prof)
        if profile is None:
            continue
        level = float(consumption_levels.get(prof, profile.base_level))
        for good in GOODS:
            good_profile = GOOD_CONSUMPTION_PROFILES.get(good, DEFAULT_CONSUMPTION_GOOD_PROFILE)
            per_person = good_profile.base_per_person * good_consumption_multiplier(good, level, prof)
            city.demand[good] += per_person * count

    _apply_substitute_demands(city)

    # Grain is a staple: enforce a minimum demand floor tied to population.
    grain_base = CITY_BASE_DEMAND_PER_POP.get("grain", 0.0)
    if grain_base > 0.0:
        pop = max(0.0, float(getattr(city, "population", 0.0)))
        grain_floor = pop * grain_base * float(GRAIN_DEMAND_FLOOR_MULTIPLIER)
        if city.demand.get("grain", 0.0) < grain_floor:
            city.demand["grain"] = grain_floor


def _compute_market_satisfaction(city: City) -> float:
    """Return weighted demand fulfillment for the city.

    This is a welfare signal, not treasury income: it is high when the city
    meets most of its own demand and low when a few bottlenecks dominate.
    """
    total_demand = 0.0
    total_fulfilled = 0.0
    for good in GOODS:
        demand = max(0.0, float(city.demand.get(good, 0.0)))
        if demand <= 0.0:
            continue
        effective_supply = float(city.supply.get(good, 0.0)) + float(city.net_imports.get(good, 0.0))
        fulfilled = min(effective_supply, demand)
        total_demand += demand
        total_fulfilled += fulfilled
    if total_demand <= 0.0:
        return 1.0
    return max(0.0, min(1.0, total_fulfilled / total_demand))


def _touch_city_layout(civ: Civ) -> None:
    civ._layout_version = getattr(civ, "_layout_version", 0) + 1


def _tick_trade(
    civs: List[Civ], ter: list, om: list,
    *,
    settle_financials: bool = True,
    record_trades: bool = True,
    max_passes: int = 4,
    stats: Optional[Dict[str, int]] = None,
):
    """Arbitrage between cities within each civ — multi-pass deterministic
    equilibrium.

    Each pass builds a list of (pair, good) candidates whose *current*
    effective-price gap exceeds the transport cost, sorts by gap descending,
    and processes them. "Current" means re-evaluated against
    ``supply + net_imports`` so chain arbitrage (producer → hub → consumer)
    can resolve across passes: on pass 1 the producer fills the hub; on
    pass 2 the hub re-exports to the consumer.

    The old random-shuffle approach caused two visible bugs:
      * flicker — when multiple buyers competed for one seller's surplus,
        pair order determined who got served; shuffled order rotated the
        "winner" tick to tick.
      * permanent price gaps — if ``hub→consumer`` happened before
        ``producer→hub``, the hub had no surplus yet and the trade was
        skipped, so the consumer's price never came down.
    """
    for civ in civs:
        if not civ.alive or len(civ.cities) < 2:
            continue

        cities = civ.cities
        city_state = []
        for city in cities:
            supply = city.supply
            demand = city.demand
            price_row = {}
            surplus_row = {}
            deficit_row = {}
            surplus_keys = set()
            deficit_keys = set()
            for good in TRADABLE_GOODS:
                eff_supply = supply.get(good, 0.0) + city.net_imports.get(good, 0.0)
                eff_demand = demand.get(good, 0.0)
                price_row[good] = _get_price(good, eff_supply, eff_demand)
                if eff_supply > eff_demand + 0.05:
                    surplus_row[good] = eff_supply - eff_demand
                    surplus_keys.add(good)
                elif eff_demand > eff_supply + 0.05:
                    deficit_row[good] = eff_demand - eff_supply
                    deficit_keys.add(good)
            city_state.append((city, price_row, surplus_row, deficit_row, surplus_keys, deficit_keys))

        state_by_city = {city.cell: (price_row, surplus_row, deficit_row, surplus_keys, deficit_keys) for city, price_row, surplus_row, deficit_row, surplus_keys, deficit_keys in city_state}

        # Precompute distances once.
        pair_cost = []
        for i in range(len(cities)):
            for j in range(i + 1, len(cities)):
                c1, c2 = cities[i], cities[j]
                d = dist(c1.cell, c2.cell)
                pair_cost.append((c1, c2, d * TRANSPORT_COST_PER_DIST * 0.01))

        pair_count = len(pair_cost)
        for _pass in range(max_passes):
            # Build candidates using CURRENT effective prices.
            candidates = []
            for c1, c2, cost in pair_cost:
                p1, s1, d1, s1_keys, d1_keys = state_by_city[c1.cell]
                p2, s2, d2, s2_keys, d2_keys = state_by_city[c2.cell]
                goods = (s1_keys & d2_keys) | (s2_keys & d1_keys)
                for good in goods:
                    gap = abs(p1.get(good, 0.0) - p2.get(good, 0.0))
                    if gap <= cost:
                        continue
                    candidates.append((gap, c1, c2, good, cost))

            if not candidates:
                break

            if stats is not None:
                stats["pairs"] = stats.get("pairs", 0) + pair_count
                stats["candidates"] = stats.get("candidates", 0) + len(candidates)
                stats["passes"] = stats.get("passes", 0) + 1

            # Highest-gap (most profitable) first. Deterministic — same
            # conditions produce the same ordering every tick.
            candidates.sort(key=lambda x: -x[0])

            any_trade = False
            for _gap, c1, c2, good, cost_per_unit in candidates:
                p1_row, s1_row, d1_row, s1_keys, d1_keys = state_by_city[c1.cell]
                p2_row, s2_row, d2_row, s2_keys, d2_keys = state_by_city[c2.cell]
                s1 = c1.supply.get(good, 0.0) + c1.net_imports.get(good, 0.0)
                d1 = c1.demand.get(good, 0.0)
                s2 = c2.supply.get(good, 0.0) + c2.net_imports.get(good, 0.0)
                d2 = c2.demand.get(good, 0.0)
                p1 = p1_row.get(good, _get_price(good, s1, d1))
                p2 = p2_row.get(good, _get_price(good, s2, d2))

                if p1 > p2 + cost_per_unit:
                    buyer, seller = c1, c2
                    b_supply, b_demand = s1, d1
                    s_supply, s_demand = s2, d2
                elif p2 > p1 + cost_per_unit:
                    buyer, seller = c2, c1
                    b_supply, b_demand = s2, d2
                    s_supply, s_demand = s1, d1
                else:
                    continue

                seller_surplus = max(0.0, s_supply - s_demand)
                buyer_deficit  = max(0.0, b_demand - b_supply)
                hard_cap = min(seller_surplus, buyer_deficit)
                if hard_cap <= 0.05:
                    continue

                # Short bisection — good enough for a flow model and much cheaper.
                lo, hi = 0.0, hard_cap
                for _ in range(6):
                    mid = (lo + hi) * 0.5
                    new_pb = _get_price(good, b_supply + mid, b_demand)
                    new_ps = _get_price(good, s_supply - mid, s_demand)
                    if new_pb - new_ps > cost_per_unit:
                        lo = mid
                    else:
                        hi = mid
                volume = lo
                if volume <= 0.05:
                    continue

                seller_quote = _get_price(good, s_supply, s_demand)
                unit_price = seller_quote + cost_per_unit
                total_cost = volume * unit_price
                if settle_financials and buyer.gold < total_cost and unit_price > 0:
                    volume = buyer.gold / unit_price
                    total_cost = volume * unit_price
                if volume <= 0.05:
                    continue

                if settle_financials:
                    buyer.gold  -= total_cost
                    seller.gold += total_cost * 0.95

                    real_value = volume * BASE_PRICES.get(good, unit_price)
                    buyer.income_import[good]  = buyer.income_import.get(good, 0.0)  + real_value
                    seller.income_export[good] = seller.income_export.get(good, 0.0) + real_value * 0.95

                buyer.net_imports[good]  = buyer.net_imports.get(good, 0.0)  + volume
                seller.net_imports[good] = seller.net_imports.get(good, 0.0) - volume

                # Update cached state for this good so later trades in the pass
                # use the current effective balances without recomputing all goods.
                b_price_row, b_surplus_row, b_deficit_row, b_surplus_keys, b_deficit_keys = state_by_city[buyer.cell]
                s_price_row, s_surplus_row, s_deficit_row, s_surplus_keys, s_deficit_keys = state_by_city[seller.cell]
                buyer_supply_eff = buyer.supply.get(good, 0.0) + buyer.net_imports.get(good, 0.0)
                seller_supply_eff = seller.supply.get(good, 0.0) + seller.net_imports.get(good, 0.0)
                b_price_row[good] = _get_price(good, buyer_supply_eff, b_demand)
                s_price_row[good] = _get_price(good, seller_supply_eff, s_demand)
                if buyer_supply_eff > b_demand + 0.05:
                    b_surplus_row[good] = buyer_supply_eff - b_demand
                    b_deficit_row.pop(good, None)
                    b_surplus_keys.add(good)
                    b_deficit_keys.discard(good)
                elif b_demand > buyer_supply_eff + 0.05:
                    b_deficit_row[good] = b_demand - buyer_supply_eff
                    b_surplus_row.pop(good, None)
                    b_deficit_keys.add(good)
                    b_surplus_keys.discard(good)
                if seller_supply_eff > s_demand + 0.05:
                    s_surplus_row[good] = seller_supply_eff - s_demand
                    s_deficit_row.pop(good, None)
                    s_surplus_keys.add(good)
                    s_deficit_keys.discard(good)
                elif s_demand > seller_supply_eff + 0.05:
                    s_deficit_row[good] = s_demand - seller_supply_eff
                    s_surplus_row.pop(good, None)
                    s_deficit_keys.add(good)
                    s_surplus_keys.discard(good)

                if record_trades:
                    buyer.last_trades.setdefault(good, []).append(
                        (volume, seller.name, unit_price)
                    )
                    seller.last_trades.setdefault(good, []).append(
                        (-volume, buyer.name, seller_quote)
                    )
                any_trade = True

            if not any_trade:
                break


# ── Settle scoring ─────────────────────────────────────────────────────────

def _settle_score(cell, ter, rivers, res, all_city_cells, params):
    """Score a cell as a potential city site. Returns a float or None."""
    t = ter[cell]
    if t in (T.MTN, T.SNOW, T.DESERT) or t <= T.COAST:
        return None

    score = 0.0

    if all_city_cells:
        min_d = min(dist(cell, oc) for oc in all_city_cells)
        if min_d <= 2:
            score -= 500
        elif min_d <= 4:
            score -= 80 / min_d
        elif min_d <= 7:
            score -= 30 / min_d
        score += min(min_d * 0.3, 5)

    if cell_river_mouth(cell, ter, rivers):
        score += 60
    elif cell_on_river(cell, rivers):
        score += params.get("river_pref", 10) * 1.5
    if cell_coastal(cell, ter):
        score += params.get("coast_pref", 5) * 1.5
    if t in CAN_FARM or (cell_on_river(cell, rivers) and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT)):
        score += 2
    else:
        for n in neighbors(cell):
            if cell_on_river(n, rivers) and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT):
                score += 2
                break
    if cell in res:
        score += 3

    return score


def _eval_settle_candidate(civ, cell, ter, rivers, res, all_city_cells, params):
    sc = _settle_score(cell, ter, rivers, res, all_city_cells, params)
    if sc is not None and sc > getattr(civ, "_settle_score", float("-inf")):
        civ._settle_candidate = cell
        civ._settle_score = sc


# ── Peace (restore pre-war borders, keep captured cities) ─────────────────

def _settle_peace(war, a, b, om, civs, tick, add_event):
    att_id, def_id = war.att, war.def_id
    att  = next((c for c in civs if c.id == att_id), None)
    defn = next((c for c in civs if c.id == def_id), None)
    if not att or not defn:
        return

    pre_a = getattr(war, "pre_ter_a", set())
    pre_d = getattr(war, "pre_ter_d", set())
    cap_by_a = set(getattr(war, "captured_cities_a", []))
    cap_by_d = set(getattr(war, "captured_cities_d", []))

    att_city_cells = [ci.cell for ci in att.cities]
    def_city_cells = [ci.cell for ci in defn.cities]

    perm_to_att = set()
    for t in pre_d:
        best_cap_d = min((dist(t, cc) for cc in cap_by_a), default=float("inf"))
        best_def_d = min((dist(t, dc) for dc in def_city_cells), default=float("inf"))
        if cap_by_a and best_cap_d < best_def_d:
            perm_to_att.add(t)

    perm_to_def = set()
    for t in pre_a:
        best_cap_d = min((dist(t, cc) for cc in cap_by_d), default=float("inf"))
        best_att_d = min((dist(t, ac) for ac in att_city_cells), default=float("inf"))
        if cap_by_d and best_cap_d < best_att_d:
            perm_to_def.add(t)

    disputed = (att.territory | defn.territory) & (pre_a | pre_d)

    for cell in list(disputed):
        if cell in perm_to_att:
            defn.territory.discard(cell)
            att.territory.add(cell)
            om[cell] = att_id
        elif cell in perm_to_def:
            att.territory.discard(cell)
            defn.territory.add(cell)
            om[cell] = def_id
        elif cell in pre_a:
            defn.territory.discard(cell)
            att.territory.add(cell)
            om[cell] = att_id
        elif cell in pre_d:
            att.territory.discard(cell)
            defn.territory.add(cell)
            om[cell] = def_id

    # Prune cities that ended up outside their owner's territory
    att.cities  = [c for c in att.cities  if c.cell in att.territory]
    defn.cities = [c for c in defn.cities if c.cell in defn.territory]

    # Peace treaty disbands all armies
    war.armies_a = []
    war.armies_d = []

    add_event(f"🕊 Year {tick}: {a.name} & {b.name} made peace")
    a.events.append(f"Year {a.age}: Peace with {b.name}")
    b.events.append(f"Year {b.age}: Peace with {a.name}")


# ── Per-tick simulation driver ─────────────────────────────────────────────

def tick_sim(
    civs:      List[Civ],
    ter:       list,
    res:       dict,
    om:        list,
    wars:      dict,
    rivers:    Rivers,
    impr:      list,
    tick:      int,
    add_event: Callable[[str], None],
    params:    dict,
    good_efficiency: Optional[Dict[str, List[float]]] = None,
) -> List[Civ]:
    """Run one simulation step. Returns any newly-spawned civs (currently
    always empty — fragmentation is disabled)."""

    tick_start = time.perf_counter()
    section_prev = tick_start
    perf: dict[str, float] = {}

    def _mark(name: str) -> None:
        nonlocal section_prev
        now = time.perf_counter()
        perf[name] = perf.get(name, 0.0) + (now - section_prev)
        section_prev = now

    alive = [c for c in civs if c.alive]
    civs_all_by_id: dict[int, Civ] = {c.id: c for c in civs}

    # Remove stale wars when either side has collapsed. Without this,
    # armies can remain stuck in a non-progressing war shell.
    for wk, war in list(wars.items()):
        att_all = civs_all_by_id.get(war.att)
        def_all = civs_all_by_id.get(war.def_id)
        att_alive = bool(att_all and att_all.alive)
        def_alive = bool(def_all and def_all.alive)
        if att_alive and def_alive:
            continue

        winner = att_all if att_alive else (def_all if def_alive else None)
        loser = def_all if att_alive else (att_all if def_alive else None)

        war.armies_a = []
        war.armies_d = []
        wars.pop(wk, None)

        if winner is not None and loser is not None:
            add_event(
                f"🕊 Year {tick}: {winner.name}'s war with {loser.name} ended after collapse"
            )
            winner.events.append(
                f"Year {winner.age}: War with {loser.name} ended (collapse)"
            )


    # ── Diplomacy ─────────────────────────────────────────────────────────
    # Cache border cells per civ once — the pair loop is O(C²).
    border_cache: dict = {c.id: border_cells(c.territory) for c in alive}
    civs_by_id: dict = {c.id: c for c in alive}

    # Drift relations + refresh power snapshots.
    diplomacy.tick_relations(alive, wars, border_cache, tick)
    diplomacy.tick_dispositions(alive, border_cache, wars, tick)

    for i in range(len(alive)):
        for j in range(i + 1, len(alive)):
            a, b = alive[i], alive[j]
            k = war_key(a.id, b.id)
            at_war = k in wars

            border = any(bc in b.territory for bc in border_cache[a.id])

            if at_war:
                war = wars[k]
                diplomacy.tick_war_morale(war)
                if diplomacy.should_sue_for_peace(war):
                    _settle_peace(war, a, b, om, civs, tick, add_event)
                    diplomacy.apply_post_war_baseline(a, b)
                    del wars[k]
                continue

            # Peace — try to declare war (either side may be the aggressor),
            # otherwise consider an alliance.
            declared = False
            # Randomise which side gets first dibs on the declaration roll
            # so we don't systematically favour civ `a`.
            pair = [(a, b), (b, a)]
            random.shuffle(pair)
            for declarer, target in pair:
                new_war = diplomacy.consider_war_declaration(
                    declarer, target, wars, civs_by_id, tick, k, border,
                )
                if new_war:
                    wars[k] = new_war
                    att  = declarer
                    defn = target
                    add_event(
                        f"⚔ Year {tick}: {att.name} declared WAR on {defn.name}!"
                    )
                    att.events.append(f"Year {att.age}: War on {defn.name}")
                    defn.events.append(f"Year {defn.age}: {att.name} attacked")
                    combat.spawn_war_armies(att,  new_war, "a", impr, k)
                    combat.spawn_war_armies(defn, new_war, "d", impr, k)
                    declared = True
                    break

            if (not declared
                    and diplomacy.consider_alliance(a, b, wars, civs_by_id)):
                diplomacy.form_alliance(a, b)
                add_event(
                    f"🤝 Year {tick}: {a.name} and {b.name} formed an alliance"
                )
                a.events.append(f"Year {a.age}: Allied with {b.name}")
                b.events.append(f"Year {b.age}: Allied with {a.name}")

    _mark("diplomacy")

    # ── Per-civ tick ───────────────────────────────────────────────────────
    for civ in alive:
        civ.age += 1
        layout_dirty = False

        # ── Voronoi: assign each territory cell to nearest city ──────────
        layout_version = getattr(civ, "_layout_version", 0)
        cache_version = getattr(civ, "_city_cells_cache_version", -1)
        city_cells_map = getattr(civ, "_city_cells_cache", None)
        if cache_version != layout_version or city_cells_map is None:
            city_cells_map = {}
            for city in civ.cities:
                # Seed each city with its own cell to ensure it always has at least one tile
                city_cells_map[city.cell] = [city.cell]

            if civ.cities:
                for cell in civ.territory:
                    # Skip cells that are already seeds
                    if cell in city_cells_map:
                        continue

                    best_city = None
                    best_d = float("inf")
                    for city in civ.cities:
                        d = dist(cell, city.cell)
                        if d < best_d:
                            best_d = d
                            best_city = city.cell
                    if best_city is not None:
                        city_cells_map[best_city].append(cell)

            civ._city_cells_cache = city_cells_map
            civ._city_cells_cache_version = layout_version

        # ── 1. Production & Local Markets ────────────────────────────────────
        # Regional efficiency shorthands — default to 1.0 (no modulation) if
        # the caller didn't pass a map (e.g. legacy tests).
        def _eff(field, cell):
            return field[cell] if field is not None else 1.0

        gov = sync_fort_funding(civ, impr)
        fort_hosts: dict[int, City] = {}
        for fort_cell in gov.forts.keys():
            host_city = fort_host_city(civ, fort_cell)
            if host_city is not None:
                fort_hosts[fort_cell] = host_city

        for city in civ.cities:
            # Initialize economic dicts
            city.last_trades = {}
            for good in GOODS:
                city.supply[good] = 0.0
                city.demand[good] = 0.0
                city.net_imports[good] = 0.0
                city.income_domestic[good] = 0.0
                city.income_export[good] = 0.0
                city.income_import[good] = 0.0
            city.income_misc = 0.0

            # Every city has baseline staple output.
            city.supply["grain"] = 1.0

            city.near_river = cell_on_river(city.cell, rivers)
            city.coastal    = cell_coastal(city.cell, ter)
            assigned = city_cells_map.get(city.cell, [])
            if not assigned:
                assigned = [city.cell]
            city.tiles = assigned
            city.farm_tiles = []

            # Cache this city's average efficiency per good. This only needs
            # refreshing when the city layout changes because terrain and
            # regional efficiency are otherwise static.
            layout_version = getattr(civ, "_layout_version", 0)
            if (
                not getattr(city, "local_efficiency", None)
                or getattr(city, "_local_efficiency_layout_version", -1) != layout_version
            ):
                if good_efficiency:
                    from .regions import city_avg_efficiency
                    city.local_efficiency = city_avg_efficiency(assigned, good_efficiency)
                else:
                    city.local_efficiency = {g: 1.0 for g in GOODS}
                city._local_efficiency_layout_version = layout_version

            prod_version = getattr(city, "_production_version", 0)
            if (
                getattr(city, "_production_cells_cache_layout_version", -1) != layout_version
                or getattr(city, "_production_cells_cache_version", -1) != prod_version
            ):
                city._production_cells_cache = [
                    cell for cell in assigned if imp_level(impr[cell]) > 0
                ]
                city._production_cells_cache_layout_version = layout_version
                city._production_cells_cache_version = prod_version

            if tick % EMPLOYMENT_PERIOD == 0:
                employment.update_city_employment(city, impr)

            # Periodic profit-based reallocation: every 10 ticks move workers
            # from low-profit buildings to high-profit ones. Uses last tick's
            # post-trade prices (still in city.prices at this point).
            if (tick % WORKER_REALLOC_PERIOD) == 0:
                employment.reallocate_workers_by_profit(
                    city, impr, ter, res, rivers, good_efficiency,
                )

            # Production (Supply)
            for cell in city._production_cells_cache:
                raw = impr[cell]
                it = imp_type(raw)
                lvl = staffed_level(city, cell, imp_level(raw))
                if lvl <= 0: continue

                on_river = cell_on_river(cell, rivers)
                riv = 2.0 if on_river else 1.0
                coast_mult = 1.5 if cell_coastal(cell, ter) else 1.0
                r = res.get(cell)

                profile = IMPROVEMENT_ECONOMY.get(it)
                if profile:
                    if profile.counts_as_worked_tile:
                        city.farm_tiles.append(cell)

                    if profile.output_good:
                        out = profile.output_base + profile.output_per_level * lvl
                        if profile.use_river_mult:
                            out *= riv
                        if profile.use_coast_mult:
                            out *= coast_mult

                        if profile.windmill_bonus_per_staffed_level > 0:
                            wm_mult = 1.0
                            for n in neighbors(cell):
                                if 0 <= n < N:
                                    n_raw = impr[n]
                                    if imp_type(n_raw) == IMP.WINDMILL:
                                        n_lvl = imp_level(n_raw)
                                        n_staff = staffed_level(city, n, n_lvl)
                                        wm_mult += n_staff * profile.windmill_bonus_per_staffed_level
                            out *= wm_mult

                        if profile.resource_output_multiplier and r is not None:
                            out *= profile.resource_output_multiplier.get(r, 1.0)

                        eff_map = good_efficiency.get(profile.output_eff_good) if (good_efficiency and profile.output_eff_good) else None
                        out *= _eff(eff_map, cell)

                        city.supply[profile.output_good] += out
                        if profile.demand_good and profile.demand_per_output > 0:
                            city.demand[profile.demand_good] += out * profile.demand_per_output

                        # Mines can co-produce special outputs from vein cells.
                        if it == IMP.MINE:
                            if r == "sapphires":
                                sapp_out = (0.10 + 0.06 * lvl)
                                sapp_map = good_efficiency.get("sapphires") if good_efficiency else None
                                sapp_out *= _eff(sapp_map, cell)
                                city.supply["sapphires"] += sapp_out
                            elif r == "iron":
                                iron_out = (0.30 + 0.12 * lvl)
                                iron_map = good_efficiency.get("iron_ore") if good_efficiency else None
                                iron_out *= _eff(iron_map, cell)
                                city.supply["iron_ore"] += iron_out

                # Flat resource tile effects are data-driven too.
                effect = RESOURCE_TILE_EFFECTS.get(r)
                if effect:
                    if "income_misc" in effect:
                        bonus = float(effect["income_misc"])
                        city.gold += bonus
                        city.income_misc += bonus
                    else:
                        g = effect.get("good")
                        if g:
                            amount = float(effect.get("amount", 0.0))
                            if effect.get("river_mult"):
                                amount *= riv
                            if effect.get("coast_mult"):
                                amount *= coast_mult
                            city.supply[g] += amount

            # Demand is driven by slow-moving profession consumption tiers.
            _apply_consumption_demands(city)

            # Government upkeep demand must be applied after local demand has
            # been initialized for the tick; otherwise it gets reset away.
            for fort_cell, host_city in fort_hosts.items():
                if host_city is city:
                    for good, qty in gov.fort_upkeep_goods.items():
                        city.demand[good] = city.demand.get(good, 0.0) + float(qty)

            if not hasattr(city, "building_profit") or city.building_profit is None:
                city.building_profit = {}
            if tick % BUILDING_PROFIT_PERIOD == 0:
                city.building_profit.clear()

            # Base flow state for the intra-tick coupled building/trade solver.
            city._base_supply = {g: city.supply.get(g, 0.0) for g in GOODS}
            city._base_demand = {g: city.demand.get(g, 0.0) for g in GOODS}

    _mark("production_local")

    # ── 1.5 Coupled building/trade flow solve (no inventory) ─────────────────
    # Converter buildings and trade are solved together inside the tick to
    # avoid one-tick lag oscillations (fabric <-> clothes ping-pong).
    all_cities = [ct for cv in alive for ct in cv.cities]
    util_state: dict[tuple[int, str], float] = {}
    for city in all_cities:
        blevels = getattr(city, "buildings", None) or {}
        bstaff = getattr(city, "building_staffing", None) or {}
        for bkey, btype in BUILDING_TYPES.items():
            lvl = int(blevels.get(bkey, 0))
            staff_cap = min(lvl, int(bstaff.get(bkey, 0)))
            if staff_cap > 0:
                util_state[(city.cell, bkey)] = float(staff_cap)

    if len(all_cities) >= 100:
        COUPLED_ITERS = 2
    elif len(all_cities) >= 70:
        COUPLED_ITERS = 3
    else:
        COUPLED_ITERS = 4
    UTIL_DAMP = 0.4
    provisional_trade_passes = 1 if len(all_cities) >= 100 else 2
    provisional_trade_stats: dict[str, int] = {}

    for _it in range(COUPLED_ITERS):
        # Reset to base flows for this coupled iteration.
        for city in all_cities:
            city.last_trades = {}
            city.building_profit.clear()
            for g in GOODS:
                city.supply[g] = city._base_supply.get(g, 0.0)
                city.demand[g] = city._base_demand.get(g, 0.0)
                city.net_imports[g] = 0.0

        # Apply converter plans as flow intents for this iteration.
        for city in all_cities:
            for bkey, btype in BUILDING_TYPES.items():
                u = util_state.get((city.cell, bkey), 0.0)
                if u <= 0:
                    continue
                for g, amt in btype.inputs.items():
                    city.demand[g] += amt * u
                for g, amt in btype.outputs.items():
                    city.supply[g] += amt * u

        # Provisional physical trade only (no gold/income side effects).
        _tick_trade(
            alive,
            ter,
            om,
            settle_financials=False,
            record_trades=False,
            max_passes=provisional_trade_passes,
            stats=provisional_trade_stats,
        )

        # Re-estimate feasible converter utilization from effective inputs.
        max_delta = 0.0
        for city in all_cities:
            blevels = getattr(city, "buildings", None) or {}
            bstaff = getattr(city, "building_staffing", None) or {}
            for bkey, btype in BUILDING_TYPES.items():
                lvl = int(blevels.get(bkey, 0))
                staff_cap = min(lvl, int(bstaff.get(bkey, 0)))
                if staff_cap <= 0:
                    continue
                old_u = util_state.get((city.cell, bkey), 0.0)

                ratio = 1.0
                for g, amt in btype.inputs.items():
                    desired = amt * staff_cap
                    if desired <= 0:
                        continue
                    eff_avail = city.supply.get(g, 0.0) + city.net_imports.get(g, 0.0)
                    ratio = min(ratio, eff_avail / desired)
                ratio = max(0.0, min(1.0, ratio))

                target_u = staff_cap * ratio
                new_u = old_u * (1.0 - UTIL_DAMP) + target_u * UTIL_DAMP
                util_state[(city.cell, bkey)] = max(0.0, new_u)
                d = abs(new_u - old_u)
                if d > max_delta:
                    max_delta = d

        if max_delta < 0.05:
            break

    _mark("coupled_flow_solver")

    # Final settled pass with money/income/trade logs.
    for city in all_cities:
        city.last_trades = {}
        city.building_profit.clear()
        for g in GOODS:
            city.supply[g] = city._base_supply.get(g, 0.0)
            city.demand[g] = city._base_demand.get(g, 0.0)
            city.net_imports[g] = 0.0

    for city in all_cities:
        for bkey, btype in BUILDING_TYPES.items():
            u = util_state.get((city.cell, bkey), 0.0)
            if u <= 0:
                continue
            for g, amt in btype.inputs.items():
                city.demand[g] += amt * u
            for g, amt in btype.outputs.items():
                city.supply[g] += amt * u

            out_val = sum(
                amt * u * city.prices.get(g, BASE_PRICES.get(g, 1.0))
                for g, amt in btype.outputs.items()
            )
            in_val = sum(
                amt * u * city.prices.get(g, BASE_PRICES.get(g, 1.0))
                for g, amt in btype.inputs.items()
            )
            city.building_profit[bkey] = out_val - in_val

    # ── 2. Arbitrage (Trade) ──────────────────────────────────────────────────
    # Runs every tick so trade flows are persistent in the UI. Volume per
    # pair self-regulates to the marginal-profit-zero point (see _tick_trade).
    final_trade_passes = 1 if len(all_cities) >= 140 else (2 if len(all_cities) >= 100 else (3 if len(all_cities) >= 80 else 4))

    staple_pressure = any(
        (city.supply.get("grain", 0.0) + city.supply.get("bread", 0.0))
        < (city.demand.get("grain", 0.0) + city.demand.get("bread", 1.0))
        for city in all_cities
    )
    if staple_pressure:
        # Import-only cities need a few more passes for grain/bread to route
        # through hubs. Keep the optimization for everything else.
        final_trade_passes = max(final_trade_passes, 4)

    final_trade_stats: dict[str, int] = {}
    _tick_trade(
        alive,
        ter,
        om,
        settle_financials=True,
        record_trades=True,
        max_passes=final_trade_passes,
        stats=final_trade_stats,
    )

    _mark("trade_settlement")

    # Refresh prices to reflect the post-trade equilibrium. Effective supply
    # is (local production + net_imports), so a city that imports heavily
    # sees its price drop, and an exporter's price rises toward equilibrium.
    for civ in alive:
        for city in civ.cities:
            gross_output = 0.0
            for good in GOODS:
                effective = city.supply.get(good, 0.0) + city.net_imports.get(good, 0.0)
                demand_g = city.demand.get(good, 0.0)
                city.prices[good] = _get_price(good, effective, demand_g)

                base_p = BASE_PRICES.get(good, city.prices[good])
                served = min(effective, demand_g)

                # Gold flow from domestic commerce: preserved at its historical
                # scale (served × base × 3.75). Decoupled from the reported
                # income so the ledger can be symmetric with export pricing
                # without changing treasury balance.
                city.gold += served * base_p * 3.75

                # Domestic income is now valued at the same rate as exports
                # (0.95 × base). Previously 0.15 × base, which made any net
                # importer structurally negative in income_total even though
                # their treasury was fine.
                dom_val = served * base_p * 0.95
                city.income_domestic[good] += dom_val

                # Gross economic throughput at base prices: locally-served
                # demand plus goods shipped abroad. Counts hub consumption
                # and producer exports equally, so economy size tracks
                # activity rather than trade balance.
                gross_exports = max(0.0, -city.net_imports.get(good, 0.0))
                gross_output += (served + gross_exports) * base_p

            # ── Income & employment snapshots ────────────────────────────
            dom = sum(city.income_domestic.values())
            exp = sum(city.income_export.values())
            imp = sum(city.income_import.values())
            city.income_total = dom + exp - imp + city.income_misc
            city.economic_output = gross_output + city.income_misc
            pop = max(1.0, city.population)
            city.income_per_person = city.income_total / pop
            city.market_satisfaction = _compute_market_satisfaction(city)

            if tick % CONSUMPTION_REBALANCE_PERIOD == 0:
                employment.update_city_consumption_state(city)

            city.workforce     = int(pop)
            city.employed_pop  = city.employee_level_count * N_EMPLOYEES_PER_LEVEL
            city.unemployed_pop = max(0, city.workforce - city.employed_pop)

            # Cleanup temporary solver state.
            if hasattr(city, "_base_supply"):
                del city._base_supply
            if hasattr(city, "_base_demand"):
                del city._base_demand

        collect_tax(civ)
        update_fort_funding(civ)

        if tick % GOV_CONSTRUCTION_PERIOD == 0:
            refresh_government_construction_queue(civ, civs_by_id, border_cache, impr)
            execute_government_construction(civ, civs_by_id, ter, impr, om)

    _mark("price_income")

    # ── 3. Population Dynamics (Pure Flow Logic) ──────────────────────────────
    for civ in alive:
        for city in civ.cities:
            # NET FLOW: supply + net_imports - demand
            staple_supply = city.supply.get("grain", 0.0) + city.supply.get("bread", 0.0)
            staple_imports = city.net_imports.get("grain", 0.0) + city.net_imports.get("bread", 0.0)
            staple_demand = city.demand.get("grain", 0.0) + city.demand.get("bread", 1.0)

            net_staple_flow = staple_supply + staple_imports - staple_demand

            if net_staple_flow < 0:
                # STARVATION: Direct loss based on deficit
                # 1 unit deficit = ~12.5 people
                shrinkage = abs(net_staple_flow) * 15.0
                city.population = max(80.0, city.population - shrinkage)
            else:
                # GROWTH: Based on physical surplus
                # Growth proportional to the surplus magnitude relative to demand
                surplus_ratio = min(1.0, net_staple_flow / max(1.0, staple_demand))
                growth = 0.12 * surplus_ratio * city.population
                city.population += growth

    _mark("population")

    # ── 3.5 Immigration (intra-civ) ──────────────────────────────────────────
    # Single-signal migration: attractiveness is driven almost entirely by
    # the rolling population-weighted consumption tier. Consumption level
    # already bakes in wage affordability (it rises only when a profession's
    # wages clear its basket), and unemployment is priced in because the
    # jobless tier drags the weighted mean down — so no separate
    # satisfaction, opportunity, or unemployment terms are needed.
    # Total emigrants = total immigrants per tick, so civ population is
    # conserved here (staple dynamics handles growth/decay). Cities at the
    # 40-pop floor can receive but cannot emigrate below 40.
    INCOME_HIST_LEN    = 10
    MIGRATION_RATE     = 0.05    # up to 5% of pop can move per tick
    LEVEL_WEIGHT       = 8.0     # how strongly consumption tier dominates
    # Mild satisfaction gate: a city whose markets stop clearing shouldn't
    # keep pulling migrants off a stale-high consumption tier while
    # consumption_levels slowly unwind. Range 0.75..1.0 — light touch.
    SAT_GATE_FLOOR     = 0.75

    run_migration = (tick % MIGRATION_PERIOD) == 0
    if run_migration:
        for civ in alive:
            cities = civ.cities
            if not cities:
                continue

            # 1. Update rolling consumption-level history + compute attractiveness.
            for city in cities:
                prof_counts = getattr(city, "professions", None) or {}
                levels = getattr(city, "consumption_levels", None) or {}
                weighted = 0.0
                total_count = 0.0
                for prof, count in prof_counts.items():
                    if count <= 0:
                        continue
                    weighted += float(count) * float(levels.get(prof, 0.0))
                    total_count += float(count)
                u = float(max(0, getattr(city, "unemployed_pop", 0) or 0))
                if u > 0:
                    weighted += u * float(levels.get("unemployed", 0.0))
                    total_count += u
                cur_level = weighted / total_count if total_count > 0 else 0.0
                city.avg_consumption_level = cur_level

                lvl_hist = city.avg_consumption_level_hist
                lvl_hist.append(cur_level)
                if len(lvl_hist) > INCOME_HIST_LEN:
                    lvl_hist[:] = lvl_hist[-INCOME_HIST_LEN:]
                avg_level = sum(lvl_hist) / len(lvl_hist) if lvl_hist else 0.0

                sat_hist = getattr(city, "market_satisfaction_hist", None)
                if sat_hist is None:
                    city.market_satisfaction_hist = []
                    sat_hist = city.market_satisfaction_hist
                sat_hist.append(float(getattr(city, "market_satisfaction", 0.0)))
                if len(sat_hist) > INCOME_HIST_LEN:
                    sat_hist[:] = sat_hist[-INCOME_HIST_LEN:]
                avg_sat = sum(sat_hist) / len(sat_hist) if sat_hist else 0.0

                # Attractiveness = consumption tier, log-compressed so a 2×
                # richer city isn't 2× as attractive, lightly gated by
                # satisfaction so market collapses don't lag behind the
                # slow-moving consumption-level hysteresis.
                level_quality = math.log1p(max(0.0, avg_level))
                sat_gate = SAT_GATE_FLOOR + (1.0 - SAT_GATE_FLOOR) * avg_sat
                city.attractiveness = max(
                    0.05, 1.0 + level_quality * LEVEL_WEIGHT * sat_gate,
                )

            if len(cities) < 2:
                for city in cities:
                    city.net_migration = 0.0
                continue

            max_attr   = max(c.attractiveness for c in cities)
            total_attr = sum(c.attractiveness for c in cities)
            if max_attr <= 0 or total_attr <= 0:
                for city in cities:
                    city.net_migration = 0.0
                continue

            # 2. Outflow pool: each city emits in proportion to how far below the
            # civ's best it is. Cap so pop can't drop below 40 from emigration.
            emigrants: list[float] = []
            total_pool = 0.0
            for city in cities:
                rel    = city.attractiveness / max_attr
                raw    = city.population * MIGRATION_RATE * (1.0 - rel)
                cap    = max(0.0, city.population - 40.0)
                out    = min(raw, cap)
                emigrants.append(out)
                total_pool += out

            # 3. Redistribute pool across cities proportional to attractiveness.
            # Store this as a per-tick flow so the effect persists across the
            # whole cadence window instead of spiking for a single frame.
            for i, city in enumerate(cities):
                share = total_pool * (city.attractiveness / total_attr)
                net   = share - emigrants[i]
                city.net_migration = net / MIGRATION_PERIOD

    for civ in alive:
        for city in civ.cities:
            if city.net_migration != 0.0:
                city.population = max(80.0, city.population + city.net_migration)

    _mark("migration")

    # ── Post-Economy Housekeeping ───────────────────────────────────────────
    for civ in alive:
        civ.population = sum(c.population for c in civ.cities)
        # Civ-level stats for the UI
        civ.farm_output  = sum(c.supply["grain"] for c in civ.cities)
        civ.ore_output   = sum((c.supply.get("copper_ore", 0.0) + c.supply.get("iron_ore", 0.0)) for c in civ.cities)
        civ.stone_output = sum(c.supply["stone"] for c in civ.cities)
        civ.metal_output = sum(c.supply["copper"] for c in civ.cities)
        # Sum gold for UI, but don't overwrite the city-level gold which is 
        # what matters for investment.
        total_gold = sum(c.gold for c in civ.cities)
        civ.gold   = total_gold
        civ.wealth = total_gold # legacy sync
        
        civ.tech      = getattr(civ, "tech", 0.0) + 0.007 * math.log2(civ.population + 1) * (1 + len(civ.cities) * 0.08)
        civ.culture   = getattr(civ, "culture", 0.0) + 0.004 * math.log2(len(civ.territory) + 1)
        civ.military  = max(8.0, civ.population * 0.14 + civ.tech * 2 + (civ.metal_output * 4.0 + civ.ore_output * 0.5) * 0.1)
        civ.integrity = min(1.0, max(0.1,
            getattr(civ, "integrity", 1.0)
            + civ.culture * 0.0002
            - (0.001  if len(civ.territory) > 80 else 0)
            - (0.0004 if civ.age > 100           else 0)
            + (0.0001 if civ.wealth > 100        else 0)
        ))

        # Abandon cities that drop below viability (capital never abandons)
        surviving_cities = []
        for i, city in enumerate(civ.cities):
            if i == 0 or city.population >= 7:
                surviving_cities.append(city)
            else:
                civ.events.append(f"Year {civ.age}: {city.name} was abandoned due to low population.")
        civ.cities = surviving_cities

        # ── City development (investment, focus HMM, placement) ──────────
        city_dev.tick_city_development(civ, wars, ter, res, rivers, impr, tick, om, good_efficiency)

        # ── City founding ────────────────────────────────────────────────
        all_city_cells = [ci.cell for other in alive for ci in other.cities]
        at_war = any(w.att == civ.id or w.def_id == civ.id for w in wars.values())
        
        # Determine current goal
        if civ.goal_index >= len(civ.goal_queue):
            civ.goal_index = 0
        current_goal = civ.goal_queue[civ.goal_index]

        # Expansion logic ONLY if current goal is FOUND
        if current_goal == "FOUND":
            # Increment goal age (already incremented in city_dev but keeping consistent)
            civ.goal_ticks += 1
            if civ.goal_ticks > 300:
                civ.goal_index += 1
                civ.goal_ticks = 0
                continue

            # Expansion requirements: Sufficient territory and population
            can_expand = (
                not at_war
                and len(civ.territory) > (len(civ.cities) + 1) * 20 # relaxed for queue
                and max((c.population for c in civ.cities), default=0) > 100 
                and len(civ.cities) < len(civ.territory) // 20 + 1 
            )

            if can_expand:
                paying_city = max(civ.cities, key=lambda c: c.gold) if civ.cities else None
                if paying_city is not None and paying_city.gold >= 20.0:
                    best_cell = getattr(civ, "_settle_candidate", -1)
                    if best_cell != -1 and best_cell in civ.territory:
                        sc = _settle_score(best_cell, ter, rivers, res, all_city_cells, params)
                        if sc is not None and sc > 0:
                            cn = gen_city_name(civ.onom)
                            new_city = City(
                                cell=best_cell,
                                name=cn,
                                population=25.0,
                                is_capital=False,
                                founded=tick,
                                gold=2500.0,
                                focus=random.choice([FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE, FOCUS.TRADE]),
                                near_river=cell_on_river(best_cell, rivers),
                                coastal=cell_coastal(best_cell, ter),
                                last_dmg_tick=-999,
                            )
                            new_city.max_hp = combat.city_max_hp(new_city, impr)
                            new_city.hp = new_city.max_hp
                            civ.cities.append(new_city)
                            layout_dirty = True

                            paying_city.gold -= 20

                            civ.events.append(f"Year {civ.age}: Founded {cn} (Goal Met)")
                            add_event(f"🏘 Year {tick}: {civ.name} founded {cn}")
                            if hasattr(civ, "_settle_candidate"):
                                delattr(civ, "_settle_candidate")
                            if hasattr(civ, "_settle_score"):
                                delattr(civ, "_settle_score")

                            civ.goal_index += 1
                            civ.goal_ticks = 0
            else:
                # Cannot expand physically — skip to avoid deadlock
                if len(civ.territory) <= (len(civ.cities) + 1) * 20:
                    civ.goal_index += 1
                    civ.goal_ticks = 0

        # ── Roads ────────────────────────────────────────────────────────
        if len(civ.cities) >= 2 and civ.gold > 12 and tick % 50 == 0:
            build_road(civ, ter)

        # ── Expansion ────────────────────────────────────────────────────
        city_count = max(1, len(civ.cities))
        tiles_per_city = len(civ.territory) / city_count
        can_expand_territory = tiles_per_city <= MAX_TILES_PER_CITY_FOR_EXPANSION

        if can_expand_territory:
            borders = border_cells(civ.territory)

            # 1. Immediate "pocket" filling (free)
            pocket_targets = [
                c for c in borders
                if 0 <= c < N and is_land(ter, c) and om[c] == 0
                and sum(1 for n in neighbors(c) if n in civ.territory) >= 3
            ]
            for c in pocket_targets:
                civ.territory.add(c)
                om[c] = civ.id
                _eval_settle_candidate(civ, c, ter, rivers, res, all_city_cells, params)
                layout_dirty = True

            # 2. Passive territorial creep (unconditional)
            # Cities always want more land.
            if random.random() < 0.2:  # Steady growth chance
                borders = border_cells(civ.territory)
                targets = [
                    c for c in borders
                    if 0 <= c < N and is_land(ter, c) and om[c] == 0
                ]
                if targets:
                    targets.sort(key=lambda c: (
                        sum(1 for n in neighbors(c) if n in civ.territory) * 5
                        + (4 if c in res else 0)
                        + (params["river_pref"] * 0.5 if cell_on_river(c, rivers) else 0)
                        + (2 if ter[c] in (T.PLAINS, T.GRASS) else 0)
                        - (3 if ter[c] >= T.MTN else 0)
                        + random.random() * 3.0
                    ), reverse=True)

                    # Take 1-3 cells every few ticks
                    cnt = random.randint(1, 3)
                    for c in targets[:cnt]:
                        civ.territory.add(c)
                        om[c] = civ.id
                        _eval_settle_candidate(civ, c, ter, rivers, res, all_city_cells, params)
                        layout_dirty = True

        before_cities = len(civ.cities)
        civ.cities = [c for c in civ.cities if c.cell in civ.territory]
        if len(civ.cities) != before_cities:
            layout_dirty = True
        if civ.cities and not any(c.is_capital for c in civ.cities):
            civ.cities[0].is_capital = True
            civ.capital = civ.cities[0].cell

        civ_at_war = any(w.att == civ.id or w.def_id == civ.id for w in wars.values())

        if not civ.cities and civ_at_war:
            for wk, war in list(wars.items()):
                if war.att == civ.id or war.def_id == civ.id:
                    conqueror_id = war.def_id if war.att == civ.id else war.att
                    conqueror = next((c for c in alive if c.id == conqueror_id), None)
                    if conqueror:
                        for c in list(civ.territory):
                            conqueror.territory.add(c)
                            om[c] = conqueror_id
                        _touch_city_layout(conqueror)
                        civ.alive = False
                        diplomacy.break_alliances_with(civ, civs_by_id)
                        add_event(f"🏳 Year {tick}: {civ.name} surrendered to {conqueror.name}!")
                    wars.pop(wk, None)
                    break
            continue

        if not civ.cities and civ.territory and not civ_at_war:
            all_other_cities = [ci.cell for other in alive if other.id != civ.id for ci in other.cities]
            best_refound = None
            best_min_d = -1
            for cell in list(civ.territory)[:80]:
                if ter[cell] in (T.MTN, T.SNOW) or ter[cell] <= T.COAST: continue
                md = min((dist(cell, oc) for oc in all_other_cities), default=999)
                if md > best_min_d:
                    best_min_d = md
                    best_refound = cell
            cap_cell = best_refound if best_refound is not None else next(iter(civ.territory))
            cn = gen_city_name(civ.onom)
            refounded = City(
                cell=cap_cell, name=cn, population=20.0, is_capital=True,
                founded=tick, gold=2500.0,
                focus=random.choice([FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE, FOCUS.TRADE]),
                near_river=cell_on_river(cap_cell, rivers), coastal=cell_coastal(cap_cell, ter), last_dmg_tick=-999,
            )
            refounded.max_hp = combat.city_max_hp(refounded, impr)
            refounded.hp     = refounded.max_hp
            civ.cities  = [refounded]
            civ.capital = cap_cell
            layout_dirty = True
            add_event(f"🏛 Year {tick}: {civ.name} refounded {cn}")
        if layout_dirty:
            _touch_city_layout(civ)

    _mark("post_econ_citydev")

    # ── Army tick (movement, behavior, combat, fort respawn) ──────────────
    combat.tick_armies(alive, wars, ter, impr, om, tick, add_event)

    _mark("army")

    # ── City HP regen ─────────────────────────────────────────────────────
    for civ in alive:
        for city in civ.cities:
            combat.ensure_city_hp(city, impr)
            city.max_hp = combat.city_max_hp(city, impr)
            if city.hp < city.max_hp and tick - city.last_dmg_tick > 8:
                city.hp = min(city.max_hp, city.hp + CITY_HP_REGEN)

    _mark("city_hp_regen")

    if tick % PERF_LOG_PERIOD == 0:
        total = (time.perf_counter() - tick_start) * 1000.0
        cities_n = sum(len(c.cities) for c in alive)
        hot = sorted(perf.items(), key=lambda kv: kv[1], reverse=True)[:5]
        hot_txt = ", ".join(f"{k}={v * 1000.0:.1f}ms" for k, v in hot)
        log.info(
            "[perf] tick=%d civs=%d cities=%d total=%.1fms | %s | trade(prov p=%d c=%d ps=%d, final p=%d c=%d ps=%d) | cadences realloc=%d migration=%d",
            tick,
            len(alive),
            cities_n,
            total,
            hot_txt,
            provisional_trade_stats.get("pairs", 0),
            provisional_trade_stats.get("candidates", 0),
            provisional_trade_stats.get("passes", 0),
            final_trade_stats.get("pairs", 0),
            final_trade_stats.get("candidates", 0),
            final_trade_stats.get("passes", 0),
            WORKER_REALLOC_PERIOD,
            MIGRATION_PERIOD,
        )

    return []
