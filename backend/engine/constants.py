W, H, CELL = 160, 100, 6
PX_W, PX_H = W * CELL, H * CELL
N = W * H


class T:
    DEEP    = 0
    OCEAN   = 1
    COAST   = 2
    BEACH   = 3
    PLAINS  = 4
    GRASS   = 5
    FOREST  = 6
    DFOREST = 7
    HILLS   = 8
    MTN     = 9
    SNOW    = 10
    DESERT  = 11
    TUNDRA  = 12
    SWAMP   = 13
    JUNGLE  = 14


TERRAIN_COLORS = {
    T.DEEP:    "#0a1628",
    T.OCEAN:   "#0f2847",
    T.COAST:   "#1a4a6e",
    T.BEACH:   "#d4c078",
    T.PLAINS:  "#7ab648",
    T.GRASS:   "#5a9e3a",
    T.FOREST:  "#2d7a2d",
    T.DFOREST: "#1a5c1a",
    T.HILLS:   "#8a7a50",
    T.MTN:     "#6e6e6e",
    T.SNOW:    "#e8e8f0",
    T.DESERT:  "#d4a84b",
    T.TUNDRA:  "#a8b8b0",
    T.JUNGLE:  "#1a6830",
    T.SWAMP:   "#3a5a3a",
}

TERRAIN_NAMES = {
    T.DEEP:    "Deep Ocean",
    T.OCEAN:   "Ocean",
    T.COAST:   "Coast",
    T.BEACH:   "Beach",
    T.PLAINS:  "Plains",
    T.GRASS:   "Grassland",
    T.FOREST:  "Forest",
    T.DFOREST: "Dense Forest",
    T.HILLS:   "Hills",
    T.MTN:     "Mountains",
    T.SNOW:    "Snow Peak",
    T.DESERT:  "Desert",
    T.TUNDRA:  "Tundra",
    T.JUNGLE:  "Jungle",
    T.SWAMP:   "Swamp",
}

RESOURCE_ICONS = {
    "iron": "⛏", "gold": "✦", "horses": "🐎", "wheat": "🌾",
    "fish": "🐟", "gems": "💎", "wood": "🪵", "stone": "🪨", "fabric": "🧵",
    "spices": "🌶", "ivory": "🦷",
}


class IMP:
    NONE     = 0
    FARM     = 1
    MINE     = 2  # Produces Copper Ore
    LUMBER   = 3
    QUARRY   = 4  # Produces Stone
    PASTURE  = 5
    WINDMILL = 6
    FORT     = 7
    PORT     = 8
    SMITHERY = 9
    FISHERY  = 10  # Coastal grain + modest trade
    COTTON   = 11  # Produces fabric

# Bit-packed encoding: low 5 bits = type (0-31), remaining bits = level-1.
# Use helpers in engine.improvements to pack/unpack — never touch bits directly.
IMP_TYPE_BITS = 5
IMP_TYPE_MASK = (1 << IMP_TYPE_BITS) - 1     # 0x1F
IMP_LEVEL_STEP = 1 << IMP_TYPE_BITS          # 32

IMP_COLORS = {
    IMP.FARM:     "#c8a000",
    IMP.MINE:     "#666666",
    IMP.LUMBER:   "#44aa22",
    IMP.QUARRY:   "#999999",
    IMP.PASTURE:  "#77bb55",
    IMP.WINDMILL: "#e8d088",
    IMP.FORT:     "#444444",
    IMP.PORT:     "#336699",
    IMP.SMITHERY: "#b84422",
    IMP.FISHERY:  "#4aaed8",
    IMP.COTTON:   "#d8e3a5",
}

IMP_NAMES = {
    IMP.NONE:     "—",
    IMP.FARM:     "Farm",
    IMP.MINE:     "Mine",
    IMP.LUMBER:   "Lumber",
    IMP.QUARRY:   "Quarry",
    IMP.PASTURE:  "Pasture",
    IMP.WINDMILL: "Windmill",
    IMP.FORT:     "Fort",
    IMP.PORT:     "Port",
    IMP.SMITHERY: "Smithery",
    IMP.FISHERY:  "Fishery",
    IMP.COTTON:   "Cotton Farm",
}

# City focus — what the city prioritises. Biases its build queue and the
# improvements it will accept on its tiles. The HMM transition logic lives
# in city_dev.py.
class FOCUS:
    FARMING = 0
    MINING  = 1
    DEFENSE = 2
    TRADE   = 3

FOCUS_NAMES = {
    FOCUS.FARMING: "Farming",
    FOCUS.MINING:  "Mining",
    FOCUS.DEFENSE: "Defense",
    FOCUS.TRADE:   "Trade",
}

FOCUS_COLORS = {
    FOCUS.FARMING: "#c8a000",
    FOCUS.MINING:  "#6a737d",
    FOCUS.DEFENSE: "#d73a49",
    FOCUS.TRADE:   "#3b8bd6",
}

CAN_FARM = {T.PLAINS, T.GRASS, T.JUNGLE, T.SWAMP}
RES_LIST = ["iron", "gold", "horses", "wheat", "fish", "gems", "wood", "stone", "spices", "ivory"]

CIV_PALETTE = ["#e74c3c","#3498db","#f39c12","#2ecc71","#9b59b6","#e67e22","#1abc9c","#c0392b","#2980b9","#27ae60","#8e44ad","#d35400","#16a085","#f1c40f","#e84393","#00b894","#6c5ce7","#fd79a8","#00cec9","#d63031","#0984e3","#00b4d8","#a29bfe","#636e72","#b2bec3"]

MIN_CITY_DIST = 9

# ── Army / fort / siege constants ────────────────────────────────────────────
ARMY_BASE_STRENGTH    = 100.0
# Strength multiplier by fort level (lvl 1..5 → index 0..4)
ARMY_FORT_MULT        = [1.0, 1.5, 2.1, 2.8, 3.7]
ARMY_MOVE_RANGE       = 2          # cells per tick (lenient)
ARMY_SUPPLY_FREE_DIST = 6          # supply doesn't decay within this radius of origin
ARMY_SUPPLY_DECAY     = 0.55       # per cell beyond free dist, per tick
ARMY_SUPPLY_REPLEN    = 7.0        # replenish/tick when on a farm/pasture/fishery cell
ARMY_COMBAT_RANGE     = 1          # adjacent
ARMY_COMBAT_DAMAGE    = 0.07       # base damage coefficient (army vs army)
ARMY_CITY_DAMAGE      = 0.05       # base damage coefficient (army vs city)
ARMY_ENGAGE_RANGE     = 4          # range at which army "sees" enemy army for HMM
ARMY_TARGET_CITY_RANGE = 35        # range at which army considers city as a target
ARMY_RESPAWN_DELAY    = 25         # ticks before a fort can respawn a destroyed army
ARMY_PATHFIND_BUDGET  = 400        # max BFS frontier cells per pathfind (cap cost)

# Organisation thresholds (retreat / recovery). Scale-free fractions of 100.
ARMY_BROKEN_ORG       = 5.0        # at/below: army is "broken", enters retreat
ARMY_RECOVER_ORG      = 45.0       # above: retreating army resumes normal HMM
ARMY_FRONT_DIST       = 10         # fort/army is "near the front" within this cells

# Fortification bonuses (expressed as fractions — damage taken is divided
# by (1 + fortification), so +0.5 means 33% less damage).
FORT_BONUS_PER_LEVEL   = 0.18      # +18% fortification per fort level
CITY_DEFENSE_BONUS     = 0.35      # flat bonus when sitting on a friendly city
CAPITAL_DEFENSE_BONUS  = 0.25      # additional bonus on top of CITY_DEFENSE_BONUS for capitals
FRIENDLY_TERRAIN_BONUS = 0.08      # small defensive edge anywhere inside own borders

FORT_METAL_UPKEEP     = 10.0       # per fort level per tick
FORT_BUILD_METAL_COST = 25.0       # metal required (and consumed) to raise a new fort
CITY_BASE_HP          = 65.0
CAPITAL_HP_BONUS      = 50.0
FORT_HP_BONUS         = 25.0       # per level when a fort sits on the same cell
CITY_HP_REGEN         = 0.6        # per tick when not under attack

# ── City development (investment) ────────────────────────────────────────────
# Upgrades are paid from a city's own gold stockpile + physical materials.
INVEST_MAX_PER_TICK     = 3      # allow multiple actions if budget permits
INVEST_PERIOD_TICKS     = 2      # check every tick

FOCUS_HMM_PERIOD        = 12     # how often a city reconsiders its focus

# ── Employment ──────────────────────────────────────────────────────────────
# Buildings take workers. A level-K building can be staffed up to K levels
# (one level = ``N_EMPLOYEES_PER_LEVEL`` people). Production scales with the
# fraction of levels that are actually staffed. See engine.employment.
N_EMPLOYEES_PER_LEVEL   = 10

# ── Economy ──
GOODS = ["grain", "bread", "lumber", "copper_ore", "stone", "copper", "fabric", "clothes"]
BASE_PRICES = {
    "grain":  1.0,
    "bread":  2.2,
    "lumber": 2.0,
    "copper_ore": 3.0,
    "stone":  6.0,
    "copper": 9.0,
    "fabric": 4.0,
    "clothes": 8.0,
}

# Trade / Arbitrage constants
TRADE_PERIOD_TICKS = 5
TRANSPORT_COST_PER_DIST = 0.5

DEFAULT_PARAMS = {
    "river_pref":  3.0,
    "coast_pref":  2.5,
    "max_civs":    14,
    "spawn_rate":  35,
}
