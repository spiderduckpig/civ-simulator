"""Per-cell good-efficiency maps.

For each tradable good we compute a multiplier per cell that combines:

  * a biome base (e.g. food is naturally strong on plains, weak on snow), and
  * a low-frequency global noise field (so there are large regions of
    surplus and deficit far apart from each other — cities inside a
    low-stone region can't just build their own quarries and must trade).

Efficiency is a pure multiplier on the relevant improvement's raw output.
Expected range is roughly [0.05, 2.0].
"""

from __future__ import annotations

from typing import Dict, List

from .constants import T, W, H, N, GOODS
from .noise import make_noise
from .models import Rivers


# ── Biome efficiency tables (per good) ─────────────────────────────────────
# Missing terrains fall back to _DEFAULT_BIOME. Numbers are chosen so the
# best biome for each good is around 1.3-1.5× and the worst is near zero.

_DEFAULT_BIOME = 0.30

_BIOME_EFF: Dict[str, Dict[int, float]] = {
    "grain": {
        T.PLAINS: 1.25, T.GRASS: 1.15, T.JUNGLE: 0.80, T.SWAMP: 0.95,
        T.FOREST: 0.70, T.DFOREST: 0.50, T.HILLS: 0.45,
        T.DESERT: 0.15, T.TUNDRA: 0.20, T.SNOW: 0.05, T.MTN: 0.05,
        T.BEACH:  0.50,
    },
    "fabric": {
        # Similar to food, but cotton is a bit more warm-climate-biased.
        T.PLAINS: 1.20, T.GRASS: 1.10, T.JUNGLE: 1.15, T.SWAMP: 0.80,
        T.FOREST: 0.55, T.DFOREST: 0.40, T.HILLS: 0.35,
        T.DESERT: 0.30, T.TUNDRA: 0.12, T.SNOW: 0.05, T.MTN: 0.05,
        T.BEACH:  0.45,
    },
    "lumber": {
        T.FOREST: 1.35, T.DFOREST: 1.55, T.JUNGLE: 1.30, T.SWAMP: 0.80,
        T.PLAINS: 0.35, T.GRASS: 0.45, T.HILLS: 0.60, T.MTN: 0.15,
        T.SNOW: 0.10, T.TUNDRA: 0.20, T.DESERT: 0.08, T.BEACH: 0.10,
    },
    "copper_ore": {
        T.MTN: 1.55, T.HILLS: 1.20, T.SNOW: 0.90, T.DESERT: 0.50,
        T.PLAINS: 0.25, T.GRASS: 0.25, T.FOREST: 0.40, T.DFOREST: 0.50,
        T.JUNGLE: 0.30, T.SWAMP: 0.20, T.TUNDRA: 0.60, T.BEACH: 0.10,
    },
    "stone": {
        T.HILLS: 1.30, T.MTN: 1.50, T.SNOW: 1.00,
        T.PLAINS: 0.40, T.GRASS: 0.40, T.FOREST: 0.55, T.DFOREST: 0.65,
        T.JUNGLE: 0.40, T.SWAMP: 0.25, T.DESERT: 0.60, T.TUNDRA: 0.70,
        T.BEACH: 0.30,
    },
    "copper": {
        # Metal is smelted, not extracted — biome modulation is mild.
        T.HILLS: 1.20, T.MTN: 1.10, T.PLAINS: 0.95, T.GRASS: 0.95,
        T.FOREST: 0.85, T.DFOREST: 0.75, T.DESERT: 0.80, T.TUNDRA: 0.70,
        T.SNOW: 0.50, T.JUNGLE: 0.65, T.SWAMP: 0.55, T.BEACH: 0.70,
    },
}

# Food near rivers gets a direct bump (fertile valleys).
_RIVER_GRAIN_BONUS = 0.6

# Regional noise frequency. Smaller → larger contiguous regions. 0.06 gives
# features roughly 16-20 cells across on a 160×100 map — big enough that a
# whole civ can sit inside one region and have to import from another.
_NOISE_FREQ = 0.06

# Per-good seed offsets so each good's regional pattern is independent.
_SEED_OFFSETS: Dict[str, int] = {
    "grain":  11111,
    "lumber": 22222,
    "copper_ore": 33333,
    "stone":  44444,
    "copper": 55555,
    "fabric": 66666,
    "bread":  77777,
    "clothes": 88888,
}


def gen_efficiency_maps(
    ter: List[int], rivers: Rivers, seed: int, tm: List[float] | None = None,
) -> Dict[str, List[float]]:
    """Return {good: list[float] of length N}. See module docstring.

    For fabric we apply an additional temperature multiplier so cotton is
    strongest in hot equatorial bands and weak in cold polar bands.
    """
    cell_river = rivers.cell_river
    out: Dict[str, List[float]] = {}
    for good in GOODS:
        table = _BIOME_EFF.get(good, {})
        rng = make_noise(seed + _SEED_OFFSETS.get(good, 0))
        field: List[float] = [0.0] * N
        for y in range(H):
            for x in range(W):
                i = y * W + x
                base = table.get(ter[i], _DEFAULT_BIOME)
                if good in ("grain", "fabric") and i in cell_river:
                    base = min(1.8, base + _RIVER_GRAIN_BONUS)

                if good == "fabric" and tm is not None:
                    # Clamp to [0, 1] and heavily reward warm climates.
                    t = max(0.0, min(1.0, tm[i]))
                    # ~0.20x at very cold poles, up to ~1.45x at hot equator.
                    temp_mult = 0.20 + (t ** 1.6) * 1.25
                    base *= temp_mult

                # Single-octave low-frequency noise → roughly [0.55, 1.45]
                n = rng(x * _NOISE_FREQ, y * _NOISE_FREQ)
                noise_mult = 1.0 + n * 0.45
                field[i] = max(0.0, base * noise_mult)
        out[good] = field
    return out


# ── Helpers for production / profitability ────────────────────────────────

# Map each goods-producing improvement to the good it primarily produces.
# Used by the city-development profitability pick and by the UI tooltip.
from .constants import IMP  # noqa: E402  (import after module-level code)

IMP_PRIMARY_GOOD: Dict[int, str] = {
    IMP.FARM:     "grain",
    IMP.FISHERY:  "grain",
    IMP.COTTON:   "fabric",
    IMP.MINE:     "copper_ore",
    IMP.QUARRY:   "stone",
    IMP.LUMBER:   "lumber",
    IMP.SMITHERY: "copper",
}


def city_avg_efficiency(tiles: List[int], good_efficiency: Dict[str, List[float]]) -> Dict[str, float]:
    """Average efficiency per good across a city's assigned tiles. Returns
    1.0 per good if tiles is empty (safe default)."""
    if not tiles:
        return {g: 1.0 for g in good_efficiency}
    out: Dict[str, float] = {}
    n = len(tiles)
    for good, field in good_efficiency.items():
        total = 0.0
        for c in tiles:
            if 0 <= c < N:
                total += field[c]
        out[good] = total / n if n else 1.0
    return out
