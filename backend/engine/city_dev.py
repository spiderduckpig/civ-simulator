"""Per-city investment, focus HMM, and improvement placement."""

from __future__ import annotations

import random
from typing import Callable, Optional

from .constants import (
    N, IMP, FOCUS, T, CAN_FARM,
    INVEST_PERIOD_TICKS, FOCUS_HMM_PERIOD,
    FORT_BUILD_METAL_COST, BASE_PRICES, N_EMPLOYEES_PER_LEVEL,
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
from .models import City, Civ


# Terrain types a fort is allowed to sit on.
_FORT_TERRAIN = (T.PLAINS, T.GRASS, T.FOREST, T.HILLS)

# Construction economics tuning.
NEW_TILE_BASE_COST = 500.0
NEW_BUILDING_BASE_COST = 3000.0
CAPEX_PAYBACK_TICKS = 240.0
BUILDING_PAYBACK_TICKS = 180.0

# Slack-driven payback relief. When a city has idle labor AND gold piling up,
# the payback window is stretched so marginal builds still clear the gate —
# absorbing workers and draining reserves instead of stalling in an
# oversupply trap. Both weights are per-person ratios so the knobs are
# scale-invariant; the final boost multiplies (unemp × gold) so both
# conditions must be present, matching the user's intent.
_SLACK_UNEMP_FLOOR = 0.03   # unemp rate below this → no relief
_SLACK_UNEMP_SPAN  = 0.17   # full weight at ~20% unemp
_SLACK_GOLD_FULL_PP = 2.0   # gold/person that saturates the gold weight
_SLACK_MAX_BOOST    = 3.0   # payback can stretch up to 4× (1 + 3)


def _slack_payback_mult(city: City) -> float:
    """Return the payback-window multiplier (≥1). Larger = more lenient.

    Only exceeds 1 when BOTH unemployment and gold-per-person are above
    their floors; either alone leaves the gate at baseline strictness.
    """
    pop = max(1.0, float(getattr(city, "population", 0.0)))
    unemp = int(getattr(city, "unemployed_pop", 0) or 0)
    unemp_rate = unemp / pop
    gold = float(getattr(city, "gold", 0.0))

    unemp_weight = max(0.0, min(1.0, (unemp_rate - _SLACK_UNEMP_FLOOR) / _SLACK_UNEMP_SPAN))
    if unemp_weight <= 0.0:
        return 1.0
    gold_weight = max(0.0, min(1.0, (gold / pop) / _SLACK_GOLD_FULL_PP))
    if gold_weight <= 0.0:
        return 1.0
    return 1.0 + unemp_weight * gold_weight * _SLACK_MAX_BOOST


def _touch_city_production(city: City) -> None:
    city._production_version = getattr(city, "_production_version", 0) + 1


# ── Upgrade cost (Gold only, but scaled by material prices) ──────────────────

def _upgrade_cost(city: City, it: int, current_level: int) -> float:
    # Base gold cost scales with level. New builds and the first upgrade
    # (current_level 0 or 1) stay at baseline; the ramp is intentionally steep
    # so high-level buildings dominate a city's gold budget.
    level_mult = max(1.0, current_level) ** 1.4
    if current_level <= 0:
        # New improvements on new tiles are intentionally expensive.
        base_gold = NEW_TILE_BASE_COST
    else:
        base_gold = 8.0 * level_mult
    
    # Material requirements (now purely for price scaling)
    mats = {}
    if current_level > 0:
        if it == IMP.FARM:
            mats["lumber"] = 4.0 * level_mult
        elif it == IMP.MINE or it == IMP.QUARRY:
            mats["lumber"] = 6.0 * level_mult
            mats["stone"]  = 3.0 * level_mult
        elif it == IMP.SMITHERY:
            mats["stone"]  = 10.0 * level_mult
            mats["metal"]  = 4.0 * level_mult
        elif it == IMP.FORT:
            mats["stone"]  = 15.0 * level_mult
            mats["metal"]  = 8.0 * level_mult
        else:
            mats["lumber"] = 4.0 * level_mult
            mats["stone"]  = 2.0 * level_mult

    # Add material costs based on local prices
    total_cost = base_gold
    for good, amt in mats.items():
        price = city.prices.get(good, 1.0)
        total_cost += amt * price
        
    return total_cost


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
    base_gold = NEW_BUILDING_BASE_COST if current_level <= 0 else 24.0 * level_mult
    mat_cost = 0.0
    for good in b.cost_resources:
        mat_cost += city.prices.get(good, BASE_PRICES.get(good, 1.0)) * 4.0 * level_mult
    return base_gold + mat_cost


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


def _try_build_city_buildings(city: City) -> bool:
    """Market-driven city-building investment.

    Loops so a gold-rich city can stack several upgrades in one call. We
    project staffing forward each iteration (assuming new levels get filled
    from unemployment) so the vacancy gate doesn't cap us at two upgrades
    per building while there are still jobless workers to absorb the levels.
    """
    if not hasattr(city, "buildings") or city.buildings is None:
        city.buildings = {}
    if not hasattr(city, "building_staffing") or city.building_staffing is None:
        city.building_staffing = {}

    if not _city_is_profitable_for_expansion(city):
        return False

    staffing = city.building_staffing
    remaining_unemployed = int(getattr(city, "unemployed_pop", 0))
    # Virtual "staffed" delta per key: levels we upgraded this call that we
    # assume unemployment absorbs. Keeps the vacancy gate from false-tripping
    # on our own just-added capacity.
    filled: dict[str, int] = {}
    upgraded_any = False
    payback = BUILDING_PAYBACK_TICKS * _slack_payback_mult(city)

    while True:
        best_key: Optional[str] = None
        best_score = 0.0
        for key, b in BUILDING_TYPES.items():
            cur_lvl = int(city.buildings.get(key, 0))
            if cur_lvl >= b.max_level:
                continue
            virtual_staffed = int(staffing.get(key, 0)) + filled.get(key, 0)
            if cur_lvl - virtual_staffed > 1:
                continue
            out_v = 0.0
            for g, amt in b.outputs.items():
                out_v += amt * city.prices.get(g, BASE_PRICES.get(g, 1.0))
            in_v = 0.0
            for g, amt in b.inputs.items():
                in_v += amt * city.prices.get(g, BASE_PRICES.get(g, 1.0))
            margin = out_v - in_v
            cost = _building_upgrade_cost(city, key, cur_lvl)
            # Payback-style ROI score: positive means the building should
            # earn back its capex within the payback window. Window stretches
            # when the city has idle labor + idle gold (see _slack_payback_mult).
            score = margin - (cost / payback)
            if score > best_score:
                best_score = score
                best_key = key

        if best_key is None or best_score <= 0.0:
            break

        cur_lvl = int(city.buildings.get(best_key, 0))
        cost = _building_upgrade_cost(city, best_key, cur_lvl)

        # Require either unemployment to absorb, or strong enough margin to
        # pull workers away from existing lower-profit jobs. Friction scales
        # with the upgrade's own cost: pulling staff into a 2000-gold
        # megafactory needs a bigger edge than pulling them into a cheap
        # mill, and this stays meaningful as prices drift.
        friction_threshold = cost / payback * 0.5
        if remaining_unemployed < N_EMPLOYEES_PER_LEVEL and best_score < friction_threshold:
            break

        if not _try_buy(city, cost):
            break

        city.buildings[best_key] = cur_lvl + 1
        if remaining_unemployed >= N_EMPLOYEES_PER_LEVEL:
            filled[best_key] = filled.get(best_key, 0) + 1
            remaining_unemployed -= N_EMPLOYEES_PER_LEVEL
        # else: high-margin upgrade without workers on hand. Don't advance
        # `filled`; next iter's vacancy check will catch it.
        upgraded_any = True

    return upgraded_any


# ── Profitability hint ────────────────────────────────────────────────────
# Producer imp for each tradable good. Used to bias "what to build next"
# toward whichever output is currently scarce (high local price).
_GOOD_TO_PRODUCER = {
    "grain":  IMP.FARM,
    "fabric": IMP.COTTON,
    "lumber": IMP.LUMBER,
    "copper_ore": IMP.MINE,
    "iron_ore": IMP.MINE,
    "stone":  IMP.QUARRY,
    "copper": IMP.SMITHERY,
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
    for good, imp_t in _GOOD_TO_PRODUCER.items():
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


# Primary good produced by each tile imp. Drives the need signal (local
# price ratio) and the intra-call saturation decay. FARM and FISHERY share
# "grain" deliberately: both feed the same market, so building one should
# dampen desire for the other in the same call.
_IMP_TO_GOOD = {
    IMP.FARM:    "grain",
    IMP.FISHERY: "grain",
    IMP.COTTON:  "fabric",
    IMP.LUMBER:  "lumber",
    IMP.MINE:    "copper_ore",
    IMP.QUARRY:  "stone",
}

# After placing N of the same good in one call, multiply the next candidate
# of that good by this factor^N. 0.5 is aggressive enough that the loop
# flips to a different scarce good after ~1-2 builds.
_SATURATION_DECAY = 0.5

# Score multiplier when a candidate matches the civ's current build_type.
# Kept moderate so a genuinely scarce good can still beat a mismatched goal.
_GOAL_PREFERENCE_BONUS = 1.4

# Clamp for the need multiplier — ratio floor prevents negative feedback
# from collapsing a glutted good's score to zero (we may still want to
# replace a unstaffed tile), ceiling keeps a single price spike from
# overwhelming profitability entirely.
_NEED_MIN = 0.3
_NEED_MAX = 3.5


def _need_multiplier(city: City, good: str | None) -> float:
    """How badly the city wants more of ``good``, as a score multiplier.

    Uses the local price ratio (``city.prices[good] / BASE_PRICES[good]``)
    directly — scarce goods trade above base, glutted goods below. A ratio
    of 1.0 is neutral; 2.0 doubles the tile's score, 0.5 halves it.
    """
    if not good:
        return 1.0
    base = BASE_PRICES.get(good, 1.0)
    price = city.prices.get(good, base)
    ratio = price / max(base, 0.01)
    return max(_NEED_MIN, min(_NEED_MAX, ratio))


# Max tile-level upgrades the development loop will allow stacked on a single
# tile in one call. Stops a greedy city from taking a farm L1 → L49 in one
# invest tick just because it has the gold; forces growth to spread across
# tiles and types. Across multiple ticks the tile can still climb to max.
_MAX_UPGRADES_PER_TILE_PER_CALL = 5

# Tolerance for unstaffed levels on a tile before we refuse to upgrade it.
# Ensures we fill existing capacity before piling on more.
_MAX_UNSTAFFED_FOR_UPGRADE = 1


_BUILD_CANDS = (IMP.FARM, IMP.COTTON, IMP.FISHERY, IMP.LUMBER, IMP.MINE, IMP.QUARRY)


# ── Focus HMM transitions ──────────────────────────────────────────────────

def _focus_transition(
    city: City, civ_is_at_war: bool, *, rand: Callable[[], float] = random.random,
) -> None:
    f = city.focus
    pop = max(1.0, city.population)
    
    # Use supply dict for signals
    grain = city.supply.get("grain", 0.0) + city.supply.get("bread", 0.0)
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
    payback = CAPEX_PAYBACK_TICKS * _slack_payback_mult(city)

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
        for cand in (IMP.FARM, IMP.COTTON, IMP.FISHERY, IMP.LUMBER, IMP.MINE, IMP.QUARRY):
            if cand == cur_type:
                continue
            p = _tile_profit(city, cell, cand, ter, res, rivers, good_efficiency)
            if p > best_alt_profit:
                best_alt_profit = p
                best_alt_type = cand

        if best_alt_type is None or best_alt_profit <= 0.0:
            continue

        rebuild_cost = _upgrade_cost(city, best_alt_type, 0) * 1.6
        gain = best_alt_profit - keep_value - (rebuild_cost / payback)
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

def _pick_build_type(city: City, goal_imp: Optional[int], impr: list) -> int:
    """Decide what this city should work on this tick.

    Priority order:
      1. Profitability bias (same 70/30 as before) picks the most profitable
         producer when one is meaningfully above base price.
      2. Otherwise the civ's current goal.
      3. Fallback: a farm (cheap, always useful when you're starving).
    """
    profitable = _most_profitable_imp(city)
    if profitable is not None and random.random() < _PROFIT_BIAS:
        return profitable
    if goal_imp is not None:
        return goal_imp
    return profitable if profitable is not None else IMP.FARM


def _develop_tiles(
    city: City, civ: Civ, build_type: int, impr: list,
    ter: list, res: dict, rivers: dict, om: list,
    *, good_efficiency: dict | None = None,
) -> bool:
    """Greedy unified development loop.

    Prices/efficiencies/terrain are stable during one call, so we precompute
    the (profit, good, kind) tuple per (tile, candidate) once and re-score
    cheaply each iteration — dropping the hot ``_tile_profit`` path from
    O(K × T × 6) to O(T × 6) + O(K × T).

    Each entry list mutates in place after an action:
      * build  → cell's list becomes at most one upgrade entry at lvl 1
      * upgrade → entry's lvl bumps; list drops when max_level hits

    Gates preserved: unemployment for builds, unstaffed cap for upgrades,
    per-tile upgrade cap. Saturation and need are applied at scan time.
    """
    remaining_unemployed = int(getattr(city, "unemployed_pop", 0))
    built_by_good: dict[str, int] = {}
    upgrades_this_call: dict[int, int] = {}
    any_action = False
    staffing = getattr(city, "staffing", None) or {}
    payback = CAPEX_PAYBACK_TICKS * _slack_payback_mult(city)

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

    # Cache need multipliers per good — prices stable during call.
    need_cache: dict[Optional[str], float] = {}

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
                    base = profit - capex / payback
                else:
                    if tile_up_count >= _MAX_UPGRADES_PER_TILE_PER_CALL:
                        continue
                    unstaffed = lvl - staffed
                    if unstaffed > _MAX_UNSTAFFED_FOR_UPGRADE:
                        continue
                    if remaining_unemployed < N_EMPLOYEES_PER_LEVEL and unstaffed >= 1:
                        continue
                    cost = _upgrade_cost(city, imp_t, lvl)
                    base = profit - cost / payback
                if base <= 0:
                    continue

                if good in need_cache:
                    need = need_cache[good]
                else:
                    need = _need_multiplier(city, good)
                    need_cache[good] = need
                sat = _SATURATION_DECAY ** built_by_good.get(good, 0) if good else 1.0
                goal = _GOAL_PREFERENCE_BONUS if build_type == imp_t else 1.0
                score = base * need * sat * goal
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
    """Run one investment action for this city.

    The unified ``_develop_tiles`` loop handles builds + upgrades under a
    single market-weighted score. Falls through to fort placement (which
    has its own border-biased logic) when the goal is FORT, and to a tile
    rebuild if nothing else scored positive.
    """
    if build_type is None:
        build_type = _pick_build_type(city, goal_imp, impr)

    if _develop_tiles(city, civ, build_type, impr, ter, res, rivers, om,
                      good_efficiency=good_efficiency):
        return True

    if build_type == IMP.FORT and civ is not None:
        if _place_fort(city, civ, ter, impr, set(city.tiles), set(), om or []):
            return True

    return _rebuild_improvement(city, ter, res, rivers, impr, good_efficiency)


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
        "FARM":     IMP.FARM,
        "MINE":     IMP.MINE,
        "LUMBER":   IMP.LUMBER,
        "QUARRY":   IMP.QUARRY,
        "FORT":     IMP.FORT,
    }

    if civ.goal_index >= len(civ.goal_queue):
        civ.goal_index = 0
    current_goal = civ.goal_queue[civ.goal_index]

    # Goal expiration — nothing fulfilled in 300 ticks, move on.
    if civ.goal_ticks > 300:
        civ.goal_index = (civ.goal_index + 1) % len(civ.goal_queue)
        civ.goal_ticks = 0
        return

    # FOUND goals are handled in simulation.py — but unlike before we still
    # let cities build production. The old code froze the whole civ, which
    # left new cities stuck at 40 pop with no buildings while the civ saved
    # for another settle. Cities still need farms to grow.
    goal_imp = goal_map.get(current_goal)  # None for FOUND

    city_count = len(civ.cities)

    # Rotate city-development work across ticks so the per-tick cost scales
    # sublinearly with city count.
    stride = 1
    if city_count >= 240:
        stride = 4
    elif city_count >= 160:
        stride = 3
    elif city_count >= 96:
        stride = 2

    if stride > 1:
        cursor = getattr(civ, "_invest_cursor", 0) % stride
        city_iter = civ.cities[cursor::stride]
        civ._invest_cursor = (cursor + 1) % stride
    else:
        city_iter = civ.cities

    any_built = False
    changed_cities: dict[int, City] = {}
    for city in city_iter:
        city_build_type = _pick_build_type(city, goal_imp, impr)
        if _try_one_action(
            city, civ, goal_imp, impr, ter, res, rivers, om,
            build_type=city_build_type, good_efficiency=good_efficiency,
        ):
            any_built = True
            changed_cities[city.cell] = city

        # City-center buildings (factories etc.) invest off market signals.
        # Separate from tile improvements so we can grow urban industry.
        if _try_build_city_buildings(city):
            any_built = True
            changed_cities[city.cell] = city

    # Re-run employment so workers move into buildings placed this tick.
    # Without this, new cells wait a whole tick before producing.
    for city in changed_cities.values():
        employment.update_city_employment(city, impr)

    # Advance the civ goal when SOMEONE built this tick (keeps the queue
    # from stalling but doesn't gate per-city work on it).
    if any_built and goal_imp is not None:
        civ.goal_index = (civ.goal_index + 1) % len(civ.goal_queue)
        civ.goal_ticks = 0
