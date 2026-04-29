import math
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import numpy as np

from backend.engine.economy_profiles import (
    GOOD_CONSUMPTION_CURVE_TABLES,
    CONSUMPTION_CURVE_LEVEL_MIN,
    CONSUMPTION_CURVE_LEVEL_MAX,
)

# python -m backend.engine.test_driver


def _x_axis(table: list[float]) -> list[float]:
    n = len(table)
    return [
        CONSUMPTION_CURVE_LEVEL_MIN
        + i * (CONSUMPTION_CURVE_LEVEL_MAX - CONSUMPTION_CURVE_LEVEL_MIN) / max(1, n - 1)
        for i in range(n)
    ]


if __name__ == "__main__":
    goods = list(GOOD_CONSUMPTION_CURVE_TABLES.keys())
    n = len(goods)

    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.5, rows * 2.8))
    axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]

    colors = cm.tab20(np.linspace(0, 1, n))

    for idx, good in enumerate(goods):
        ax = axes_flat[idx]
        table = GOOD_CONSUMPTION_CURVE_TABLES[good]
        xs = _x_axis(table)
        ax.plot(xs, table, color=colors[idx], linewidth=1.5)
        ax.set_title(good, fontsize=8, pad=3)
        ax.set_xlabel("consumption level", fontsize=6)
        ax.set_ylabel("multiplier", fontsize=6)
        ax.tick_params(labelsize=6)
        ax.grid(True, linewidth=0.4, alpha=0.5)

    for idx in range(n, len(axes_flat)):
        axes_flat[idx].set_visible(False)

    fig.suptitle("Consumption demand curves", fontsize=11, y=1.01)
    plt.tight_layout()
    plt.show()
