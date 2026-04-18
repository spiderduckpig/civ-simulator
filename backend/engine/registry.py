"""Registry of game content: improvements, goods, and resources.

This module instantiates all ImprovementType, Good, and Resource models,
making it easy to add new ones without modifying multiple constant dicts.
"""

from .constants import IMP, GOODS, BASE_PRICES, IMP_COLORS, IMP_NAMES, RESOURCE_ICONS
from .models import ImprovementType, Good, Resource


# ── Improvement registry ─────────────────────────────────────────────────────

# Mapping of which good each improvement produces (if any)
_IMP_PRODUCES = {
    IMP.FARM:     "grain",
    IMP.FISHERY:  "grain",
    IMP.COTTON:   "fabric",
    IMP.MINE:     "copper_ore",
    IMP.QUARRY:   "stone",
    IMP.LUMBER:   "lumber",
    IMP.SMITHERY: "copper",
    IMP.WINDMILL: None,    # No direct good; provides bonuses
    IMP.PASTURE:  None,    # No direct good; provides bonuses
    IMP.PORT:     None,    # No direct good; trade bonus
    IMP.FORT:     None,    # No direct good; military
}

# Which improvements are staffable (consume workers)
_STAFFABLE_TYPES = frozenset({
    IMP.FARM, IMP.MINE, IMP.LUMBER, IMP.QUARRY, IMP.PASTURE,
    IMP.WINDMILL, IMP.PORT, IMP.SMITHERY, IMP.FISHERY, IMP.COTTON,
})

# Which improvements can be upgraded beyond level 1
_UPGRADABLE_TYPES = {
    IMP.FARM, IMP.COTTON, IMP.MINE, IMP.QUARRY, IMP.WINDMILL,
    IMP.FORT, IMP.PORT, IMP.SMITHERY, IMP.FISHERY,
}

# Maximum level each improvement can reach
_MAX_LEVELS = {
    IMP.FARM:     20,
    IMP.COTTON:   10,
    IMP.MINE:     5,
    IMP.QUARRY:   5,
    IMP.LUMBER:   1,      # Single-level flavour improvement
    IMP.PASTURE:  1,      # Single-level flavour improvement
    IMP.WINDMILL: 5,
    IMP.FORT:     5,
    IMP.PORT:     5,
    IMP.SMITHERY: 5,
    IMP.FISHERY:  5,
}


def _build_improvements() -> dict:
    """Build ImprovementType registry from constants."""
    registry = {}
    
    # NONE improvement (placeholder)
    registry[IMP.NONE] = ImprovementType(
        type_id=IMP.NONE,
        name=IMP_NAMES.get(IMP.NONE, "None"),
        color="#ffffff",
        max_level=0,
        staffable=False,
        upgradable=False,
        produces_good=None,
    )
    
    # All other improvement types
    for type_id in [IMP.FARM, IMP.COTTON, IMP.MINE, IMP.LUMBER, IMP.QUARRY, IMP.PASTURE,
                     IMP.WINDMILL, IMP.FORT, IMP.PORT, IMP.SMITHERY, IMP.FISHERY]:
        registry[type_id] = ImprovementType(
            type_id=type_id,
            name=IMP_NAMES.get(type_id, f"Improvement {type_id}"),
            color=IMP_COLORS.get(type_id, "#808080"),
            max_level=_MAX_LEVELS.get(type_id, 1),
            staffable=type_id in _STAFFABLE_TYPES,
            upgradable=type_id in _UPGRADABLE_TYPES,
            produces_good=_IMP_PRODUCES.get(type_id),
        )
    
    return registry


def _build_goods() -> dict:
    """Build Good registry from constants."""
    registry = {}
    
    for good_name in GOODS:
        # Find which improvements produce this good
        produced_by = [
            type_id for type_id, good in _IMP_PRODUCES.items()
            if good == good_name
        ]
        
        registry[good_name] = Good(
            name=good_name.capitalize(),
            base_price=BASE_PRICES.get(good_name, 1.0),
            icon="📦",  # Default icon; can be customized per good
            produced_by=produced_by,
        )
    
    return registry


def _build_resources() -> dict:
    """Build Resource registry from constants."""
    registry = {}
    
    # Map resource name to icon
    for resource_name, icon in RESOURCE_ICONS.items():
        registry[resource_name] = Resource(
            name=resource_name.capitalize(),
            icon=icon,
        )
    
    return registry


# ── Singleton registries ─────────────────────────────────────────────────────

# Build once at import time
IMPROVEMENTS = _build_improvements()
GOODS = _build_goods()
RESOURCES = _build_resources()


# ── Convenience accessors ────────────────────────────────────────────────────

def get_improvement(type_id: int) -> ImprovementType:
    """Get an improvement type by ID."""
    return IMPROVEMENTS.get(type_id)


def get_good(good_name: str) -> Good:
    """Get a good by name."""
    return GOODS.get(good_name)


def get_resource(resource_name: str) -> Resource:
    """Get a resource by name."""
    return RESOURCES.get(resource_name)


def is_improvement_staffable(type_id: int) -> bool:
    """Check if an improvement type consumes workers."""
    imp = get_improvement(type_id)
    return imp.staffable if imp else False


def is_improvement_upgradable(type_id: int) -> bool:
    """Check if an improvement type can be upgraded."""
    imp = get_improvement(type_id)
    return imp.upgradable if imp else False


def get_improvement_max_level(type_id: int) -> int:
    """Get maximum level for an improvement type."""
    imp = get_improvement(type_id)
    return imp.max_level if imp else 1
