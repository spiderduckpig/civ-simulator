import math
from collections import deque
from typing import Set, List, Tuple, Optional, Dict

from .constants import W, H, N, T, IMP, CAN_FARM


# ── Grid utilities ──────────────────────────────────────────────────────────

def neighbors(cell: int) -> List[int]:
    x = cell % W
    y = cell // W
    result = []
    if x > 0:     result.append(cell - 1)
    if x < W - 1: result.append(cell + 1)
    if y > 0:     result.append(cell - W)
    if y < H - 1: result.append(cell + W)
    return result


def is_land(ter: list, i: int) -> bool:
    return 0 <= i < N and ter[i] > T.COAST


def border_cells(territory: Set[int]) -> Set[int]:
    """Return cells outside territory that are adjacent to it."""
    result = set()
    for cell in territory:
        for n in neighbors(cell):
            if n not in territory:
                result.add(n)
    return result


def centroid(territory: Set[int]) -> Tuple[int, int]:
    if not territory:
        return 0, 0
    sx = sy = 0
    for cell in territory:
        sx += cell % W
        sy += cell // W
    c = len(territory)
    return sx // c, sy // c


def dist(a: int, b: int) -> int:
    return abs(a % W - b % W) + abs(a // W - b // W)


def war_key(a: int, b: int) -> str:
    lo, hi = (a, b) if a < b else (b, a)
    return f"{lo}|{hi}"


# ── Pathfinding (BFS) ────────────────────────────────────────────────────────

def find_path(
    from_cell: int,
    to_cell: int,
    territory: Set[int],
    ter: list,
) -> Optional[List[int]]:
    prev: Dict[int, int] = {from_cell: -1}
    queue = deque([from_cell])
    while queue:
        cur = queue.popleft()
        if cur == to_cell:
            path = []
            c = to_cell
            while c != -1:
                path.append(c)
                c = prev[c]
            path.reverse()
            return path
        for n in neighbors(cur):
            if n not in prev and n in territory and ter[n] != T.MTN and ter[n] != T.SNOW:
                prev[n] = cur
                queue.append(n)
    return None


# ── Connected regions (flood fill) ──────────────────────────────────────────

def find_regions(cells) -> List[List[int]]:
    cell_set = set(cells)
    visited: Set[int] = set()
    regions: List[List[int]] = []
    for cell in cell_set:
        if cell in visited:
            continue
        region = []
        queue = deque([cell])
        visited.add(cell)
        while queue:
            u = queue.popleft()
            region.append(u)
            for n in neighbors(u):
                if n in cell_set and n not in visited:
                    visited.add(n)
                    queue.append(n)
        regions.append(region)
    return regions


# ── Improvement selection ────────────────────────────────────────────────────

def best_improvement(ter: list, res: dict, cell: int) -> int:
    t = ter[cell]
    r = res.get(cell)

    if t in (T.MTN, T.HILLS):
        if r in ("iron", "gold", "gems"):
            return IMP.MINE
        return IMP.QUARRY

    if t in (T.FOREST, T.DFOREST, T.JUNGLE):
        return IMP.LUMBER

    if r == "horses":
        return IMP.PASTURE

    if t in CAN_FARM:
        return IMP.FARM

    return IMP.NONE
