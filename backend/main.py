"""
Civitas – FastAPI backend
Runs the simulation loop and streams state to the browser via WebSocket.
"""

import asyncio
import json
import mimetypes
import random as stdlib_random
import logging
import traceback
from typing import Optional, List, Dict

# Fix Windows MIME type issue — Python reads from registry which is often wrong
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from engine.constants import N, W, H, IMP, DEFAULT_PARAMS, TERRAIN_COLORS, IMP_COLORS
from engine.mapgen import gen_map
from engine.civ import make_civ, reset_counters
from engine.simulation import tick_sim
from engine.noise import make_noise

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("civitas")

app = FastAPI()


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
        "ter":            md.ter,
        "res":            {str(k): v for k, v in md.res.items()},
        "rivers": {
            "paths":      md.rivers.paths,
            "cell_river": list(md.rivers.cell_river),
        },
        "hm":             [round(v, 3) for v in md.hm],
        "terrain_colors": {str(k): v for k, v in TERRAIN_COLORS.items()},
        "imp_colors":     {str(k): v for k, v in IMP_COLORS.items()},
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
                "trade":          round(ci.trade, 1),
                "trade_potential": round(ci.trade_potential, 1),
                "road_trade":     round(ci.road_trade, 1),
                "wealth":         round(ci.wealth, 1),
                "near_river":     ci.near_river,
                "coastal":        ci.coastal,
                "river_mouth":    ci.river_mouth,
                "food_production": round(ci.food_production, 1),
                "city_ore":       round(ci.city_ore, 1),
                "city_stone":     round(ci.city_stone, 1),
                "city_metal":     round(ci.city_metal, 1),
                "city_ore_total":   round(ci.city_ore_total, 1),
                "city_stone_total": round(ci.city_stone_total, 1),
                "city_metal_total": round(ci.city_metal_total, 1),
                "focus":          ci.focus,
                "carrying_cap":   round(ci.carrying_cap, 0),
                "tiles":          ci.tiles,
                "farm_tiles":     ci.farm_tiles,
                "staffing":       {str(k): v for k, v in ci.staffing.items()},
                "employee_level_count": ci.employee_level_count,
                "hp":             round(ci.hp, 1),
                "max_hp":         round(ci.max_hp, 1),
                "last_dmg_tick":  ci.last_dmg_tick,
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

                new_civs = tick_sim(
                    civs, md.ter, md.res, state.om, state.wars,
                    md.rivers, state.impr, t, state.add_event, state.params,
                )
                civs.extend(new_civs)
                state.tick = t
                payload = json.dumps(_ser_state(state))

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

        await ws.send_text(json.dumps(_ser_map(state.map_data)))
        await ws.send_text(json.dumps(_ser_state(state)))
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
                    await ws.send_text(json.dumps(_ser_map(state.map_data)))
                    await ws.send_text(json.dumps(_ser_state(state)))
                    log.info("Reset complete")

                elif action == "speed":
                    state.speed = float(msg.get("value", 1.0))

                elif action == "params":
                    state.params.update(msg.get("values", {}))

                elif action == "get_state":
                    await ws.send_text(json.dumps(_ser_state(state)))

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
