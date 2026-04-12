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
    roads: List[int] = field(default_factory=list)
    road_paths: List[List[int]] = field(default_factory=list)
    metal_stock: float = 5.0
    fort_cooldowns: Dict[int, int] = field(default_factory=dict)
    _settle_score: float = float("-inf")
    _target_settle_cell: Optional[int] = None

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
    commander: dict
    behavior: str
    objective: dict
    fortification: float
    fort_source: str

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

