"""Per-city investment, focus HMM, and improvement placement."""

from __future__ import annotations

import random
from typing import Callable, Optional

from .constants import (
    N, IMP, FOCUS, T,
    INVEST_MAX_PER_TICK, INVEST_PERIOD_TICKS, FOCUS_HMM_PERIOD,
    FORT_BUILD_METAL_COST, BASE_PRICES, N_EMPLOYEES_PER_LEVEL,
)
from . import employment
from .employment import STAFFABLE_TYPES as _STAFFABLE
from .improvements import (
    imp_type, imp_level, upgrade_imp, make_imp,
    max_level, UPGRADABLE_TYPES,
    best_improvement, advanced_structure_for,
)
from .helpers import neighbors
from .models import City, Civ


# Terrain types a fort is allowed to sit on.
_FORT_TERRAIN = (T.PLAINS, T.GRASS, T.FOREST, T.HILLS)

# Rare-rebuild probability per eligible city per investment tick.
REBUILD_CHANCE = 0.004


# ── Upgrade cost (Gold only, but scaled by material prices) ──────────────────

def _upgrade_cost(city: City, it: int, current_level: int) -> float:
    # Base gold cost scales with level. New builds and the first upgrade
    # (current_level 0 or 1) stay at baseline; the ramp is intentionally steep
    # so high-level buildings dominate a city's gold budget.
    level_mult = max(1.0, current_level) ** 1.8
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


# ── Profitability hint ────────────────────────────────────────────────────
# Producer imp for each tradable good. Used to bias "what to build next"
# toward whichever output is currently scarce (high local price).
_GOOD_TO_PRODUCER = {
    "food":   IMP.FARM,
    "lumber": IMP.LUMBER,
    "ore":    IMP.MINE,
    "stone":  IMP.QUARRY,
    "metal":  IMP.SMITHERY,
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
        return {IMP.FARM, IMP.WINDMILL, IMP.FISHERY}
    if focus == FOCUS.MINING:
        return {IMP.MINE, IMP.QUARRY, IMP.SMITHERY}
    if focus == FOCUS.DEFENSE:
        return {IMP.FORT, IMP.FARM}
    if focus == FOCUS.TRADE:
        return {IMP.PORT, IMP.FISHERY, IMP.FARM}
    return {IMP.FARM}


# ── Focus HMM transitions ──────────────────────────────────────────────────

def _focus_transition(
    city: City, civ_is_at_war: bool, *, rand: Callable[[], float] = random.random,
) -> None:
    f = city.focus
    pop = max(1.0, city.population)
    
    # Use supply dict for signals
    food = city.supply.get("food", 0.0)
    ore  = city.supply.get("ore", 0.0)
    stone = city.supply.get("stone", 0.0)
    
    # Trade potential is now more abstract but we can use gold income or 
    # specific trade-related stocks if needed. For now, use a baseline.
    trade = city.gold / 50.0 
    coastal = city.coastal

    # Scale-free evidence signals (all fractions of population, not raw numbers)
    food_deficit = food < pop * 0.12
    food_surplus = food > pop * 0.25
    ore_rich     = (ore + stone) > pop * 0.05
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

    # Smitheries are only useful if the city has ore coming in.
    has_ore_potential = any(
        0 <= t < N and (
            imp_type(impr[t]) == IMP.MINE or
            (res.get(t) == "iron")
        )
        for t in tiles
    )
    # Check current ore availability too
    current_production_ore = city.supply.get("ore", 0.0)
    current_imports_ore = city.net_imports.get("ore", 0.0)
    has_ore = current_production_ore > 0.1 or current_imports_ore > 0.1 or has_ore_potential

    focus = city.focus
    # Increase search depth
    for cell in empties[:20]:
        pick = advanced_structure_for(cell, ter, impr, focus, rand=rand)
        if pick == IMP.NONE:
            continue
        if pick == IMP.SMITHERY and not has_ore:
            continue
        it = pick
        costs = _upgrade_cost(city, it, 0)
        if not _try_buy(city, costs):
            continue
        impr[cell] = make_imp(pick, 1)
        return True
    return False


# ── Fort placement (border-biased) ─────────────────────────────────────────

def _place_fort(
    city: City, civ: Civ, ter: list, impr: list, territory_set: set,
    enemy_ids: set, om: list,
) -> bool:
    """Try to place a new IMP.FORT on one of the city's walkable empty tiles.

    Prefers tiles closest to an enemy border. Returns True on success.
    Gated by the caller — this function assumes the civ wants a fort.
    """
    tiles = city.tiles
    if not tiles:
        return False

    # Empty walkable candidates within the city's worked tiles.
    empties = [
        c for c in tiles
        if 0 <= c < N and impr[c] == IMP.NONE and ter[c] in _FORT_TERRAIN
    ]
    if not empties:
        return False

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

    # Metal gate: building a fort consumes raw metal from the civ stockpile.
    # No metal → no new fort (upkeep still draws from the same pool).
    if getattr(civ, "metal_stock", 0.0) < FORT_BUILD_METAL_COST:
        return False

    empties.sort(key=border_score)
    # Cheap-ish: forts are strategic so they cost a bit more than a farm.
    it = IMP.FORT
    costs = _upgrade_cost(city, it, 0)
    if not _try_buy(city, costs):
        return False

    civ.metal_stock -= FORT_BUILD_METAL_COST
    impr[empties[0]] = make_imp(IMP.FORT, 1)
    return True


# ── Rare: rebuild a different improvement on an occupied tile ──────────────

def _rebuild_improvement(
    city: City, ter: list, res: dict, rivers: dict, impr: list,
    *, rand: Callable[[], float] = random.random,
) -> bool:
    """Replace one existing improvement in this city with a freshly-picked
    one. Intentionally rare and expensive — cities don't normally bulldoze.

    Prefers tiles whose current type doesn't match the city's focus.
    """
    tiles = city.tiles
    if not tiles:
        return False

    focus = city.focus
    pref  = _focus_preferred_types(focus)

    # Candidates: occupied tiles whose current improvement is NOT in the
    # focus-preferred set. That way you don't bulldoze a level-5 mine to
    # build another mine.
    mismatches = []
    for cell in tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        if not raw:
            continue
        if imp_type(raw) in pref:
            continue
        mismatches.append(cell)

    if not mismatches:
        return False
    random.shuffle(mismatches)

    for cell in mismatches[:4]:
        pick = best_improvement(ter, res, cell, rivers, focus, rand=rand)
        if pick == IMP.NONE or pick == imp_type(impr[cell]):
            continue
        # Rebuild is expensive: roughly 3× a normal placement.
        cost = _upgrade_cost(city, pick, 0) * 3.0
        if not _try_buy(city, cost):
            return False
        impr[cell] = make_imp(pick, 1)
        return True
    return False


# ── Public entry: per-civ per-tick city development ───────────────────────

def _try_upgrade(city: City, build_type: int, impr: list) -> bool:
    """Upgrade the lowest-level instance of ``build_type`` in this city."""
    best_cell = -1
    best_lvl = max_level(build_type)
    for cell in city.tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        if imp_type(raw) != build_type:
            continue
        lvl = imp_level(raw)
        if lvl >= max_level(build_type):
            continue
        if lvl < best_lvl:
            best_lvl = lvl
            best_cell = cell
    if best_cell < 0:
        return False
    cost = _upgrade_cost(city, build_type, best_lvl)
    if not _try_buy(city, cost):
        return False
    impr[best_cell] = upgrade_imp(impr[best_cell])
    return True


def _try_build_new(city: City, build_type: int, impr: list) -> bool:
    """Place a fresh improvement of ``build_type`` on an empty tile."""
    empties = [c for c in city.tiles if 0 <= c < N and impr[c] == IMP.NONE]
    if not empties:
        return False
    random.shuffle(empties)
    for cell in empties[:15]:
        cost = _upgrade_cost(city, build_type, 0)
        if not _try_buy(city, cost):
            return False  # can't afford — no point trying more cells
        impr[cell] = make_imp(build_type, 1)
        return True
    return False


def _city_has_no_buildings(city: City, impr: list) -> bool:
    for c in city.tiles:
        if 0 <= c < N and impr[c] != IMP.NONE:
            return False
    return True


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


def _unstaffed_levels(city: City, impr: list) -> int:
    """Total staffable building levels not currently filled. A city with
    plenty of empty capacity shouldn't keep building new structures — it
    should upgrade existing ones or just save gold until population grows.
    """
    total = 0
    staffing = city.staffing
    for cell in city.tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        it = imp_type(raw)
        if it not in _STAFFABLE:
            continue
        total += imp_level(raw) - staffing.get(cell, 0)
    return total


def _try_one_action(
    city: City, goal_imp: Optional[int], impr: list,
) -> bool:
    """Attempt a single build-or-upgrade action for this city. Biased to
    build new capacity when the city has unemployed workers (they need
    somewhere to work).
    """
    build_type = _pick_build_type(city, goal_imp, impr)
    unemployed = getattr(city, "unemployed_pop", 0)
    # Don't stack unstaffed capacity: if there's already room for a full
    # employment level sitting idle, upgrading is the only sensible move.
    idle_capacity = _unstaffed_levels(city, impr)
    excess_capacity = idle_capacity >= 2

    # Unemployment → prefer new building. No unemployment → prefer upgrade.
    if unemployed >= N_EMPLOYEES_PER_LEVEL and not excess_capacity:
        if _try_build_new(city, build_type, impr):
            return True
        return _try_upgrade(city, build_type, impr)
    else:
        if _try_upgrade(city, build_type, impr):
            return True
        if excess_capacity:
            return False  # don't sprawl more unstaffed buildings
        return _try_build_new(city, build_type, impr)


def tick_city_development(
    civ: Civ, wars: dict, ter: list, res: dict, rivers: dict, impr: list,
    tick: int, om: Optional[list] = None,
) -> None:
    """Per-tick building decisions for every city in the civ.

    Each city attempts up to ``INVEST_MAX_PER_TICK`` build/upgrade actions,
    independently, so non-capital cities actually develop. The civ-level
    goal queue still biases *what* to build (via ``_pick_build_type``) but
    no longer gates *who* gets to build: the rule used to be "one city per
    goal-advance," which left everything but the capital empty.
    """
    if not civ.cities:
        return

    civ.goal_ticks += 1

    goal_map = {
        "FARM":     IMP.FARM,
        "MINE":     IMP.MINE,
        "LUMBER":   IMP.LUMBER,
        "QUARRY":   IMP.QUARRY,
        "SMITHERY": IMP.SMITHERY,
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

    any_built = False
    for city in civ.cities:
        # Struggling cities (no buildings at all) get extra tries so they
        # never sit at the starvation floor forever.
        budget = INVEST_MAX_PER_TICK
        if _city_has_no_buildings(city, impr):
            budget += 2
        for _ in range(budget):
            if not _try_one_action(city, goal_imp, impr):
                break
            any_built = True

    # Re-run employment so workers move into buildings placed this tick.
    # Without this, new cells wait a whole tick before producing.
    for city in civ.cities:
        employment.update_city_employment(city, impr)

    # Advance the civ goal when SOMEONE built this tick (keeps the queue
    # from stalling but doesn't gate per-city work on it).
    if any_built and goal_imp is not None:
        civ.goal_index = (civ.goal_index + 1) % len(civ.goal_queue)
        civ.goal_ticks = 0
