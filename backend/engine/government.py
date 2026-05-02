"""Government finance helpers for taxes, fort funding, and planning."""

from __future__ import annotations

from typing import Optional

from .constants import (
    N,
    BASE_PRICES,
    GOV_IMPROVEMENT_PROFILES,
    FORT_REOPEN_TREASURY_MIN,
)
from .helpers import dist
from .models import (
    Civ, City, Government, FortFunding, GovernmentOwnershipProfile,
    GovernmentConstructionOrder, GovernmentFlow,
)


GOV_CONSTRUCTION_QUEUE_LIMIT = 4
GOV_CONSTRUCTION_REVENUE_MARGIN = 1.0
GOV_CONSTRUCTION_SPEND_MULT = 3.0
GOV_HOSTILE_RELATION_THRESHOLD = 0.0
UNEMPLOYMENT_BENEFIT_BASE = 0.12
UNEMPLOYMENT_BENEFIT_INCOME_MULT = 0.08


def _available_net_revenue(gov: Government) -> float:
    return max(0.0, float(gov.last_tax_collected) - float(gov.last_fort_spending))


def reset_government_flows(gov: Government) -> None:
    if not hasattr(gov, "last_flows") or gov.last_flows is None:
        gov.last_flows = []
    else:
        gov.last_flows.clear()
    gov.last_benefit_spending = 0.0
    gov.last_build_spending = 0.0
    gov.last_fort_spending = 0.0
    gov.last_interest_charged = 0.0


def record_government_flow(
    gov: Government,
    *,
    kind: str,
    label: str,
    amount: float,
    category: str = "income",
    city_cell: int | None = None,
    city_name: str = "",
    note: str = "",
) -> None:
    if not hasattr(gov, "last_flows") or gov.last_flows is None:
        gov.last_flows = []
    gov.last_flows.append(GovernmentFlow(
        kind=kind,
        label=label,
        amount=float(amount),
        category=category,
        city_cell=city_cell,
        city_name=city_name,
        note=note,
    ))


def _fort_profile() -> GovernmentOwnershipProfile:
    return GOV_IMPROVEMENT_PROFILES["fort"]


def _fort_states(gov: Government) -> dict[int, FortFunding]:
    if not hasattr(gov, "owned_improvements") or gov.owned_improvements is None:
        gov.owned_improvements = {}
    legacy_forts = getattr(gov, "forts", None) or {}
    forts = gov.owned_improvements.setdefault("fort", legacy_forts)
    gov.forts = forts
    return forts


def ensure_government(civ: Civ) -> Government:
    gov = getattr(civ, "government", None)
    if gov is None:
        gov = Government()
        civ.government = gov
    fort_profile = _fort_profile()

    if not hasattr(gov, "owned_city_buildings") or gov.owned_city_buildings is None:
        gov.owned_city_buildings = {}
    forts = _fort_states(gov)

    # Keep runtime state synced to canonical profile defaults.
    gov.fort_upkeep_goods = dict(fort_profile.upkeep_goods)
    gov.fort_buffer_on = float(fort_profile.buffer_on)
    gov.fort_buffer_off = float(fort_profile.buffer_off)

    gov.forts = forts
    return gov


def sync_fort_funding(civ: Civ, impr: list) -> Government:
    gov = ensure_government(civ)
    forts = _fort_states(gov)
    live = set()
    for cell in getattr(civ, "territory", set()):
        if 0 <= cell < N and impr[cell]:
            raw = impr[cell]
            from .improvements import imp_type
            from .constants import IMP
            if imp_type(raw) == IMP.FORT:
                live.add(cell)
                if cell not in forts:
                    forts[cell] = FortFunding()
    for cell in list(forts.keys()):
        if cell not in live:
            forts.pop(cell, None)
    return gov


def fort_host_city(civ: Civ, fort_cell: int) -> Optional[City]:
    for city in getattr(civ, "cities", []):
        tiles = getattr(city, "tiles", None) or []
        if fort_cell in tiles:
            return city
    if not civ.cities:
        return None
    best_city = min(civ.cities, key=lambda city: abs(city.cell - fort_cell))
    return best_city


def fort_upkeep_value(city: City, gov: Government) -> float:
    total = 0.0
    for good, qty in gov.fort_upkeep_goods.items():
        total += float(qty) * city.prices.get(good, BASE_PRICES.get(good, 1.0))
    return total


def fort_construction_spend_value(city: City, gov: Government) -> float:
    return max(1.0, fort_upkeep_value(city, gov) * GOV_CONSTRUCTION_SPEND_MULT)


def fort_is_active(civ: Civ, cell: int) -> bool:
    gov = ensure_government(civ)
    state = _fort_states(gov).get(cell)
    return bool(state and state.active)


def collect_tax(civ: Civ) -> float:
    gov = ensure_government(civ)
    total = 0.0
    for city in getattr(civ, "cities", []):
        taxable = max(0.0, getattr(city, "income_total", 0.0)) * gov.tax_rate
        if taxable <= 0:
            continue
        city.gold = max(0.0, city.gold - taxable)
        total += taxable
    gov.treasury += total
    gov.last_tax_collected = total
    record_government_flow(
        gov,
        kind="tax",
        label="Taxes",
        amount=total,
        category="income",
        note=f"tax rate {gov.tax_rate:.2%}",
    )
    return total


def pay_unemployment_benefits(civ: Civ) -> float:
    gov = ensure_government(civ)
    total = 0.0
    if gov.treasury <= 0.0:
        return 0.0

    for city in getattr(civ, "cities", []):
        unemployed = max(0, int(getattr(city, "unemployed_pop", 0) or 0))
        if unemployed <= 0:
            continue

        per_person = max(
            UNEMPLOYMENT_BENEFIT_BASE,
            float(getattr(city, "income_per_person", 0.0)) * UNEMPLOYMENT_BENEFIT_INCOME_MULT,
        )
        benefit = min(gov.treasury, per_person * unemployed)
        if benefit <= 0.0:
            continue

        gov.treasury -= benefit
        total += benefit
        city.gold += benefit
        city.income_misc += benefit
        # Record actual per-unemployed payment for use in consumption/wage
        # accounting. When treasury is insufficient, this may be less than
        # the nominal `per_person` value.
        actual_per_person = benefit / unemployed if unemployed > 0 else 0.0
        city.last_unemployment_benefit_per_person = actual_per_person
        record_government_flow(
            gov,
            kind="unemployment_benefit",
            label="Unemployment benefits",
            amount=benefit,
            category="expense",
            city_cell=city.cell,
            city_name=city.name,
            note=f"{unemployed} unemployed × ₿{per_person:.3f}",
        )

    gov.last_benefit_spending = total
    return total


def apply_fort_demand(civ: Civ, fort_hosts: dict[int, City]) -> None:
    gov = ensure_government(civ)
    for cell, city in fort_hosts.items():
        if city is None:
            continue
        for good, qty in gov.fort_upkeep_goods.items():
            city.demand[good] = city.demand.get(good, 0.0) + float(qty)


def _enemy_border_cells(enemy: Civ, border_cache: dict[int, set[int]]) -> set[int]:
    border = set(border_cache.get(enemy.id, set()))
    if border:
        return border
    fallback = set()
    for city in getattr(enemy, "cities", []):
        fallback.add(city.cell)
    return fallback


def _host_city_for_enemy(
    civ: Civ,
    enemy: Civ,
    border_cache: dict[int, set[int]],
    impr: list,
) -> tuple[Optional[City], float, int]:
    enemy_border = _enemy_border_cells(enemy, border_cache)
    if not enemy_border or not getattr(civ, "cities", None):
        return None, float("inf"), 0

    best_city: Optional[City] = None
    best_distance = float("inf")
    best_existing_forts = 0

    for city in civ.cities:
        tiles = getattr(city, "tiles", None) or [city.cell]
        city_distance = min(dist(cell, enemy_cell) for cell in tiles for enemy_cell in enemy_border)
        if city_distance > 24:
            continue
        existing_forts = 0
        for cell in tiles:
            if 0 <= cell < len(impr):
                from .improvements import imp_type
                from .constants import IMP
                if imp_type(impr[cell]) == IMP.FORT:
                    existing_forts += 1
        if best_city is None or city_distance < best_distance or (
            city_distance == best_distance and existing_forts < best_existing_forts
        ):
            best_city = city
            best_distance = city_distance
            best_existing_forts = existing_forts

    return best_city, best_distance, best_existing_forts


def refresh_government_construction_queue(
    civ: Civ,
    civs_by_id: dict[int, Civ],
    border_cache: dict[int, set[int]],
    impr: list,
) -> None:
    gov = ensure_government(civ)
    orders: list[GovernmentConstructionOrder] = []

    for other_id, relation in sorted(civ.relations.items(), key=lambda kv: kv[1]):
        if relation >= GOV_HOSTILE_RELATION_THRESHOLD:
            continue
        enemy = civs_by_id.get(other_id)
        if enemy is None or not enemy.alive:
            continue

        host_city, frontier_distance, existing_forts = _host_city_for_enemy(civ, enemy, border_cache, impr)
        if host_city is None:
            continue

        upkeep_value = fort_upkeep_value(host_city, gov)
        spend_value = fort_construction_spend_value(host_city, gov)
        hostility = max(0.0, -relation)
        frontier_bonus = max(0.0, 24.0 - frontier_distance)
        priority = hostility * 100.0 + frontier_bonus * 2.0 - existing_forts * 8.0

        orders.append(GovernmentConstructionOrder(
            asset_key="fort",
            asset_label="Fort",
            priority=priority,
            target_civ_id=enemy.id,
            target_civ_name=enemy.name,
            host_city_cell=host_city.cell,
            host_city_name=host_city.name,
            relation=relation,
            estimated_upkeep=upkeep_value,
            estimated_spending=spend_value,
            reason=f"near {enemy.name} (rel {relation:.2f}, dist {frontier_distance:.0f})",
        ))

    orders.sort(key=lambda order: (-order.priority, order.relation, order.target_civ_name))

    # Revenue-budgeted queue: only schedule what the current net fiscal
    # surplus can sustain after existing fort spending.
    revenue_budget = _available_net_revenue(gov) * GOV_CONSTRUCTION_REVENUE_MARGIN
    scheduled: list[GovernmentConstructionOrder] = []
    for order in orders:
        if order.estimated_upkeep > revenue_budget:
            continue
        scheduled.append(order)
        revenue_budget -= order.estimated_upkeep
        if len(scheduled) >= GOV_CONSTRUCTION_QUEUE_LIMIT:
            break

    gov.construction_queue = scheduled


def execute_government_construction(
    civ: Civ,
    civs_by_id: dict[int, Civ],
    ter: list,
    impr: list,
    om: list,
) -> bool:
    gov = ensure_government(civ)
    gov.last_build_spending = 0.0
    if not gov.construction_queue:
        return False

    order = gov.construction_queue[0]
    if order.asset_key != "fort" or order.target_civ_id is None or order.host_city_cell is None:
        order.status = "blocked"
        return False

    enemy = civs_by_id.get(order.target_civ_id)
    if enemy is None or not enemy.alive:
        order.status = "blocked_target"
        return False

    host_city = next((city for city in civ.cities if city.cell == order.host_city_cell), None)
    if host_city is None:
        order.status = "blocked_host"
        return False

    available_revenue = _available_net_revenue(gov) * GOV_CONSTRUCTION_REVENUE_MARGIN
    if available_revenue < order.estimated_upkeep:
        order.status = "blocked_revenue"
        return False

    if gov.treasury < order.estimated_spending + order.estimated_upkeep:
        order.status = "blocked_budget"
        return False

    from . import city_dev
    success = city_dev._place_fort(
        host_city,
        civ,
        ter,
        impr,
        set(civ.territory),
        {enemy.id},
        om,
        pay_cost=False,
        require_metal=False,
    )
    if not success:
        order.status = "blocked_site"
        return False

    gov.treasury -= order.estimated_spending
    gov.last_build_spending = order.estimated_spending
    record_government_flow(
        gov,
        kind="construction_spending",
        label="Government construction",
        amount=order.estimated_spending,
        category="expense",
        city_cell=host_city.cell,
        city_name=host_city.name,
        note=order.asset_label,
    )
    gov.construction_queue.pop(0)

    from . import employment
    employment.update_city_employment(host_city, impr)
    sync_fort_funding(civ, impr)
    return True


def update_fort_funding(civ: Civ) -> None:
    gov = ensure_government(civ)
    forts = _fort_states(gov)
    gov.last_fort_spending = 0.0
    if not forts:
        return
    ordered = sorted(forts.items(), key=lambda kv: (kv[1].buffer, kv[0]))
    reserve = float(gov.treasury)

    # Compute an allowable debt limit based on a simple creditworthiness
    # heuristic: larger economies, higher recent revenue, and greater
    # military/economic power get more favourable terms.
    def _compute_debt_limit() -> float:
        econ_size = 0.0
        for city in getattr(civ, "cities", []):
            econ_size += float(getattr(city, "economic_output", 0.0) or 0.0)
        revenue = float(getattr(gov, "last_tax_collected", 0.0) or 0.0)
        power = float(getattr(civ, "power", 0.0) or getattr(civ, "power", 0.0) or 0.0)
        # Scale contributors to comparable magnitudes and ensure a floor.
        limit = max(50.0, econ_size * 0.06 + revenue * 2.0 + power * 5.0)
        return limit

    def _compute_interest_rate() -> float:
        # Interest rate in [min_rate, max_rate] where better credit lowers rate.
        min_rate = 0.01
        max_rate = 0.12
        econ_size = 0.0
        for city in getattr(civ, "cities", []):
            econ_size += float(getattr(city, "economic_output", 0.0) or 0.0)
        revenue = float(getattr(gov, "last_tax_collected", 0.0) or 0.0)
        power = float(getattr(civ, "power", 0.0) or 0.0)
        score = revenue + econ_size * 0.02 + power * 1.0
        K = 200.0
        frac = score / (score + K)
        # Higher score -> lower rate
        rate = min_rate + (max_rate - min_rate) * (1.0 - frac)
        return max(min_rate, min(max_rate, rate))

    debt_limit = _compute_debt_limit()
    for cell, state in ordered:
        host = fort_host_city(civ, cell)
        upkeep = fort_upkeep_value(host, gov) if host is not None else sum(
            float(qty) * BASE_PRICES.get(good, 1.0)
            for good, qty in gov.fort_upkeep_goods.items()
        )
        upkeep = max(1.0, upkeep)

        # Hysteresis: inactive forts only reopen above the reserve floor.
        if not state.active and reserve < FORT_REOPEN_TREASURY_MIN:
            state.buffer = min(state.buffer, gov.fort_buffer_off)
            state.active = False
            state.last_upkeep_value = upkeep
            continue

        # Allow governments to borrow up to `debt_limit`. Compute how much
        # additional spending is allowed before hitting the limit.
        allowed_spend = reserve + debt_limit

        if allowed_spend <= 0.0:
            # No room to pay this fort — keep it inactive.
            state.buffer = min(state.buffer, gov.fort_buffer_off)
            state.active = False
            state.last_upkeep_value = upkeep
            continue

        # Attempt to pay full upkeep; if not enough headroom, pay up to
        # the limit (this may push treasury negative but not beyond limit).
        spend = min(upkeep, allowed_spend)
        reserve -= spend
        gov.last_fort_spending += spend
        record_government_flow(
            gov,
            kind="fort_upkeep",
            label="Fort upkeep",
            amount=spend,
            category="expense",
            city_cell=cell,
            city_name=host.name if host is not None else "",
            note=f"fort {cell}",
        )

        # Fort considered active only when fully funded this tick; partial
        # payments leave it inactive and reduce its buffer.
        if spend >= upkeep:
            state.buffer = max(state.buffer, gov.fort_buffer_on)
            state.active = True
        else:
            state.buffer = min(state.buffer, gov.fort_buffer_off)
            state.active = False
        state.last_upkeep_value = upkeep
        continue
    gov.treasury = reserve

    # Apply interest on outstanding debt (treasury < 0). Interest rate is
    # a function of creditworthiness so each nation gets a different rate.
    if gov.treasury < 0.0:
        rate = _compute_interest_rate()
        interest = -gov.treasury * rate
        gov.treasury -= interest
        gov.last_interest_charged = interest
        record_government_flow(
            gov,
            kind="interest",
            label="Debt interest",
            amount=interest,
            category="expense",
            city_cell=None,
            city_name="",
            note=f"rate {rate:.3%}",
        )
