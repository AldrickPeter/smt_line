"""A production plan: for each line, the sequence of lots to make.

No lot splitting: each order becomes exactly one lot, on one line. A planner
(dispatching rule or optimizer) produces a Plan; `predict()` says what the
planning model expects; evaluate.py checks it in the simulator.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from planning.model import PlanningModel
from smt_sim.factory import DEFAULT_FACTORY_PATH, build_line_config
from smt_sim.orders import Order, orders_to_config


@dataclass(frozen=True)
class Lot:
    order_id: str
    product: str
    quantity: int


@dataclass
class Plan:
    method: str
    lines: dict[str, list[Lot]]          # line name -> lots in production order
    solve_time_s: float = 0.0
    notes: str = ""                      # e.g. solver status

    def validate(self, orders: list[Order]) -> None:
        """Every order exactly once, with its own product and quantity."""
        planned = [lot.order_id for lots in self.lines.values() for lot in lots]
        expected = sorted(o.id for o in orders)
        if sorted(planned) != expected:
            raise ValueError(f"{self.method}: plan covers {sorted(planned)}, orders are {expected}")
        by_id = {o.id: o for o in orders}
        for lots in self.lines.values():
            for lot in lots:
                o = by_id[lot.order_id]
                if (lot.product, lot.quantity) != (o.product, o.quantity):
                    raise ValueError(f"{self.method}: lot {lot} does not match order {o}")


@dataclass
class Prediction:
    completion_s: dict[str, float]       # order id -> predicted completion time
    makespan_s: float
    weighted_tardiness_h: float
    late_orders: int
    changeovers: int


def predict(plan: Plan, orders: list[Order], model: PlanningModel) -> Prediction:
    """What the planning model expects this plan to do (no randomness)."""
    by_id = {o.id: o for o in orders}
    completion: dict[str, float] = {}
    changeovers = 0
    for line, lots in plan.lines.items():
        t = model.fill_s[line] if lots else 0.0
        previous = None
        for lot in lots:
            if previous is not None and previous != lot.product:
                changeovers += 1
            t += model.setup_s(line, previous, lot.product) + model.lot_duration_s(line, lot.product, lot.quantity)
            completion[lot.order_id] = t
            previous = lot.product
    return Prediction(
        completion_s=completion,
        makespan_s=max(completion.values(), default=0.0),
        weighted_tardiness_h=sum(by_id[o].weight * max(0.0, t - by_id[o].due_s) for o, t in completion.items()) / 3600,
        late_orders=sum(1 for o, t in completion.items() if t > by_id[o].due_s),
        changeovers=changeovers,
    )


# ---- turning a plan into simulator configs ---------------------------------------


def load_line_configs(factory_path: str | Path | None = None) -> tuple[dict[str, dict[str, Any]], int]:
    """Base config per line from the factory file, plus the factory seed."""
    with open(factory_path or DEFAULT_FACTORY_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {name: build_line_config(cfg or {}) for name, cfg in raw["lines"].items()}, raw["seed"]


def line_configs_for_plan(plan: Plan, orders: list[Order], base_configs: dict[str, dict[str, Any]],
                          planned_yield: dict[str, dict[str, float]] | None = None) -> dict[str, dict[str, Any]]:
    """Each line's config with its lots as the production plan and its orders to track.

    planned_yield[line][product] sets each job's scrap allowance (see
    ProductionPlan); usually the model's measured first-pass yield.
    """
    by_id = {o.id: o for o in orders}
    configs = {}
    for line, base in base_configs.items():
        lots = plan.lines.get(line, [])
        cfg = dict(base)   # shallow copy is enough: we only replace top-level keys
        cfg["production_plan"] = {"jobs": [{"product": lot.product, "quantity": lot.quantity,
                                            "order": lot.order_id,
                                            "planned_yield": (planned_yield or {}).get(line, {}).get(lot.product, 1.0)}
                                           for lot in lots]}
        cfg["orders"] = orders_to_config([by_id[lot.order_id] for lot in lots])
        configs[line] = cfg
    return configs
