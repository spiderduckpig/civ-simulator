import math
import heapq
from collections import deque
from typing import Set, List, Tuple, Optional, Dict, Iterable

from .constants import W, H, N, T

from .models import Rivers

def cell_on_river(cell: int, rivers: Rivers) -> bool:
    return cell in rivers.cell_river


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


# ── Pathfinding (Dijkstra, bends toward cities) ─────────────────────────────

def find_path(
    from_cell: int,
    to_cell: int,
    territory: Set[int],
    ter: list,
    city_cells: Set[int] = None,
    road_cells: Set[int] = None,
) -> Optional[List[int]]:
    """Weighted shortest path. Cells near cities are cheaper so roads
    naturally route through intermediate towns.  Existing road cells
    are nearly free so new roads merge onto the existing network."""
    if city_cells is None:
        city_cells = set()
    if road_cells is None:
        road_cells = set()

    # Pre-compute cheap cells: city cells and their neighbors
    near_city = set()
    for cc in city_cells:
        for n in neighbors(cc):
            near_city.add(n)

    costs: Dict[int, float] = {from_cell: 0.0}
    prev: Dict[int, int] = {from_cell: -1}
    heap = [(0.0, from_cell)]

    while heap:
        cost, cur = heapq.heappop(heap)
        if cur == to_cell:
            path = []
            c = to_cell
            while c != -1:
                path.append(c)
                c = prev[c]
            path.reverse()
            return path
        if cost > costs.get(cur, float("inf")):
            continue
        for n in neighbors(cur):
            if n not in territory or ter[n] == T.MTN or ter[n] == T.SNOW:
                continue
            # Movement cost: existing roads are nearly free, cities are cheap
            if n in road_cells:
                step = 0.05
            elif n in city_cells:
                step = 0.1
            elif n in near_city:
                step = 0.3
            else:
                step = 1.0
            nc = cost + step
            if nc < costs.get(n, float("inf")):
                costs[n] = nc
                prev[n] = cur
                heapq.heappush(heap, (nc, n))
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


# ── Land pathfinding ─────────────────────────────────────────────────────────
# Two variants over the walkable-land mask:
#   - land_bfs_path: plain BFS, returns shortest path in step count.
#     Kept for flood-fill-ish callers that want predictable radial expansion.
#   - land_astar_path: A* with Manhattan heuristic. Same result on this
#     uniform-cost 4-connected grid (Manhattan is tight admissible), but
#     explores O(d) cells in open terrain instead of O(d²). Preferred for
#     per-tick army movement where long targets are common.
# Both take a `blocked` wall set and a `frontier_budget` hard cap so a
# single unreachable target can never pin the sim.

def _land_walkable(ter: list, c: int) -> bool:
    if c < 0 or c >= N:
        return False
    t = ter[c]
    if t <= T.COAST or t == T.MTN or t == T.SNOW:
        return False
    return True


def land_bfs_path(
    from_cell: int,
    to_cell: int,
    ter: list,
    blocked: Optional[Iterable[int]] = None,
    *,
    frontier_budget: int = 800,
) -> Optional[List[int]]:
    """BFS shortest path over walkable land. Returns None if unreachable or
    if the frontier budget runs out."""
    if from_cell == to_cell:
        return [from_cell]

    blocked_set: Set[int] = set(blocked) if blocked else set()
    blocked_set.discard(to_cell)

    visited = {from_cell: -1}
    q: deque = deque([from_cell])
    expanded = 0
    while q:
        cur = q.popleft()
        expanded += 1
        if expanded > frontier_budget:
            return None
        for n in neighbors(cur):
            if n in visited:
                continue
            if n != to_cell and (not _land_walkable(ter, n) or n in blocked_set):
                continue
            visited[n] = cur
            if n == to_cell:
                path = [n]
                p = cur
                while p != -1:
                    path.append(p)
                    p = visited[p]
                path.reverse()
                return path
            q.append(n)
    return None


def land_bfs_distance_field(
    from_cell: int,
    ter: list,
    blocked: Optional[Iterable[int]] = None,
    *,
    frontier_budget: int = 4000,
) -> Dict[int, int]:
    """BFS from ``from_cell`` over walkable land; returns ``{cell: distance}``.

    Shared flow-field primitive: when many armies head to the same target,
    build this field once and let every army greedy-descend it instead of
    running per-army A*. Pairs with priority-sorted movement (closest
    first) to produce natural column-flow through chokepoints — the lead
    army vacates its cell, the next army descends into it, and so on.

    ``blocked`` cells (e.g. enemy armies) are treated as walls. Friendlies
    should NOT be passed in — occupancy is resolved at step-time so the
    field remains valid across an entire moving group. ``from_cell`` is
    always included even if it was in ``blocked``.
    """
    blocked_set: Set[int] = set(blocked) if blocked else set()
    blocked_set.discard(from_cell)

    dist_map: Dict[int, int] = {from_cell: 0}
    q: deque = deque([from_cell])
    expanded = 0
    while q:
        cur = q.popleft()
        expanded += 1
        if expanded > frontier_budget:
            break
        cd = dist_map[cur]
        for n in neighbors(cur):
            if n in dist_map:
                continue
            if not _land_walkable(ter, n) or n in blocked_set:
                continue
            dist_map[n] = cd + 1
            q.append(n)
    return dist_map


def land_astar_path(
    from_cell: int,
    to_cell: int,
    ter: list,
    blocked: Optional[Iterable[int]] = None,
    *,
    frontier_budget: int = 800,
) -> Optional[List[int]]:
    """A* shortest path over walkable land using Manhattan heuristic.

    On our 4-connected uniform-cost grid Manhattan is tight and consistent,
    so the first pop of the goal is optimal and a closed set is safe.
    """
    if from_cell == to_cell:
        return [from_cell]

    blocked_set: Set[int] = set(blocked) if blocked else set()
    blocked_set.discard(to_cell)

    tx = to_cell % W
    ty = to_cell // W

    def h(cell: int) -> int:
        return abs((cell % W) - tx) + abs((cell // W) - ty)

    parent: Dict[int, int] = {from_cell: -1}
    g: Dict[int, int] = {from_cell: 0}
    closed: Set[int] = set()
    # Heap entries: (f, tiebreak, cell). Tiebreak keeps ordering deterministic
    # and avoids comparing ints to ints on equal f.
    open_heap: list = [(h(from_cell), 0, from_cell)]
    counter = 1
    expanded = 0

    while open_heap:
        _, _, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        if cur == to_cell:
            path: List[int] = []
            c = cur
            while c != -1:
                path.append(c)
                c = parent[c]
            path.reverse()
            return path
        closed.add(cur)
        expanded += 1
        if expanded > frontier_budget:
            return None

        ng = g[cur] + 1
        for n in neighbors(cur):
            if n in closed:
                continue
            if n != to_cell and (not _land_walkable(ter, n) or n in blocked_set):
                continue
            if n in g and ng >= g[n]:
                continue
            g[n] = ng
            parent[n] = cur
            heapq.heappush(open_heap, (ng + h(n), counter, n))
            counter += 1
    return None
