from dataclasses import dataclass, field
from typing import Set, List, Dict, Optional, Any

@dataclass
class City:
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

    def to_dict(self) -> dict:
        return {
            "cell": self.cell,
            "name": self.name,
            "population": self.population,
            "is_capital": self.is_capital,
            "founded": self.founded,
            "trade": self.trade,
            "wealth": self.wealth,
            "focus": self.focus,
            "near_river": self.near_river,
            "coastal": self.coastal,
            "food_production": self.food_production,
            "carrying_cap": self.carrying_cap,
            "tiles": self.tiles,
            "farm_tiles": self.farm_tiles,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "last_dmg_tick": self.last_dmg_tick,
        }

@dataclass
class Civ:
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
    power: float = 10.0
    _settle_score: float = float("-inf")
    _target_settle_cell: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "leader": self.leader,
            "onom": self.onom,
            "color": self.color,
            "capital": self.capital,
            "territory": self.territory,
            "cities": [c.to_dict() for c in self.cities],
            "population": self.population,
            "military": self.military,
            "gold": self.gold,
            "food": self.food,
            "tech": self.tech,
            "culture": self.culture,
            "age": self.age,
            "alive": self.alive,
            "integrity": self.integrity,
            "aggressiveness": self.aggressiveness,
            "relations": self.relations,
            "allies": self.allies,
            "power": self.power,
            "_settle_score": self._settle_score,
            "_target_settle_cell": self._target_settle_cell,
        }

@dataclass
class War:
    key: str
    att: int
    def_: int
    start: int
    confidence_a: float
    confidence_d: float
    exhaustion_a: float
    exhaustion_d: float
    ended: bool = False

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "att": self.att,
            "def": self.def_,
            "start": self.start,
            "confidence_a": self.confidence_a,
            "confidence_d": self.confidence_d,
            "exhaustion_a": self.exhaustion_a,
            "exhaustion_d": self.exhaustion_d,
            "ended": self.ended,
        }
