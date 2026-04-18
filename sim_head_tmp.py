"""Main per-tick simulation loop. Army logic lives in engine.combat,
city development in engine.city_dev."""

import heapq
import math
import random
from typing import List, Callable, Optional, Dict

from .constants import (
    W, H, N, T, IMP, CAN_FARM, FOCUS,
    FORT_METAL_UPKEEP, CITY_HP_REGEN,
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


def _get_price(good: str, supply: float, demand: float) -> float:
    base = BASE_PRICES.get(good, 1.0)
    ratio = demand / max(supply, 0.1)
    # Bounded logistic: min 0.2x, max 5.0x
    # Offset 1.804 ensures that mult is exactly 1.0 when ratio is 1.0
    mult = 0.2 + 4.8 / (1.0 + math.exp(-2.0 * (ratio - 1.804)))
    return base * mult


def _tick_trade(civs: List[Civ], ter: list, om: list):
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
        # Precompute distances once.
        pair_cost = []
        for i in range(len(cities)):
            for j in range(i + 1, len(cities)):
                c1, c2 = cities[i], cities[j]
                d = dist(c1.cell, c2.cell)
                pair_cost.append((c1, c2, d * TRANSPORT_COST_PER_DIST * 0.01))

        for _pass in range(4):
            # Build candidates using CURRENT effective prices.
            candidates = []
            for c1, c2, cost in pair_cost:
                for good in GOODS:
                    s1 = c1.supply.get(good, 0.0) + c1.net_imports.get(good, 0.0)
                    d1 = c1.demand.get(good, 0.0)
                    s2 = c2.supply.get(good, 0.0) + c2.net_imports.get(good, 0.0)
                    d2 = c2.demand.get(good, 0.0)
                    gap = abs(_get_price(good, s1, d1) - _get_price(good, s2, d2))
                    if gap <= cost:
                        continue
                    candidates.append((gap, c1, c2, good, cost))

            if not candidates:
                break

            # Highest-gap (most profitable) first. Deterministic — same
            # conditions produce the same ordering every tick.
            candidates.sort(key=lambda x: -x[0])

            any_trade = False
            for _gap, c1, c2, good, cost_per_unit in candidates:
                # Re-read prices — earlier trades in this pass may have
                # moved them.
                s1 = c1.supply.get(good, 0.0) + c1.net_imports.get(good, 0.0)
                d1 = c1.demand.get(good, 0.0)
                s2 = c2.supply.get(good, 0.0) + c2.net_imports.get(good, 0.0)
                d2 = c2.demand.get(good, 0.0)
                p1 = _get_price(good, s1, d1)
                p2 = _get_price(good, s2, d2)

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

                # Bisection on volume — drive post-trade gap down to cost.
                lo, hi = 0.0, hard_cap
                for _ in range(14):
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
                if buyer.gold < total_cost and unit_price > 0:
                    volume = buyer.gold / unit_price
                    total_cost = volume * unit_price
                if volume <= 0.05:
                    continue

                buyer.gold  -= total_cost
                seller.gold += total_cost * 0.95

                buyer.income_import[good]  = buyer.income_import.get(good, 0.0)  + total_cost
                seller.income_export[good] = seller.income_export.get(good, 0.0) + total_cost * 0.95

                buyer.net_imports[good]  = buyer.net_imports.get(good, 0.0)  + volume
                seller.net_imports[good] = seller.net_imports.get(good, 0.0) - volume

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

    alive = [c for c in civs if c.alive]

    # ── Diplomacy ─────────────────────────────────────────────────────────
    # Cache border cells per civ once — the pair loop is O(C²).
    border_cache: dict = {c.id: border_cells(c.territory) for c in alive}
    civs_by_id: dict = {c.id: c for c in alive}

    # Drift relations + refresh power snapshots.
    diplomacy.tick_relations(alive, wars, border_cache)

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

    # ── Per-civ tick ───────────────────────────────────────────────────────
    for civ in alive:
        civ.age += 1

        # ── Voronoi: assign each territory cell to nearest city ──────────
        city_cells_map: dict = {}
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

        # ── 1. Production & Local Markets ────────────────────────────────────
        # Regional efficiency shorthands — default to 1.0 (no modulation) if
        # the caller didn't pass a map (e.g. legacy tests).
        eff_food   = good_efficiency["food"]   if good_efficiency else None
        eff_lumber = good_efficiency["lumber"] if good_efficiency else None
        eff_ore    = good_efficiency["ore"]    if good_efficiency else None
        eff_stone  = good_efficiency["stone"]  if good_efficiency else None
        eff_metal  = good_efficiency["metal"]  if good_efficiency else None

        def _eff(field, cell):
            return field[cell] if field is not None else 1.0

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

            # BASE FOOD: Every city has a baseline production of 1.0 food
            city.supply["food"] = 1.0

            city.near_river = cell_on_river(city.cell, rivers)
            city.coastal    = cell_coastal(city.cell, ter)
            assigned = city_cells_map.get(city.cell, [])
            if not assigned:
                assigned = [city.cell]
            city.tiles = assigned
            city.farm_tiles = []

            # Cache this city's average efficiency per good — used by
            # city_dev for profitability and by the UI tooltip.
            if good_efficiency:
                from .regions import city_avg_efficiency
                city.local_efficiency = city_avg_efficiency(assigned, good_efficiency)
            else:
                city.local_efficiency = {g: 1.0 for g in GOODS}

            employment.update_city_employment(city, impr)

            # Periodic profit-based reallocation: every 10 ticks move workers
            # from low-profit buildings to high-profit ones. Uses last tick's
            # post-trade prices (still in city.prices at this point).
            if tick % 10 == 0:
                employment.reallocate_workers_by_profit(
                    city, impr, ter, res, rivers, good_efficiency,
                )

            # Production (Supply)
            for cell in assigned:
                raw = impr[cell]
                it = imp_type(raw)
                lvl = staffed_level(city, cell, imp_level(raw))
                if lvl <= 0: continue

                on_river = cell_on_river(cell, rivers)
                riv = 2.0 if on_river else 1.0
                coast_mult = 1.5 if cell_coastal(cell, ter) else 1.0
                r = res.get(cell)

                if it == IMP.FARM:
                    # Windmill neighbour bonus
                    wm_mult = 1.0
                    for n in neighbors(cell):
                        if 0 <= n < N:
                            n_raw = impr[n]
                            if imp_type(n_raw) == IMP.WINDMILL:
                                n_lvl = imp_level(n_raw)
                                n_staff = staffed_level(city, n, n_lvl)
                                wm_mult += n_staff * 0.5
                    city.supply["food"] += (2.5 + lvl * 1.5) * riv * coast_mult * wm_mult * _eff(eff_food, cell)
                    city.farm_tiles.append(cell)
                elif it == IMP.FISHERY:
                    city.supply["food"] += (2.0 + lvl * 1.2) * coast_mult * _eff(eff_food, cell)
                    city.farm_tiles.append(cell)
                elif it == IMP.MINE:
                    if r == "iron":
                        city.supply["ore"] += (1.0 + lvl * 0.5) * 2.0 * _eff(eff_ore, cell)
                    else:
                        city.supply["ore"] += (0.5 + lvl * 0.25) * _eff(eff_ore, cell)
                    city.farm_tiles.append(cell)
                elif it == IMP.QUARRY:
                    city.supply["stone"] += (lvl * 2.0) * _eff(eff_stone, cell)
                    city.farm_tiles.append(cell)
                elif it == IMP.LUMBER:
                    city.supply["lumber"] += (lvl * 2.0) * _eff(eff_lumber, cell)
                    city.farm_tiles.append(cell)
                elif it == IMP.SMITHERY:
                    cap = lvl * 2.0 * _eff(eff_metal, cell)
                    city.supply["metal"] += cap
                    # Ore consumption scales with ACTUAL metal output so a
                    # low-metal-efficiency smithery doesn't hoover ore it
                    # can't convert.
                    city.demand["ore"] += cap
                    city.farm_tiles.append(cell)
                elif it in (IMP.PASTURE, IMP.WINDMILL, IMP.PORT, IMP.FORT):
                    city.farm_tiles.append(cell)

                # Flat bonuses from map resources (unmodulated — these are
                # point resources, not the regional biome bonus).
                if r == "wheat": city.supply["food"] += 2.0 * riv
                elif r == "fish": city.supply["food"] += 1.5 * coast_mult
                elif r == "iron": city.supply["ore"] += 1.0
                elif r == "stone": city.supply["stone"] += 1.0
                elif r == "gold":
                    city.gold += 2.0
                    city.income_misc += 2.0
            
            # Basic Demands
            city.demand["food"] = city.population * 0.08
            city.demand["lumber"] += city.population * 0.015
            city.demand["stone"] += city.population * 0.01
            fort_count = sum(1 for t in assigned if imp_type(impr[t]) == IMP.FORT)
            city.demand["metal"] += fort_count * 1.5

            # Update Prices
            for good in GOODS:
                city.prices[good] = _get_price(good, city.supply[good], city.demand[good])

                # In a flow economy, consumption is immediate based on demand
                # Domestic gold income is from population buying their needs
                dom = city.demand[good] * city.prices[good] * 0.15
                city.gold += dom
                city.income_domestic[good] += dom

    # ── 2. Arbitrage (Trade) ──────────────────────────────────────────────────
    # Runs every tick so trade flows are persistent in the UI. Volume per
    # pair self-regulates to the marginal-profit-zero point (see _tick_trade).
    _tick_trade(alive, ter, om)

    # Refresh prices to reflect the post-trade equilibrium. Effective supply
    # is (local production + net_imports), so a city that imports heavily
    # sees its price drop, and an exporter's price rises toward equilibrium.
    for civ in alive:
        for city in civ.cities:
            for good in GOODS:
                effective = city.supply.get(good, 0.0) + city.net_imports.get(good, 0.0)
                city.prices[good] = _get_price(good, effective, city.demand.get(good, 0.0))

            # ── Income & employment snapshots ────────────────────────────
            dom = sum(city.income_domestic.values())
            exp = sum(city.income_export.values())
            imp = sum(city.income_import.values())
            city.income_total = dom + exp - imp + city.income_misc
            pop = max(1.0, city.population)
            city.income_per_person = city.income_total / pop

            city.workforce     = int(pop // 20)   # N_EMPLOYEES_PER_LEVEL = 20
            city.employed_pop  = city.employee_level_count * 20
            city.unemployed_pop = max(0, city.workforce * 20 - city.employed_pop)

    # ── 3. Population Dynamics (Pure Flow Logic) ──────────────────────────────
    for civ in alive:
        for city in civ.cities:
            # NET FLOW: supply + net_imports - demand
            food_supply = city.supply.get("food", 0.0)
            food_imports = city.net_imports.get("food", 0.0)
            food_demand = city.demand.get("food", 1.0)
            
            net_food_flow = food_supply + food_imports - food_demand
            
            if net_food_flow < 0:
                # STARVATION: Direct loss based on deficit
                # 1 unit deficit = ~12.5 people
                shrinkage = abs(net_food_flow) * 15.0
                city.population = max(40.0, city.population - shrinkage)
            else:
                # GROWTH: Based on physical surplus
                # Growth proportional to the surplus magnitude relative to demand
                surplus_ratio = min(1.0, net_food_flow / max(1.0, food_demand))
                growth = 0.12 * surplus_ratio * city.population
                city.population += growth

    # ── 3.5 Immigration (intra-civ) ──────────────────────────────────────────
    # Each city gets an attractiveness score (unemployment penalty + rolling
    # income bonus). Within the civ, emigrants from unattractive cities flow
    # to attractive ones. Total emigrants = total immigrants per tick, so
    # civ population is conserved here (food dynamics handles growth/decay).
    # Cities at the 40-pop floor can receive but cannot emigrate below 40.
    INCOME_HIST_LEN = 10
    MIGRATION_RATE  = 0.01  # up to 1% of pop moves per tick from a 0-rel city

    for civ in alive:
        cities = civ.cities
        if not cities:
            continue

        # 1. Update rolling income history + compute attractiveness.
        for city in cities:
            hist = city.income_per_person_hist
            hist.append(city.income_per_person)
            if len(hist) > INCOME_HIST_LEN:
                hist[:] = hist[-INCOME_HIST_LEN:]
            avg_income = sum(hist) / len(hist) if hist else 0.0

            # Best unstaffed producer slot — an "opportunity" signal that
            # pulls migrants toward cities with idle productive capacity.
            # Normalised per-person (divide by workers/level) so it's on
            # the same scale as avg_income, then weighted a bit heavier.
            best_vac = employment.best_vacancy_profit(
                city, impr, ter, res, rivers, good_efficiency,
            )
            opp_per_person = best_vac / 20.0  # N_EMPLOYEES_PER_LEVEL

            pop         = max(1.0, city.population)
            unemp_rate  = city.unemployed_pop / pop
            static_pen  = 0.05 if city.unemployed_pop >= 20 else 0.0
            scale_pen   = unemp_rate * 3.0
            income_bon  = avg_income    * 4.0
            opp_bon     = opp_per_person * 1.5

            city.attractiveness = max(
                0.05, 1.0 + income_bon + opp_bon - static_pen - scale_pen,
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
        for i, city in enumerate(cities):
            share = total_pool * (city.attractiveness / total_attr)
            net   = share - emigrants[i]
            city.net_migration = net
            city.population   += net

    # ── Post-Economy Housekeeping ───────────────────────────────────────────
    for civ in alive:
        civ.population = sum(c.population for c in civ.cities)
        # Civ-level stats for the UI
        civ.farm_output  = sum(c.supply["food"] for c in civ.cities)
        civ.ore_output   = sum(c.supply["ore"] for c in civ.cities)
        civ.stone_output = sum(c.supply["stone"] for c in civ.cities)
        civ.metal_output = sum(c.supply["metal"] for c in civ.cities)
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
        city_dev.tick_city_development(civ, wars, ter, res, rivers, impr, tick, om)

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
                                gold=40.0,
                                focus=random.choice([FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE, FOCUS.TRADE]),
                                near_river=cell_on_river(best_cell, rivers),
                                coastal=cell_coastal(best_cell, ter),
                                last_dmg_tick=-999,
                            )
                            new_city.max_hp = combat.city_max_hp(new_city, impr)
                            new_city.hp = new_city.max_hp
                            civ.cities.append(new_city)

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

        # 2. Passive territorial creep (unconditional)
        # Cities always want more land. 
        if random.random() < 0.2: # Steady growth chance
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

        civ.cities = [c for c in civ.cities if c.cell in civ.territory]
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
                founded=tick, gold=10.0,
                focus=random.choice([FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE, FOCUS.TRADE]),
                near_river=cell_on_river(cap_cell, rivers), coastal=cell_coastal(cap_cell, ter), last_dmg_tick=-999,
            )
            refounded.max_hp = combat.city_max_hp(refounded, impr)
            refounded.hp     = refounded.max_hp
            civ.cities  = [refounded]
            civ.capital = cap_cell
            add_event(f"🏛 Year {tick}: {civ.name} refounded {cn}")

    # ── Army tick (movement, behavior, combat, fort respawn) ──────────────
    combat.tick_armies(alive, wars, ter, impr, om, tick, add_event)

    # ── City HP regen ─────────────────────────────────────────────────────
    for civ in alive:
        for city in civ.cities:
            combat.ensure_city_hp(city, impr)
            city.max_hp = combat.city_max_hp(city, impr)
            if city.hp < city.max_hp and tick - city.last_dmg_tick > 8:
                city.hp = min(city.max_hp, city.hp + CITY_HP_REGEN)

    return []
