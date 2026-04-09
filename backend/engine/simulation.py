import math
import random
from typing import List, Callable, Dict

from .constants import W, H, N, T, IMP, CAN_FARM
from .helpers import (
    neighbors, is_land, border_cells, centroid, dist,
    war_key, find_regions, best_improvement,
)
from .mapgen import cell_on_river, cell_coastal
from .civ import build_road, gen_city_name, _next_id, _next_color, gen_civ_name, gen_leader_name


def tick_sim(
    civs:   List[dict],
    ter:    list,
    res:    dict,
    om:     list,       # ownership map: cell -> civ_id (0 = unclaimed)
    wars:   dict,       # war_key -> {a_id, d_id, start_tick}
    rivers: dict,
    impr:   list,
    tick:   int,
    add_event: Callable[[str], None],
    params: dict,
) -> List[dict]:
    """Run one simulation step. Returns any newly-spawned civs (from fragmentation)."""

    alive = [c for c in civs if c["alive"]]

    # ── Diplomacy ─────────────────────────────────────────────────────────────
    for i in range(len(alive)):
        for j in range(i + 1, len(alive)):
            a, b = alive[i], alive[j]
            k = war_key(a["id"], b["id"])
            at_war = k in wars

            # Check shared border
            border = False
            for bc in border_cells(a["territory"]):
                if bc in b["territory"]:
                    border = True
                    break

            if not at_war and border:
                agg = (1 - a["peacefulness"] + 1 - b["peacefulness"]) / 2
                size_bonus = 0.004 if a["territory"].__len__() > len(b["territory"]) * 2 else 0
                if random.random() < agg * 0.006 + size_bonus:
                    att = a if a["military"] > b["military"] else b
                    defn = b if att is a else a
                    wars[k] = {"a_id": att["id"], "d_id": defn["id"], "start_tick": tick}
                    add_event(f"⚔ Year {tick}: {att['name']} declared WAR on {defn['name']}!")
                    att["events"].append(f"Year {att['age']}: War on {defn['name']}")
                    defn["events"].append(f"Year {defn['age']}: {att['name']} attacked")

            if at_war:
                war = wars[k]
                dur = tick - war["start_tick"]
                low_mil = a["military"] < 15 or b["military"] < 15
                peace_chance = (
                    (0.012 + (dur - 20) * 0.002 if dur > 20 else 0)
                    + (0.04 if low_mil else 0)
                    + (a["peacefulness"] + b["peacefulness"]) * 0.003
                )
                if random.random() < peace_chance:
                    del wars[k]
                    add_event(f"🕊 Year {tick}: {a['name']} & {b['name']} made peace")
                    a["events"].append(f"Year {a['age']}: Peace with {b['name']}")
                    b["events"].append(f"Year {b['age']}: Peace with {a['name']}")

    # ── Per-civ tick ───────────────────────────────────────────────────────────
    for civ in alive:
        civ["age"] += 1
        mine_out = trade_out = raw_gold = 0.0

        # ── Assign tiles to nearest city & compute per-city food ──────────
        # Each city gets: its own cell + all territory cells closest to it
        city_cells = {}   # city_cell -> list of territory cells
        for city in civ["cities"]:
            city_cells[city["cell"]] = []

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
                    city_cells[best_city].append(cell)

        # Compute per-city food production and carrying capacity
        farm_out = 0.0
        for city in civ["cities"]:
            city_food = 0.0
            city_mine = 0.0
            city_gold = 0.0
            city["near_river"] = cell_on_river(city["cell"], rivers)
            city["coastal"]    = cell_coastal(city["cell"], ter)

            assigned = city_cells.get(city["cell"], [])
            city["tiles"] = assigned
            city["farm_tiles"] = []

            for cell in assigned:
                imp = impr[cell]
                on_river = cell_on_river(cell, rivers)
                riv = 2.0 if on_river else 1.0
                coast_mult = 1.5 if cell_coastal(cell, ter) else 1.0

                if imp == IMP.FARM:
                    food_val = 2.5 * riv * coast_mult
                    city_food += food_val
                    city["farm_tiles"].append(cell)
                elif imp == IMP.MINE:
                    city_mine += 2.0; city_gold += 1.5
                elif imp == IMP.LUMBER:
                    city_gold += 0.3
                elif imp == IMP.QUARRY:
                    city_mine += 1.0; city_gold += 0.5
                    city["farm_tiles"].append(cell)  # quarries also shown
                elif imp == IMP.PASTURE:
                    city_food += 1.5 * riv
                    city["farm_tiles"].append(cell)

                t = ter[cell]
                if t in (T.PLAINS, T.GRASS):
                    city_food += 0.4 * riv * 0.5

                r = res.get(cell)
                if r == "wheat":    city_food += 2.0 * riv
                elif r == "fish":   city_food += 1.5 * coast_mult
                elif r == "gold":   city_gold += 2.0
                elif r == "gems":   city_gold += 1.5
                elif r in ("iron", "stone"): city_mine += 1.0
                elif r:             city_gold += 0.5

            # River/coast bonuses on the city itself
            city_riv_b   = 1.5 if city["near_river"] else 1.0
            city_coast_b = 1.3 if city["coastal"]    else 1.0
            city_food *= city_riv_b * city_coast_b

            # Carrying capacity: base 50 + food production scaled up
            city["food_production"] = round(city_food, 1)
            city["carrying_cap"]    = max(50, 50 + city_food * 18)

            # Trade
            road_conns = sum(1 for r in civ["roads"] if r["from"] == city["cell"] or r["to"] == city["cell"])
            city["trade"] = (4 + road_conns * 6 + city["population"] * 0.04) * city_riv_b * city_coast_b
            city["wealth"] = min(city["wealth"] + city["trade"] * 0.008 + city_gold * 0.001, 9999)

            farm_out  += city_food
            mine_out  += city_mine
            raw_gold  += city_gold
            trade_out += city["trade"]

        civ["farm_output"]  = farm_out
        civ["mine_output"]  = mine_out
        civ["trade_output"] = trade_out

        civ["food"]   += farm_out * 0.3 - civ["population"] * 0.025
        civ["gold"]   += raw_gold * 0.3 + trade_out * 0.015 + len(civ["territory"]) * 0.015
        civ["wealth"]  = max(0, civ["gold"] * 0.3 + trade_out * 0.5 + mine_out * 0.2)

        # ── Logistic population growth per city ───────────────────────────
        # dp/dt = r * p * (K - p) / K  (discrete approximation)
        GROWTH_RATE = 0.008
        for city in civ["cities"]:
            p = city["population"]
            K = city["carrying_cap"]
            dp = GROWTH_RATE * p * (K - p) / max(K, 1)
            # Trade bonus on top of logistic growth
            dp += city["trade"] * 0.0003 * p * max(0, (K - p) / max(K, 1))
            if civ["food"] < civ["population"] * 0.2:
                dp = min(dp, 0)  # no growth during famine
                dp -= p * 0.003  # slow decline
            city["population"] = max(5, p + dp)

        civ["population"]  = sum(c["population"] for c in civ["cities"])
        civ["tech"]       += 0.007 * math.log2(civ["population"] + 1) * (1 + len(civ["cities"]) * 0.08)
        civ["culture"]    += 0.004 * math.log2(len(civ["territory"]) + 1)
        civ["military"]    = max(8.0, civ["population"] * 0.14 + civ["tech"] * 2 + mine_out * 0.1)
        civ["integrity"]   = min(1.0, max(0.1,
            civ["integrity"]
            + civ["culture"] * 0.0002
            - (0.001 if len(civ["territory"]) > 80 else 0)
            - (0.0004 if civ["age"] > 100 else 0)
            + (0.0001 if civ["wealth"] > 100 else 0)
        ))

        # Build improvements
        if tick % 3 == 0 and civ["gold"] > 8 and civ["territory"]:
            cells = list(civ["territory"])
            for _ in range(4):
                c = cells[int(random.random() * len(cells))]
                if impr[c] == IMP.NONE:
                    bi = best_improvement(ter, res, c)
                    if bi != IMP.NONE:
                        impr[c] = bi
                        civ["gold"] -= 1.5
                        break

        # City founding — suppress during active wars to prevent spam
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
        if should_found and civ["territory"]:
            best_cell = -1
            best_score = float("-inf")
            cells = list(civ["territory"])
            for _ in range(60):
                c = cells[int(random.random() * len(cells))]
                t2 = ter[c]
                if t2 in (T.MTN, T.SNOW, T.DESERT) or t2 <= T.COAST:
                    continue
                # Check distance against ALL cities from ALL civs
                too_close = False
                for other in alive:
                    for ci in other["cities"]:
                        if dist(c, ci["cell"]) < 9:
                            too_close = True
                            break
                    if too_close:
                        break
                if too_close:
                    continue
                min_d = min((dist(c, ci["cell"]) for other in alive for ci in other["cities"]), default=0)
                score = min_d * 0.5
                if cell_on_river(c, rivers): score += params["river_pref"] * 1.5
                if cell_coastal(c, ter):     score += params["coast_pref"] * 1.5
                if t2 in CAN_FARM:           score += 2
                if c in res:                 score += 3
                if score > best_score:
                    best_score = score
                    best_cell  = c

            if best_cell != -1:
                cn = gen_city_name()
                civ["cities"].append({
                    "cell":         best_cell,
                    "name":         cn,
                    "population":   25.0,
                    "is_capital":   False,
                    "founded":      tick,
                    "trade":        3.0,
                    "wealth":       3.0,
                    "near_river":   cell_on_river(best_cell, rivers),
                    "coastal":      cell_coastal(best_cell, ter),
                    "food_production": 0.0,
                    "carrying_cap":   50,
                    "tiles":        [],
                    "farm_tiles":   [],
                })
                civ["gold"] -= 20
                civ["events"].append(f"Year {civ['age']}: Founded {cn}")
                add_event(f"🏘 Year {tick}: {civ['name']} founded {cn}")

        # Build roads
        if len(civ["cities"]) >= 2 and civ["gold"] > 12 and tick % 7 == 0:
            build_road(civ, ter)

        # Expansion — organic growth into unclaimed land
        if (civ["food"] > 15
                and civ["population"] > len(civ["territory"]) * 2
                and random.random() < civ["expansion_rate"] * 0.6):
            borders = border_cells(civ["territory"])
            targets = [
                c for c in borders
                if 0 <= c < N and is_land(ter, c) and om[c] == 0
            ]
            # Random jitter breaks tie-ordering artifacts (prevents horizontal-line expansion)
            targets.sort(key=lambda c: (
                (4 if c in res else 0)
                + (params["river_pref"] * 0.5 if cell_on_river(c, rivers) else 0)
                + (2 if ter[c] in (T.PLAINS, T.GRASS) else 0)
                - (3 if ter[c] >= T.MTN else 0)
                + random.random() * 3.0
            ), reverse=True)
            cnt = min(int(len(civ["territory"]) * 0.09) + 1, len(targets), 9)
            for c in targets[:cnt]:
                civ["territory"].add(c)
                om[c] = civ["id"]

        # War combat
        my_wars = [
            (k, w) for k, w in wars.items()
            if w["a_id"] == civ["id"] or w["d_id"] == civ["id"]
        ]
        for wk, war in my_wars:
            eid = war["d_id"] if war["a_id"] == civ["id"] else war["a_id"]
            enemy = next((c for c in alive if c["id"] == eid), None)
            if not enemy or not enemy["alive"]:
                wars.pop(wk, None)
                continue
            if civ["military"] < 12:
                continue

            for cell in list(border_cells(civ["territory"])):
                if cell < 0 or cell >= N or om[cell] != eid:
                    continue
                pr = civ["military"] / max(1, enemy["military"])
                if pr > 0.6 and random.random() < 0.2 * pr:
                    cap_cells = [cell]
                    for n in neighbors(cell):
                        if om[n] == eid and random.random() < 0.2 * pr:
                            cap_cells.append(n)
                    for c in cap_cells:
                        enemy["territory"].discard(c)
                        civ["territory"].add(c)
                        om[c] = civ["id"]
                        taken_city = next((ci for ci in enemy["cities"] if ci["cell"] == c), None)
                        if taken_city:
                            enemy["cities"] = [ci for ci in enemy["cities"] if ci["cell"] != c]
                            taken_city["is_capital"] = False
                            civ["cities"].append(taken_city)
                            add_event(f"🔥 Year {tick}: {civ['name']} took {taken_city['name']}!")
                    civ["military"]      *= 0.94
                    enemy["military"]    *= 0.87
                    enemy["population"]  *= 0.98
                    break

        # Prune cities/roads that lost territory
        civ["cities"] = [c for c in civ["cities"] if c["cell"] in civ["territory"]]
        civ["roads"]  = [r for r in civ["roads"]  if r["from"] in civ["territory"] and r["to"] in civ["territory"]]

        # Restore a capital if lost
        if civ["cities"] and not any(c["is_capital"] for c in civ["cities"]):
            civ["cities"][0]["is_capital"] = True
            civ["capital"] = civ["cities"][0]["cell"]

        # Survival — civs never disappear, they recover from hard times
        if civ["food"] < -30:
            civ["food"] = 0
            add_event(f"🍞 Year {tick}: {civ['name']} endured a famine")
        if civ["population"] < 15:
            civ["population"] = 15
            for city in civ["cities"]:
                city["population"] = max(city["population"], 10)
        # If territory got too small, reclaim a few surrounding cells
        if len(civ["territory"]) < 4 and civ["cities"]:
            cap = civ["cities"][0]["cell"]
            sx, sy = cap % W, cap // W
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    ni = (sy + dy) * W + (sx + dx)
                    if 0 <= ni < N and is_land(ter, ni) and om[ni] == 0:
                        civ["territory"].add(ni)
                        om[ni] = civ["id"]
        # If all cities lost, refound — pick cell farthest from other civs' cities
        if not civ["cities"] and civ["territory"]:
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
            cn = gen_city_name()
            civ["cities"] = [{
                "cell": cap_cell, "name": cn, "population": 20.0,
                "is_capital": True, "founded": tick, "trade": 3.0,
                "wealth": 5.0, "near_river": cell_on_river(cap_cell, rivers),
                "coastal": cell_coastal(cap_cell, ter),
                "food_production": 0.0, "carrying_cap": 50,
                "tiles": [], "farm_tiles": [],
            }]
            civ["capital"] = cap_cell
            add_event(f"🏛 Year {tick}: {civ['name']} refounded {cn}")

    # ── Fragmentation ─────────────────────────────────────────────────────────
    new_civs = []
    for civ in alive:
        if not civ["alive"]:
            continue
        fc = (1 - civ["integrity"]) * 0.001 * (1.0 if len(civ["territory"]) > 100 else 0.1)
        if len(civ["territory"]) > 80 and civ["age"] > 120 and random.random() < fc:
            cx, cy = centroid(civ["territory"])
            angle = random.random() * math.pi
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            side_a, side_b = [], []
            for c in civ["territory"]:
                sign = (c % W - cx) * cos_a + (c // W - cy) * sin_a
                (side_a if sign > 0 else side_b).append(c)

            if len(side_a) > 8 and len(side_b) > 8:
                all_regions = sorted(
                    [r for r in find_regions(side_a) + find_regions(side_b) if len(r) > 5],
                    key=lambda r: -len(r),
                )
                if len(all_regions) >= 2:
                    tot = len(civ["territory"])
                    civ["territory"] = set(all_regions[0])
                    civ["population"] *= len(all_regions[0]) / tot
                    civ["military"]   *= 0.5
                    civ["food"]       *= 0.5
                    civ["cities"] = [c for c in civ["cities"] if c["cell"] in civ["territory"]]
                    civ["roads"]  = [r for r in civ["roads"]  if r["from"] in civ["territory"] and r["to"] in civ["territory"]]
                    if civ["cities"] and not any(c["is_capital"] for c in civ["cities"]):
                        civ["cities"][0]["is_capital"] = True
                        civ["capital"] = civ["cities"][0]["cell"]
                    for c in all_regions[0]:
                        om[c] = civ["id"]

                    for ri, reg in enumerate(all_regions[1:4], 1):
                        cc = reg[len(reg) // 2]
                        cn = gen_city_name()
                        rebel = {
                            "id":          _next_id(),
                            "name":        gen_civ_name(),
                            "leader":      gen_leader_name(),
                            "color":       _next_color(),
                            "capital":     cc,
                            "territory":   set(reg),
                            "cities": [{
                                "cell":           cc,
                                "name":           cn,
                                "population":     40.0,
                                "is_capital":     True,
                                "founded":        tick,
                                "trade":          5.0,
                                "wealth":         10.0,
                                "near_river":     cell_on_river(cc, rivers),
                                "coastal":        cell_coastal(cc, ter),
                                "food_production": 0.0,
                                "carrying_cap":   100,
                                "tiles":          [],
                                "farm_tiles":     [],
                            }],
                            "population":     civ["population"] * (len(reg) / tot) * 0.8,
                            "military":       civ["military"] * 0.3,
                            "gold":           civ["gold"] * 0.2,
                            "food":           civ["food"] * 0.3,
                            "tech":           civ["tech"] * 0.8,
                            "culture":        civ["culture"] * 0.4,
                            "age":            0,
                            "alive":          True,
                            "integrity":      0.5 + random.random() * 0.3,
                            "peacefulness":   0.3 + random.random() * 0.5,
                            "wealth":         civ["wealth"] * 0.2,
                            "farm_output":    0.0,
                            "mine_output":    0.0,
                            "trade_output":   0.0,
                            "expansion_rate": 0.3 + random.random() * 0.4,
                            "events":         [f"Year 0: Broke from {civ['name']}"],
                            "parent_name":    civ["name"],
                            "roads":          [],
                        }
                        for c in reg:
                            om[c] = rebel["id"]
                        inherited = [c for c in civ["cities"] if c["cell"] in rebel["territory"]]
                        if inherited:
                            for c in inherited:
                                c["is_capital"] = False
                            rebel["cities"] = inherited + rebel["cities"]
                            civ["cities"] = [c for c in civ["cities"] if c["cell"] not in rebel["territory"]]
                        new_civs.append(rebel)
                        add_event(f"🏴 Year {tick}: {rebel['name']} broke from {civ['name']}!")

                    civ["integrity"] = min(1.0, civ["integrity"] + 0.2)
                    civ["events"].append(f"Year {civ['age']}: Fragmented")
                    add_event(f"💥 Year {tick}: {civ['name']} shattered!")

        # Keep only the largest contiguous region
        if civ["alive"] and len(civ["territory"]) > 8:
            regions = find_regions(list(civ["territory"]))
            if len(regions) > 1:
                regions.sort(key=lambda r: -len(r))
                for r in regions[1:]:
                    for c in r:
                        civ["territory"].discard(c)
                        om[c] = 0
                civ["cities"] = [c for c in civ["cities"] if c["cell"] in civ["territory"]]

    return new_civs
