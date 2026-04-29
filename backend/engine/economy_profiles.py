"""Centralized economy parameters for improvements, resources, and demand.

Simulation should read from this module rather than embedding per-good or
per-improvement constants in the tick loop.
"""

from __future__ import annotations

import math
from dataclasses import field
from typing import Callable

from .constants import IMP, BASE_PRICES
from .models import (
    ImprovementEconomyProfile,
    ProfessionConsumptionProfile,
    ConsumptionGoodProfile,
)


IMPROVEMENT_ECONOMY: dict[int, ImprovementEconomyProfile] = {
    IMP.FARM: ImprovementEconomyProfile(
        improvement_type=IMP.FARM,
        output_good="grain",
        output_eff_good="grain",
        output_base=2.5,
        output_per_level=1.5,
        use_river_mult=True,
        use_coast_mult=True,
        windmill_bonus_per_staffed_level=0.5,
        counts_as_worked_tile=True,
    ),
    IMP.FISHERY: ImprovementEconomyProfile(
        improvement_type=IMP.FISHERY,
        output_good="meat",
        output_eff_good="grain",
        output_base=2.0,
        output_per_level=1.2,
        use_coast_mult=True,
        counts_as_worked_tile=True,
    ),
    IMP.COTTON: ImprovementEconomyProfile(
        improvement_type=IMP.COTTON,
        output_good="fabric",
        output_eff_good="fabric",
        output_base=1.5,
        output_per_level=0.9,
        use_river_mult=True,
        use_coast_mult=True,
        counts_as_worked_tile=True,
    ),
    IMP.MINE: ImprovementEconomyProfile(
        improvement_type=IMP.MINE,
        output_good="copper_ore",
        output_eff_good="copper_ore",
        output_base=0.5,
        output_per_level=0.25,
        resource_output_multiplier={"iron": 2.0},
        counts_as_worked_tile=True,
    ),
    IMP.QUARRY: ImprovementEconomyProfile(
        improvement_type=IMP.QUARRY,
        output_good="stone",
        output_eff_good="stone",
        output_base=0.0,
        output_per_level=2.0,
        counts_as_worked_tile=True,
    ),
    IMP.LUMBER: ImprovementEconomyProfile(
        improvement_type=IMP.LUMBER,
        output_good="lumber",
        output_eff_good="lumber",
        output_base=0.0,
        output_per_level=2.0,
        counts_as_worked_tile=True,
    ),
    IMP.SMITHERY: ImprovementEconomyProfile(
        improvement_type=IMP.SMITHERY,
        output_good="copper",
        output_eff_good="copper",
        output_base=0.0,
        output_per_level=2.0,
        demand_good="copper_ore",
        demand_per_output=1.0,
        counts_as_worked_tile=True,
    ),
    # Non-output improvements still count as worked tiles for city stats.
    IMP.PASTURE: ImprovementEconomyProfile(improvement_type=IMP.PASTURE),
    IMP.WINDMILL: ImprovementEconomyProfile(improvement_type=IMP.WINDMILL),
    IMP.PORT: ImprovementEconomyProfile(improvement_type=IMP.PORT),
    IMP.FORT: ImprovementEconomyProfile(improvement_type=IMP.FORT),
}


# Per-resource flat tile effects applied when the city controls that tile.
# Values are added directly to city supply (or misc income).
RESOURCE_TILE_EFFECTS: dict[str, dict[str, float | bool]] = {
    "wheat": {"good": "grain", "amount": 2.0, "river_mult": True},
    "fish": {"good": "grain", "amount": 1.5, "coast_mult": True},
    "iron": {"good": "iron_ore", "amount": 1.0},
    "stone": {"good": "stone", "amount": 1.0},
    "gold": {"income_misc": 2.0},
}


CITY_BASE_DEMAND_PER_POP: dict[str, float] = {
    "grain": 0.08,
    "fabric": 0.002,
    "clothes": 0.005,
    "lumber": 0.015,
    "stone": 0.01,
    "paper": 0.002,
    "housing": 0.01,
    "jewelry": 0.0005,
    "medical_services": 0.005,
    "education": 0.003,
}

# Grain-specific minimum-demand floor, applied in simulation after
# substitute allocation so final grain demand cannot collapse.
# Keep at 0.0 to allow grain demand to fall to near-zero at high wealth.
GRAIN_DEMAND_FLOOR_MULTIPLIER = 0.0

FORT_METAL_DEMAND_PER_TILE = 1.5


# ── Consumption / wage tuning ──────────────────────────────────────────────

PROFESSION_WAGE_POOL_SHARE = 0.68

# ── Slack stimulus ─────────────────────────────────────────────────────────
# When a city has idle labor AND idle gold reserves, households draw on
# savings to supplement current-tick income. That boosts the wage pool,
# which raises consumption tiers, which raises demand, which restores the
# profitability signal investment relies on.
#
# Without this, a city can stall in an oversupply trap: low prices suppress
# margins, nothing new gets built, unemployment grows, and gold piles up
# forever with no way back into circulation.
#
# Unemployment rate must exceed this for stimulus to kick in.
STIMULUS_UNEMP_FLOOR = 0.03
# Gold must exceed this many ticks of current income before we touch it.
STIMULUS_RESERVE_TICKS = 3.0
# Max fraction of *excess* reserves (above the floor) drained per update.
STIMULUS_DRAWDOWN_CAP = 0.05
# Target drawdown = slack × income × this. Slack = unemp_rate − floor.
STIMULUS_INCOME_MULT = 4.0

# Asymmetric tier movement: consumption rises quickly when wages clear the
# basket (so slack cities can ratchet demand up fast once stimulus lands)
# and falls slowly (so a transient dip doesn't collapse standards of living).
CONSUMPTION_INCREASE_MULT = 4.0
CONSUMPTION_DECREASE_MULT = 0.4

PROFESSION_CONSUMPTION_PROFILES: dict[str, ProfessionConsumptionProfile] = {
    "unemployed": ProfessionConsumptionProfile(
        key="unemployed",
        income_weight=0.01,
        spend_share=0.25,
        base_level=0.00,
        min_level=0.00,
        increase_step=0.01,
        decrease_step=0.04,
        raise_threshold=1.05,
        lower_threshold=0.92,
        reference_wage=0.45,
    ),
    "farmer": ProfessionConsumptionProfile(
        key="farmer",
        income_weight=0.95,
        spend_share=0.58,
        base_level=0.20,
        min_level=0.00,
        increase_step=0.03,
        decrease_step=0.05,
        raise_threshold=1.18,
        lower_threshold=0.88,
        reference_wage=1.0,
    ),
    "rancher": ProfessionConsumptionProfile(
        key="rancher",
        income_weight=0.98,
        spend_share=0.60,
        base_level=0.22,
        min_level=0.00,
        increase_step=0.03,
        decrease_step=0.05,
        raise_threshold=1.18,
        lower_threshold=0.88,
        reference_wage=1.0,
    ),
    "fisherman": ProfessionConsumptionProfile(
        key="fisherman",
        income_weight=0.98,
        spend_share=0.60,
        base_level=0.22,
        min_level=0.00,
        increase_step=0.03,
        decrease_step=0.05,
        raise_threshold=1.18,
        lower_threshold=0.88,
        reference_wage=1.0,
    ),
    "lumberjack": ProfessionConsumptionProfile(
        key="lumberjack",
        income_weight=1.00,
        spend_share=0.60,
        base_level=0.22,
        min_level=0.00,
        increase_step=0.03,
        decrease_step=0.05,
        raise_threshold=1.18,
        lower_threshold=0.88,
        reference_wage=1.0,
    ),
    "miner": ProfessionConsumptionProfile(
        key="miner",
        income_weight=1.08,
        spend_share=0.64,
        base_level=0.30,
        min_level=0.05,
        increase_step=0.035,
        decrease_step=0.05,
        raise_threshold=1.20,
        lower_threshold=0.88,
        reference_wage=1.1,
    ),
    "worker": ProfessionConsumptionProfile(
        key="worker",
        income_weight=1.05,
        spend_share=0.64,
        base_level=0.30,
        min_level=0.05,
        increase_step=0.035,
        decrease_step=0.05,
        raise_threshold=1.20,
        lower_threshold=0.88,
        reference_wage=1.1,
    ),
    "artisan": ProfessionConsumptionProfile(
        key="artisan",
        income_weight=1.35,
        spend_share=0.72,
        base_level=0.48,
        min_level=0.10,
        increase_step=0.04,
        decrease_step=0.05,
        raise_threshold=1.22,
        lower_threshold=0.90,
        reference_wage=1.4,
    ),
    "owner": ProfessionConsumptionProfile(
        key="owner",
        income_weight=2.90,
        spend_share=0.80,
        base_level=0.85,
        min_level=0.25,
        increase_step=0.05,
        decrease_step=0.05,
        raise_threshold=1.24,
        lower_threshold=0.92,
        reference_wage=2.0,
    ),
    "aristocrat": ProfessionConsumptionProfile(
        key="aristocrat",
        income_weight=3.50,
        spend_share=0.90,
        base_level=1.25,
        min_level=0.50,
        increase_step=0.06,
        decrease_step=0.05,
        raise_threshold=1.28,
        lower_threshold=0.94,
        reference_wage=3.0,
    ),
    "smith": ProfessionConsumptionProfile(
        key="smith",
        income_weight=1.55,
        spend_share=0.74,
        base_level=0.55,
        min_level=0.10,
        increase_step=0.04,
        decrease_step=0.05,
        raise_threshold=1.22,
        lower_threshold=0.90,
        reference_wage=1.6,
    ),
    "sailor": ProfessionConsumptionProfile(
        key="sailor",
        income_weight=1.10,
        spend_share=0.65,
        base_level=0.32,
        min_level=0.05,
        increase_step=0.035,
        decrease_step=0.05,
        raise_threshold=1.20,
        lower_threshold=0.88,
        reference_wage=1.15,
    ),
    "merchant": ProfessionConsumptionProfile(
        key="merchant",
        income_weight=1.28,
        spend_share=0.70,
        base_level=0.42,
        min_level=0.08,
        increase_step=0.04,
        decrease_step=0.05,
        raise_threshold=1.20,
        lower_threshold=0.90,
        reference_wage=1.25,
    ),
}

# All sigmoid-based curve profiles store early_mid / late_mid as fractions of
# CONSUMPTION_CURVE_LEVEL_MAX (0.0 = bottom, 1.0 = top of sampled range) and
# early_slope / late_slope in normalized units (slope × old_max). The curve
# functions apply _normalized_level() before passing to the sigmoid, so the
# shape stays identical regardless of what CONSUMPTION_CURVE_LEVEL_MAX is set
# to. Points-based and code-override curves are exempt — they define their own
# coordinate systems.
DEFAULT_CONSUMPTION_GOOD_PROFILE = ConsumptionGoodProfile(
    good="__default__",
    base_per_person=0.0008,
    floor_multiplier=0.05,
    early_mid=0.150,
    early_amp=0.10,
    late_mid=0.417,
    late_amp=0.10,
    early_slope=7.8,
    late_slope=6.0,
    profession_multipliers={
        "unemployed": 0.70,
        "farmer": 0.95,
        "rancher": 0.95,
        "fisherman": 0.95,
        "lumberjack": 0.95,
        "miner": 1.0,
        "worker": 1.0,
        "artisan": 1.1,
        "owner": 1.25,
        "merchant": 1.1,
        "aristocrat": 1.45,
        "professional": 1.1,
        "smith": 1.15,
        "sailor": 1.0,
    },
)

# Shared profession bias for raw-material / metal luxuries: only the top of
# the income pyramid ever wants to burn raw ore or bars as status goods.
_INDUSTRIAL_LUXURY_MULTIPLIERS = {
    "unemployed": 0.0,
    "farmer": 0.0,
    "rancher": 0.0,
    "fisherman": 0.0,
    "lumberjack": 0.0,
    "miner": 0.1,
    "worker": 0.1,
    "artisan": 0.3,
    "owner": 1.2,
    "merchant": 0.2,
    "aristocrat": 2.5,
    "professional": 0.1,
    "smith": 0.5,
    "sailor": 0.1,
}


# Midpoints are normalized fractions [0, 1] of CONSUMPTION_CURVE_LEVEL_MAX.
# Slopes are in normalized units (multiply by max to get raw steepness).
# Code-override goods (grain, bread) ignore these params for their shape
# but keep them for reference; _grain_curve_code / _bread_curve_code use
# floor_multiplier / early_amp / late_amp directly.
GOOD_CONSUMPTION_PROFILES: dict[str, ConsumptionGoodProfile] = {
    "grain": ConsumptionGoodProfile(
        good="grain",
        base_per_person=0.1,
        floor_multiplier=0.03,
        early_mid=-0.033,
        early_amp=0.95,
        late_mid=0.208,
        late_amp=0.45,
        early_slope=9.0,
        late_slope=5.7,
        curve_kind="staple_shift",
        curve_params={
            # Rich societies substitute away from raw grain.
            "decline_amp": 1.65,
            "decline_mid": 0.392,    # normalized: was 2.35 / 6
            "decline_slope": 9.3,    # normalized: was 1.55 * 6
            "decline_power": 1.0,
        },
    ),
    "bread": ConsumptionGoodProfile(
        good="bread",
        base_per_person=0.1,
        floor_multiplier=0.005,
        early_mid=0.033,
        early_amp=0.28,
        late_mid=0.233,
        late_amp=2.05,
        early_slope=8.4,
        late_slope=6.9,
        curve_kind="sigmoid2",
    ),
    "meat": ConsumptionGoodProfile(
        good="meat",
        base_per_person=0.1,
        floor_multiplier=0.01,
        early_mid=0.167,
        early_amp=1.0,
        late_mid=0.417,
        late_amp=2.0,
        early_slope=36.0,
        late_slope=9.0,
        curve_kind="sigmoid2",
    ),
    "lumber": ConsumptionGoodProfile(
        good="lumber",
        base_per_person=0.012,
        floor_multiplier=0.18,
        early_mid=0.017,
        early_amp=0.22,
        late_mid=0.300,
        late_amp=0.18,
        early_slope=7.2,
        late_slope=6.0,
    ),
    "stone": ConsumptionGoodProfile(
        good="stone",
        base_per_person=0.010,
        floor_multiplier=0.16,
        early_mid=0.033,
        early_amp=0.20,
        late_mid=0.300,
        late_amp=0.15,
        early_slope=7.2,
        late_slope=6.0,
    ),
    "fabric": ConsumptionGoodProfile(
        good="fabric",
        base_per_person=0.008,
        floor_multiplier=0.05,
        early_mid=0.133,
        early_amp=0.20,
        late_mid=0.400,
        late_amp=0.55,
        early_slope=7.8,
        late_slope=7.2,
        profession_multipliers={
            "unemployed": 0.40,
            "farmer": 0.8,
            "rancher": 0.8,
            "fisherman": 0.8,
            "lumberjack": 0.8,
            "miner": 0.9,
            "worker": 0.95,
            "artisan": 1.15,
            "owner": 1.45,
            "merchant": 1.70,
            "aristocrat": 1.10,
            "professional": 1.00,
            "smith": 1.20,
            "sailor": 1.0,
        },
    ),
    "clothes": ConsumptionGoodProfile(
        good="clothes",
        base_per_person=0.03,
        floor_multiplier=0.03,
        early_mid=0.167,
        early_amp=0.22,
        late_mid=0.467,
        late_amp=0.95,
        early_slope=7.8,
        late_slope=6.6,
        profession_multipliers={
            "unemployed": 0.28,
            "farmer": 0.85,
            "rancher": 0.85,
            "fisherman": 0.85,
            "lumberjack": 0.85,
            "miner": 0.95,
            "worker": 1.0,
            "artisan": 1.20,
            "owner": 1.65,
            "merchant": 1.70,
            "aristocrat": 2.10,
            "professional": 1.00,
            "smith": 1.30,
            "sailor": 1.05,
        },
    ),
    "paper": ConsumptionGoodProfile(
        good="paper",
        base_per_person=0.0015,
        floor_multiplier=0.02,
        early_mid=0.200,
        early_amp=0.12,
        late_mid=0.500,
        late_amp=0.75,
        early_slope=7.2,
        late_slope=6.0,
        profession_multipliers={
            "unemployed": 0.20,
            "farmer": 0.75,
            "rancher": 0.75,
            "fisherman": 0.75,
            "lumberjack": 0.75,
            "miner": 0.90,
            "worker": 1.0,
            "artisan": 1.20,
            "owner": 1.70,
            "merchant": 1.70,
            "aristocrat": 2.30,
            "professional": 3.00,
            "smith": 1.35,
            "sailor": 1.0,
        },
    ),
    "jewelry": ConsumptionGoodProfile(
        good="jewelry",
        base_per_person=0.0002,
        floor_multiplier=0.004,
        early_mid=0.80,
        early_amp=0.08,
        late_mid=0.667,
        late_amp=1.0,
        early_slope=6.0,
        late_slope=5.4,
        profession_multipliers={
            "unemployed": 0.05,
            "farmer": 0.4,
            "rancher": 0.4,
            "fisherman": 0.4,
            "lumberjack": 0.4,
            "miner": 0.55,
            "worker": 0.7,
            "artisan": 1.0,
            "owner": 2.0,
            "merchant": 1.70,
            "aristocrat": 3.5,
            "professional": 1.20,
            "smith": 1.1,
            "sailor": 0.75,
        },
    ),
    "housing": ConsumptionGoodProfile(
        good="housing",
        base_per_person=0.010,
        floor_multiplier=0.25,
        curve_kind="poly",
        curve_params={"coeffs": [0, 0.05, 0.35, 0.15, 0.03, 0.015, 0.008]},
    ),
    # Medical services: ~0 demand below consumption ~1.0, then steadily rises.
    "medical_services": ConsumptionGoodProfile(
        good="medical_services",
        base_per_person=0.008,
        floor_multiplier=0.0,
        early_mid=0.167,
        early_amp=0.35,
        late_mid=0.467,
        late_amp=0.55,
        early_slope=9.6,
        late_slope=6.6,
        profession_multipliers={
            "unemployed": 0.30,
            "farmer": 0.80,
            "rancher": 0.80,
            "fisherman": 0.80,
            "lumberjack": 0.80,
            "miner": 1.20,
            "worker": 1.00,
            "artisan": 1.20,
            "owner": 1.60,
            "merchant": 1.00,
            "aristocrat": 2.20,
            "professional": 1.00,
            "smith": 1.20,
            "sailor": 0.95,
        },
    ),
    # Education: ~0 demand below consumption ~1.5, then ramps with affluence.
    "education": ConsumptionGoodProfile(
        good="education",
        base_per_person=0.01,
        floor_multiplier=0.0,
        early_mid=0.250,
        early_amp=0.25,
        late_mid=0.533,
        late_amp=0.85,
        early_slope=9.6,
        late_slope=6.6,
        profession_multipliers={
            "unemployed": 0.15,
            "farmer": 0.55,
            "rancher": 0.55,
            "fisherman": 0.55,
            "lumberjack": 0.55,
            "miner": 0.75,
            "worker": 0.90,
            "artisan": 2.50,
            "owner": 3.50,
            "merchant": 3.00,
            "aristocrat": 2.60,
            "professional": 3.50,
            "smith": 1.40,
            "sailor": 0.90,
        },
    ),
    # ── Industrial / raw luxury goods ─────────────────────────────────────
    # Ores, metals, ships, and sapphires aren't consumer staples — their
    # demand comes from production chains (smitheries consume ore, etc.).
    # Population-level consumption is pinned to zero until consumption_level
    # approaches the top of the scale, where they become collector/display
    # goods for the ultra-wealthy. early_amp=0 keeps the curve at zero; only
    # the late sigmoid (centered at ~83–88% of max) ever produces nonzero demand.
    "copper_ore": ConsumptionGoodProfile(
        good="copper_ore",
        base_per_person=0.002,
        floor_multiplier=0.0,
        early_mid=1.667,
        early_amp=0.0,
        late_mid=0.833,
        late_amp=0.40,
        early_slope=6.0,
        late_slope=18.0,
        profession_multipliers=_INDUSTRIAL_LUXURY_MULTIPLIERS,
    ),
    "iron_ore": ConsumptionGoodProfile(
        good="iron_ore",
        base_per_person=0.0015,
        floor_multiplier=0.0,
        early_mid=1.000,
        early_amp=0.0,
        late_mid=0.850,
        late_amp=0.40,
        early_slope=6.0,
        late_slope=18.0,
        profession_multipliers=_INDUSTRIAL_LUXURY_MULTIPLIERS,
    ),
    "copper": ConsumptionGoodProfile(
        good="copper",
        base_per_person=0.0015,
        floor_multiplier=0.0,
        early_mid=1.000,
        early_amp=0.0,
        late_mid=0.833,
        late_amp=0.50,
        early_slope=6.0,
        late_slope=18.0,
        profession_multipliers=_INDUSTRIAL_LUXURY_MULTIPLIERS,
    ),
    "iron": ConsumptionGoodProfile(
        good="iron",
        base_per_person=0.0010,
        floor_multiplier=0.0,
        early_mid=1.000,
        early_amp=0.0,
        late_mid=0.850,
        late_amp=0.50,
        early_slope=6.0,
        late_slope=18.0,
        profession_multipliers=_INDUSTRIAL_LUXURY_MULTIPLIERS,
    ),
    "ships": ConsumptionGoodProfile(
        good="ships",
        base_per_person=0.0005,
        floor_multiplier=0.0,
        early_mid=1.000,
        early_amp=0.0,
        late_mid=0.867,
        late_amp=0.60,
        early_slope=6.0,
        late_slope=18.0,
        profession_multipliers={
            "unemployed": 0.0,
            "farmer": 0.0,
            "rancher": 0.0,
            "fisherman": 0.2,
            "lumberjack": 0.0,
            "miner": 0.0,
            "worker": 0.1,
            "artisan": 0.3,
            "owner": 1.5,
            "aristocrat": 3.0,
            "professional": 1.00,
            "smith": 0.2,
            "sailor": 1.0,
        },
    ),
    "sapphires": ConsumptionGoodProfile(
        good="sapphires",
        base_per_person=0.00015,
        floor_multiplier=0.0,
        early_mid=1.000,
        early_amp=0.0,
        late_mid=0.883,
        late_amp=0.70,
        early_slope=6.0,
        late_slope=21.0,
        profession_multipliers={
            "unemployed": 0.0,
            "farmer": 0.0,
            "rancher": 0.0,
            "fisherman": 0.0,
            "lumberjack": 0.0,
            "miner": 0.1,
            "worker": 0.2,
            "artisan": 0.5,
            "owner": 2.0,
            "aristocrat": 4.0,
            "professional": 1.00,
            "smith": 0.5,
            "sailor": 0.2,
        },
    ),
}


def _sigmoid(x: float, mid: float, slope: float) -> float:
    return 1.0 / (1.0 + math.exp(-slope * (x - mid)))


# Sigmoid-based curves operate in normalized space u ∈ [0, 1] so that
# changing CONSUMPTION_CURVE_LEVEL_MAX automatically rescales the shape.
# Profile midpoints are fractions of max; profile slopes are in normalized units.

def _curve_sigmoid2(profile: ConsumptionGoodProfile, consumption_level: float) -> float:
    u = _normalized_level(consumption_level)
    return (
        profile.floor_multiplier
        + profile.early_amp * _sigmoid(u, profile.early_mid, profile.early_slope)
        + profile.late_amp * _sigmoid(u, profile.late_mid, profile.late_slope)
    )


def _curve_staple(profile: ConsumptionGoodProfile, consumption_level: float) -> float:
    u = _normalized_level(consumption_level)
    early = _sigmoid(u, profile.early_mid, profile.early_slope)
    late = _sigmoid(u, profile.late_mid, profile.late_slope)
    return profile.floor_multiplier + profile.early_amp * (early ** 0.7) + profile.late_amp * late


def _curve_param(profile: ConsumptionGoodProfile, key: str, default: float) -> float:
    params = getattr(profile, "curve_params", None) or {}
    return float(params.get(key, default))


def _curve_staple_shift(profile: ConsumptionGoodProfile, consumption_level: float) -> float:
    """Staple consumption that can peak and then decline at high wealth."""
    u = _normalized_level(consumption_level)
    base = _curve_staple(profile, consumption_level)
    decline_amp = _curve_param(profile, "decline_amp", 0.0)
    decline_mid = _curve_param(profile, "decline_mid", profile.late_mid + 0.2)
    decline_slope = _curve_param(profile, "decline_slope", 7.2)
    decline_power = _curve_param(profile, "decline_power", 1.0)
    decline = decline_amp * (_sigmoid(u, decline_mid, decline_slope) ** max(0.1, decline_power))
    return max(0.0, base - decline)


def _curve_points(profile: ConsumptionGoodProfile, consumption_level: float) -> float:
    """Piecewise-linear multiplier from ``profile.curve_points``.

    Below the first point: clamped to that point's value (no negative
    extrapolation — at zero consumption you spend the floor amount).

    Above the last point: extrapolated using the slope of the *last
    segment* so the curve keeps growing instead of plateauing. This is
    the mechanism that lets late-game development keep absorbing labor.
    """
    pts = profile.curve_points
    if not pts:
        return _curve_sigmoid2(profile, consumption_level)
    if consumption_level <= pts[0][0]:
        return pts[0][1]
    if consumption_level >= pts[-1][0]:
        if len(pts) < 2:
            return pts[-1][1]
        x0, y0 = pts[-2]
        x1, y1 = pts[-1]
        slope = (y1 - y0) / max(1e-9, x1 - x0)
        return y1 + slope * (consumption_level - x1)
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        if consumption_level <= x1:
            span = max(1e-9, x1 - x0)
            t = (consumption_level - x0) / span
            return y0 + (y1 - y0) * t
    return pts[-1][1]


def _curve_poly(profile: ConsumptionGoodProfile, consumption_level: float) -> float:
    """Polynomial curve in normalized space.

    Coefficients in ``curve_params["coeffs"]`` = [a0, a1, a2, ...].
    Result = floor_multiplier + a0 + a1·u + a2·u² + ...  where u = level/MAX.
    """
    params = getattr(profile, "curve_params", None) or {}
    coeffs = params.get("coeffs") or []
    if not coeffs:
        return profile.floor_multiplier
    u = _normalized_level(consumption_level)
    total = sum(float(a) * (u ** i) for i, a in enumerate(coeffs))
    return max(0.0, profile.floor_multiplier + total)


_GOOD_CURVE_FUNCTIONS: dict[str, Callable[[ConsumptionGoodProfile, float], float]] = {
    "sigmoid2": _curve_sigmoid2,
    "staple": _curve_staple,
    "staple_shift": _curve_staple_shift,
    "points": _curve_points,
    "poly": _curve_poly,
}


def points_profile(
    good: str,
    points: list[tuple[float, float]],
    *,
    base_per_person: float,
    floor_multiplier: float = 0.0,
    profession_multipliers: dict[str, float] | None = None,
) -> ConsumptionGoodProfile:
    """Define a good's demand curve as a list of ``(level, multiplier)`` pairs.

    Linear interpolation between consecutive points; the last segment's
    slope continues past the final point so the curve never plateaus —
    push the slope steep at the end if you want late-game consumption to
    keep absorbing labor as cities develop.

    Example::

        points_profile("luxury_widgets",
                       [(0, 0), (3, 5), (5, 7), (8, 100)],
                       base_per_person=0.001)

    Below the first point the multiplier is clamped to that point's value;
    above the last point the slope of the last segment continues forever.
    """
    return ConsumptionGoodProfile(
        good=good,
        base_per_person=base_per_person,
        floor_multiplier=floor_multiplier,
        curve_kind="points",
        curve_points=[(float(x), float(y)) for x, y in points],
        profession_multipliers=dict(profession_multipliers or {}),
    )


CONSUMPTION_CURVE_LEVEL_MIN = 0.0
CONSUMPTION_CURVE_LEVEL_MAX = 25.0
CONSUMPTION_CURVE_SAMPLES = 441


def _normalized_level(consumption_level: float) -> float:
    span = max(1e-9, CONSUMPTION_CURVE_LEVEL_MAX - CONSUMPTION_CURVE_LEVEL_MIN)
    return (consumption_level - CONSUMPTION_CURVE_LEVEL_MIN) / span


def _grain_curve_code(consumption_level: float, profile: ConsumptionGoodProfile) -> float:
    """Expressive coded curve: staple demand peaks then fades with affluence."""
    u = max(0.0, min(1.0, _normalized_level(consumption_level)))
    # Early staple baseline + gentle mid bump.
    staple = profile.floor_multiplier + profile.early_amp * (1.0 - u) ** 0.75
    bump = profile.late_amp * math.exp(-((u - 0.35) / 0.22) ** 2)
    # Strong late substitution away from raw grain.
    fade = 1.0 / (1.0 + math.exp(14.0 * (u - 0.62)))
    return max(0.0, (staple + bump) * fade)


def _bread_curve_code(consumption_level: float, profile: ConsumptionGoodProfile) -> float:
    """Expressive coded curve: processed-food demand accelerates with wealth."""
    u = max(0.0, min(1.0, _normalized_level(consumption_level)))
    adoption = 1.0 / (1.0 + math.exp(-10.0 * (u - 0.32)))
    premium = u ** 1.35
    return max(0.0, profile.floor_multiplier + profile.early_amp * adoption + profile.late_amp * premium)


# First-class per-good code overrides. Add entries here to define demand curves
# directly in Python while still benefiting from precomputed table lookup.
GOOD_CURVE_CODE_OVERRIDES: dict[str, Callable[[float, ConsumptionGoodProfile], float]] = {
    "grain": _grain_curve_code,
    "bread": _bread_curve_code,
}


def register_good_curve_function(good: str, fn: Callable[[float, ConsumptionGoodProfile], float]) -> None:
    GOOD_CURVE_CODE_OVERRIDES[good] = fn
    rebuild_consumption_curve_tables()


def configure_consumption_curve_sampling(*, level_min: float | None = None, level_max: float | None = None, samples: int | None = None) -> None:
    """Update global sampling controls and rebuild precomputed curve tables.

    This enables coarse/fine granularity and custom level domains (e.g. 0..N)
    without changing per-good curve formulas.
    """
    global CONSUMPTION_CURVE_LEVEL_MIN, CONSUMPTION_CURVE_LEVEL_MAX, CONSUMPTION_CURVE_SAMPLES
    if level_min is not None:
        CONSUMPTION_CURVE_LEVEL_MIN = float(level_min)
    if level_max is not None:
        CONSUMPTION_CURVE_LEVEL_MAX = float(level_max)
    if samples is not None:
        CONSUMPTION_CURVE_SAMPLES = max(2, int(samples))
    if CONSUMPTION_CURVE_LEVEL_MAX <= CONSUMPTION_CURVE_LEVEL_MIN:
        CONSUMPTION_CURVE_LEVEL_MAX = CONSUMPTION_CURVE_LEVEL_MIN + 1.0
    rebuild_consumption_curve_tables()


def _build_curve_samples(good: str, profile: ConsumptionGoodProfile) -> list[float]:
    curve_fn = _GOOD_CURVE_FUNCTIONS.get(profile.curve_kind, _curve_sigmoid2)
    code_fn = GOOD_CURVE_CODE_OVERRIDES.get(good)
    step = (CONSUMPTION_CURVE_LEVEL_MAX - CONSUMPTION_CURVE_LEVEL_MIN) / max(1, CONSUMPTION_CURVE_SAMPLES - 1)
    out: list[float] = []
    for i in range(CONSUMPTION_CURVE_SAMPLES):
        lvl = CONSUMPTION_CURVE_LEVEL_MIN + i * step
        val = code_fn(lvl, profile) if code_fn is not None else curve_fn(profile, lvl)
        out.append(max(0.0, val))
    return out


GOOD_CONSUMPTION_CURVE_TABLES: dict[str, list[float]] = {}
DEFAULT_CONSUMPTION_CURVE_TABLE: list[float] = []


def rebuild_consumption_curve_tables() -> None:
    """Recompute sampled demand curves from the current dynamic functions."""
    global GOOD_CONSUMPTION_CURVE_TABLES, DEFAULT_CONSUMPTION_CURVE_TABLE
    GOOD_CONSUMPTION_CURVE_TABLES = {
        good: _build_curve_samples(good, profile)
        for good, profile in GOOD_CONSUMPTION_PROFILES.items()
    }
    DEFAULT_CONSUMPTION_CURVE_TABLE = _build_curve_samples("__default__", DEFAULT_CONSUMPTION_GOOD_PROFILE)


rebuild_consumption_curve_tables()


def good_consumption_curve(good: str, consumption_level: float) -> float:
    table = GOOD_CONSUMPTION_CURVE_TABLES.get(good, DEFAULT_CONSUMPTION_CURVE_TABLE)
    if not table:
        return 0.0
    x = float(consumption_level)
    if x <= CONSUMPTION_CURVE_LEVEL_MIN:
        return table[0]
    span = CONSUMPTION_CURVE_LEVEL_MAX - CONSUMPTION_CURVE_LEVEL_MIN
    if x >= CONSUMPTION_CURVE_LEVEL_MAX:
        # Extrapolate above the sampled domain using the slope of the last
        # two table samples. Curves that genuinely level off (sigmoids) keep
        # near-flat behavior; points-based curves with a steep last segment
        # keep growing.
        if len(table) < 2:
            return table[-1]
        step = span / (len(table) - 1)
        slope = (table[-1] - table[-2]) / max(1e-9, step)
        return max(0.0, table[-1] + slope * (x - CONSUMPTION_CURVE_LEVEL_MAX))
    pos = (x - CONSUMPTION_CURVE_LEVEL_MIN) * (len(table) - 1) / max(1e-9, span)
    i0 = int(pos)
    i1 = min(len(table) - 1, i0 + 1)
    t = pos - i0
    return table[i0] + (table[i1] - table[i0]) * t


def good_consumption_multiplier(good: str, consumption_level: float, profession: str) -> float:
    profile = GOOD_CONSUMPTION_PROFILES.get(good, DEFAULT_CONSUMPTION_GOOD_PROFILE)
    curve = good_consumption_curve(good, consumption_level)
    prof_mult = profile.profession_multipliers.get(profession, 1.0)
    return curve * prof_mult


def profession_consumption_cost(
    prices: dict[str, float], profession: str, consumption_level: float,
) -> float:
    total = 0.0
    for good, profile in GOOD_CONSUMPTION_PROFILES.items():
        per_person = profile.base_per_person * good_consumption_multiplier(good, consumption_level, profession)
        total += per_person * prices.get(good, BASE_PRICES.get(good, 1.0))
    return total


# Base-demand good and its substitutes. Demand for base_good is reallocated
# across members in simulation based on weighted local availability.
SUBSTITUTE_GROUPS: tuple[dict[str, object], ...] = (
    {
        "base_good": "grain",
        # Static base preferences. The simulation scales these by each
        # member good's own consumption-level curve, so the effective split
        # shifts toward bread/meat as a city's wealth rises.
        "members": {
            "grain": 1.0,
            "bread": 1.8,
            "meat": 2.0,
        },
    },
)
