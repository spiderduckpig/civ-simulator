"""Diplomacy, relations, alliances, and war psychology.

Each civ carries:
    relations     {other_id: float in [-1, 1]}   symmetric per-pair score
    allies        set of civ ids
    aggressiveness float in [0, 1]               personality stat
    power         float                          cached raw power snapshot

Each war dict now carries per-side morale state:
    confidence_a / confidence_d   [-0.5, 1.5]  higher = willing to fight on
    exhaustion_a / exhaustion_d   [0, 1]       higher = wants peace

A war ends when either side's (confidence - exhaustion) drops below
MORALE_PEACE_THRESHOLD — peace is now an emergent outcome, not a dice roll.
All thresholds are scale-invariant (fractions / ratios of power).
"""

from __future__ import annotations

import random
from typing import Dict, Optional, List, Set

from .helpers import war_key
from .models import Civ, War


# ── Relation drift constants ─────────────────────────────────────────
REL_MIN, REL_MAX = -1.0, 1.0
REL_DRIFT_PEACE_NEIGHBOR = 0.0006
REL_DRIFT_PEACE_DISTANT  = 0.00008
REL_DRIFT_WAR            = -0.0030
REL_CITY_CAPTURED        = -0.30
REL_WAR_START            = -0.12
REL_WAR_PEACE            = -0.08
REL_ALLIANCE_BONUS       = 0.20

REL_ALLIANCE_THRESHOLD   = 0.55   # both sides need >= this to ally
REL_WAR_TOLERANCE        = 0.20   # aggressors tolerate relations up to this

# ── War psychology ───────────────────────────────────────────────────
CONFIDENCE_START_AGG = 0.80
CONFIDENCE_START_DEF = 0.55
EXH_DRIFT_PER_TICK   = 0.0022
EXH_CITY_LOST        = 0.16
EXH_ARMY_BROKEN      = 0.06
EXH_CITY_TAKEN       = -0.05
CONF_CITY_LOST       = -0.18
CONF_ARMY_BROKEN     = -0.06
CONF_CITY_TAKEN      = 0.20
CONF_ENEMY_BROKEN    = 0.08
MORALE_PEACE_THRESHOLD = -0.18


# ── Long-horizon disposition (HMM-like) ─────────────────────────────
DISPOSITION_STATES = ("calm", "aggressive", "fortifying")
DISPOSITION_PERIOD_TICKS = 500
DISPOSITION_STAY_PROB = 0.94
AGGRESSIVE_PRESSURE_PERIOD = 100
AGGRESSIVE_RELATION_HIT = -0.24
AGGRESSIVE_TARGET_LIMIT = 2


# ── Setup / ticking ──────────────────────────────────────────────────

def ensure_civ_diplo(civ: Civ, all_civs: List[Civ]) -> None:
    """Initialise / backfill diplomacy fields on a civ."""
    for other in all_civs:
        if other.id == civ.id:
            continue
        civ.relations.setdefault(other.id, 0.0)


def compute_power(civ: Civ) -> float:
    """Scale-invariant power score. Higher = stronger bloc."""
    return (
        civ.military * 1.0
        + civ.tech * 4.0
        + len(civ.territory) * 0.5
        + civ.wealth * 0.15
        + len(civ.cities) * 6.0
    )


def bloc_power(civ: Civ, civs_by_id: Dict[int, Civ]) -> float:
    """Civ power + power of every living ally."""
    total = civ.power
    for aid in civ.allies:
        ally = civs_by_id.get(aid)
        if ally and ally.alive:
            total += ally.power
    return total


def _clamp(x: float, lo: float = REL_MIN, hi: float = REL_MAX) -> float:
    return max(lo, min(hi, x))


def _symmetric_shift(a: Civ, b: Civ, delta: float) -> None:
    a.relations[b.id] = _clamp(a.relations.get(b.id, 0.0) + delta)
    b.relations[a.id] = _clamp(b.relations.get(a.id, 0.0) + delta)


def _weighted_pick(weighted_states: List[tuple[str, float]]) -> str:
    total = sum(w for _, w in weighted_states)
    if total <= 0.0:
        return "calm"
    roll = random.random() * total
    acc = 0.0
    for state, weight in weighted_states:
        acc += weight
        if roll <= acc:
            return state
    return weighted_states[-1][0]


def _choose_aggressive_targets(civ: Civ, alive: List[Civ], border_cache: Dict[int, Set[int]]) -> List[int]:
    borders = border_cache.get(civ.id, set())
    if not borders:
        return []

    neighbor_candidates: list[tuple[float, int]] = []
    for other in alive:
        if other.id == civ.id or other.id in civ.allies:
            continue
        if not any(cell in other.territory for cell in borders):
            continue
        power_ratio = civ.power / max(other.power, 1.0)
        hostility = 0.5 - civ.relations.get(other.id, 0.0)
        score = power_ratio * 1.2 + hostility
        neighbor_candidates.append((score, other.id))

    neighbor_candidates.sort(reverse=True)
    return [oid for _, oid in neighbor_candidates[:AGGRESSIVE_TARGET_LIMIT]]


def tick_dispositions(alive: List[Civ], border_cache: Dict[int, Set[int]], wars: Dict[str, War], tick: int) -> None:
    """Rare long-horizon policy shifts and aggressive neighbor selection."""
    for civ in alive:
        if getattr(civ, "disposition", None) not in DISPOSITION_STATES:
            civ.disposition = "calm"
        if not hasattr(civ, "disposition_ticks"):
            civ.disposition_ticks = 0
        if not hasattr(civ, "disposition_targets") or civ.disposition_targets is None:
            civ.disposition_targets = []
        civ.disposition_ticks += 1

    if tick <= 0 or (tick % DISPOSITION_PERIOD_TICKS) != 0:
        return

    avg_power = sum(c.power for c in alive) / max(1, len(alive))
    active_war_ids = {
        w.att for w in wars.values()
    } | {
        w.def_id for w in wars.values()
    }

    for civ in alive:
        current = civ.disposition
        base_shift = (1.0 - DISPOSITION_STAY_PROB) / 2.0
        probs = {
            "calm": base_shift,
            "aggressive": base_shift,
            "fortifying": base_shift,
        }
        probs[current] = DISPOSITION_STAY_PROB

        power_ratio = civ.power / max(avg_power, 1.0)
        if power_ratio >= 1.25:
            probs["aggressive"] += 0.10
            probs["fortifying"] = max(0.0, probs["fortifying"] - 0.05)
        elif power_ratio <= 0.80:
            probs["fortifying"] += 0.12
            probs["aggressive"] = max(0.0, probs["aggressive"] - 0.07)
        else:
            probs["calm"] += 0.04

        if civ.id in active_war_ids:
            probs["aggressive"] += 0.04

        if len(civ.cities) <= 2:
            probs["fortifying"] += 0.03

        next_state = _weighted_pick(list(probs.items()))
        if next_state != current:
            civ.disposition = next_state
            civ.disposition_ticks = 0
            civ.events.append(f"Year {civ.age}: Adopted {next_state} disposition")

        if civ.disposition == "aggressive":
            civ.disposition_targets = _choose_aggressive_targets(civ, alive, border_cache)
        elif civ.disposition == "fortifying":
            civ.disposition_targets = []


def tick_relations(
    alive: List[Civ],
    wars: Dict[str, War],
    border_cache: Dict[int, Set[int]],
    tick: int,
) -> None:
    """Drift relations every tick based on borders and war state, and
    refresh each civ's cached power snapshot."""
    for civ in alive:
        ensure_civ_diplo(civ, alive)
        civ.power = compute_power(civ)

    for i in range(len(alive)):
        for j in range(i + 1, len(alive)):
            a, b = alive[i], alive[j]
            k = war_key(a.id, b.id)
            at_war = k in wars

            border = any(bc in b.territory for bc in border_cache[a.id])

            if at_war:
                _symmetric_shift(a, b, REL_DRIFT_WAR)
            elif border:
                _symmetric_shift(a, b, REL_DRIFT_PEACE_NEIGHBOR)
            else:
                _symmetric_shift(a, b, REL_DRIFT_PEACE_DISTANT)

    # Aggressive civs periodically poison relations with chosen border rivals.
    for civ in alive:
        if getattr(civ, "disposition", "calm") != "aggressive":
            continue
        if not getattr(civ, "disposition_targets", None):
            civ.disposition_targets = _choose_aggressive_targets(civ, alive, border_cache)

    # Keep this shock cadence sparse so policy remains long-horizon.
    if tick % AGGRESSIVE_PRESSURE_PERIOD == 0:
        civs_by_id = {c.id: c for c in alive}
        for civ in alive:
            if getattr(civ, "disposition", "calm") != "aggressive":
                continue
            targets = list(getattr(civ, "disposition_targets", []))
            if not targets:
                continue
            for tid in targets:
                target = civs_by_id.get(tid)
                if not target or not target.alive or tid in civ.allies:
                    continue
                _symmetric_shift(civ, target, AGGRESSIVE_RELATION_HIT)


# ── War declaration ──────────────────────────────────────────────────

def consider_war_declaration(
    a: Civ, b: Civ, wars: Dict[str, War], civs_by_id: Dict[int, Civ], tick: int,
    k: str, border: bool,
) -> Optional[War]:
    """Return a new war dict if `a` should declare war on `b`, else None."""
    if not border or k in wars or b.id in a.allies:
        return None

    # Count active wars this civ is already in.
    existing_wars = sum(
        1 for w in wars.values()
        if w.att == a.id or w.def_id == a.id
    )

    # Only the most aggressive civs will even consider a second front.
    if existing_wars >= 1 and a.aggressiveness < 0.75:
        return None

    rel = a.relations.get(b.id, 0.0)
    # Aggressiveness lets a civ shrug off worse relations before refusing war.
    hostility = (REL_WAR_TOLERANCE - rel) * a.aggressiveness
    disposition = getattr(a, "disposition", "calm")
    if disposition == "aggressive":
        hostility *= 1.45
        if b.id in getattr(a, "disposition_targets", []):
            hostility *= 1.30
    elif disposition == "fortifying":
        hostility *= 0.55
    else:
        hostility *= 0.90
    if hostility <= 0:
        return None

    # Bloc power check — only attack similar or weaker blocs.
    a_bloc = bloc_power(a, civs_by_id)
    b_bloc = bloc_power(b, civs_by_id)
    ratio  = a_bloc / max(b_bloc, 1.0)
    # Already-at-war civs need a much bigger power edge to open a second front.
    min_ratio = 0.85 + (0.35 if existing_wars >= 1 else 0.0)
    if disposition == "aggressive":
        min_ratio -= 0.08
    elif disposition == "fortifying":
        min_ratio += 0.12
    if ratio < min_ratio:
        return None

    ratio_bonus = min(1.5, (ratio - 0.85) * 1.4)
    weak_target_bonus = 1.0 + min(0.7, max(0.0, ratio - 1.0) * 0.6)
    chance = hostility * 0.014 * (0.7 + ratio_bonus) * weak_target_bonus
    # Being already at war makes a civ an order of magnitude less likely to
    # declare another — multi-front wars are a rare, costly choice.
    if existing_wars >= 1:
        chance *= 0.06
    if random.random() >= chance:
        return None

    new_war = War(
        key=k, att=a.id, def_id=b.id, start=tick,
        confidence_a=CONFIDENCE_START_AGG,
        confidence_d=CONFIDENCE_START_DEF,
        exhaustion_a=0.0,
        exhaustion_d=0.0,
        armies_a=[],
        armies_d=[],
        ended=False
    )
    _symmetric_shift(a, b, REL_WAR_START)
    return new_war


# ── Alliance formation ───────────────────────────────────────────────

def consider_alliance(
    a: Civ, b: Civ, wars: Dict[str, War], civs_by_id: Dict[int, Civ],
) -> bool:
    """Pair-wise alliance check. Both sides must like each other above
    REL_ALLIANCE_THRESHOLD, and neither can be at war with the other."""
    if b.id in a.allies:
        return False
    if war_key(a.id, b.id) in wars:
        return False

    rel_ab = a.relations.get(b.id, 0.0)
    rel_ba = b.relations.get(a.id, 0.0)
    if rel_ab < REL_ALLIANCE_THRESHOLD or rel_ba < REL_ALLIANCE_THRESHOLD:
        return False

    # Under-pressure civs (many powerful enemies) push for alliances harder.
    pressure_bonus = 0.0
    for civ in (a, b):
        hostile_power = 0.0
        for w in wars.values():
            if w.att == civ.id:
                enemy = civs_by_id.get(w.def_id)
            elif w.def_id == civ.id:
                enemy = civs_by_id.get(w.att)
            else:
                continue
            if enemy:
                hostile_power += bloc_power(enemy, civs_by_id)
        own_bloc = bloc_power(civ, civs_by_id)
        if hostile_power > own_bloc * 0.8:
            pressure_bonus += 0.012

    chance = 0.003 + pressure_bonus
    return random.random() < chance


def form_alliance(a: Civ, b: Civ) -> None:
    a.allies.add(b.id)
    b.allies.add(a.id)
    _symmetric_shift(a, b, REL_ALLIANCE_BONUS)


def break_alliances_with(civ: Civ, civs_by_id: Dict[int, Civ]) -> None:
    """Called when `civ` dies — remove it from every ally's set."""
    for aid in list(civ.allies):
        ally = civs_by_id.get(aid)
        if ally:
            ally.allies.discard(civ.id)
    civ.allies = set()


# ── War morale hooks ─────────────────────────────────────────────────

def tick_war_morale(war: War) -> None:
    """Per-tick baseline drift. Confidence slowly equilibrates to 0.5."""
    war.exhaustion_a = min(1.0, war.exhaustion_a + EXH_DRIFT_PER_TICK)
    war.exhaustion_d = min(1.0, war.exhaustion_d + EXH_DRIFT_PER_TICK)
    
    war.confidence_a += (0.5 - war.confidence_a) * 0.002
    war.confidence_d += (0.5 - war.confidence_d) * 0.002


def should_sue_for_peace(war: War) -> bool:
    """Either side breaking ends the war: the winner wins peace, the
    loser needs it."""
    score_a = war.confidence_a - war.exhaustion_a
    score_d = war.confidence_d - war.exhaustion_d
    return score_a < MORALE_PEACE_THRESHOLD or score_d < MORALE_PEACE_THRESHOLD


def apply_city_lost(war: War, loser_side: str) -> None:
    """`loser_side` is 'a' or 'd' — the side whose city was captured."""
    if loser_side == "a":
        war.exhaustion_a = min(1.0, war.exhaustion_a + EXH_CITY_LOST)
        war.confidence_a = max(-0.5, war.confidence_a + CONF_CITY_LOST)
        war.exhaustion_d = max(0.0, war.exhaustion_d + EXH_CITY_TAKEN)
        war.confidence_d = min(1.5, war.confidence_d + CONF_CITY_TAKEN)
    else:
        war.exhaustion_d = min(1.0, war.exhaustion_d + EXH_CITY_LOST)
        war.confidence_d = max(-0.5, war.confidence_d + CONF_CITY_LOST)
        war.exhaustion_a = max(0.0, war.exhaustion_a + EXH_CITY_TAKEN)
        war.confidence_a = min(1.5, war.confidence_a + CONF_CITY_TAKEN)


def apply_army_broken(war: War, loser_side: str) -> None:
    if loser_side == "a":
        war.exhaustion_a = min(1.0, war.exhaustion_a + EXH_ARMY_BROKEN)
        war.confidence_a = max(-0.5, war.confidence_a + CONF_ARMY_BROKEN)
        war.confidence_d = min(1.5, war.confidence_d + CONF_ENEMY_BROKEN)
    else:
        war.exhaustion_d = min(1.0, war.exhaustion_d + EXH_ARMY_BROKEN)
        war.confidence_d = max(-0.5, war.confidence_d + CONF_ARMY_BROKEN)
        war.confidence_a = min(1.5, war.confidence_a + CONF_ENEMY_BROKEN)


def apply_post_war_baseline(a: Civ, b: Civ) -> None:
    """Applied when a war is settled — leaves a small lasting grudge."""
    _symmetric_shift(a, b, REL_WAR_PEACE)
