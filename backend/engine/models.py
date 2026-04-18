"""Typed data models for the simulation.

Everything that used to be a free-form dict (cities, civs, armies, wars,
roads, commanders, objectives, the map data bundle, river paths …) lives
here as a dataclass. The ``ModelMeta`` mixin keeps dictionary-style access
working so legacy ``obj["field"]`` call sites don't have to be migrated
all at once — but new code should prefer attribute access.
"""

from dataclasses import dataclass, field, asdict
from typing import Set, List, Dict, Optional, Any, Tuple


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
    # Per-good efficiency map: {good: list[float] of length N}. Modulates
    # each tile's raw production. See engine.regions.
    good_efficiency: Dict[str, List[float]] = field(default_factory=dict)


# ── City ────────────────────────────────────────────────────────────────────

@dataclass
class City(ModelMeta):
    cell: int
    name: str
    population: float
    is_capital: bool
    founded: int
    focus: int
    near_river: bool
    coastal: bool
    tiles: List[int] = field(default_factory=list)
    farm_tiles: List[int] = field(default_factory=list)
    hp: float = 115.0
    max_hp: float = 115.0
    last_dmg_tick: int = -999
    # ── Economy (refreshed each tick) ──
    prices: Dict[str, float] = field(default_factory=dict)
    supply: Dict[str, float] = field(default_factory=dict)
    demand: Dict[str, float] = field(default_factory=dict)
    net_imports: Dict[str, float] = field(default_factory=dict)
    gold:   float = 10.0

    trade_potential: float = 0.0
    road_trade: float = 0.0
    river_mouth: bool = False
    siege_immune_until: int = 0
    # Stash used between the production and trade passes within a single tick.
    _city_gold: float = 0.0
    # Per-tick trade history for tooltips: {good: (volume, other_city_name, price)}
    last_trades: Dict[str, List[Tuple[float, str, float]]] = field(default_factory=dict)

    # ── Employment ─────────────────────────────────
    # cell → how many building levels are staffed (<= building's level).
    staffing: Dict[int, int] = field(default_factory=dict)
    # city-building key -> built level in this city.
    buildings: Dict[str, int] = field(default_factory=dict)
    # city-building key -> staffed levels in this city building.
    building_staffing: Dict[str, int] = field(default_factory=dict)
    # city-building key -> net profit this tick.
    building_profit: Dict[str, float] = field(default_factory=dict)
    employee_level_count: int = 0
    # Population-derived workforce snapshots (refreshed per tick).
    workforce: int = 0
    employed_pop: int = 0
    unemployed_pop: int = 0

    # ── Income ledger (refreshed each tick) ──
    # Per-good gold flow buckets, so the UI can break down where money comes
    # from. "domestic" is the share of consumer spending that ends up in the
    # city treasury; "export"/"import" reflect trade receipts and payments.
    income_domestic: Dict[str, float] = field(default_factory=dict)
    income_export: Dict[str, float] = field(default_factory=dict)
    income_import: Dict[str, float] = field(default_factory=dict)
    income_misc: float = 0.0           # raw-gold resource cells, etc.
    income_total: float = 0.0          # net gold gained this tick
    income_per_person: float = 0.0
    # Rolling window of income_per_person, used to smooth attractiveness.
    income_per_person_hist: List[float] = field(default_factory=list)

    # ── Migration ──────────────────────────────────
    # Composite pull score (higher = immigrants want to move here). Computed
    # from unemployment and rolling income.
    attractiveness: float = 1.0
    # Net flow of people this tick (+ incoming, − leaving).
    net_migration: float = 0.0

    # Average regional efficiency per good across this city's tiles.
    # Refreshed each tick from MapData.good_efficiency.
    local_efficiency: Dict[str, float] = field(default_factory=dict)


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
    metal_stock: float = 0.0
    fort_cooldowns: Dict[int, int] = field(default_factory=dict)

    # AI Strategy: Strict priority queue of objectives
    goal_queue: List[str] = field(default_factory=lambda: [
        "FARM", "FARM", "FOUND", "MINE", "FARM", "FOUND", "LUMBER", "FARM", "FOUND"
    ])
    goal_index: int = 0
    goal_ticks: int = 0 # How long we've been on the current goal

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


# ── Economic goods (tradable resources) ──────────────────────────────────────

@dataclass
class Good(ModelMeta):
    """A tradable good produced by cities and traded between civilizations.
    
    Attributes:
        name: Display name (e.g., "Food", "Lumber")
        base_price: Default market price (multiplied by supply/demand)
        icon: Unicode character for UI display
        produced_by: List of improvement type IDs that produce this good
    """
    name: str
    base_price: float
    icon: str
    produced_by: List[int] = field(default_factory=list)


# ── Map resources (deposit goods) ────────────────────────────────────────────

@dataclass
class Resource(ModelMeta):
    """A resource that appears on map tiles (iron, gold, horses, etc.).
    
    Attributes:
        name: Display name (e.g., "Iron", "Gold")
        icon: Unicode character for UI display
    """
    name: str
    icon: str


# ── Improvement types ────────────────────────────────────────────────────────

@dataclass
class ImprovementType(ModelMeta):
    """Metadata for an improvement type (building).
    
    Attributes:
        type_id: Unique type identifier (mirrors IMP.FARM, IMP.MINE, etc.)
        name: Display name
        color: Hex color for map rendering
        max_level: Maximum level this improvement can reach
        staffable: Whether this improvement consumes workers
        upgradable: Whether this improvement can be upgraded beyond level 1
        produces_good: Name of the good this produces (if any)
    """
    type_id: int
    name: str
    color: str
    max_level: int
    staffable: bool
    upgradable: bool
    produces_good: Optional[str] = None


@dataclass
class ImprovementEconomyProfile(ModelMeta):
    """Data-driven production/effect profile for a tile improvement.

    This lets simulation compute supply/demand contributions without
    hardcoding per-improvement formulas in the tick loop.
    """
    improvement_type: int
    output_good: Optional[str] = None
    output_eff_good: Optional[str] = None
    output_base: float = 0.0
    output_per_level: float = 0.0
    demand_good: Optional[str] = None
    demand_per_output: float = 0.0
    use_river_mult: bool = False
    use_coast_mult: bool = False
    windmill_bonus_per_staffed_level: float = 0.0
    # Resource-specific output multiplier (e.g., mines on iron).
    resource_output_multiplier: Dict[str, float] = field(default_factory=dict)
    # Whether this improvement should be tracked in city.farm_tiles.
    counts_as_worked_tile: bool = True


# ── City buildings ───────────────────────────────────────────────────────────

@dataclass
class BuildingType(ModelMeta):
    """City-center building metadata.

    Buildings are distinct from tile improvements: they are built in the city
    itself and can consume one set of goods to produce another.
    """
    key: str
    name: str
    max_level: int
    staffable: bool
    cost_resources: Set[str] = field(default_factory=set)
    inputs: Dict[str, float] = field(default_factory=dict)
    outputs: Dict[str, float] = field(default_factory=dict)
