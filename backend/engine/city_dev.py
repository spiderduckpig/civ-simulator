"""Per-city investment, focus HMM, and improvement placement."""

from __future__ import annotations

import random
from typing import Callable, Optional

from .constants import (
    N, IMP, FOCUS, T,
    INVEST_COST_BASE_FRAC, INVEST_COST_LEVEL_POW, INVEST_MIN_WEALTH_FLOOR,
    INVEST_MAX_PER_TICK, INVEST_PERIOD_TICKS, FOCUS_HMM_PERIOD,
)
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


# ── Upgrade cost (scale-free fraction of city wealth) ──────────────────────

def _upgrade_cost(city: City, current_level: int) -> float:
    # Fraction of city wealth, not a flat gold cost — stays scale-free.
    base_wealth = max(city.wealth, INVEST_MIN_WEALTH_FLOOR)
    level_mult = max(1.0, current_level) ** INVEST_COST_LEVEL_POW
    return base_wealth * INVEST_COST_BASE_FRAC * level_mult


def _try_debit(city: City, cost: float) -> bool:
    if cost <= 0:
        return True
    if city.wealth >= cost:
        city.wealth -= cost
        return True
    return False


# ── Tile picking heuristics ────────────────────────────────────────────────

def _pick_upgrade_candidate(
    city: City, impr: list, *, preferred_types: set,
) -> Optional[int]:
    # Lowest-level upgradable tile, preferring the focus's types.
    tiles = city.tiles
    if not tiles:
        return None

    best_pref: Optional[tuple[int, int]] = None  # (level, cell)
    best_any:  Optional[tuple[int, int]] = None

    for cell in tiles:
        if not (0 <= cell < N):
            continue
        raw = impr[cell]
        if not raw:
            continue
        it  = imp_type(raw)
        lvl = imp_level(raw)
        if it not in UPGRADABLE_TYPES:
            continue
        if lvl >= max_level(it):
            continue
        key = (lvl, cell)
        if it in preferred_types:
            if best_pref is None or key < best_pref:
                best_pref = key
        else:
            if best_any is None or key < best_any:
                best_any = key

    if best_pref is not None:
        return best_pref[1]
    if best_any is not None:
        return best_any[1]
    return None


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
    food = city.food_production
    ore  = getattr(city, "city_ore", 0.0)
    stone = getattr(city, "city_stone", 0.0)
    trade = getattr(city, "trade_potential", 0.0)
    coastal = city.coastal

    # Scale-free evidence signals (all fractions of population, not raw numbers)
    food_deficit = food < pop * 0.3
    food_surplus = food > pop * 0.6
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

    # Look at up to 8 random empty tiles; pick the first one a placer likes.
    empties = [c for c in tiles if 0 <= c < N and impr[c] == IMP.NONE]
    if not empties:
        return False
    random.shuffle(empties)

    focus = city.focus
    for cell in empties[:8]:
        pick = best_improvement(ter, res, cell, rivers, focus, rand=rand)
        if pick == IMP.NONE:
            continue
        # Cost: one-level-1 build is a small fraction of city wealth
        cost = max(INVEST_MIN_WEALTH_FLOOR * 0.6,
                   city.wealth * INVEST_COST_BASE_FRAC * 0.5)
        if not _try_debit(city, cost):
            return False
        impr[cell] = make_imp(pick, 1)
        return True
    return False


def _place_advanced_structure(
    city: City, ter: list, impr: list,
    *, rand: Callable[[], float] = random.random,
) -> bool:
    """Try to place an advanced structure (port / fishery / windmill /
    smithery) on one of the city's empty tiles.

    Adjacent structures get more benefit to the city by being near existing
    improvements. Costs are scaled vs. city wealth, same as upgrades.
    """
    tiles = city.tiles
    if not tiles:
        return False

    empties = [c for c in tiles if 0 <= c < N and impr[c] == IMP.NONE]
    if not empties:
        return False
    random.shuffle(empties)

    focus = city.focus
    for cell in empties[:12]:
        pick = advanced_structure_for(cell, ter, impr, focus, rand=rand)
        if pick == IMP.NONE:
            continue
        cost = max(INVEST_MIN_WEALTH_FLOOR,
                   city.wealth * INVEST_COST_BASE_FRAC * 0.8)
        if not _try_debit(city, cost):
            return False
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

    empties.sort(key=border_score)
    # Cheap-ish: forts are strategic so they cost a bit more than a farm.
    cost = max(INVEST_MIN_WEALTH_FLOOR * 1.2,
               city.wealth * INVEST_COST_BASE_FRAC * 1.0)
    if not _try_debit(city, cost):
        return False

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
        cost = max(INVEST_MIN_WEALTH_FLOOR * 2.0,
                   city.wealth * INVEST_COST_BASE_FRAC * 2.5)
        if not _try_debit(city, cost):
            return False
        impr[cell] = make_imp(pick, 1)
        return True
    return False


# ── Public entry: per-civ per-tick city development ───────────────────────

def tick_city_development(
    civ: Civ, wars: dict, ter: list, res: dict, rivers: dict, impr: list,
    tick: int, om: list | None = None,
) -> None:
    """Update one civ's cities: focus HMM, investment, placement.

    Called from the main simulation loop once per civ per tick (after
    per-city production has been computed). Per-city state is expected to
    include: ``tiles``, ``wealth``, ``focus``, ``food_production``,
    ``city_ore``, ``city_stone``, ``trade_potential``, ``population``,
    ``coastal``.
    """
    if not civ.cities:
        return

    civ_is_at_war = any(
        w.att == civ.id or w.def_id == civ.id for w in wars.values()
    )

    # ── Focus transitions (infrequent) ───────────────────────────────────
    if tick % FOCUS_HMM_PERIOD == 0:
        for city in civ.cities:
            _focus_transition(city, civ_is_at_war)

    # ── Investment loop (per-city budget) ────────────────────────────────
    if tick % INVEST_PERIOD_TICKS != 0:
        return

    # Precompute fort-placement context once per civ per tick.
    territory_set = set(civ.territory)
    enemy_ids: set = set()
    for w in wars.values():
        if w.att == civ.id:
            enemy_ids.add(w.def_id)
        elif w.def_id == civ.id:
            enemy_ids.add(w.att)

    # Does the civ already have any fort in its territory? (for the "always
    # at least one" guarantee when the starter fort has fallen into ruin.)
    has_any_fort = any(
        imp_type(impr[c]) == IMP.FORT
        for c in territory_set
        if 0 <= c < N
    )

    for city in civ.cities:
        focus = city.focus
        pref = _focus_preferred_types(focus)
        upgrades_done = 0
        # Each city gets up to INVEST_MAX_PER_TICK upgrade attempts per tick.
        # We loop a few times so if a cheap upgrade succeeds we can consider
        # another one in the same tick (bounded).
        for _ in range(INVEST_MAX_PER_TICK):
            cell = _pick_upgrade_candidate(city, impr, preferred_types=pref)
            if cell is None:
                break
            raw = impr[cell]
            lvl = imp_level(raw)
            cost = _upgrade_cost(city, lvl)
            if not _try_debit(city, cost):
                break
            impr[cell] = upgrade_imp(raw)
            upgrades_done += 1

        # ── Fort placement ───────────────────────────────────────────────
        # Cities will build a brand new fort on an empty border tile when:
        #   - the civ is at war, OR
        #   - this city's focus is DEFENSE, OR
        #   - the civ has zero forts anywhere (safety net).
        # Counted per-city: don't spam forts, only one attempt per tick.
        want_fort = (
            civ_is_at_war
            or focus == FOCUS.DEFENSE
            or not has_any_fort
        )
        if want_fort and om is not None:
            # Roughly cap fort density: one fort per ~4 worked tiles in this city.
            city_tiles = city.tiles
            fort_here = sum(
                1 for t in city_tiles
                if 0 <= t < N and imp_type(impr[t]) == IMP.FORT
            )
            allowed_forts = max(1, len(city_tiles) // 4)
            if fort_here < allowed_forts and random.random() < 0.35:
                if _place_fort(city, civ, ter, impr, territory_set, enemy_ids, om):
                    has_any_fort = True
                    continue  # counted as this tick's build — skip normal placement

        # Also: if the city couldn't find an upgrade, try to BUILD something
        # new on an empty tile. This matters for young cities whose tiles
        # start with nothing on them.
        if upgrades_done == 0:
            placed = _place_new_improvement(city, ter, res, rivers, impr)
            if not placed:
                # Finally, try an advanced structure (port/fishery/…)
                _place_advanced_structure(city, ter, impr)

        # ── Rare: rebuild a different improvement on an occupied tile ────
        # Very low per-city chance. Only fires when upgrades aren't hogging
        # the wealth budget so we don't fight with normal investment.
        if random.random() < REBUILD_CHANCE:
            _rebuild_improvement(city, ter, res, rivers, impr)
