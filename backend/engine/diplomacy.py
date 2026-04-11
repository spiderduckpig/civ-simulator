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
from typing import Dict, Optional

from .helpers import war_key


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


# ── Setup / ticking ──────────────────────────────────────────────────

def ensure_civ_diplo(civ: dict, all_civs: list) -> None:
    """Initialise / backfill diplomacy fields on a civ."""
    civ.setdefault("relations", {})
    civ.setdefault("allies", set())
    # Migrate legacy peacefulness -> aggressiveness if needed
    if "aggressiveness" not in civ:
        civ["aggressiveness"] = 1.0 - civ.get("peacefulness", 0.5)
    civ.setdefault("power", 0.0)
    for other in all_civs:
        if other["id"] == civ["id"]:
            continue
        civ["relations"].setdefault(other["id"], 0.0)


def compute_power(civ: dict) -> float:
    """Scale-invariant power score. Higher = stronger bloc."""
    return (
        civ.get("military", 0.0) * 1.0
        + civ.get("tech", 1.0) * 4.0
        + len(civ.get("territory", [])) * 0.5
        + civ.get("wealth", 0.0) * 0.15
        + len(civ.get("cities", [])) * 6.0
    )


def bloc_power(civ: dict, civs_by_id: dict) -> float:
    """Civ power + power of every living ally."""
    total = civ.get("power", 0.0)
    for aid in civ.get("allies", set()):
        ally = civs_by_id.get(aid)
        if ally and ally.get("alive"):
            total += ally.get("power", 0.0)
    return total


def _clamp(x: float, lo: float = REL_MIN, hi: float = REL_MAX) -> float:
    return max(lo, min(hi, x))


def _symmetric_shift(a: dict, b: dict, delta: float) -> None:
    a["relations"][b["id"]] = _clamp(a["relations"].get(b["id"], 0.0) + delta)
    b["relations"][a["id"]] = _clamp(b["relations"].get(a["id"], 0.0) + delta)


def tick_relations(alive: list, wars: dict, border_cache: dict) -> None:
    """Drift relations every tick based on borders and war state, and
    refresh each civ's cached power snapshot."""
    for civ in alive:
        ensure_civ_diplo(civ, alive)
        civ["power"] = compute_power(civ)

    for i in range(len(alive)):
        for j in range(i + 1, len(alive)):
            a, b = alive[i], alive[j]
            k = war_key(a["id"], b["id"])
            at_war = k in wars

            border = any(bc in b["territory"] for bc in border_cache[a["id"]])

            if at_war:
                _symmetric_shift(a, b, REL_DRIFT_WAR)
            elif border:
                _symmetric_shift(a, b, REL_DRIFT_PEACE_NEIGHBOR)
            else:
                _symmetric_shift(a, b, REL_DRIFT_PEACE_DISTANT)


# ── War declaration ──────────────────────────────────────────────────

def consider_war_declaration(
    a: dict, b: dict, wars: dict, civs_by_id: dict, tick: int,
    k: str, border: bool,
) -> Optional[dict]:
    """Return a new war dict if `a` should declare war on `b`, else None."""
    if not border or k in wars or b["id"] in a.get("allies", set()):
        return None

    # Count active wars this civ is already in.
    existing_wars = sum(
        1 for w in wars.values()
        if w["a_id"] == a["id"] or w["d_id"] == a["id"]
    )

    # Only the most aggressive civs will even consider a second front.
    if existing_wars >= 1 and a.get("aggressiveness", 0.5) < 0.75:
        return None

    rel = a["relations"].get(b["id"], 0.0)
    # Aggressiveness lets a civ shrug off worse relations before refusing war.
    hostility = (REL_WAR_TOLERANCE - rel) * a["aggressiveness"]
    if hostility <= 0:
        return None

    # Bloc power check — only attack similar or weaker blocs.
    a_bloc = bloc_power(a, civs_by_id)
    b_bloc = bloc_power(b, civs_by_id)
    ratio  = a_bloc / max(b_bloc, 1.0)
    # Already-at-war civs need a much bigger power edge to open a second front.
    min_ratio = 0.85 + (0.35 if existing_wars >= 1 else 0.0)
    if ratio < min_ratio:
        return None

    ratio_bonus = min(1.5, (ratio - 0.85) * 1.4)
    chance = hostility * 0.014 * (0.7 + ratio_bonus)
    # Being already at war makes a civ an order of magnitude less likely to
    # declare another — multi-front wars are a rare, costly choice.
    if existing_wars >= 1:
        chance *= 0.06
    if random.random() >= chance:
        return None

    new_war = {
        "a_id": a["id"], "d_id": b["id"], "start_tick": tick,
        "pre_ter_a": set(a["territory"]),
        "pre_ter_d": set(b["territory"]),
        "captured_cities_a": [],
        "captured_cities_d": [],
        "armies_a": [],
        "armies_d": [],
        "confidence_a": CONFIDENCE_START_AGG,
        "confidence_d": CONFIDENCE_START_DEF,
        "exhaustion_a": 0.0,
        "exhaustion_d": 0.0,
    }
    _symmetric_shift(a, b, REL_WAR_START)
    return new_war


# ── Alliance formation ───────────────────────────────────────────────

def consider_alliance(
    a: dict, b: dict, wars: dict, civs_by_id: dict,
) -> bool:
    """Pair-wise alliance check. Both sides must like each other above
    REL_ALLIANCE_THRESHOLD, and neither can be at war with the other."""
    if b["id"] in a.get("allies", set()):
        return False
    if war_key(a["id"], b["id"]) in wars:
        return False

    rel_ab = a["relations"].get(b["id"], 0.0)
    rel_ba = b["relations"].get(a["id"], 0.0)
    if rel_ab < REL_ALLIANCE_THRESHOLD or rel_ba < REL_ALLIANCE_THRESHOLD:
        return False

    # Under-pressure civs (many powerful enemies) push for alliances harder.
    pressure_bonus = 0.0
    for civ in (a, b):
        hostile_power = 0.0
        for w in wars.values():
            if w["a_id"] == civ["id"]:
                enemy = civs_by_id.get(w["d_id"])
            elif w["d_id"] == civ["id"]:
                enemy = civs_by_id.get(w["a_id"])
            else:
                continue
            if enemy:
                hostile_power += bloc_power(enemy, civs_by_id)
        own_bloc = bloc_power(civ, civs_by_id)
        if hostile_power > own_bloc * 0.8:
            pressure_bonus += 0.012

    chance = 0.003 + pressure_bonus
    return random.random() < chance


def form_alliance(a: dict, b: dict) -> None:
    a.setdefault("allies", set()).add(b["id"])
    b.setdefault("allies", set()).add(a["id"])
    _symmetric_shift(a, b, REL_ALLIANCE_BONUS)


def break_alliances_with(civ: dict, civs_by_id: dict) -> None:
    """Called when `civ` dies — remove it from every ally's set."""
    for aid in list(civ.get("allies", set())):
        ally = civs_by_id.get(aid)
        if ally:
            ally.get("allies", set()).discard(civ["id"])
    civ["allies"] = set()


# ── War morale hooks ─────────────────────────────────────────────────

def tick_war_morale(war: dict) -> None:
    """Per-tick baseline drift. Confidence slowly equilibrates to 0.5."""
    war["exhaustion_a"] = min(1.0, war.get("exhaustion_a", 0.0) + EXH_DRIFT_PER_TICK)
    war["exhaustion_d"] = min(1.0, war.get("exhaustion_d", 0.0) + EXH_DRIFT_PER_TICK)
    for side in ("a", "d"):
        key = f"confidence_{side}"
        v = war.get(key, 0.5)
        war[key] = v + (0.5 - v) * 0.002


def should_sue_for_peace(war: dict) -> bool:
    """Either side breaking ends the war: the winner wins peace, the
    loser needs it."""
    score_a = war.get("confidence_a", 0.5) - war.get("exhaustion_a", 0.0)
    score_d = war.get("confidence_d", 0.5) - war.get("exhaustion_d", 0.0)
    return score_a < MORALE_PEACE_THRESHOLD or score_d < MORALE_PEACE_THRESHOLD


def apply_city_lost(war: dict, loser_side: str) -> None:
    """`loser_side` is 'a' or 'd' — the side whose city was captured."""
    winner = "d" if loser_side == "a" else "a"
    war[f"exhaustion_{loser_side}"] = min(1.0,
        war.get(f"exhaustion_{loser_side}", 0.0) + EXH_CITY_LOST)
    war[f"confidence_{loser_side}"] = max(-0.5,
        war.get(f"confidence_{loser_side}", 0.5) + CONF_CITY_LOST)
    war[f"exhaustion_{winner}"] = max(0.0,
        war.get(f"exhaustion_{winner}", 0.0) + EXH_CITY_TAKEN)
    war[f"confidence_{winner}"] = min(1.5,
        war.get(f"confidence_{winner}", 0.5) + CONF_CITY_TAKEN)


def apply_army_broken(war: dict, loser_side: str) -> None:
    winner = "d" if loser_side == "a" else "a"
    war[f"exhaustion_{loser_side}"] = min(1.0,
        war.get(f"exhaustion_{loser_side}", 0.0) + EXH_ARMY_BROKEN)
    war[f"confidence_{loser_side}"] = max(-0.5,
        war.get(f"confidence_{loser_side}", 0.5) + CONF_ARMY_BROKEN)
    war[f"confidence_{winner}"] = min(1.5,
        war.get(f"confidence_{winner}", 0.5) + CONF_ENEMY_BROKEN)


def apply_post_war_baseline(a: dict, b: dict) -> None:
    """Applied when a war is settled — leaves a small lasting grudge."""
    _symmetric_shift(a, b, REL_WAR_PEACE)
