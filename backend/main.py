"""
Civitas – FastAPI backend
Runs the simulation loop and streams state to the browser via WebSocket.
"""

import asyncio
import importlib
import json
import mimetypes
import random as stdlib_random
import logging
import traceback
import time
from typing import Optional, List, Dict

# Fix Windows MIME type issue — Python reads from registry which is often wrong
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from engine.constants import (
    N, W, H, CELL, IMP, GOODS, GOOD_META, DEFAULT_PARAMS, N_EMPLOYEES_PER_LEVEL,
    TERRAIN_COLORS, IMP_COLORS, TERRAIN_NAMES, IMP_NAMES, RESOURCE_ICONS,
    GOV_OWNERSHIP_PROFILES, PROFESSION_META,
)
from engine.buildings import BUILDING_TYPES
from engine.mapgen import gen_map
from engine.civ import make_civ, reset_counters
from engine.simulation import tick_sim
from engine.noise import make_noise
from engine.employment import STAFFABLE_TYPES
from engine.regions import IMP_PRIMARY_GOOD
from engine.capacity import PRODUCER_BUILDINGS, SHARED_CAPACITY_GROUPS

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("civitas")

MAIN_PERF_LOG_PERIOD = 250

try:
    _ORJSON = importlib.import_module("orjson")
except ImportError:
    _ORJSON = None

app = FastAPI()


def _dumps_json(payload: dict) -> str:
    if _ORJSON is not None:
        return _ORJSON.dumps(payload).decode("utf-8")
    return json.dumps(payload, separators=(",", ":"))


# ── Game state ─────────────────────────────────────────────────────────────

from engine.models import MapData, Civ, City, Army, War, Road

class GameState:
    def __init__(self):
        self.map_data:  Optional[MapData] = None
        self.civs:      List[Civ] = []
        self.om:        list = []
        self.wars:      Dict[str, War] = {}
        self.impr:      list = []
        self.tick:      int  = 0
        self.running:   bool = False
        self.speed:     float = 1.0
        self.params:    dict = dict(DEFAULT_PARAMS)
        self.seed:      int  = int(stdlib_random.random() * 99999)
        self.log:       list = []

    def add_event(self, msg: str):
        self.log.append(msg)
        if len(self.log) > 100:
            self.log = self.log[-100:]


def _do_reset(state: GameState, seed: int):
    """Blocking reset — run in a thread so we don't freeze the event loop."""
    reset_counters()
    state.seed     = seed
    state.map_data = gen_map(seed)
    state.om       = [0] * N
    state.impr     = list(state.map_data.impr)
    state.civs     = []
    state.wars     = {}
    state.tick     = 0
    state.log      = []


# ── Serialisation helpers ──────────────────────────────────────────────────

def _ser_map(md: MapData) -> dict:
    return {
        "type":           "map",
        "width":          W,
        "height":         H,
        "cell_size":      CELL,
        "goods":          GOODS,
        "good_meta":      GOOD_META,
        "ter":            md.ter,
        "res":            {str(k): v for k, v in md.res.items()},
        "rivers": {
            "paths":      md.rivers.paths,
            "cell_river": list(md.rivers.cell_river),
        },
        "hm":             [round(v, 3) for v in md.hm],
        "terrain_colors": {str(k): v for k, v in TERRAIN_COLORS.items()},
        "imp_colors":     {str(k): v for k, v in IMP_COLORS.items()},
        "terrain_names":  {str(k): v for k, v in TERRAIN_NAMES.items()},
        "imp_names":      {str(k): v for k, v in IMP_NAMES.items()},
        "resource_icons": RESOURCE_ICONS,
        "government_profiles": GOV_OWNERSHIP_PROFILES,
        "profession_meta":    PROFESSION_META,
        "employee_per_level": N_EMPLOYEES_PER_LEVEL,
        "staffable_imp_types": sorted(int(v) for v in STAFFABLE_TYPES),
        "imp_primary_good": {str(k): v for k, v in IMP_PRIMARY_GOOD.items()},
        "producer_buildings": PRODUCER_BUILDINGS,
        "shared_capacity_groups": {
            k: list(v) for k, v in SHARED_CAPACITY_GROUPS.items()
        },
        "good_efficiency": {
            g: [round(v, 3) for v in field]
            for g, field in md.good_efficiency.items()
        },
    }


def _ser_civs(civs: List[Civ]) -> list:
    result = []
    for c in civs:
        result.append({
            "id":             c.id,
            "name":           c.name,
            "leader":         c.leader,
            "color":          c.color,
            "capital":        c.capital,
            "territory":      sorted(c.territory),
            "cities":         [{
                "cell":           ci.cell,
                "name":           ci.name,
                "population":     round(ci.population, 1),
                "is_capital":     ci.is_capital,
                "founded":        ci.founded,
                "gold":           round(ci.gold, 1),
                "supply":         {k: round(v, 1) for k, v in ci.supply.items()},
                "demand":         {k: round(v, 1) for k, v in ci.demand.items()},
                "prices":         {k: round(v, 2) for k, v in ci.prices.items()},
                "last_trades":    ci.last_trades,
                "near_river":     ci.near_river,
                "coastal":        ci.coastal,
                "river_mouth":    ci.river_mouth,
                "focus":          ci.focus,
                "tiles":          ci.tiles,
                "farm_tiles":     ci.farm_tiles,
                "staffing":       {str(k): v for k, v in ci.staffing.items()},
                "buildings":      ci.buildings,
                "building_staffing": ci.building_staffing,
                "building_profit": {k: round(v, 2) for k, v in ci.building_profit.items()},
                "capacities":     {k: int(v) for k, v in (ci.capacities or {}).items()},
                "shared_capacities": {k: int(v) for k, v in (ci.shared_capacities or {}).items()},
                "capacity_bonuses": {
                    k: {
                        "slots": int((v or {}).get("slots", 0)),
                        "mult": round(float((v or {}).get("mult", 0.0)), 4),
                    }
                    for k, v in (ci.capacity_bonuses or {}).items()
                },
                "tile_capacities": {
                    str(cell): {k: int(v) for k, v in vals.items()}
                    for cell, vals in (ci.tile_capacities or {}).items()
                },
                "tile_capacity_bonuses": {
                    str(cell): {
                        key: {
                            "slots": int((b or {}).get("slots", 0)),
                            "mult": round(float((b or {}).get("mult", 0.0)), 4),
                        }
                        for key, b in vals.items()
                    }
                    for cell, vals in (ci.tile_capacity_bonuses or {}).items()
                },
                "building_details": [
                    {
                        "key": key,
                        "name": b.name,
                        "level": int(ci.buildings.get(key, 0)),
                        "staffed": int(ci.building_staffing.get(key, 0)),
                        "inputs": b.inputs,
                        "outputs": b.outputs,
                        "profit": round(ci.building_profit.get(key, 0.0), 2),
                    }
                    for key, b in BUILDING_TYPES.items()
                    if int(ci.buildings.get(key, 0)) > 0
                ] + [
                    {
                        "key": key,
                        "name": meta.get("label", key),
                        "level": int(ci.buildings.get(key, 0)),
                        "staffed": int(ci.building_staffing.get(key, 0)),
                        "inputs": (
                            {meta.get("input_good"): float(meta.get("input_per_level", 0.0))}
                            if meta.get("input_good") and float(meta.get("input_per_level", 0.0)) > 0.0
                            else {}
                        ),
                        "outputs": (
                            {meta.get("good"): float(meta.get("base_output", 0.0))}
                            if meta.get("good")
                            else {}
                        ),
                        "profit": round(ci.building_profit.get(key, 0.0), 2),
                        "capacity": int((ci.capacities or {}).get(key, 0)),
                    }
                    for key, meta in PRODUCER_BUILDINGS.items()
                    if int(ci.buildings.get(key, 0)) > 0
                ],
                "employee_level_count": ci.employee_level_count,
                "professions":    dict(ci.professions or {}),
                "profession_wages": dict(ci.profession_wages or {}),
                "profession_income_shares": dict(ci.profession_income_shares or {}),
                "consumption_levels": dict(ci.consumption_levels or {}),
                "hp":             round(ci.hp, 1),
                "max_hp":         round(ci.max_hp, 1),
                "last_dmg_tick":  ci.last_dmg_tick,
                "workforce":      ci.workforce,
                "employed_pop":   ci.employed_pop,
                "unemployed_pop": ci.unemployed_pop,
                "income_domestic": {k: round(v, 2) for k, v in ci.income_domestic.items()},
                "income_export":   {k: round(v, 2) for k, v in ci.income_export.items()},
                "income_import":   {k: round(v, 2) for k, v in ci.income_import.items()},
                "income_misc":    round(ci.income_misc, 2),
                "income_total":   round(ci.income_total, 2),
                "income_per_person": round(ci.income_per_person, 3),
                "economic_output": round(getattr(ci, "economic_output", 0.0), 2),
                "trade_export_volume": round(getattr(ci, "trade_export_volume", 0.0), 2),
                "trade_export_income": round(getattr(ci, "trade_export_income", 0.0), 2),
                "trade_capacity_required": round(getattr(ci, "trade_capacity_required", 0.0), 2),
                "trade_capacity_provided": round(getattr(ci, "trade_capacity_provided", 0.0), 2),
                "avg_consumption_level": round(getattr(ci, "avg_consumption_level", 0.0), 3),
                "market_satisfaction": round(getattr(ci, "market_satisfaction", 0.0), 3),
                "population_growth_rate": round(getattr(ci, "population_growth_rate", 0.0), 5),
                "growth_food_contribution":   round(getattr(ci, "growth_food_contribution", 0.0), 5),
                "growth_consumption_penalty": round(getattr(ci, "growth_consumption_penalty", 0.0), 5),
                "growth_unemployment_penalty": round(getattr(ci, "growth_unemployment_penalty", 0.0), 5),
                "attractiveness": round(ci.attractiveness, 3),
                "net_migration":  round(ci.net_migration, 2),
            } for ci in c.cities],
            "population":     round(c.population, 1),
            "military":       round(c.military, 1),
            "gold":           round(c.gold, 1),
            "food":           round(c.food, 1),
            "tech":           round(c.tech, 2),
            "culture":        round(c.culture, 2),
            "age":            c.age,
            "alive":          c.alive,
            "integrity":      round(c.integrity, 3),
            "aggressiveness": round(c.aggressiveness, 3),
            "disposition":    getattr(c, "disposition", "calm"),
            "disposition_ticks": int(getattr(c, "disposition_ticks", 0)),
            "power":          round(c.power, 1),
            "relations":      {str(k): round(v, 3) for k, v in c.relations.items()},
            "allies":         sorted(c.allies),
            "wealth":         round(c.wealth, 1),
            "farm_output":    round(c.farm_output, 1),
            "ore_output":     round(c.ore_output, 1),
            "stone_output":   round(c.stone_output, 1),
            "metal_output":   round(c.metal_output, 1),
            "trade_output":   round(c.trade_output, 1),
            "expansion_rate": round(c.expansion_rate, 3),
            "events":         c.events[-10:],
            "parent_name":    c.parent_name,
            "roads":          [{"from": r.from_cell, "to": r.to_cell} for r in c.roads],
            "road_paths":     [r.path for r in c.roads],
            "metal_stock":    round(c.metal_stock, 1),
            "government": {
                "tax_rate": round(getattr(getattr(c, "government", None), "tax_rate", 0.0), 3),
                "treasury": round(getattr(getattr(c, "government", None), "treasury", 0.0), 2),
                "last_tax_collected": round(getattr(getattr(c, "government", None), "last_tax_collected", 0.0), 2),
                "last_build_spending": round(getattr(getattr(c, "government", None), "last_build_spending", 0.0), 2),
                "last_fort_spending": round(getattr(getattr(c, "government", None), "last_fort_spending", 0.0), 2),
                "last_benefit_spending": round(getattr(getattr(c, "government", None), "last_benefit_spending", 0.0), 2),
                "last_flows": [
                    {
                        "kind": flow.kind,
                        "label": flow.label,
                        "amount": round(flow.amount, 2),
                        "category": flow.category,
                        "city_cell": flow.city_cell,
                        "city_name": flow.city_name,
                        "note": flow.note,
                    }
                    for flow in getattr(getattr(c, "government", None), "last_flows", [])
                ],
                "construction_queue": [
                    {
                        "asset_key": order.asset_key,
                        "asset_label": order.asset_label,
                        "priority": round(order.priority, 2),
                        "target_civ_id": order.target_civ_id,
                        "target_civ_name": order.target_civ_name,
                        "host_city_cell": order.host_city_cell,
                        "host_city_name": order.host_city_name,
                        "relation": round(order.relation, 3),
                        "estimated_upkeep": round(order.estimated_upkeep, 2),
                        "estimated_spending": round(order.estimated_spending, 2),
                        "reason": order.reason,
                        "status": order.status,
                    }
                    for order in getattr(getattr(c, "government", None), "construction_queue", [])
                ],
                "fort_upkeep_goods": {
                    k: round(v, 1)
                    for k, v in getattr(getattr(c, "government", None), "fort_upkeep_goods", {}).items()
                },
                "fort_buffer_on": round(getattr(getattr(c, "government", None), "fort_buffer_on", 0.0), 2),
                "fort_buffer_off": round(getattr(getattr(c, "government", None), "fort_buffer_off", 0.0), 2),
                "forts": [
                    {
                        "cell": cell,
                        "active": state.active,
                        "buffer": round(state.buffer, 2),
                        "last_upkeep_value": round(state.last_upkeep_value, 2),
                    }
                    for cell, state in getattr(getattr(c, "government", None), "forts", {}).items()
                ],
                "owned_assets": {
                    "improvements": {
                        asset_key: [
                            {
                                "cell": cell,
                                "active": state.active,
                                "buffer": round(state.buffer, 2),
                                "last_upkeep_value": round(state.last_upkeep_value, 2),
                            }
                            for cell, state in states.items()
                        ]
                        for asset_key, states in getattr(getattr(c, "government", None), "owned_improvements", {}).items()
                    },
                    "buildings": {
                        str(city_cell): holdings
                        for city_cell, holdings in getattr(getattr(c, "government", None), "owned_city_buildings", {}).items()
                    },
                },
            },
        })
    return result


def _ser_army(a: Army) -> dict:
    return {
        "id":            a.id,
        "civ_id":        a.civ_id,
        "cell":          a.cell,
        "origin_cell":   a.origin_cell,
        "fort_level":    a.fort_level,
        "strength":      round(a.strength, 1),
        "max_strength":  round(a.max_strength, 1),
        "organization":  round(a.organization, 1),
        "supply":        round(a.supply, 1),
        "commander":     {"name": a.commander.name, "skill": a.commander.skill},
        "behavior":      a.behavior,
        "objective":     {
            "type":        a.objective.type,
            "target_cell": a.objective.target_cell,
            "target_id":   a.objective.target_id,
            "walk_cell":   a.objective.walk_cell,
        } if a.objective else None,
        "fortification": round(a.fortification, 3),
        "fort_source":   a.fort_source,
    }


def _ser_state(state: GameState) -> dict:
    return {
        "type":  "state",
        "tick":  state.tick,
        "civs":  _ser_civs(state.civs),
        "wars":  [{
            "key":         k,
            "att":         v.att,
            "def_id":      v.def_id,
            "start_tick":  v.start,
            "confidence_a": round(v.confidence_a, 3),
            "confidence_d": round(v.confidence_d, 3),
            "exhaustion_a": round(v.exhaustion_a, 3),
            "exhaustion_d": round(v.exhaustion_d, 3),
            "armies_a":    [_ser_army(a) for a in v.armies_a],
            "armies_d":    [_ser_army(a) for a in v.armies_d],
        } for k, v in state.wars.items()],
        "impr":  state.impr,
        "log":   state.log[-20:],
    }


# ── Simulation loop ────────────────────────────────────────────────────────

async def _sim_loop(ws: WebSocket, state: GameState, lock: asyncio.Lock):
    try:
        while state.running:
            interval = 0.13 / state.speed
            await asyncio.sleep(interval)

            async with lock:
                if not state.running or not state.map_data:
                    break

                md   = state.map_data
                t    = state.tick + 1
                civs = state.civs

                if t == 1 or (t % int(state.params["spawn_rate"]) == 0
                              and sum(1 for c in civs if c.alive) < state.params["max_civs"]):
                    rng = make_noise(state.seed + t * 13)
                    count = 5 if t == 1 else 1
                    for i in range(count):
                        nv = make_civ(
                            md.ter, [c for c in civs if c.alive],
                            md.rivers, rng, t, state.om, state.impr,
                        )
                        if nv:
                            for cell in nv.territory:
                                state.om[cell] = nv.id
                            civs.append(nv)
                            state.add_event(f"🏛 Year {t}: {nv.name} founded")

                tick_t0 = time.perf_counter()
                new_civs = tick_sim(
                    civs, md.ter, md.res, state.om, state.wars,
                    md.rivers, state.impr, t, state.add_event, state.params,
                    md.good_efficiency,
                )
                civs.extend(new_civs)
                state.tick = t
                tick_ms = (time.perf_counter() - tick_t0) * 1000.0
                if t % MAIN_PERF_LOG_PERIOD == 0:
                    alive_civs = sum(1 for c in civs if c.alive)
                    alive_cities = sum(len(c.cities) for c in civs if c.alive)
                    log.info(
                        "[perf] main_tick tick=%d dt=%.1fms alive_civs=%d alive_cities=%d",
                        t,
                        tick_ms,
                        alive_civs,
                        alive_cities,
                    )
                payload = _dumps_json(_ser_state(state))

            await ws.send_text(payload)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.error("sim_loop crashed:\n%s", traceback.format_exc())


# ── WebSocket endpoint ─────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("WebSocket connected")

    state    = GameState()
    lock     = asyncio.Lock()
    sim_task: Optional[asyncio.Task] = None

    try:
        # Generate the map in a thread so we don't block the event loop
        log.info("Generating map (seed=%d) ...", state.seed)
        await asyncio.to_thread(_do_reset, state, state.seed)
        log.info("Map ready — sending to client")

        await ws.send_text(_dumps_json(_ser_map(state.map_data)))
        await ws.send_text(_dumps_json(_ser_state(state)))
        log.info("Initial state sent")

        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            async with lock:
                if action == "play":
                    if not state.running:
                        state.running = True
                        sim_task = asyncio.create_task(_sim_loop(ws, state, lock))
                        log.info("Simulation started")

                elif action == "pause":
                    state.running = False
                    if sim_task:
                        sim_task.cancel()
                        sim_task = None
                    log.info("Simulation paused")

                elif action == "reset":
                    state.running = False
                    if sim_task:
                        sim_task.cancel()
                        sim_task = None
                    seed = msg.get("seed", int(stdlib_random.random() * 99999))
                    log.info("Resetting (seed=%d) ...", seed)
                    await asyncio.to_thread(_do_reset, state, seed)
                    await ws.send_text(_dumps_json(_ser_map(state.map_data)))
                    await ws.send_text(_dumps_json(_ser_state(state)))
                    log.info("Reset complete")

                elif action == "speed":
                    state.speed = float(msg.get("value", 1.0))

                elif action == "params":
                    state.params.update(msg.get("values", {}))

                elif action == "get_state":
                    await ws.send_text(_dumps_json(_ser_state(state)))

    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception:
        log.error("WebSocket handler error:\n%s", traceback.format_exc())
    finally:
        state.running = False
        if sim_task:
            sim_task.cancel()


# ── Serve frontend static files ────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

app.add_middleware(NoCacheMiddleware)

app.mount("/static", StaticFiles(directory="../frontend"), name="static")


@app.get("/")
async def serve_index():
    return FileResponse("../frontend/index.html")
