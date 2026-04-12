"""Typed data models for the simulation.

Everything that used to be a free-form dict (cities, civs, armies, wars,
roads, commanders, objectives, the map data bundle, river paths …) lives
here as a dataclass. The ``ModelMeta`` mixin keeps dictionary-style access
working so legacy ``obj["field"]`` call sites don't have to be migrated
all at once — but new code should prefer attribute access.
"""

from dataclasses import dataclass, field, asdict
from typing import Set, List, Dict, Optional, Any


class ModelMeta:
    """Provides dictionary-like access to dataclasses to ease the transition
    from dicts to fully typed objects across a large codebase."""
    def __getitem__(self, item):
        return getattr(self, item)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __contains__(self, item):
        return hasattr(self, item)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Army sub-objects ────────────────────────────────────────────────────────

@dataclass
class Commander(ModelMeta):
    """Named officer attached to an army. Skill multiplies effective strength."""
    name: str
    skill: float = 1.0


@dataclass
class Objective(ModelMeta):
    """An army's current order. ``walk_cell`` is the actual step destination
    (used when the army should walk *toward* a target without stacking on
    it, e.g. an enemy army or city). ``target_cell`` is the conceptual goal.
    """
    type: str
    target_cell: int
    target_id: Optional[int] = None
    walk_cell: Optional[int] = None


# ── Civ sub-objects ─────────────────────────────────────────────────────────

@dataclass
class Road(ModelMeta):
    """A built road segment between two of a civ's cities. ``path`` is the
    full list of cells the road traverses; intermediate cells that happen
    to be city cells are treated as transit hops by the trade network."""
    from_cell: int
    to_cell: int
    path: List[int] = field(default_factory=list)


# ── World / map ─────────────────────────────────────────────────────────────

@dataclass
class Rivers(ModelMeta):
    """River system: ``paths`` is per-river ordered cell lists, ``cell_river``
    is the flattened set used for fast on-river membership tests."""
    paths: List[List[int]] = field(default_factory=list)
    cell_river: Set[int] = field(default_factory=set)


@dataclass
class MapData(ModelMeta):
    """Everything mapgen produces in one place. Held by GameState; passed
    into the per-tick simulation by reference. Mutable in-place — improve-
    ments and ownership live alongside the read-only terrain."""
    hm: List[float] = field(default_factory=list)   # heightmap (0..1)
    mm: List[float] = field(default_factory=list)   # moisture
    tm: List[float] = field(default_factory=list)   # temperature
    ter: List[int] = field(default_factory=list)    # terrain type per cell
    res: Dict[int, str] = field(default_factory=dict)
    rivers: Rivers = field(default_factory=Rivers)
    impr: List[int] = field(default_factory=list)   # bit-packed improvements


# ── City ────────────────────────────────────────────────────────────────────

@dataclass
class City(ModelMeta):
    cell: int
    name: str
    population: float
    is_capital: bool
    founded: int
    trade: float
    wealth: float
    focus: int
    near_river: bool
    coastal: bool
    food_production: float
    carrying_cap: int
    tiles: List[int] = field(default_factory=list)
    farm_tiles: List[int] = field(default_factory=list)
    hp: float = 115.0
    max_hp: float = 115.0
    last_dmg_tick: int = -999
    # ── Production accounting (refreshed each tick) ──
    city_ore: float = 0.0
    city_stone: float = 0.0
    city_metal: float = 0.0
    # ── Total production (before consumption) ──
    city_ore_total: float = 0.0
    city_stone_total: float = 0.0
    city_metal_total: float = 0.0
    trade_potential: float = 0.0
    road_trade: float = 0.0
    river_mouth: bool = False
    siege_immune_until: int = 0
    # Stash used between the production and trade passes within a single tick.
    _city_gold: float = 0.0
    # ── Employment ─────────────────────────────────
    # cell → how many building levels are staffed (<= building's level).
    staffing: Dict[int, int] = field(default_factory=dict)
    employee_level_count: int = 0


# ── Civ ─────────────────────────────────────────────────────────────────────

@dataclass
class Civ(ModelMeta):
    id: int
    name: str
    leader: str
    onom: dict
    color: str
    capital: int
    territory: Set[int]
    cities: List[City]
    population: float = 100.0
    military: float = 20.0
    gold: float = 50.0
    food: float = 80.0
    tech: float = 1.0
    culture: float = 1.0
    age: int = 0
    alive: bool = True
    integrity: float = 1.0
    aggressiveness: float = 0.5
    relations: Dict[int, float] = field(default_factory=dict)
    allies: Set[int] = field(default_factory=set)
    power: float = 0.0
    wealth: float = 30.0
    farm_output: float = 0.0
    ore_output: float = 0.0
    stone_output: float = 0.0
    metal_output: float = 0.0
    trade_output: float = 0.0
    expansion_rate: float = 0.5
    events: List[str] = field(default_factory=list)
    parent_name: Optional[str] = None
    roads: List[Road] = field(default_factory=list)
    metal_stock: float = 5.0
    fort_cooldowns: Dict[int, int] = field(default_factory=dict)
    # Per-tick scratch state for the city-founding logic.
    _settle_candidate: int = -1
    _settle_score: float = float("-inf")


# ── Army ────────────────────────────────────────────────────────────────────

@dataclass
class Army(ModelMeta):
    id: int
    civ_id: int
    war_key: str
    cell: int
    origin_cell: int
    fort_level: int
    strength: float
    max_strength: float
    organization: float
    supply: float
    commander: Commander
    behavior: str
    objective: Objective
    fortification: float = 0.0
    fort_source: str = "open field"
    # True after the "broken army" event has fired since the last recovery.
    broken_fired: bool = False


# ── War ─────────────────────────────────────────────────────────────────────

@dataclass
class War(ModelMeta):
    key: str
    att: int
    def_id: int
    start: int
    confidence_a: float
    confidence_d: float
    exhaustion_a: float
    exhaustion_d: float
    armies_a: List[Army] = field(default_factory=list)
    armies_d: List[Army] = field(default_factory=list)
    ended: bool = False
    # Pre-war territory snapshots — used by the peace-settlement logic to
    # decide which captured tiles permanently change hands.
    pre_ter_a: Set[int] = field(default_factory=set)
    pre_ter_d: Set[int] = field(default_factory=set)
    captured_cities_a: List[int] = field(default_factory=list)
    captured_cities_d: List[int] = field(default_factory=list)
