import math
import random as _random

from .constants import W, H, N, T, RES_LIST, IMP
from .noise import make_noise, fbm
from .helpers import neighbors, is_land
from .models import Rivers, MapData
from .regions import gen_efficiency_maps


# ── River generation ─────────────────────────────────────────────────────────

def _source_too_close(cell: int, sources: list, min_dist: int) -> bool:
    """Check if a cell is within min_dist Manhattan distance of any existing source."""
    cx, cy = cell % W, cell // W
    for s in sources:
        sx, sy = s % W, s // W
        if abs(cx - sx) + abs(cy - sy) < min_dist:
            return True
    return False


def gen_rivers(hm: list, ter: list, seed: int) -> Rivers:
    """
    Generate rivers with erosion simulation.

    Each river carves into the heightmap as it flows, creating natural valleys.
    The erosion amount scales with river length (more water = more erosion).
    Rivers can merge into existing rivers (tributaries).
    Sources must be well-spaced apart.
    """
    rng = make_noise(seed + 7777)
    num_attempts = 150
    min_source_dist = 6  # min Manhattan distance between river sources
    all_paths = []
    global_used: set = set()
    sources: list = []  # track source cells for spacing

    for r in range(num_attempts):
        # Find a high-altitude start cell, well-spaced from existing sources
        start_cell = -1
        start_h = 0.0
        for att in range(120):
            x = int(5 + ((rng(r * 3.1 + att * 7.3, r * 1.7 + att * 2.9) + 1) / 2) * (W - 10))
            y = int(5 + ((rng(r * 2.3 + att * 5.1, r * 4.1 + att * 1.3) + 1) / 2) * (H - 10))
            i = y * W + x
            if (hm[i] > 0.50 and hm[i] < 0.80
                    and ter[i] >= T.PLAINS
                    and ter[i] not in (T.MTN, T.SNOW)
                    and i not in global_used
                    and hm[i] > start_h
                    and not _source_too_close(i, sources, min_source_dist)):
                start_h = hm[i]
                start_cell = i

        if start_cell == -1:
            continue

        # Flow with erosion: at each step, consider neighbors within an
        # erosion threshold of cur height, pick the lowest, carve it down.
        path = [start_cell]
        visited = {start_cell}
        cur = start_cell
        reached_end = False

        for step in range(800):
            # Erosion power grows with river length (more water downstream)
            erosion_reach  = 0.3 + step * 0.05   # how far "uphill" we can look
            erosion_carve  = 0.02 + step * 0.001    # how much we carve the chosen cell
            erosion_spread = erosion_carve * 0.3     # how much we carve neighbours

            # Gather all non-visited neighbours
            candidates = []
            for n in neighbors(cur):
                if n in visited:
                    continue
                # Optional: penalize coiling by checking if n touches other visited cells
                # To prevent massive twisty clumps, skip candidates touching older parts of the river
                touches_old = sum(1 for nn in neighbors(n) if nn in visited and nn != cur)
                if touches_old > 0:
                    continue
                candidates.append(n)

            if not candidates:
                break

            # First: try strictly downhill (prefer natural flow)
            downhill = [(n, hm[n]) for n in candidates if hm[n] < hm[cur]]

            if downhill:
                # Pick the lowest downhill neighbour
                best_n = min(downhill, key=lambda pair: pair[1])[0]
            else:
                # No downhill: try erosion — consider cells within reach
                erodable = [(n, hm[n]) for n in candidates if hm[n] < hm[cur] + erosion_reach]
                if not erodable:
                    break
                best_n = min(erodable, key=lambda pair: pair[1])[0]

            # Apply erosion: carve the chosen cell and spread to its neighbours
            hm[best_n] = min(hm[best_n], hm[cur] - 0.001)  # ensure downhill
            hm[best_n] -= erosion_carve
            for nb in neighbors(best_n):
                if nb != cur and 0 <= nb < N:
                    hm[nb] -= erosion_spread

            visited.add(best_n)
            path.append(best_n)

            # Reached ocean
            if ter[best_n] <= T.COAST:
                reached_end = True
                break

            # Joined an existing river (tributary)
            if best_n in global_used:
                reached_end = True
                break

            cur = best_n

        if reached_end and len(path) >= 15:
            all_paths.append(path)
            global_used.update(path)
            sources.append(start_cell)

    cell_river: set = set()
    for p in all_paths:
        cell_river.update(p)

    return Rivers(paths=all_paths, cell_river=cell_river)

def cell_coastal(cell: int, ter: list) -> bool:
    for n in neighbors(cell):
        if 0 <= n < N and ter[n] in (T.OCEAN, T.COAST, T.DEEP):
            return True
    return False


def cell_river_mouth(cell: int, ter: list, rivers: Rivers) -> bool:
    """A river mouth is a land cell on a river adjacent to ocean/coast."""
    if cell not in rivers.cell_river:
        return False
    if ter[cell] <= T.COAST:
        return False
    for n in neighbors(cell):
        if 0 <= n < N and ter[n] in (T.OCEAN, T.COAST, T.DEEP):
            return True
    return False


# ── Map generation ────────────────────────────────────────────────────────────

def gen_map(seed: int) -> MapData:
    n1 = make_noise(seed)
    n2 = make_noise(seed + 1000)
    n3 = make_noise(seed + 2000)
    n4 = make_noise(seed + 3000)

    hm  = [0.0] * N   # heightmap
    mm  = [0.0] * N   # moisture map
    tm  = [0.0] * N   # temperature map
    ter = [0]   * N   # terrain type

    for y in range(H):
        for x in range(W):
            i   = y * W + x
            nx2 = x / W
            ny  = y / H

            h = fbm(n1, nx2 * 4, ny * 4, 6) + fbm(n2, nx2 * 8, ny * 8, 4) * 0.3
            dx = (nx2 - 0.5) * 2
            dy = (ny  - 0.5) * 2
            h = (h * 0.6
                 + (1 - math.sqrt(dx * dx * 0.6 + dy * dy)) * 0.4
                 + fbm(n4, nx2 * 2.5, ny * 2.5, 3) * 0.25)

            hm[i] = h
            mm[i] = (fbm(n3, nx2 * 5, ny * 5, 4) + 1) / 2
            tm[i] = 1 - abs(ny - 0.5) * 2 + fbm(n2, nx2 * 3, ny * 3, 3) * 0.2

    # Normalise heightmap to [0, 1]
    mn = min(hm)
    mx = max(hm)
    span = mx - mn
    hm = [(v - mn) / span for v in hm]

    # Assign terrain types
    for i in range(N):
        h = hm[i]
        m = mm[i]
        t = tm[i]

        if   h < 0.28: ter[i] = T.DEEP
        elif h < 0.35: ter[i] = T.OCEAN
        elif h < 0.40: ter[i] = T.COAST
        elif h < 0.42: ter[i] = T.BEACH
        elif h < 0.75:
            if t < 0.3:
                ter[i] = T.TUNDRA if m > 0.5 else T.SNOW
            elif t > 0.7 and m < 0.3:
                ter[i] = T.DESERT
            elif t > 0.65 and m > 0.6:
                ter[i] = T.JUNGLE
            elif m > 0.65 and h < 0.55:
                ter[i] = T.SWAMP
            elif m > 0.55:
                ter[i] = T.DFOREST if h > 0.6 else T.FOREST
            elif m > 0.35:
                ter[i] = T.GRASS
            else:
                ter[i] = T.PLAINS
        elif h < 0.85: ter[i] = T.HILLS
        elif h < 0.93: ter[i] = T.MTN
        else:          ter[i] = T.SNOW

    # Place resources
    rng2 = make_noise(seed + 5000)
    res: dict = {}
    for y in range(2, H - 2, 3):
        for x in range(2, W - 2, 3):
            i = y * W + x
            t2 = ter[i]
            if t2 <= T.COAST:
                continue
            rl = (rng2(x * 0.7, y * 0.7) + 1) / 2
            if rl < 0.12:
                idx = int(rl * 100) % 4
                if t2 in (T.MTN, T.HILLS):
                    tp = ["iron", "gold", "stone", "gems"][idx % 4]
                elif t2 in (T.FOREST, T.DFOREST, T.JUNGLE):
                    tp = ["wood", "spices", "ivory"][idx % 3]
                elif t2 == T.BEACH:
                    tp = "fish"
                elif t2 in (T.PLAINS, T.GRASS):
                    tp = ["wheat", "horses"][idx % 2]
                elif t2 == T.DESERT:
                    tp = ["gold", "gems", "spices"][idx % 3]
                else:
                    tp = "iron"
                res[i] = tp

    rivers = gen_rivers(hm, ter, seed)
    impr   = [IMP.NONE] * N

    # Per-good efficiency maps: biome × low-frequency regional noise.
    good_efficiency = gen_efficiency_maps(ter, rivers, seed)

    return MapData(
        hm=hm,
        mm=mm,
        tm=tm,
        ter=ter,
        res=res,
        rivers=rivers,
        impr=impr,
        good_efficiency=good_efficiency,
    )
