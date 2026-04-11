"""Main per-tick simulation loop. Army logic lives in engine.combat,
city development in engine.city_dev."""

import heapq
import math
import random
from typing import List, Callable

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
from .civ import build_road, gen_city_name

from . import combat
from . import city_dev
from . import diplomacy


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
    if sc is not None and sc > civ.get("_settle_score", float("-inf")):
        civ["_settle_candidate"] = cell
        civ["_settle_score"] = sc


# ── Peace (restore pre-war borders, keep captured cities) ─────────────────

def _settle_peace(war, a, b, om, civs, tick, add_event):
    att_id, def_id = war["a_id"], war["d_id"]
    att  = next((c for c in civs if c["id"] == att_id), None)
    defn = next((c for c in civs if c["id"] == def_id), None)
    if not att or not defn:
        return

    pre_a = war.get("pre_ter_a", set())
    pre_d = war.get("pre_ter_d", set())
    cap_by_a = set(war.get("captured_cities_a", []))
    cap_by_d = set(war.get("captured_cities_d", []))

    att_city_cells = [ci["cell"] for ci in att["cities"]]
    def_city_cells = [ci["cell"] for ci in defn["cities"]]

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

    disputed = (att["territory"] | defn["territory"]) & (pre_a | pre_d)

    for cell in list(disputed):
        if cell in perm_to_att:
            defn["territory"].discard(cell)
            att["territory"].add(cell)
            om[cell] = att_id
        elif cell in perm_to_def:
            att["territory"].discard(cell)
            defn["territory"].add(cell)
            om[cell] = def_id
        elif cell in pre_a:
            defn["territory"].discard(cell)
            att["territory"].add(cell)
            om[cell] = att_id
        elif cell in pre_d:
            att["territory"].discard(cell)
            defn["territory"].add(cell)
            om[cell] = def_id

    # Prune cities that ended up outside their owner's territory
    att["cities"]  = [c for c in att["cities"]  if c["cell"] in att["territory"]]
    defn["cities"] = [c for c in defn["cities"] if c["cell"] in defn["territory"]]

    # Peace treaty disbands all armies
    war["armies_a"] = []
    war["armies_d"] = []

    add_event(f"🕊 Year {tick}: {a['name']} & {b['name']} made peace")
    a["events"].append(f"Year {a['age']}: Peace with {b['name']}")
    b["events"].append(f"Year {b['age']}: Peace with {a['name']}")


# ── Per-tick simulation driver ─────────────────────────────────────────────

def tick_sim(
    civs:   List[dict],
    ter:    list,
    res:    dict,
    om:     list,
    wars:   dict,
    rivers: dict,
    impr:   list,
    tick:   int,
    add_event: Callable[[str], None],
    params: dict,
) -> List[dict]:
    """Run one simulation step. Returns any newly-spawned civs (currently
    always empty — fragmentation is disabled)."""

    alive = [c for c in civs if c["alive"]]

    # ── Diplomacy ─────────────────────────────────────────────────────────
    # Cache border cells per civ once — the pair loop is O(C²).
    border_cache: dict = {c["id"]: border_cells(c["territory"]) for c in alive}
    civs_by_id: dict = {c["id"]: c for c in alive}

    # Drift relations + refresh power snapshots.
    diplomacy.tick_relations(alive, wars, border_cache)

    for i in range(len(alive)):
        for j in range(i + 1, len(alive)):
            a, b = alive[i], alive[j]
            k = war_key(a["id"], b["id"])
            at_war = k in wars

            border = any(bc in b["territory"] for bc in border_cache[a["id"]])

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
                        f"⚔ Year {tick}: {att['name']} declared WAR on {defn['name']}!"
                    )
                    att ["events"].append(f"Year {att ['age']}: War on {defn['name']}")
                    defn["events"].append(f"Year {defn['age']}: {att['name']} attacked")
                    combat.spawn_war_armies(att,  new_war, "a", impr, k)
                    combat.spawn_war_armies(defn, new_war, "d", impr, k)
                    declared = True
                    break

            if (not declared
                    and diplomacy.consider_alliance(a, b, wars, civs_by_id)):
                diplomacy.form_alliance(a, b)
                add_event(
                    f"🤝 Year {tick}: {a['name']} and {b['name']} formed an alliance"
                )
                a["events"].append(f"Year {a['age']}: Allied with {b['name']}")
                b["events"].append(f"Year {b['age']}: Allied with {a['name']}")

    # ── Per-civ tick ───────────────────────────────────────────────────────
    for civ in alive:
        civ["age"] += 1
        ore_out = stone_out = metal_out = trade_out = raw_gold = 0.0

        # ── Voronoi: assign each territory cell to nearest city ──────────
        city_cells_map: dict = {}
        for city in civ["cities"]:
            city_cells_map[city["cell"]] = []

        if civ["cities"]:
            for cell in civ["territory"]:
                best_city = None
                best_d = float("inf")
                for city in civ["cities"]:
                    d = dist(cell, city["cell"])
                    if d < best_d:
                        best_d = d
                        best_city = city["cell"]
                if best_city is not None:
                    city_cells_map[best_city].append(cell)

        # ── Per-city production ──────────────────────────────────────────
        farm_out = 0.0
        for city in civ["cities"]:
            city_food  = 0.0
            city_ore   = 0.0
            city_stone = 0.0
            city_gold  = 0.0
            smithery_cap = 0.0
            port_bonus   = 0.0

            city["near_river"] = cell_on_river(city["cell"], rivers)
            city["coastal"]    = cell_coastal(city["cell"], ter)

            assigned = city_cells_map.get(city["cell"], [])
            city["tiles"] = assigned
            city["farm_tiles"] = []

            for cell in assigned:
                raw = impr[cell]
                it  = imp_type(raw)
                lvl = imp_level(raw)
                on_river = cell_on_river(cell, rivers)
                riv = 2.0 if on_river else 1.0
                coast_mult = 1.5 if cell_coastal(cell, ter) else 1.0
                r = res.get(cell)

                if it == IMP.FARM:
                    food_val = (1.5 + lvl * 1.0) * riv * coast_mult
                    # Windmill neighbour bonus
                    wm_mult = 1.0
                    for n in neighbors(cell):
                        if 0 <= n < N:
                            n_raw = impr[n]
                            if imp_type(n_raw) == IMP.WINDMILL:
                                wm_mult += imp_level(n_raw) * 0.5
                    city_food += food_val * wm_mult
                    city["farm_tiles"].append(cell)
                elif it == IMP.WINDMILL:
                    city["farm_tiles"].append(cell)
                elif it == IMP.MINE:
                    if r == "stone":
                        city_stone += 0.5 + lvl * 0.5
                        city_gold  += 0.2 + lvl * 0.3
                    elif r == "iron":
                        city_ore  += 1.0 + lvl * 0.5
                        city_gold += 1.5
                    elif r in ("gold", "gems"):
                        city_gold += 3.0 + lvl * 1.0
                    else:
                        city_ore   += 0.5 + lvl * 0.25
                        city_stone += 0.2 + lvl * 0.25
                        city_gold  += 0.5
                    city["farm_tiles"].append(cell)
                elif it == IMP.LUMBER:
                    city_gold += 0.3
                elif it == IMP.PASTURE:
                    city_food += 1.5 * riv
                    city["farm_tiles"].append(cell)
                elif it == IMP.PORT:
                    port_bonus += lvl * 2.0
                    city["farm_tiles"].append(cell)
                elif it == IMP.SMITHERY:
                    smithery_cap += lvl * 2.0
                    city["farm_tiles"].append(cell)
                elif it == IMP.FISHERY:
                    # Fisheries: food from sea + a small trade bonus. Scale
                    # with level like farms, but slightly weaker per level.
                    city_food += (1.0 + lvl * 0.8) * coast_mult
                    port_bonus += lvl * 0.8
                    city["farm_tiles"].append(cell)
                elif it == IMP.FORT:
                    city["farm_tiles"].append(cell)

                t = ter[cell]
                if t in (T.PLAINS, T.GRASS):
                    city_food += 0.4 * riv * 0.5

                if   r == "wheat":  city_food += 2.0 * riv
                elif r == "fish":   city_food += 1.5 * coast_mult
                elif r == "gold":   city_gold += 2.0
                elif r == "gems":   city_gold += 1.5
                elif r == "iron":   city_ore  += 1.0
                elif r == "stone":  city_stone += 1.0
                elif r:             city_gold += 0.5

            # Refine ore to metal
            city_metal = min(city_ore, smithery_cap)
            city_ore  -= city_metal

            city["city_ore"]   = city_ore
            city["city_stone"] = city_stone
            city["city_metal"] = city_metal

            # Site bonuses
            city_riv_b   = 1.5 if city["near_river"] else 1.0
            city_coast_b = 1.3 if city["coastal"]    else 1.0
            is_mouth     = cell_river_mouth(city["cell"], ter, rivers)
            city["river_mouth"] = is_mouth
            mouth_b = 2.0 if is_mouth else 1.0
            city_food *= city_riv_b * city_coast_b

            city["food_production"] = round(city_food, 1)
            city["carrying_cap"]    = max(50, 50 + city_food * 18)

            # Trade potential (from assigned tile resources + pop)
            trade_res_bonus = 0.0
            for cell in assigned:
                r2 = res.get(cell)
                if   r2 in ("gold", "gems"):    trade_res_bonus += 8.0
                elif r2 in ("spices", "ivory"): trade_res_bonus += 6.0
                elif r2 in ("iron", "stone"):   trade_res_bonus += 3.0
                elif r2 == "horses":            trade_res_bonus += 2.0
            city["trade_potential"] = (
                city["population"] * 0.15 + trade_res_bonus + 4
            ) * city_riv_b * city_coast_b * mouth_b + port_bonus

            farm_out  += city_food
            ore_out   += city_ore
            stone_out += city_stone
            metal_out += city_metal
            raw_gold  += city_gold

            city["_city_gold"] = city_gold  # stash for wealth accumulation below

        # ── Road trade: connected cities share trade ─────────────────────
        road_graph: dict = {}
        city_set = {c["cell"] for c in civ["cities"]}
        for r in civ["roads"]:
            a2, b2 = r["from"], r["to"]
            rd = len(r["path"])
            if a2 in city_set and b2 in city_set:
                road_graph.setdefault(a2, []).append((b2, rd))
                road_graph.setdefault(b2, []).append((a2, rd))
            # Intermediate cities on the path: enumerate once (was O(|path|²)
            # via repeated r["path"].index(pc)).
            for idx, pc in enumerate(r["path"]):
                if pc in city_set and pc != a2 and pc != b2:
                    if a2 in city_set:
                        road_graph.setdefault(a2, []).append((pc, idx + 1))
                        road_graph.setdefault(pc, []).append((a2, idx + 1))
                    if b2 in city_set:
                        d2 = rd - idx
                        road_graph.setdefault(b2, []).append((pc, d2))
                        road_graph.setdefault(pc, []).append((b2, d2))

        tp_map = {c["cell"]: c["trade_potential"] for c in civ["cities"]}

        # Proper Dijkstra over the road graph (edge weights vary by segment
        # length, so the old list.pop(0) Bellman-Ford-ish relaxation was both
        # incorrect on ties and O(V²) per step).
        for city in civ["cities"]:
            cc = city["cell"]
            road_trade = 0.0
            best_dist: dict = {cc: 0}
            heap: list = [(0, cc)]
            while heap:
                d_so_far, cur = heapq.heappop(heap)
                if d_so_far > best_dist.get(cur, float("inf")):
                    continue
                for (nb_city, seg_d) in road_graph.get(cur, []):
                    total_d = d_so_far + seg_d
                    if total_d < best_dist.get(nb_city, float("inf")):
                        best_dist[nb_city] = total_d
                        heapq.heappush(heap, (total_d, nb_city))
            for other_cell, road_d in best_dist.items():
                if other_cell == cc or road_d <= 0:
                    continue
                road_trade += tp_map.get(other_cell, 0) / (1 + road_d * 0.08)

            city["road_trade"] = road_trade
            city["trade"]      = city["trade_potential"] + road_trade
            city["wealth"]     = city.get("wealth", 0.0) + city["trade"] * 0.08 + city.pop("_city_gold", 0.0) * 0.02
            trade_out += city["trade"]

        civ["farm_output"]  = farm_out
        civ["ore_output"]   = ore_out
        civ["stone_output"] = stone_out
        civ["metal_output"] = metal_out
        civ["trade_output"] = trade_out

        civ["food"]  += farm_out * 0.8 - civ["population"] * 0.015
        civ["gold"]  += raw_gold * 0.3 + trade_out * 0.015 + len(civ["territory"]) * 0.015
        civ["wealth"] = max(0, civ["gold"] * 0.3 + trade_out * 0.5 + (ore_out + stone_out + metal_out * 3) * 0.2)

        # ── Fort metal upkeep ─────────────────────────────────────────────
        civ.setdefault("metal_stock", 0.0)
        civ["metal_stock"] += metal_out * 0.6
        fort_cells = [
            c for c in civ["territory"]
            if 0 <= c < N and imp_type(impr[c]) == IMP.FORT
        ]
        total_upkeep = 0.0
        for fc in fort_cells:
            total_upkeep += FORT_METAL_UPKEEP * imp_level(impr[fc])
        if civ["metal_stock"] >= total_upkeep:
            civ["metal_stock"] -= total_upkeep
        else:
            civ["metal_stock"] = 0.0
            if fort_cells:
                fc = random.choice(fort_cells)
                new_raw = downgrade_imp(impr[fc])
                if new_raw == IMP.NONE:
                    impr[fc] = IMP.NONE
                    civ["events"].append(f"Year {civ['age']}: A Fort fell into ruin — no metal")
                else:
                    impr[fc] = new_raw
                    civ["events"].append(f"Year {civ['age']}: A Fort was downgraded — supply shortage")
        civ["metal_stock"] = min(civ["metal_stock"], 80.0)

        # ── Logistic population growth ────────────────────────────────────
        GROWTH_RATE = 0.008
        for city in civ["cities"]:
            p = city["population"]
            K = city["carrying_cap"]
            dp = GROWTH_RATE * p * (K - p) / max(K, 1)
            city["population"] = p + dp

        # Abandon cities that drop below viability (capital never abandons)
        surviving_cities = []
        for i, city in enumerate(civ["cities"]):
            if i == 0 or city["population"] >= 7:
                surviving_cities.append(city)
            else:
                civ["events"].append(f"Year {civ['age']}: {city['name']} was abandoned due to low population.")
        civ["cities"] = surviving_cities

        civ["population"] = sum(c["population"] for c in civ["cities"])
        civ["tech"]      += 0.007 * math.log2(civ["population"] + 1) * (1 + len(civ["cities"]) * 0.08)
        civ["culture"]   += 0.004 * math.log2(len(civ["territory"]) + 1)
        civ["military"]   = max(8.0, civ["population"] * 0.14 + civ["tech"] * 2 + (metal_out * 4.0 + ore_out * 0.5) * 0.1)
        civ["integrity"]  = min(1.0, max(0.1,
            civ["integrity"]
            + civ["culture"] * 0.0002
            - (0.001  if len(civ["territory"]) > 80 else 0)
            - (0.0004 if civ["age"] > 100           else 0)
            + (0.0001 if civ["wealth"] > 100        else 0)
        ))

        # ── City development (investment, focus HMM, placement) ──────────
        city_dev.tick_city_development(civ, wars, ter, res, rivers, impr, tick, om)

        # ── City founding ────────────────────────────────────────────────
        all_city_cells = [ci["cell"] for other in alive for ci in other["cities"]]
        at_war = any(
            w["a_id"] == civ["id"] or w["d_id"] == civ["id"]
            for w in wars.values()
        )
        largest = max((c["population"] for c in civ["cities"]), default=0)
        should_found = (
            not at_war
            and len(civ["territory"]) > (len(civ["cities"]) + 1) * 40
            and largest > 200
            and civ["gold"] > 40
            and len(civ["cities"]) < len(civ["territory"]) // 35 + 1
        )
        best_cell = civ.get("_settle_candidate", -1)
        if should_found and best_cell != -1 and best_cell in civ["territory"]:
            sc = _settle_score(best_cell, ter, rivers, res, all_city_cells, params)
            if sc is not None and sc > 0:
                cn = gen_city_name(civ["onom"])
                new_city = {
                    "cell":         best_cell,
                    "name":         cn,
                    "population":   25.0,
                    "is_capital":   False,
                    "founded":      tick,
                    "trade":        3.0,
                    "wealth":       3.0,
                    "focus":        random.choice([
                        FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE, FOCUS.TRADE,
                    ]),
                    "near_river":   cell_on_river(best_cell, rivers),
                    "coastal":      cell_coastal(best_cell, ter),
                    "food_production": 0.0,
                    "carrying_cap":   50,
                    "tiles":        [],
                    "farm_tiles":   [],
                    "last_dmg_tick": -999,
                }
                new_city["max_hp"] = combat.city_max_hp(new_city, impr)
                new_city["hp"]     = new_city["max_hp"]
                civ["cities"].append(new_city)
                civ["gold"] -= 20
                civ["events"].append(f"Year {civ['age']}: Founded {cn}")
                add_event(f"🏘 Year {tick}: {civ['name']} founded {cn}")
                civ.pop("_settle_candidate", None)
                civ.pop("_settle_score", None)

        # ── Roads ────────────────────────────────────────────────────────
        if len(civ["cities"]) >= 2 and civ["gold"] > 12 and tick % 50 == 0:
            build_road(civ, ter)

        # ── Expansion ────────────────────────────────────────────────────
        borders = border_cells(civ["territory"])
        pocket_targets = [
            c for c in borders
            if 0 <= c < N and is_land(ter, c) and om[c] == 0
            and sum(1 for n in neighbors(c) if n in civ["territory"]) >= 3
        ]
        for c in pocket_targets:
            civ["territory"].add(c)
            om[c] = civ["id"]
            _eval_settle_candidate(civ, c, ter, rivers, res, all_city_cells, params)

        if (civ["food"] > 15
                and civ["population"] > len(civ["territory"]) * 2
                and random.random() < civ["expansion_rate"] * 0.6):
            borders = border_cells(civ["territory"])
            targets = [
                c for c in borders
                if 0 <= c < N and is_land(ter, c) and om[c] == 0
            ]
            targets.sort(key=lambda c: (
                sum(1 for n in neighbors(c) if n in civ["territory"]) * 5
                + (4 if c in res else 0)
                + (params["river_pref"] * 0.5 if cell_on_river(c, rivers) else 0)
                + (2 if ter[c] in (T.PLAINS, T.GRASS) else 0)
                - (3 if ter[c] >= T.MTN else 0)
                + random.random() * 3.0
            ), reverse=True)
            cnt = min(int(len(civ["territory"]) * 0.09) + 1, len(targets), 9)
            for c in targets[:cnt]:
                civ["territory"].add(c)
                om[c] = civ["id"]
                _eval_settle_candidate(civ, c, ter, rivers, res, all_city_cells, params)

        # ── Housekeeping (no encirclement, no front-line smoothing) ──────
        # Non-contiguous nations are legal. Captured cities keep whatever
        # territory the combat subsystem gave them. We only prune cities
        # whose cell is no longer ours, and restore a capital if needed.
        civ["cities"] = [c for c in civ["cities"] if c["cell"] in civ["territory"]]
        civ["roads"]  = [r for r in civ["roads"]  if r["from"] in civ["territory"] and r["to"] in civ["territory"]]

        if civ["cities"] and not any(c["is_capital"] for c in civ["cities"]):
            civ["cities"][0]["is_capital"] = True
            civ["capital"] = civ["cities"][0]["cell"]

        civ_at_war = any(
            w["a_id"] == civ["id"] or w["d_id"] == civ["id"] for w in wars.values()
        )

        # Surrender: no cities and at war → absorbed by enemy
        if not civ["cities"] and civ_at_war:
            for wk, war in list(wars.items()):
                if war["a_id"] == civ["id"] or war["d_id"] == civ["id"]:
                    conqueror_id = war["d_id"] if war["a_id"] == civ["id"] else war["a_id"]
                    conqueror = next((c for c in alive if c["id"] == conqueror_id), None)
                    if conqueror:
                        for c in list(civ["territory"]):
                            civ["territory"].discard(c)
                            conqueror["territory"].add(c)
                            om[c] = conqueror_id
                        civ["alive"] = False
                        diplomacy.break_alliances_with(civ, civs_by_id)
                        add_event(f"🏳 Year {tick}: {civ['name']} surrendered to {conqueror['name']}!")
                        civ["events"].append(f"Year {civ['age']}: Surrendered to {conqueror['name']}")
                        conqueror["events"].append(f"Year {conqueror['age']}: Conquered {civ['name']}")
                    wars.pop(wk, None)
                    break
            continue

        # Survival safeguards
        if civ["food"] < -30:
            civ["food"] = 0
            add_event(f"🍞 Year {tick}: {civ['name']} endured a famine")
        if civ["population"] < 15:
            civ["population"] = 15
            for city in civ["cities"]:
                city["population"] = max(city["population"], 10)
        if len(civ["territory"]) < 4 and civ["cities"]:
            cap = civ["cities"][0]["cell"]
            sx, sy = cap % W, cap // W
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    ni = (sy + dy) * W + (sx + dx)
                    if 0 <= ni < N and is_land(ter, ni) and om[ni] == 0:
                        civ["territory"].add(ni)
                        om[ni] = civ["id"]

        # Peacetime re-foundation if the civ lost all cities but still has land
        if not civ["cities"] and civ["territory"] and not civ_at_war:
            all_other_cities = [
                ci["cell"] for other in alive if other["id"] != civ["id"]
                for ci in other["cities"]
            ]
            best_refound = None
            best_min_d = -1
            for cell in list(civ["territory"])[:80]:
                if ter[cell] in (T.MTN, T.SNOW) or ter[cell] <= T.COAST:
                    continue
                md = min((dist(cell, oc) for oc in all_other_cities), default=999)
                if md > best_min_d:
                    best_min_d = md
                    best_refound = cell
            cap_cell = best_refound if best_refound is not None else next(iter(civ["territory"]))
            cn = gen_city_name(civ["onom"])
            refounded = {
                "cell": cap_cell, "name": cn, "population": 20.0,
                "is_capital": True, "founded": tick, "trade": 3.0,
                "wealth": 5.0,
                "focus": random.choice([
                    FOCUS.FARMING, FOCUS.MINING, FOCUS.DEFENSE, FOCUS.TRADE,
                ]),
                "near_river": cell_on_river(cap_cell, rivers),
                "coastal":    cell_coastal(cap_cell, ter),
                "food_production": 0.0, "carrying_cap": 50,
                "tiles": [], "farm_tiles": [],
                "last_dmg_tick": -999,
            }
            refounded["max_hp"] = combat.city_max_hp(refounded, impr)
            refounded["hp"]     = refounded["max_hp"]
            civ["cities"]  = [refounded]
            civ["capital"] = cap_cell
            add_event(f"🏛 Year {tick}: {civ['name']} refounded {cn}")

    # ── Army tick (movement, behavior, combat, fort respawn) ──────────────
    combat.tick_armies(alive, wars, ter, impr, om, tick, add_event)

    # ── City HP regen ─────────────────────────────────────────────────────
    for civ in alive:
        for city in civ["cities"]:
            combat.ensure_city_hp(city, impr)
            # Recompute max_hp in case a fort was built/destroyed under the city
            city["max_hp"] = combat.city_max_hp(city, impr)
            if city["hp"] < city["max_hp"] and tick - city.get("last_dmg_tick", -999) > 8:
                city["hp"] = min(city["max_hp"], city["hp"] + CITY_HP_REGEN)

    # Fragmentation disabled — nations may be non-contiguous.
    return []
