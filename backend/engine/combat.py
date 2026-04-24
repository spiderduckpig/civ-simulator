"""Army subsystem: spawning, behavior selection, pathfinding, combat, and
fortification bonuses.

All army-related state lives in war dicts under ``armies_a`` / ``armies_d``.
Each army is a dict with these fields:

    id             int       unique
    civ_id         int       owning civ
    war_key        str
    cell           int       current cell
    origin_cell    int       the fort/city it was spawned from
    fort_level     int       strength multiplier when spawned (1..5)
    strength       float     current raw strength (dies at 0)
    max_strength   float
    organization   float     0..100 — combat morale
    supply         float     0..100
    commander      {name, skill}
    behavior       str       one of BEHAVIORS
    objective      dict      {type, target_cell, target_id}
    fortification  float     defensive multiplier (refreshed each tick)
    fort_source    str       human label: "open", "fort Lv.2", "capital", ...

Public entry points:
    spawn_war_armies(civ, war, side, impr, war_key)
    tick_armies(civs, wars, ter, impr, om, tick, add_event)

Everything else is private (prefixed with `_`).
"""

from __future__ import annotations

import random
from typing import Callable, List, Optional, Set

from .constants import (
    W, H, N, T, IMP,
    ARMY_BASE_STRENGTH, ARMY_FORT_MULT, ARMY_MOVE_RANGE,
    ARMY_SUPPLY_FREE_DIST, ARMY_SUPPLY_DECAY, ARMY_SUPPLY_REPLEN,
    ARMY_COMBAT_RANGE, ARMY_COMBAT_DAMAGE, ARMY_CITY_DAMAGE,
    ARMY_ENGAGE_RANGE, ARMY_TARGET_CITY_RANGE, ARMY_RESPAWN_DELAY,
    ARMY_PATHFIND_BUDGET,
    ARMY_BROKEN_ORG, ARMY_RECOVER_ORG, ARMY_FRONT_DIST,
    FORT_BONUS_PER_LEVEL, CITY_DEFENSE_BONUS, CAPITAL_DEFENSE_BONUS,
    FRIENDLY_TERRAIN_BONUS,
    CITY_BASE_HP, CAPITAL_HP_BONUS, FORT_HP_BONUS,
)
from .helpers import neighbors, dist, land_bfs_distance_field
from .government import fort_is_active
from .improvements import imp_type, imp_level
from .civ import gen_commander_name, next_army_id
from . import diplomacy
from .models import Civ, City, Army, War, Commander, Objective


# ── Behavior states (public strings so the frontend can label them) ─────────

BEHAVIOR_DEFEND_FORT      = "defend_fort"
BEHAVIOR_DEFEND_TERRITORY = "defend_territory"
BEHAVIOR_ATTACK_ARMY      = "attack_army"
BEHAVIOR_ATTACK_CITY      = "attack_city"
BEHAVIOR_RELIEVE_CITY     = "relieve_city"
BEHAVIOR_RETREAT          = "retreating"

BEHAVIORS = (
    BEHAVIOR_DEFEND_FORT,
    BEHAVIOR_DEFEND_TERRITORY,
    BEHAVIOR_ATTACK_ARMY,
    BEHAVIOR_ATTACK_CITY,
    BEHAVIOR_RELIEVE_CITY,
    BEHAVIOR_RETREAT,
)


# ── City HP helpers (lazy init + max HP) ────────────────────────────────────

def city_max_hp(city: City, impr: list) -> float:
    base = CITY_BASE_HP
    if city.is_capital:
        base += CAPITAL_HP_BONUS
    raw = impr[city.cell] if 0 <= city.cell < N else 0
    if raw and imp_type(raw) == IMP.FORT:
        base += FORT_HP_BONUS * imp_level(raw)
    return base


def ensure_city_hp(city: City, impr: list) -> None:
    if city.max_hp <= 0:
        city.max_hp = city_max_hp(city, impr)
        city.hp = city.max_hp


# ── Army factory ────────────────────────────────────────────────────────────

def _make_army(civ: Civ, origin_cell: int, fort_level: int, war_id: str) -> Army:
    mult = ARMY_FORT_MULT[min(max(fort_level - 1, 0), len(ARMY_FORT_MULT) - 1)]
    max_str = ARMY_BASE_STRENGTH * mult
    return Army(
        id=next_army_id(),
        civ_id=civ.id,
        war_key=war_id,
        cell=origin_cell,
        origin_cell=origin_cell,
        fort_level=fort_level,
        max_strength=max_str,
        strength=max_str,
        organization=100.0,
        supply=100.0,
        commander=Commander(
            name=gen_commander_name(civ.onom),
            skill=round(0.75 + random.random() * 0.55, 2),
        ),
        behavior=BEHAVIOR_DEFEND_FORT,
        objective=Objective(
            type="defend", target_cell=origin_cell, target_id=None,
        ),
        fortification=0.0,
        fort_source="open field",
    )


def spawn_war_armies(civ: Civ, war: War, side: str, impr: list, war_id: str) -> None:
    """Populate war[armies_<side>] with one army per fort, or a capital
    fallback if the civ has no forts yet."""
    forts: list = []
    for cell in civ.territory:
        raw = impr[cell] if 0 <= cell < N else 0
        if imp_type(raw) == IMP.FORT and fort_is_active(civ, cell):
            forts.append((cell, imp_level(raw)))

    armies: list = []
    if forts:
        for cell, lvl in forts:
            armies.append(_make_army(civ, cell, lvl, war_id))
    else:
        cap = civ.capital if civ.capital >= 0 else next(iter(civ.territory), -1)
        if cap >= 0:
            armies.append(_make_army(civ, cap, 1, war_id))

    if side == "a":
        war.armies_a = armies
    else:
        war.armies_d = armies


# ── Fortification ───────────────────────────────────────────────────────────

def _compute_fortification(army: Army, civ: Civ, impr: list) -> tuple[float, str]:
    """Return (fortification, human label) for an army sitting on `army.cell`.

    Stacks additively:
      - Fort (own territory):  +FORT_BONUS_PER_LEVEL * level
      - Friendly city:         +CITY_DEFENSE_BONUS, +CAPITAL_DEFENSE_BONUS if capital
      - Own territory:         +FRIENDLY_TERRAIN_BONUS (base)
    """
    cur = army.cell
    if not (0 <= cur < N):
        return 0.0, "open field"

    bonus = 0.0
    labels: list[str] = []

    # Only friendly territory gives any bonus
    own_territory = civ.territory
    if cur in own_territory:
        bonus += FRIENDLY_TERRAIN_BONUS
        labels.append("home soil")

    raw = impr[cur]
    if imp_type(raw) == IMP.FORT and cur in own_territory and fort_is_active(civ, cur):
        lvl = imp_level(raw)
        bonus += FORT_BONUS_PER_LEVEL * lvl
        labels.append(f"fort Lv.{lvl}")

    # Friendly city on the same cell? Cities are addressed by cell.
    for city in civ.cities:
        if getattr(city, "cell", getattr(city, "id", -1)) == cur:
            bonus += CITY_DEFENSE_BONUS
            labels.append("city walls")
            if getattr(city, "is_capital", False):
                bonus += CAPITAL_DEFENSE_BONUS
                labels.append("capital")
            break

    if not labels:
        labels.append("open field")
    return bonus, " + ".join(labels)


def _eff_strength(army: Army) -> float:
    """Raw combat power (offense side). Fortification applies separately."""
    org = army.organization / 100.0
    cmd = army.commander.skill
    sup = 0.55 + 0.45 * min(1.0, army.supply / 50.0)
    return army.strength * org * cmd * sup


# ── Pathfinding (flow fields) ───────────────────────────────────────────────
# Per-army A* with friendlies-as-walls caused chokepoint bottlenecks: when
# many armies queued toward a single city through a narrow corridor, each
# A* saw the friendlies ahead as permanent walls and bailed, so rear armies
# just stopped. Fixed by switching to a shared flow-field:
#
#   1. For each unique walk target, build one BFS distance field outward.
#      Enemies are walls; friendlies are NOT — occupancy is resolved at
#      step-time instead. So the field stays valid for a whole moving column.
#   2. Armies heading to the same target share the field (N A*s → 1 BFS).
#   3. Sort armies by current distance-to-target ascending; closest moves
#      first. That army vacates its cell, which becomes the next army's
#      step destination — natural column-flow through chokepoints.
#   4. Each army greedy-descends: step to the unoccupied neighbour with the
#      lowest field value, up to ARMY_MOVE_RANGE. If every lower neighbour
#      is held by a friendly who hasn't moved yet, wait — priority sort
#      means they'll move before this army's next chance anyway.

def _descend_field(
    army: Army, field: dict[int, int],
    occupied: Set[int], blocked_enemy: Set[int],
    friendly_by_cell: dict[int, Army],
) -> None:
    """Greedy descent over a shared BFS distance field.

    ``field`` is the output of ``land_bfs_distance_field`` for this army's
    walk target (built with enemies as walls). ``occupied`` holds every
    live army cell in the war (friend + foe) and is mutated in place as
    the army steps — priority-sorted callers rely on this to free cells
    for armies behind them.
    """
    my_cell = army.cell
    if my_cell not in field:
        return
    steps = 0
    while steps < ARMY_MOVE_RANGE:
        cur_d = field[my_cell]
        if cur_d == 0:
            break
        best_n = -1
        best_d = cur_d
        for n in neighbors(my_cell):
            if n in occupied or n in blocked_enemy:
                continue
            d = field.get(n)
            if d is None:
                continue
            if d < best_d:
                best_d = d
                best_n = n
        if best_n >= 0:
            occupied.discard(my_cell)
            occupied.add(best_n)
            friendly_by_cell.pop(my_cell, None)
            friendly_by_cell[best_n] = army
            my_cell = best_n
            steps += 1
            continue

        # Frontline rotation: if blocked by a weaker/retreating friendly,
        # allow a one-cell swap so fresh armies can move through chokepoints.
        swap_n = -1
        swap_d = cur_d
        for n in neighbors(my_cell):
            other = friendly_by_cell.get(n)
            if other is None or other is army:
                continue
            d = field.get(n)
            if d is None or d >= swap_d:
                continue

            other_org = float(getattr(other, "organization", 0.0))
            other_retreat = getattr(other, "behavior", None) == BEHAVIOR_RETREAT
            other_weak = other_org <= (ARMY_BROKEN_ORG + 8.0)
            mine_ready = float(getattr(army, "organization", 0.0)) >= max(
                ARMY_RECOVER_ORG,
                other_org + 8.0,
            )
            if not (mine_ready and (other_retreat or other_weak)):
                continue

            swap_n = n
            swap_d = d

        if swap_n < 0:
            break

        other = friendly_by_cell.get(swap_n)
        if other is None:
            break
        other.cell = my_cell
        army.cell = swap_n
        friendly_by_cell[my_cell] = other
        friendly_by_cell[swap_n] = army
        my_cell = army.cell
        steps += 1

    army.cell = my_cell


# ── Supply / morale recovery ────────────────────────────────────────────────

def _update_supply(army: Army, impr: list, om: list) -> None:
    cur = army.cell
    d = dist(cur, army.origin_cell)
    decay = max(0.0, (d - ARMY_SUPPLY_FREE_DIST) * ARMY_SUPPLY_DECAY)

    raw = impr[cur] if 0 <= cur < N else 0
    it = imp_type(raw)
    if it in (IMP.FARM, IMP.COTTON, IMP.PASTURE, IMP.FISHERY):
        army.supply = min(100.0, army.supply + ARMY_SUPPLY_REPLEN)

    army.supply = max(0.0, army.supply - decay)

    if army.supply <= 0.5:
        army.organization = max(0.0, army.organization - 1.5)
        army.strength     = max(0.0, army.strength - 0.6)

    if 0 <= cur < N and om[cur] == army.civ_id and army.supply > 35:
        army.organization = min(100.0, army.organization + 0.7)


# ── Behavior selection (HMM-like scoring) ───────────────────────────────────

def _besieged_friendly(
    army: Army, civ: Civ, enemy_armies: list[Army]
) -> tuple[Optional[City], float, int]:
    """Return (most_threatened_city, urgency_score, distance_to_army) or (None, 0, inf).

    Urgency combines hp loss, proximity of enemy armies, and capital weight.
    """
    cur = army.cell
    best = None
    best_urgency = 0.0
    best_dist = 10**9
    for fc in civ.cities:
        max_hp = getattr(fc, "max_hp", 1) or 1
        hp_frac = max(0.0, min(1.0, getattr(fc, "hp", max_hp) / max_hp))
        nearest_enemy_d = 10**9
        for ea in enemy_armies:
            if ea.strength <= 0:
                continue
            d = dist(ea.cell, getattr(fc, "cell", -1))
            if d < nearest_enemy_d:
                nearest_enemy_d = d
        besieged = nearest_enemy_d <= 2 or hp_frac < 0.85
        if not besieged:
            continue
        cap_mult = 2.5 if getattr(fc, "is_capital", False) else 1.0
        hp_term = (1.0 - hp_frac) ** 1.3 * 4.0 + (0.4 if hp_frac < 0.6 else 0)
        prox_term = 2.0 / (1 + nearest_enemy_d * 0.5)
        urgency = (hp_term + prox_term) * cap_mult
        # Discounted by how far this army must travel
        my_d = dist(cur, getattr(fc, "cell", -1))
        score = urgency * (6.0 / (1 + my_d * 0.18))
        if score > best_urgency:
            best_urgency = score
            best = fc
            best_dist = my_d
    return best, best_urgency, best_dist


def _find_defend_territory_city(
    army: Army, civ: Civ, enemy: Civ
) -> Optional[City]:
    """Pick a frontier friendly city to garrison (proactive defence).

    Scoring: prefer cities that are close to enemy territory AND not too
    far from this army. Nothing returned if no friendly city is close to
    the enemy.
    """
    if not civ.cities or not enemy.territory:
        return None
    # For efficiency, use the enemy's territory size as a threshold proxy
    # instead of an O(city * enemy_territory) scan. We iterate enemy
    # border cells which is cheaper.
    cur = army.cell
    best = None
    best_score = 0.0
    for fc in civ.cities:
        # Approximate distance to enemy border by checking neighbours
        fc_cell = getattr(fc, "cell", -1)
        near_enemy = False
        min_enemy_d = 10**9
        for n in neighbors(fc_cell):
            if 0 <= n < N and n in enemy.territory:
                near_enemy = True
                min_enemy_d = 1
                break
        if not near_enemy:
            # Check extended neighbourhood via distance to any enemy border cell
            # (bounded — only look at first 40 border cells)
            sample = 0
            for ec in enemy.territory:
                sample += 1
                if sample > 40:
                    break
                d = dist(fc_cell, ec)
                if d < min_enemy_d:
                    min_enemy_d = d
                    if min_enemy_d <= 4:
                        break
        if min_enemy_d > 5:
            continue
        my_d = dist(cur, fc_cell)
        # Prefer capitals and cities closest to the enemy, discounted by travel time.
        cap_mult = 1.6 if getattr(fc, "is_capital", False) else 1.0
        score = cap_mult * (3.0 / (1 + min_enemy_d * 0.6)) * (5.0 / (1 + my_d * 0.15))
        if score > best_score:
            best_score = score
            best = fc
    return best


def _adjacent_land_cell_toward(
    target_cell: int, from_cell: int, ter: list, blocked: Set[int],
) -> int:
    """Return a walkable cell adjacent to `target_cell` that's closest to
    `from_cell`, or `target_cell` itself if no adjacent land exists."""
    best = target_cell
    best_d = 10**9
    for n in neighbors(target_cell):
        if n < 0 or n >= N:
            continue
        if n in blocked:
            continue
        t = ter[n]
        if t <= T.COAST or t == T.MTN or t == T.SNOW:
            continue
        d = dist(n, from_cell)
        if d < best_d:
            best_d = d
            best = n
    return best


def _nearest_cell(from_cell: int, candidates) -> int:
    """Pick the closest cell in `candidates` by Manhattan distance.
    Returns -1 if the iterable is empty."""
    best = -1
    best_d = 10**9
    for c in candidates:
        d = dist(from_cell, c)
        if d < best_d:
            best_d = d
            best = c
    return best


def _select_behavior(
    army: Army, civ: Civ, enemy: Civ,
    enemy_armies: list[Army], friendly_armies: list[Army],
    ter: list, blocked_enemy: Set[int], occupied: Set[int],
    safe_cells: Set[int], enemy_city_cells: list,
    *, is_aggressor: bool,
) -> tuple[str, Objective]:
    """HMM-like scoring over the behaviour states.

    Returns (behavior_key, objective_dict). The objective's `target_cell`
    is already de-stacked so the army walks to an adjacent cell when
    hunting an enemy army, not directly on top of it.

    `is_aggressor` shifts the score distribution: aggressors push
    offensively (ATTACK_CITY / ATTACK_ARMY, scanning the whole map for
    targets); defenders favour DEFEND_TERRITORY on border cities and only
    camp in forts that sit close to the front.

    `safe_cells` is the set of friendly retreat destinations (cities +
    forts). `enemy_city_cells` is a cheap list of enemy city cells used
    to measure "distance to front" for the fort-camping heuristic.

    `occupied` is the full set of live army cells in this war (friend + foe)
    and is used to pick walk targets that don't stack on a friendly.
    """
    cur = army.cell
    cur_b = army.behavior
    adj_blocked = (occupied | blocked_enemy) - {cur}

    # ── Retreat gate ─────────────────────────────────────────────────
    # A broken army (org <= ARMY_BROKEN_ORG) limps back toward the nearest
    # friendly city/fort and can't initiate combat. Exit retreat as soon
    # as the army stands on friendly territory (not just on a fort/city)
    # with organisation recovered — otherwise, when several broken armies
    # all target the same fort, the one parked on the cell locks the rest
    # out: they pile up adjacent, on home soil, but keep re-selecting the
    # blocked fort as their target forever. Being inside own territory is
    # safe enough to stop fleeing; supply/org already regen there.
    org = army.organization
    in_retreat = (cur_b == BEHAVIOR_RETREAT)
    if org <= ARMY_BROKEN_ORG or in_retreat:
        on_safe_ground = cur in safe_cells or cur in getattr(civ, "territory", set())
        recovered = org >= ARMY_RECOVER_ORG
        if not (in_retreat and on_safe_ground and recovered):
            # Pick nearest safe retreat target; fall back to origin.
            target = _nearest_cell(cur, safe_cells)
            if target < 0:
                target = army.origin_cell
            return BEHAVIOR_RETREAT, Objective(
                type="retreat", target_cell=target, target_id=None,
            )
        # else: fall through and run normal HMM

    # ── Closest enemy army ───────────────────────────────────────────
    best_a = None
    best_a_d = 10**9
    for ea in enemy_armies:
        if ea.strength <= 0:
            continue
        d = dist(cur, ea.cell)
        if d < best_a_d:
            best_a_d = d
            best_a = ea

    # ── Closest enemy city, with adjacency preference ────────────────
    # Aggressors look at every enemy city (no range cap) so they always
    # have a march target — this fixes the "aggressor just sits defending
    # far from the front" bug. Defenders still use the normal cap.
    city_range = 10**9 if is_aggressor else ARMY_TARGET_CITY_RANGE
    best_c = None
    best_c_score = -1.0
    best_c_d = 10**9
    civ_territory = getattr(civ, "territory", set())
    for city in getattr(enemy, "cities", []):
        d = dist(cur, city.cell)
        if d > city_range:
            continue
        adj_mine = any(
            0 <= n < N and n in civ_territory for n in neighbors(city.cell)
        )
        if is_aggressor:
            base = 7.0 if adj_mine else 3.5
        else:
            base = 4.0 if adj_mine else 1.6
        s = base / (1 + d * 0.08)
        if s > best_c_score:
            best_c_score = s
            best_c = city
            best_c_d = d

    # ── Besieged friendly city / frontier defence ────────────────────
    relieve_target, relieve_urgency, relieve_dist = _besieged_friendly(
        army, civ, enemy_armies,
    )
    defend_city = _find_defend_territory_city(army, civ, enemy)

    # ── Score each state ─────────────────────────────────────────────
    scores = {
        BEHAVIOR_DEFEND_FORT:      0.3,
        BEHAVIOR_DEFEND_TERRITORY: 0.0,
        BEHAVIOR_ATTACK_ARMY:      0.0,
        BEHAVIOR_ATTACK_CITY:      0.0,
        BEHAVIOR_RELIEVE_CITY:     0.0,
    }

    if best_a is not None and best_a_d <= ARMY_ENGAGE_RANGE * 4:
        my_eff    = _eff_strength(army)
        their_eff = _eff_strength(best_a) if best_a.strength > 0 else 0.001
        ratio = my_eff / max(their_eff, 0.1)
        scores[BEHAVIOR_ATTACK_ARMY] = (4.0 / (1 + best_a_d * 0.12)) * (0.6 + 0.7 * min(2.5, ratio))

    if best_c is not None:
        scores[BEHAVIOR_ATTACK_CITY] = best_c_score
        if getattr(best_c, "hp", 100) < getattr(best_c, "max_hp", 100) * 0.6:
            scores[BEHAVIOR_ATTACK_CITY] *= 1.9
        if getattr(best_c, "hp", 100) < getattr(best_c, "max_hp", 100) * 0.3:
            scores[BEHAVIOR_ATTACK_CITY] *= 1.6
        any_def = any(
            ea.strength > 0 and dist(ea.cell, best_c.cell) <= 5
            for ea in enemy_armies
        )
        if not any_def:
            scores[BEHAVIOR_ATTACK_CITY] *= 1.4

    if relieve_target is not None:
        scores[BEHAVIOR_RELIEVE_CITY] = relieve_urgency
        closer_friend_exists = False
        for fa in friendly_armies:
            if fa is army or fa.strength <= 0:
                continue
            if dist(fa.cell, relieve_target.cell) < relieve_dist - 1:
                closer_friend_exists = True
                break
        if not closer_friend_exists:
            scores[BEHAVIOR_RELIEVE_CITY] *= 1.6

    if defend_city is not None:
        my_d = dist(cur, defend_city.cell)
        scores[BEHAVIOR_DEFEND_TERRITORY] = 2.2 / (1 + my_d * 0.08)
        if getattr(defend_city, "is_capital", False):
            scores[BEHAVIOR_DEFEND_TERRITORY] *= 1.3

    # ── Fort-near-front check ────────────────────────────────────────
    # A fort is only a valid camping spot if it sits close to the enemy.
    # Armies stationed at a rear-area fort should march forward instead
    # of parking. "Close" = within ARMY_FRONT_DIST of any enemy city.
    origin_front_d = _nearest_cell(army.origin_cell, enemy_city_cells)
    if origin_front_d >= 0:
        origin_front_dist = dist(army.origin_cell, origin_front_d)
    else:
        origin_front_dist = 10**9
    fort_near_front = origin_front_dist <= ARMY_FRONT_DIST
    if not fort_near_front:
        # Rear-area fort: dramatically down-weight sitting there.
        scores[BEHAVIOR_DEFEND_FORT] *= 0.15

    # ── Posture multipliers (aggressor/defender) ────────────────────
    if is_aggressor:
        scores[BEHAVIOR_ATTACK_CITY]      *= 2.8
        scores[BEHAVIOR_ATTACK_ARMY]      *= 1.6
        scores[BEHAVIOR_DEFEND_FORT]      *= 0.25
        scores[BEHAVIOR_DEFEND_TERRITORY] *= 0.35
    else:
        scores[BEHAVIOR_DEFEND_TERRITORY] *= 2.10
        scores[BEHAVIOR_RELIEVE_CITY]     *= 1.55
        # Only a frontline fort is worth camping in for defenders.
        scores[BEHAVIOR_DEFEND_FORT]      *= (1.30 if fort_near_front else 0.35)
        scores[BEHAVIOR_ATTACK_CITY]      *= 0.55

    # Inertia bonus for current state (retreat is never self-sticky).
    if cur_b != BEHAVIOR_RETREAT:
        scores[cur_b] = scores.get(cur_b, 0) * 1.6 + 0.4

    total = sum(scores.values())
    if total <= 0:
        # Aggressor fallback: if nothing scored, march toward the nearest
        # enemy city anyway. This is what fixes "aggressors sit in their
        # fort forever because the enemy is outside the HMM range".
        if is_aggressor and enemy_city_cells:
            nearest = _nearest_cell(cur, enemy_city_cells)
            if nearest >= 0:
                walk = _adjacent_land_cell_toward(nearest, cur, ter, adj_blocked)
                return BEHAVIOR_ATTACK_CITY, Objective(
                    type="city", target_cell=nearest,
                    walk_cell=walk, target_id=None,
                )
        return BEHAVIOR_DEFEND_FORT, Objective(
            type="defend", target_cell=army.origin_cell, target_id=None,
        )

    chosen = max(scores, key=lambda k: scores[k] * (0.85 + random.random() * 0.3))

    if chosen == BEHAVIOR_ATTACK_ARMY and best_a is not None:
        tgt = _adjacent_land_cell_toward(best_a.cell, cur, ter, adj_blocked)
        return chosen, Objective(
            type="army",
            target_cell=tgt,
            target_id=best_a.id,
        )
    if chosen == BEHAVIOR_ATTACK_CITY and best_c is not None:
        walk = _adjacent_land_cell_toward(best_c.cell, cur, ter, adj_blocked)
        return chosen, Objective(
            type="city",
            target_cell=best_c.cell,
            walk_cell=walk,
            target_id=None,
        )
    if chosen == BEHAVIOR_RELIEVE_CITY and relieve_target is not None:
        return chosen, Objective(
            type="relieve",
            target_cell=relieve_target.cell,
            target_id=None,
        )
    if chosen == BEHAVIOR_DEFEND_TERRITORY and defend_city is not None:
        return chosen, Objective(
            type="garrison",
            target_cell=defend_city.cell,
            target_id=None,
        )
    return BEHAVIOR_DEFEND_FORT, Objective(
        type="defend",
        target_cell=army.origin_cell,
        target_id=None,
    )


# ── Combat resolution ───────────────────────────────────────────────────────

def _resolve_army_combat(
    armies_a: list[Army], armies_b: list[Army],
    civ_a: Civ, civ_b: Civ,
    tick: int, add_event,
) -> None:
    """Symmetric combat: every adjacent enemy pair trades damage once.
    Fortification is applied to damage TAKEN by the defender side.
    """
    for a in armies_a:
        if a.strength <= 0:
            continue
        for b in armies_b:
            if b.strength <= 0:
                continue
            if dist(a.cell, b.cell) > ARMY_COMBAT_RANGE:
                continue
            ea = _eff_strength(a)
            eb = _eff_strength(b)
            jitter_a = 0.75 + random.random() * 0.5
            jitter_b = 0.75 + random.random() * 0.5
            a_fort = 1.0 + getattr(a, "fortification", 0.0)
            b_fort = 1.0 + getattr(b, "fortification", 0.0)
            # Fortification reduces INCOMING damage:
            a_dmg = (eb * ARMY_COMBAT_DAMAGE * jitter_a) / a_fort
            b_dmg = (ea * ARMY_COMBAT_DAMAGE * jitter_b) / b_fort
            # Retreating armies cannot return fire (they're limping home).
            if getattr(a, "behavior", None) == BEHAVIOR_RETREAT:
                b_dmg = 0.0
            if getattr(b, "behavior", None) == BEHAVIOR_RETREAT:
                a_dmg = 0.0
            a.strength     = max(0.0, a.strength - a_dmg)
            b.strength     = max(0.0, b.strength - b_dmg)
            a.organization = max(0.0, a.organization - 4.0 - random.random() * 3)
            b.organization = max(0.0, b.organization - 4.0 - random.random() * 3)
            if a.strength <= 0:
                add_event(f"💀 Year {tick}: {civ_a.name}'s {a.commander.name} fell in battle vs {civ_b.name}")
            if b.strength <= 0:
                add_event(f"💀 Year {tick}: {civ_b.name}'s {b.commander.name} fell in battle vs {civ_a.name}")


def _resolve_city_assault(
    armies: list[Army], attacker: Civ, defender: Civ,
    om: list, impr: list, war: War, war_side: str,
    tick: int, add_event,
) -> None:
    """Siege: adjacent attackers deal HP damage; capture when hp <= 0.

    Recently-captured cities enjoy brief immunity so they can't flip back
    and forth inside a single or adjacent tick.
    """
    for a in armies:
        if a.strength <= 0:
            continue
        # Retreating armies cannot besiege.
        if getattr(a, "behavior", None) == BEHAVIOR_RETREAT:
            continue
        for city in list(getattr(defender, "cities", [])):
            if dist(a.cell, city.cell) > 1:
                continue
            ensure_city_hp(city, impr)
            if tick < getattr(city, "siege_immune_until", 0):
                continue
            ea = _eff_strength(a)
            dmg = ea * ARMY_CITY_DAMAGE * (0.7 + random.random() * 0.6)
            city.hp -= dmg
            city.last_dmg_tick = tick
            a.organization = max(0.0, a.organization - 1.4)
            if city.hp > 0:
                continue

            # ── Capture ─────────────────────────────────────────────────
            # Transfer the full city hinterland immediately.
            city_tiles = set(getattr(city, "tiles", None) or [])
            captured_cells = {
                cell for cell in city_tiles
                if 0 <= cell < N and cell in defender.territory
            }
            captured_cells.add(city.cell)

            for cell in captured_cells:
                defender.territory.discard(cell)
                attacker.territory.add(cell)
                if 0 <= cell < len(om):
                    om[cell] = attacker.id

            defender.cities = [c for c in getattr(defender, "cities", []) if c.cell != city.cell]
            city.is_capital = False
            city.max_hp = city_max_hp(city, impr)
            city.hp = city.max_hp
            city.siege_immune_until = tick + 6
            city.last_dmg_tick = tick
            if not hasattr(attacker, "cities"):
                attacker.cities = []
            attacker.cities.append(city)

            # Force territory/cell-cache refresh next simulation pass.
            defender._layout_version = getattr(defender, "_layout_version", 0) + 1
            attacker._layout_version = getattr(attacker, "_layout_version", 0) + 1

            add_event(f"🔥 Year {tick}: {attacker.name} stormed {city.name}!")

            cap_key = "captured_cities_a" if war_side == "a" else "captured_cities_d"
            if not hasattr(war, cap_key):
                setattr(war, cap_key, [])
            getattr(war, cap_key).append(city.cell)

            # War morale — defender side suffers the loss, attacker gains.
            loser_side = "d" if war_side == "a" else "a"
            diplomacy.apply_city_lost(war, loser_side)
            # Lasting grudge on the losing civ for the city seizure.
            if not hasattr(defender, "relations"):
                defender.relations = {}
            defender.relations[attacker.id] = max(
                diplomacy.REL_MIN,
                defender.relations.get(attacker.id, 0.0)
                + diplomacy.REL_CITY_CAPTURED,
            )
            if not hasattr(attacker, "relations"):
                attacker.relations = {}
            attacker.relations[defender.id] = max(
                diplomacy.REL_MIN,
                attacker.relations.get(defender.id, 0.0)
                + diplomacy.REL_CITY_CAPTURED * 0.5,
            )

            a.organization = min(100.0, a.organization + 18)
            a.supply       = min(100.0, a.supply + 25)


# ── Per-tick orchestrator ───────────────────────────────────────────────────

def tick_armies(
    civs: list[Civ], wars: dict[str, War], ter: list, impr: list, om: list,
    tick: int, add_event: Callable[[str], None],
) -> None:
    """Full army tick: behavior selection → movement → combat → fort respawn."""
    civ_by_id = {c.id: c for c in civs}

    # Build the by-war army index once (used for blocked-cell lookups)
    armies_by_war: dict = {}
    for wk, war in wars.items():
        armies_by_war[wk] = (getattr(war, "armies_a", []) or []) + (getattr(war, "armies_d", []) or [])

    # ── Phase 1: behaviour + movement ─────────────────────────────────────
    for wk, war in wars.items():
        att  = civ_by_id.get(war.att)
        defn = civ_by_id.get(war.def_id)
        if not att or not defn or not getattr(att, "alive", True) or not getattr(defn, "alive", True):
            continue

        for side, civ, enemy, my_key, enemy_key, is_aggressor in (
            ("a", att,  defn, "armies_a", "armies_d", True),
            ("d", defn, att,  "armies_d", "armies_a", False),
        ):
            armies = getattr(war, my_key, [])
            enemy_armies = getattr(war, enemy_key, [])

            # Cells currently held by any army in this war — used for
            # collision checks when stepping.
            occupied: Set[int] = {
                x.cell for x in armies_by_war[wk] if getattr(x, "strength", 0) > 0
            }
            blocked_enemy: Set[int] = {
                x.cell for x in enemy_armies if getattr(x, "strength", 0) > 0
            }

            # Safe-retreat cells: friendly cities + friendly forts.
            safe_cells: Set[int] = {c.cell for c in getattr(civ, "cities", [])}
            for cell in getattr(civ, "territory", set()):
                if 0 <= cell < N and imp_type(impr[cell]) == IMP.FORT:
                    safe_cells.add(cell)

            # Enemy city cells: cheap list used for "distance to front".
            enemy_city_cells = [c.cell for c in getattr(enemy, "cities", [])]

            # Pass 1: supply + behavior + pending move list (no movement yet).
            pending: list[tuple[Army, int]] = []
            for a in armies:
                if a.strength <= 0:
                    continue
                _update_supply(a, impr, om)

                # Refresh fortification BEFORE behavior so scoring sees it
                a.fortification, a.fort_source = _compute_fortification(a, civ, impr)

                behavior, obj = _select_behavior(
                    a, civ, enemy, enemy_armies, armies, ter,
                    blocked_enemy, occupied, safe_cells, enemy_city_cells,
                    is_aggressor=is_aggressor,
                )

                # Detect broken-army transition: once org first drops at or
                # below the break threshold, fire the morale event once.
                # Reset the flag when org recovers so future breaks count.
                if a.organization <= ARMY_BROKEN_ORG:
                    if not getattr(a, "broken_fired", False):
                        a.broken_fired = True
                        diplomacy.apply_army_broken(war, side)
                elif a.organization >= ARMY_RECOVER_ORG:
                    a.broken_fired = False

                a.behavior  = behavior
                a.objective = obj
                if obj and obj.target_cell is not None:
                    walk = obj.walk_cell if obj.walk_cell is not None else obj.target_cell
                    if walk != a.cell:
                        pending.append((a, walk))

            # Pass 2: flow-field movement. One BFS per unique walk target
            # (shared across every army heading there); then priority-sort
            # ascending by distance-to-target so the lead army moves first
            # and frees its cell for whoever's behind it — column flow.
            field_cache: dict[int, dict[int, int]] = {}
            for _, walk in pending:
                if walk not in field_cache:
                    field_cache[walk] = land_bfs_distance_field(
                        walk, ter, blocked_enemy,
                    )

            pending.sort(
                key=lambda item: field_cache[item[1]].get(item[0].cell, 10**9),
            )

            friendly_by_cell: dict[int, Army] = {
                a.cell: a for a in armies if getattr(a, "strength", 0) > 0
            }
            for a, walk in pending:
                _descend_field(
                    a,
                    field_cache[walk],
                    occupied,
                    blocked_enemy,
                    friendly_by_cell,
                )

            # Refresh fortification AFTER movement (armies may have landed
            # on a fort/city this tick).
            for a in armies:
                if a.strength > 0:
                    a.fortification, a.fort_source = _compute_fortification(a, civ, impr)

    # ── Phase 2: combat (army vs army, then army vs city) ─────────────────
    for wk, war in wars.items():
        att  = civ_by_id.get(war.att)
        defn = civ_by_id.get(war.def_id)
        if not att or not defn or not getattr(att, "alive", True) or not getattr(defn, "alive", True):
            continue
        a_list = getattr(war, "armies_a", [])
        d_list = getattr(war, "armies_d", [])

        _resolve_army_combat(a_list, d_list, att, defn, tick, add_event)
        _resolve_city_assault(a_list, att, defn, om, impr, war, "a", tick, add_event)
        _resolve_city_assault(d_list, defn, att, om, impr, war, "d", tick, add_event)

        setattr(war, "armies_a", [a for a in a_list if a.strength > 0])
        setattr(war, "armies_d", [d for d in d_list if d.strength > 0])

    # ── Phase 3: fort respawning ──────────────────────────────────────────
    for wk, war in wars.items():
        att  = civ_by_id.get(war.att)
        defn = civ_by_id.get(war.def_id)
        if not att or not defn:
            continue
        for civ, side in ((att, "a"), (defn, "d")):
            armies_key = "armies_a" if side == "a" else "armies_d"
            armies = getattr(war, armies_key, [])
            occupied_origins = {a.origin_cell for a in armies if a.strength > 0}
            if not hasattr(civ, "fort_cooldowns"):
                civ.fort_cooldowns = {}
            cooldowns = civ.fort_cooldowns

            for cell in list(getattr(civ, "territory", set())):
                raw = impr[cell] if 0 <= cell < N else 0
                if imp_type(raw) != IMP.FORT or not fort_is_active(civ, cell):
                    continue
                if cell in occupied_origins:
                    continue
                if tick < cooldowns.get(cell, 0):
                    continue
                if getattr(civ, "metal_stock", 0) < 4:
                    continue
                lvl = imp_level(raw)
                a = _make_army(civ, cell, lvl, wk)
                armies.append(a)
                civ.metal_stock -= 4
                cooldowns[cell] = tick + ARMY_RESPAWN_DELAY
            setattr(war, armies_key, armies)
