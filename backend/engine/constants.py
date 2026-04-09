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
    "fish": "🐟", "gems": "💎", "wood": "🪵", "stone": "🪨",
    "spices": "🌶", "ivory": "🦷",
}


class IMP:
    NONE    = 0
    FARM    = 1
    MINE    = 2
    LUMBER  = 3
    QUARRY  = 4
    PASTURE = 5


IMP_COLORS = {
    IMP.FARM:    "#c8a000",
    IMP.MINE:    "#888888",
    IMP.LUMBER:  "#44aa22",
    IMP.QUARRY:  "#999999",
    IMP.PASTURE: "#77bb55",
}

IMP_NAMES = {
    IMP.NONE:    "—",
    IMP.FARM:    "Farm",
    IMP.MINE:    "Mine",
    IMP.LUMBER:  "Lumber",
    IMP.QUARRY:  "Quarry",
    IMP.PASTURE: "Pasture",
}

CAN_FARM = {T.PLAINS, T.GRASS, T.JUNGLE, T.SWAMP}
RES_LIST = ["iron", "gold", "horses", "wheat", "fish", "gems", "wood", "stone", "spices", "ivory"]

PRE    = ["Ar","Bal","Cor","Dra","El","Fal","Gor","Ha","Ith","Jar","Kel","Lor","Mar","Nor","Or","Par","Qar","Ren","Sol","Tar","Ul","Val","Wor","Xen","Yr","Zan","Ak","Bri","Cael","Dur","Esh","Fen","Gil","Hel","Iro","Jul","Kha","Lun","Myr","Niv","Osh","Pyr","Rha","Syr","Thal","Ur","Ves","Wyn","Xar","Yth","Zul"]
MID_S  = ["an","eth","in","on","ul","ash","ith","or","en","al","os","ur","ak","em","id","ar","el","ok","un","is"]
SUF_S  = ["ia","os","um","is","ar","en","oth","ax","ium","ica","esh","and","or","heim","gard","rok","ven","dale","mere","hold","stan","land","rea","nia","tia"]
CPRE   = ["New ","Fort ","Port ","Saint ","North ","South ","East ","West ","Old ","Great ","","","","","","","","","",""]
CSUF   = ["ton","burg","ville","haven","ford","field","gate","bridge","keep","watch","holm","stead","crest","fall","shore","wood","vale","moor","peak","port","bay","well","dale"]
LF     = ["Arak","Belen","Cyra","Dorn","Eska","Fenn","Gael","Hira","Ivak","Jael","Kira","Lorn","Mira","Nael","Orik","Pala","Rath","Sela","Tarn","Ula","Vorn","Wyra","Xael","Yara","Zorn","Alys","Bram","Cassia","Theron","Lysa","Magnus","Freya"]
LL     = ["the Bold","the Wise","Ironhand","Stormborn","Goldeneye","the Just","the Cruel","the Great","the Conqueror","Peacemaker","the Silent","Sunbringer","the Unyielding","the Cunning","the Mad","the Young"]

CIV_PALETTE = ["#e74c3c","#3498db","#f39c12","#2ecc71","#9b59b6","#e67e22","#1abc9c","#c0392b","#2980b9","#27ae60","#8e44ad","#d35400","#16a085","#f1c40f","#e84393","#00b894","#6c5ce7","#fd79a8","#00cec9","#d63031","#0984e3","#00b4d8","#a29bfe","#636e72","#b2bec3"]

MIN_CITY_DIST = 9

DEFAULT_PARAMS = {
    "river_pref":  3.0,
    "coast_pref":  2.5,
    "max_civs":    14,
    "spawn_rate":  35,
}
