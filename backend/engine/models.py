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
    # Trading house bookkeeping.
    trade_export_volume: float = 0.0
    trade_export_income: float = 0.0
    trade_capacity_required: float = 0.0
    trade_capacity_provided: float = 0.0

    # ── Employment ─────────────────────────────────
    # cell → how many building levels are staffed (<= building's level).
    staffing: Dict[int, int] = field(default_factory=dict)
    # city-building key -> built level in this city.
    buildings: Dict[str, int] = field(default_factory=dict)
    # city-building key -> staffed levels in this city building.
    building_staffing: Dict[str, int] = field(default_factory=dict)
    # city-building key -> net profit this tick.
    building_profit: Dict[str, float] = field(default_factory=dict)
    # producer-building key -> max supported levels from current tiles.
    capacities: Dict[str, int] = field(default_factory=dict)
    # shared capacity pools (e.g. farm + cotton share one agricultural pool).
    shared_capacities: Dict[str, int] = field(default_factory=dict)
    # producer-building key -> {slots, mult} where first N levels get +mult output.
    capacity_bonuses: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # Per-tile capacity and bonus breakdown for tooltips/debugging.
    tile_capacities: Dict[int, Dict[str, int]] = field(default_factory=dict)
    tile_capacity_bonuses: Dict[int, Dict[str, Dict[str, float]]] = field(default_factory=dict)
    employee_level_count: int = 0
    # profession key -> total headcount across all staffed improvements +
    # buildings. Recomputed by employment.update_city_employment and
    # employment.reallocate_workers_by_profit.
    professions: Dict[str, int] = field(default_factory=dict)
    # profession key -> average wage per person in that profession.
    profession_wages: Dict[str, float] = field(default_factory=dict)
    # profession key -> share of this city's current profit pool.
    profession_income_shares: Dict[str, float] = field(default_factory=dict)
    # profession key -> slow-moving consumption level / wealth tier.
    consumption_levels: Dict[str, float] = field(default_factory=dict)
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
    # Gross economic activity at base prices (consumption + exports). Unlike
    # income_total this is not netted against imports, so trade hubs score
    # high. Intended as the headline "economy size" metric.
    economic_output: float = 0.0
    # Population-weighted mean of per-profession consumption tier. Used as
    # the migration quality signal — cities where residents live on higher
    # consumption tiers pull immigrants.
    avg_consumption_level: float = 0.0
    avg_consumption_level_hist: List[float] = field(default_factory=list)
    # Demand fulfillment score (0..1). High when the city is broadly balanced.
    market_satisfaction: float = 0.0
    # Rolling window of market_satisfaction, used to smooth attractiveness.
    market_satisfaction_hist: List[float] = field(default_factory=list)

    # ── Migration ──────────────────────────────────
    # Composite pull score (higher = immigrants want to move here). Computed
    # from unemployment and rolling income.
    attractiveness: float = 1.0
    # Per-tick migration flow (+ incoming, − leaving).
    net_migration: float = 0.0

    # Average regional efficiency per good across this city's tiles.
    # Refreshed each tick from MapData.good_efficiency.
    local_efficiency: Dict[str, float] = field(default_factory=dict)


# ── Government ─────────────────────────────────────────────────────────────

@dataclass
class FortFunding(ModelMeta):
    active: bool = True
    buffer: float = 1.5
    last_upkeep_value: float = 0.0


@dataclass
class GovernmentConstructionOrder(ModelMeta):
    asset_key: str
    asset_label: str
    priority: float = 0.0
    target_civ_id: Optional[int] = None
    target_civ_name: str = ""
    host_city_cell: Optional[int] = None
    host_city_name: str = ""
    relation: float = 0.0
    estimated_upkeep: float = 0.0
    estimated_spending: float = 0.0
    reason: str = ""
    status: str = "queued"


@dataclass
class GovernmentFlow(ModelMeta):
    kind: str
    label: str
    amount: float
    category: str = "income"
    city_cell: Optional[int] = None
    city_name: str = ""
    note: str = ""


@dataclass
class Government(ModelMeta):
    tax_rate: float = 0.12
    treasury: float = 0.0
    last_tax_collected: float = 0.0
    last_build_spending: float = 0.0
    last_fort_spending: float = 0.0
    last_benefit_spending: float = 0.0
    last_flows: List[GovernmentFlow] = field(default_factory=list)
    fort_upkeep_goods: Dict[str, float] = field(default_factory=dict)
    fort_buffer_on: float = 1.5
    fort_buffer_off: float = 0.5
    construction_queue: List[GovernmentConstructionOrder] = field(default_factory=list)
    forts: Dict[int, FortFunding] = field(default_factory=dict)
    # Generic ownership containers for future government-owned assets.
    # forts remains for compatibility and mirrors owned_improvements["fort"].
    owned_improvements: Dict[str, Dict[int, FortFunding]] = field(default_factory=dict)
    owned_city_buildings: Dict[int, Dict[str, int]] = field(default_factory=dict)


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
    government: Government = field(default_factory=Government)
    metal_stock: float = 0.0
    fort_cooldowns: Dict[int, int] = field(default_factory=dict)
    disposition: str = "calm"
    disposition_ticks: int = 0
    disposition_targets: List[int] = field(default_factory=list)

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


@dataclass
class GoodSpec(ModelMeta):
    """Canonical specification for a market good.

    This is the single source of truth used to derive price tables,
    frontend labels/icons, and tradability flags.
    """
    key: str
    label: str
    icon: str
    base_price: float
    tradable: bool = True


@dataclass
class GovernmentOwnershipProfile(ModelMeta):
    """Catalog entry describing one government-ownable asset type."""
    key: str
    label: str
    upkeep_goods: Dict[str, float] = field(default_factory=dict)
    imp_type: Optional[int] = None
    building_key: Optional[str] = None
    buffer_on: float = 1.5
    buffer_off: float = 0.5


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
    # Profession breakdown per staffed level; values sum to
    # N_EMPLOYEES_PER_LEVEL. Empty for non-staffable types.
    professions: Dict[str, int] = field(default_factory=dict)


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


@dataclass
class ProfessionConsumptionProfile(ModelMeta):
    """Slow-moving consumption tuning for a profession class.

    ``income_weight`` controls how much of the city's profit pool this
    profession captures relative to the others; ``spend_share`` controls
    how much of that wage is assumed to be available for consumption.
    Consumption levels are floored at ``min_level`` but otherwise grow
    boundlessly — per-good demand curves provide the natural saturation.
    """
    key: str
    income_weight: float
    spend_share: float
    base_level: float
    min_level: float
    increase_step: float
    decrease_step: float
    raise_threshold: float
    lower_threshold: float
    reference_wage: float


@dataclass
class ConsumptionGoodProfile(ModelMeta):
    """Demand curve for one good as consumption rises."""
    good: str
    base_per_person: float
    floor_multiplier: float = 0.0
    # Sigmoid-curve parameters — only used when ``curve_kind="sigmoid2"``
    # or one of the staple variants. Points-based curves ignore them.
    early_mid: float = 0.0
    early_amp: float = 0.0
    late_mid: float = 0.0
    late_amp: float = 0.0
    early_slope: float = 1.2
    late_slope: float = 0.9
    curve_kind: str = "sigmoid2"
    # Optional control points for "points" curves: (consumption_level, multiplier).
    curve_points: List[Tuple[float, float]] = field(default_factory=list)
    # Optional named parameters for custom curve functions.
    curve_params: Dict[str, float] = field(default_factory=dict)
    profession_multipliers: Dict[str, float] = field(default_factory=dict)


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
    # Profession breakdown per staffed level; values sum to
    # N_EMPLOYEES_PER_LEVEL. Empty for non-staffable buildings.
    professions: Dict[str, int] = field(default_factory=dict)
