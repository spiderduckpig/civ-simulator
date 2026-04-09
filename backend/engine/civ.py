import random
import math
from typing import List, Optional

from .constants import (
    W, H, N, T, IMP, CAN_FARM, MIN_CITY_DIST, CIV_PALETTE,
    PRE, MID_S, SUF_S, CPRE, CSUF, LF, LL,
)
from .helpers import neighbors, is_land, dist, centroid, find_path, war_key
from .mapgen import cell_on_river, cell_coastal


# ── Name generators ───────────────────────────────────────────────────────────

def _pick(arr):
    return arr[int(random.random() * len(arr))]


def gen_civ_name() -> str:
    mid = _pick(MID_S) if random.random() > 0.35 else ""
    return _pick(PRE) + mid + _pick(SUF_S)


def gen_city_name() -> str:
    return _pick(CPRE) + _pick(PRE).lower() + _pick(CSUF)


def gen_leader_name() -> str:
    return _pick(LF) + " " + _pick(LL)


# ── Colour palette counter (module-level state, reset on new game) ────────────

_civ_color_idx = 0
_civ_id_counter = 1


def reset_counters():
    global _civ_color_idx, _civ_id_counter
    _civ_color_idx = 0
    _civ_id_counter = 1


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
    if len(civ["cities"]) < 2:
        return

    cap = next((c for c in civ["cities"] if c["is_capital"]), None)
    if not cap:
        return

    # Find which city nodes are connected to the capital via existing roads
    conn_set = {cap["cell"]}
    changed = True
    while changed:
        changed = False
        for r in civ["roads"]:
            if r["from"] in conn_set and r["to"] not in conn_set:
                conn_set.add(r["to"])
                changed = True
            elif r["to"] in conn_set and r["from"] not in conn_set:
                conn_set.add(r["from"])
                changed = True

    # Pick the disconnected city closest to a connected city
    best_from = best_to = -1
    best_d = float("inf")
    for ci in civ["cities"]:
        if ci["cell"] in conn_set:
            continue
        for cj in civ["cities"]:
            if cj["cell"] not in conn_set:
                continue
            d = dist(ci["cell"], cj["cell"])
            if d < best_d:
                best_d = d
                best_from = cj["cell"]
                best_to   = ci["cell"]

    if best_from == -1:
        return

    path = find_path(best_from, best_to, civ["territory"], ter)
    if path and len(path) < 50:
        civ["roads"].append({"from": best_from, "to": best_to, "path": path})
        civ["gold"] -= 8


# ── Spot finding for new civs ─────────────────────────────────────────────────

def find_spot(ter: list, civs: list, rng) -> int:
    for a in range(600):
        x = int(5 + ((rng(a * 3.7, a * 2.1) + 1) / 2) * (W - 10))
        y = int(5 + ((rng(a * 1.3, a * 4.9) + 1) / 2) * (H - 10))
        i = y * W + x
        t = ter[i]
        if T.BEACH <= t <= T.GRASS:
            ok = True
            for c in civs:
                cx, cy = centroid(c["territory"])
                if abs(cx - x) + abs(cy - y) < 18:
                    ok = False
                    break
            if ok:
                return i
    return -1


# ── Civ factory ───────────────────────────────────────────────────────────────

def make_civ(ter: list, alive_civs: list, rivers: dict, rng, tick: int) -> Optional[dict]:
    spot = find_spot(ter, alive_civs, rng)
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

    city_name = gen_city_name()
    cid = _next_id()

    return {
        "id":          cid,
        "name":        gen_civ_name(),
        "leader":      gen_leader_name(),
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
            "near_river":     cell_on_river(spot, rivers),
            "coastal":        cell_coastal(spot, ter),
            "food_production": 0.0,
            "carrying_cap":   200,
            "tiles":          [],
            "farm_tiles":     [],
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
        "peacefulness":   0.2 + random.random() * 0.6,
        "wealth":         30.0,
        "farm_output":    0.0,
        "mine_output":    0.0,
        "trade_output":   0.0,
        "expansion_rate": 0.35 + random.random() * 0.4,
        "events":         [f"Year 0: {city_name} founded"],
        "parent_name":    None,
        "roads":          [],
    }
