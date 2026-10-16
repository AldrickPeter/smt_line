"""Random but reproducible order books, for experiments (e.g. solver scaling).

Due dates are set relative to the work in the order book: `tightness` is the
fraction of the "ideal" finish time (all work spread perfectly over both
lines) by which the LAST due date falls. Below 1.0 some orders must be late
whatever the plan; around 1.0-1.5 a good plan matters most.
"""

import numpy as np

from planning.model import PlanningModel
from smt_sim.orders import Order


def random_orders(n: int, model: PlanningModel, seed: int, tightness: float = 1.2,
                  min_qty: int = 50, max_qty: int = 300) -> list[Order]:
    rng = np.random.default_rng(seed)
    products = list(model.products)
    lines = list(model.lines)
    picks = [products[int(rng.integers(len(products)))] for _ in range(n)]
    quantities = [int(rng.integers(min_qty, max_qty + 1) // 10 * 10) for _ in range(n)]

    # Ideal finish: total work at the average pace, shared evenly over the lines.
    avg_pace = {p: np.mean([model.seconds_per_good_board[line][p] for line in lines]) for p in products}
    ideal_s = sum(q * avg_pace[p] for p, q in zip(picks, quantities)) / len(lines)
    latest_due_s = tightness * ideal_s

    orders = []
    for i, (product, qty) in enumerate(zip(picks, quantities)):
        due_s = float(rng.uniform(0.25, 1.0)) * latest_due_s
        weight = float(rng.choice([1, 1, 1, 2, 3]))
        orders.append(Order(f"R{seed}-{i + 1:02d}", product, qty, round(due_s / 900) * 900, weight))  # due on the quarter hour
    return orders
