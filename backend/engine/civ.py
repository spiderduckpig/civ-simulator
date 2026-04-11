import random
import math
import os
import json
from typing import List, Optional

from .constants import (
    W, H, N, T, IMP, CAN_FARM, CIV_PALETTE, FOCUS
)
from .helpers import neighbors, is_land, centroid, find_path, war_key, cell_on_river
from .improvements import make_imp
from .mapgen import cell_coastal, cell_river_mouth

# ── Onomastics loader ─────────────────────────────────────────────────────────

def load_onomastics():
    onoms = []
    folder = os.path.join(os.path.dirname(__file__), "onomastics")
    for fname in os.listdir(folder):
        if fname.endswith(".json"):
            with open(os.path.join(folder, fname), "r", encoding="utf-8") as f:
                onoms.append(json.load(f))
    return onoms if onoms else [{"PRE":["A"],"MID_S":["-"],"SUF_S":["A"],"CPRE":[""],"CSUF":["ton"],"LF":["A"],"LL":["the A"]}]

ALL_ONOMASTICS = load_onomastics()

# ── Name generators ───────────────────────────────────────────────────────────

def _pick(arr):
    return arr[int(random.random() * len(arr))]


def gen_civ_name(onom: dict) -> str:
    pre = _pick(onom["PRE"]).strip()
    suf = _pick(onom["SUF_S"]).strip()
    # Only inject a middle syllable when the base name is short enough
    # to keep the result readable (avoids "Alsolnoeiliriel"-style mashes).
    if len(pre) + len(suf) <= 5 and random.random() < 0.45:
        mid = _pick(onom["MID_S"]).strip()
    else:
        mid = ""
    return (pre + mid + suf).capitalize()


def gen_city_name(onom: dict) -> str:
    pre = _pick(onom["PRE"]).strip().lower()
    suf = _pick(onom["CSUF"]).strip().lower()
    root = pre + suf
    cpre = _pick(onom["CPRE"])
    # Ensure a clean space between an optional prefix and the root so
    # ``.title()`` capitalises both words instead of producing one long blob.
    if cpre and not cpre.endswith(" "):
        cpre = cpre + " "
    return (cpre + root).title()


def gen_leader_name(onom: dict) -> str:
    return _pick(onom["LF"]) + " " + _pick(onom["LL"])


# Used for army commanders — first name plus a martial title rather than the
# civic-style epithet on civ leaders.
_COMMANDER_RANKS = [
    "General", "Marshal", "Captain", "Warlord", "Lord", "Commander",
    "Hetman", "Strategos", "Voivode", "Khan",
]


def gen_commander_name(onom: dict) -> str:
    rank = _COMMANDER_RANKS[int(random.random() * len(_COMMANDER_RANKS))]
    return f"{rank} {_pick(onom['LF'])}"


# ── Colour palette counter (module-level state, reset on new game) ────────────

_civ_color_idx = 0
_civ_id_counter = 1
_army_id_counter = 1


def reset_counters():
    global _civ_color_idx, _civ_id_counter, _army_id_counter
    _civ_color_idx = 0
    _civ_id_counter = 1
    _army_id_counter = 1


def next_army_id() -> int:
    global _army_id_counter
    aid = _army_id_counter
    _army_id_counter += 1
    return aid


def _next_color() -> str:
    global _civ_color_idx
    color = CIV_PALETTE[_civ_color_idx % len(CIV_PALETTE)]
    _civ_color_idx += 1
    return color


def _next_id() -> int:
    global _civ_id_counter
    cid = _civ_id_counter
    _civ_id_counter += 1
    return cid


# ── Road building (MST toward capital) ───────────────────────────────────────

def build_road(civ: dict, ter: list) -> None:
    """Build a road between the highest-priority unconnected city pair.
    Pathfinding bends toward intermediate cities automatically via cost map.
    Existing road cells are free to traverse, so new roads reuse old ones."""
    if len(civ["cities"]) < 2:
        return

    city_set = {c["cell"] for c in civ["cities"]}

    # Collect all cells that are already paved (existing roads)
    road_cells = set()
    for r in civ["roads"]:
        road_cells.update(r["path"])

    # Build city adjacency from existing roads — a road connects any two
    # cities whose cells appear on its path (not just endpoints)
    adj: dict = {c["cell"]: set() for c in civ["cities"]}
    for r in civ["roads"]:
        # Find all cities that lie on this road's path
        cities_on_road = [c for c in r["path"] if c in city_set]
        for i in range(len(cities_on_road)):
            for j in range(i + 1, len(cities_on_road)):
                adj[cities_on_road[i]].add(cities_on_road[j])
                adj[cities_on_road[j]].add(cities_on_road[i])

    # BFS to find connected components among cities
    visited = set()
    comp_of = {}  # city_cell -> component_id
    comp_id = 0
    for c in civ["cities"]:
        if c["cell"] in visited:
            continue
        queue = [c["cell"]]
        while queue:
            cur = queue.pop()
            if cur in visited:
                continue
            visited.add(cur)
            comp_of[cur] = comp_id
            for nb in adj.get(cur, set()):
                if nb not in visited:
                    queue.append(nb)
        comp_id += 1

    # Score all unconnected city pairs by combined trade potential
    candidates = []
    tp_map = {c["cell"]: c.get("trade_potential", c["population"] * 0.15 + 4) for c in civ["cities"]}
    for i, ci in enumerate(civ["cities"]):
        for cj in civ["cities"][i + 1:]:
            if comp_of.get(ci["cell"]) == comp_of.get(cj["cell"]):
                continue
            score = tp_map[ci["cell"]] + tp_map[cj["cell"]]
            candidates.append((score, ci["cell"], cj["cell"]))

    if not candidates:
        return

    # Build the highest-priority road.
    # Existing road cells are free to traverse so paths naturally merge onto
    # the existing network rather than building redundant parallel roads.
    candidates.sort(reverse=True)
    for _, cell_a, cell_b in candidates:
        path = find_path(cell_a, cell_b, civ["territory"], ter, city_set, road_cells)
        if path and len(path) < 80:
            civ["roads"].append({"from": cell_a, "to": cell_b, "path": path})
            civ["gold"] -= 8
            return


# ── Spot finding for new civs ─────────────────────────────────────────────────

def find_spot(ter: list, civs: list, rng, rivers: dict = None, om: list = None) -> int:
    # Build set of all claimed cells for fast lookup
    claimed = set()
    if om:
        for c in civs:
            claimed.update(c["territory"])
    best = -1
    best_score = -1
    for a in range(600):
        x = int(5 + ((rng(a * 3.7, a * 2.1) + 1) / 2) * (W - 10))
        y = int(5 + ((rng(a * 1.3, a * 4.9) + 1) / 2) * (H - 10))
        i = y * W + x
        t = ter[i]
        if T.BEACH <= t <= T.GRASS:
            # Reject if this cell or any neighbor is owned
            if om and (om[i] != 0 or i in claimed):
                continue
            ok = True
            for c in civs:
                cx, cy = centroid(c["territory"])
                if abs(cx - x) + abs(cy - y) < 18:
                    ok = False
                    break
            if ok:
                score = 1
                if rivers and cell_river_mouth(i, ter, rivers):
                    score += 50
                elif rivers and cell_on_river(i, rivers):
                    score += 5
                if score > best_score:
                    best_score = score
                    best = i
                    if score > 40:  # found a river mouth, take it
                        return best
    return best


# ── Civ factory ───────────────────────────────────────────────────────────────

def make_civ(
    ter: list, alive_civs: list, rivers: dict, rng, tick: int,
    om: list = None, impr: list = None,
) -> Optional[dict]:
    spot = find_spot(ter, alive_civs, rng, rivers, om)
    if spot == -1:
        return None

    sx, sy = spot % W, spot // W
    territory = set()
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            if abs(dx) + abs(dy) > 3:
                continue
            ni = (sy + dy) * W + (sx + dx)
            if is_land(ter, ni):
                territory.add(ni)

    # Plant a starter fort on an empty land cell next to the capital so
    # every civ has somewhere to rally and spawn armies. Walkable tiles
    # (plains/grass/forest/hills) only — no mountain-top forts.
    if impr is not None:
        walkable_starts = (T.PLAINS, T.GRASS, T.FOREST, T.HILLS)
        fort_placed = False
        # Prefer an adjacent cell first; fall back to any territory cell.
        for n in neighbors(spot):
            if (0 <= n < N and n in territory
                    and ter[n] in walkable_starts and impr[n] == IMP.NONE):
                impr[n] = make_imp(IMP.FORT, 1)
                fort_placed = True
                break
        if not fort_placed:
            for cell in territory:
                if (cell != spot and ter[cell] in walkable_starts
                        and impr[cell] == IMP.NONE):
                    impr[cell] = make_imp(IMP.FORT, 1)
                    break

    onom = _pick(ALL_ONOMASTICS)
    city_name = gen_city_name(onom)
    cid = _next_id()

    return {
        "id":          cid,
        "name":        gen_civ_name(onom),
        "leader":      gen_leader_name(onom),
        "onom":        onom,
        "color":       _next_color(),
        "capital":     spot,
        "territory":   territory,
        "cities": [{
            "cell":           spot,
            "name":           city_name,
            "population":     80.0,
            "is_capital":     True,
            "founded":        tick,
            "trade":          10.0,
            "wealth":         20.0,
            "focus":          random.choice([FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE]),
            "near_river":     cell_on_river(spot, rivers),
            "coastal":        cell_coastal(spot, ter),
            "food_production": 0.0,
            "carrying_cap":   200,
            "tiles":          [],
            "farm_tiles":     [],
            # HP / siege state — capitals are tougher
            "hp":             115.0,
            "max_hp":         115.0,
            "last_dmg_tick":  -999,
        }],
        "population":     100.0,
        "military":       20.0,
        "gold":           50.0,
        "food":           80.0,
        "tech":           1.0,
        "culture":        1.0,
        "age":            0,
        "alive":          True,
        "integrity":      0.6 + random.random() * 0.35,
        "aggressiveness": 0.2 + random.random() * 0.7,
        "relations":      {},
        "allies":         set(),
        "power":          0.0,
        "wealth":         30.0,
        "farm_output":    0.0,
        "ore_output":     0.0,
        "stone_output":   0.0,
        "metal_output":   0.0,
        "trade_output":   0.0,
        "expansion_rate": 0.35 + random.random() * 0.4,
        "events":         [f"Year 0: {city_name} founded"],
        "parent_name":    None,
        "roads":          [],
        "metal_stock":    5.0,           # accumulated metal for fort upkeep
        "fort_cooldowns": {},            # origin_cell -> tick at which fort can respawn an army
    }
