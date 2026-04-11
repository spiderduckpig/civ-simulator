"""Improvement encoding, metadata, and placement heuristics.

An improvement is stored in the `impr` grid as a single nonnegative int.
The low IMP_TYPE_BITS bits hold the type (0..31); the remaining bits hold
the level minus one. All callers should go through the helpers in this
module instead of hand-rolling bit twiddles.

Placement heuristics (best_improvement) live here too, parameterised by
the current city focus. Keeping the "what should I build on tile X"
decision beside the encoding makes it easy to add new improvement types.
"""

from .constants import (
    T, IMP, FOCUS, CAN_FARM,
    IMP_TYPE_BITS, IMP_TYPE_MASK, IMP_LEVEL_STEP,
)
from .helpers import neighbors, cell_on_river


# ── Encoding helpers ────────────────────────────────────────────────────────

def imp_type(raw: int) -> int:
    """Extract improvement type id from packed int."""
    return raw & IMP_TYPE_MASK


def imp_level(raw: int) -> int:
    """Extract 1-indexed level from packed int."""
    return (raw >> IMP_TYPE_BITS) + 1


def make_imp(type_: int, level: int = 1) -> int:
    """Pack an improvement type + level into a raw int."""
    return type_ | ((max(level, 1) - 1) << IMP_TYPE_BITS)


def upgrade_imp(raw: int) -> int:
    """Raise the level by 1 — caller is responsible for the max-level check."""
    return raw + IMP_LEVEL_STEP


def downgrade_imp(raw: int) -> int:
    """Drop one level. If already at level 1, returns IMP.NONE."""
    if imp_level(raw) <= 1:
        return IMP.NONE
    return raw - IMP_LEVEL_STEP


# ── Type metadata ───────────────────────────────────────────────────────────
# Maximum level each type can reach. Farms are the main driver of food so
# they scale further than other types.
MAX_LEVELS = {
    IMP.FARM:     20,
    IMP.MINE:     5,
    IMP.QUARRY:   5,
    IMP.LUMBER:   5,
    IMP.PASTURE:  5,
    IMP.WINDMILL: 5,
    IMP.FORT:     5,
    IMP.PORT:     5,
    IMP.SMITHERY: 5,
    IMP.FISHERY:  5,
}

# Types the per-city investment loop is allowed to upgrade. Lumber and
# pasture are single-level flavour improvements right now.
UPGRADABLE_TYPES = {
    IMP.FARM, IMP.MINE, IMP.QUARRY, IMP.WINDMILL,
    IMP.FORT, IMP.PORT, IMP.SMITHERY, IMP.FISHERY,
}


def max_level(type_: int) -> int:
    return MAX_LEVELS.get(type_, 1)


# ── Placement heuristic ─────────────────────────────────────────────────────
# best_improvement returns the improvement type that fits a given cell
# under a given focus, or IMP.NONE if nothing sensible fits. The chooser
# is probabilistic (HMM-like) so two cities with the same focus won't
# always make the same choice on the same terrain.

def _coastal(cell: int, ter: list) -> bool:
    for n in neighbors(cell):
        if 0 <= n < len(ter) and ter[n] <= T.COAST:
            return True
    return False


def best_improvement(
    ter: list, res: dict, cell: int, rivers: dict,
    focus: int = FOCUS.FARMING,
    *, rand=None,
) -> int:
    """Pick the improvement TYPE to build on `cell` given terrain/resources/focus.

    rand is an optional callable returning a float in [0, 1). If None a
    deterministic fallback runs (used by tests).
    """
    import random as _r
    if rand is None:
        rand = _r.random

    t = ter[cell]
    r = res.get(cell)
    on_river = cell_on_river(cell, rivers)

    # ── Hard terrain rules first ────────────────────────────────────────
    if t in (T.MTN, T.HILLS):
        return IMP.MINE
    if t in (T.FOREST, T.DFOREST, T.JUNGLE):
        return IMP.LUMBER
    if r == "horses":
        return IMP.PASTURE

    coastal = _coastal(cell, ter)

    # ── Probabilistic chooser weighted by focus ─────────────────────────
    # The weights form a categorical distribution; focus nudges the mass
    # toward this focus's preferred improvements. No single focus gets a
    # 100% lock on any improvement type — this is explicitly HMM-ish.
    weights: dict[int, float] = {}

    # Fisheries sit on coastal tiles only. They are very attractive to
    # FARMING (food) and TRADE (bonus trade) focuses.
    if coastal:
        fw = 1.0
        if focus == FOCUS.FARMING:
            fw = 3.0
        elif focus == FOCUS.TRADE:
            fw = 2.0
        weights[IMP.FISHERY] = fw

    # Farms — wherever food can grow
    farmable = t in CAN_FARM or (
        on_river and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT)
    )
    if farmable:
        fw = 2.0
        if focus == FOCUS.FARMING: fw = 4.0
        elif focus == FOCUS.MINING: fw = 0.5
        elif focus == FOCUS.TRADE:  fw = 1.0
        weights[IMP.FARM] = fw

    # Mines where resources justify it
    if r in ("iron", "stone", "gold", "gems"):
        mw = 2.0
        if focus == FOCUS.MINING: mw = 4.0
        weights[IMP.MINE] = mw

    # Neighbours on a river can also farm
    if not weights and not farmable:
        for n in neighbors(cell):
            if cell_on_river(n, rivers) and t not in (T.DEEP, T.OCEAN, T.COAST, T.MTN, T.SNOW, T.BEACH, T.TUNDRA, T.DESERT):
                weights[IMP.FARM] = 1.5
                break

    if not weights:
        return IMP.NONE

    total = sum(weights.values())
    roll = rand() * total
    running = 0.0
    for k, v in weights.items():
        running += v
        if roll <= running:
            return k
    return next(iter(weights))


# ── Advanced structure (port / fishery / windmill / smithery) chooser ───────
# Advanced structures occupy empty tiles and are placed by a civ-level loop.
# Each focus has a different preference over the advanced set.

def advanced_structure_for(
    cell: int, ter: list, impr: list, focus: int,
    *, rand=None,
) -> int:
    """Pick an 'advanced' structure (port/fishery/windmill/smithery) for an
    empty cell, or IMP.NONE if none fit. Focus biases the distribution but
    never forces a single deterministic answer."""
    import random as _r
    if rand is None:
        rand = _r.random

    from .mapgen import cell_coastal  # local import to avoid cycles

    coastal = cell_coastal(cell, ter)
    nearby_farms = 0
    for n in neighbors(cell):
        if 0 <= n < len(impr) and imp_type(impr[n]) == IMP.FARM:
            nearby_farms += 1

    weights: dict[int, float] = {}

    if coastal:
        # Trade focus LOVES ports, farming focus leans fishery but still
        # sometimes builds ports.
        if focus == FOCUS.TRADE:
            weights[IMP.PORT] = 4.0
            weights[IMP.FISHERY] = 1.5
        elif focus == FOCUS.FARMING:
            weights[IMP.PORT] = 1.0
            weights[IMP.FISHERY] = 2.0
        else:
            weights[IMP.PORT] = 1.5
            weights[IMP.FISHERY] = 1.2

    if nearby_farms >= 3:
        # Windmill cost/benefit is best when it can buff many neighbours.
        ww = nearby_farms * 0.6
        if focus == FOCUS.FARMING: ww *= 1.8
        weights[IMP.WINDMILL] = ww

    if focus == FOCUS.MINING:
        # Smitheries convert ore → metal. Always an option under mining focus.
        weights[IMP.SMITHERY] = 2.0

    if not weights:
        return IMP.NONE

    total = sum(weights.values())
    roll = rand() * total
    running = 0.0
    for k, v in weights.items():
        running += v
        if roll <= running:
            return k
    return next(iter(weights))
