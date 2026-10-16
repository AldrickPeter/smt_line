"""Run a plan in the simulator, over many seeds, and measure what really happened.

Fair comparison: every method is run on the SAME list of seeds, and each
line's seed is derived from the run seed the same way (common random
numbers). This is only approximately "the same luck" for every method:
once two plans do different things, they draw different random numbers.
But it removes most of the seed-to-seed noise from the comparison.
"""

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from planning.plan import Plan, line_configs_for_plan
from smt_sim import Simulator
from smt_sim.events import StationState
from smt_sim.factory import Factory, factory_summary, line_seeds
from smt_sim.orders import Order

MAX_RUN_H = 7 * 24   # give up after a week of sim time (counted as unfinished)


@dataclass
class RunResult:
    method: str
    seed: int
    finished: bool
    completion_s: dict[str, float]       # order id -> actual completion time
    makespan_h: float
    weighted_tardiness_h: float
    late_orders: int
    changeovers: int                     # product switches actually made (incl. top-up lots)
    top_ups: int
    factory_oee: float
    first_pass_yield: float
    line_time_h: dict[str, float] = field(default_factory=dict)   # when each line finished its lots


def simulate_plan(plan: Plan, orders: list[Order], base_configs: dict[str, dict[str, Any]],
                  seed: int, planned_yield: dict[str, dict[str, float]] | None = None) -> RunResult:
    configs = line_configs_for_plan(plan, orders, base_configs, planned_yield)
    seeds = line_seeds(seed, list(configs))
    factory = Factory({name: Simulator(cfg, seed=seeds[name]) for name, cfg in configs.items()})
    finished = factory.run_until_orders_done(MAX_RUN_H * 3600)

    completion: dict[str, float] = {}
    line_time_h = {}
    for name, sim in factory.lines.items():
        if sim.order_tracker is not None:
            completion.update(sim.order_tracker.completed_s)
            line_time_h[name] = max(sim.order_tracker.completed_s.values(), default=0.0) / 3600
    for o in orders:   # unfinished orders count as done at the cut-off
        completion.setdefault(o.id, MAX_RUN_H * 3600.0)

    _, oee, fpy = factory_summary(factory)
    printer_setups = sum(1 for sim in factory.lines.values()
                         for e in sim.event_log.for_station("printer") if e.new_state == StationState.SETUP)
    return RunResult(
        method=plan.method,
        seed=seed,
        finished=finished,
        completion_s=completion,
        makespan_h=max(completion.values()) / 3600,
        weighted_tardiness_h=sum(o.weight * max(0.0, completion[o.id] - o.due_s) for o in orders) / 3600,
        late_orders=sum(1 for o in orders if completion[o.id] > o.due_s),
        changeovers=printer_setups,   # every product switch starts with a stencil change at the printer
        top_ups=sum(sim.source.top_up_count for sim in factory.lines.values()),
        factory_oee=oee,
        first_pass_yield=fpy,
        line_time_h=line_time_h,
    )


def evaluate(plan: Plan, orders: list[Order], base_configs: dict[str, dict[str, Any]],
             seeds: list[int], planned_yield: dict[str, dict[str, float]] | None = None) -> list[RunResult]:
    plan.validate(orders)
    return [simulate_plan(plan, orders, base_configs, seed, planned_yield) for seed in seeds]


def mean_ci95(values: list[float]) -> tuple[float, float]:
    """Mean and half-width of a 95 % confidence interval (t-distribution)."""
    arr = np.asarray(values, dtype=float)
    if len(arr) < 2:
        return float(arr.mean()), 0.0
    return float(arr.mean()), float(T_95.get(len(arr) - 1, 1.96) * arr.std(ddof=1) / math.sqrt(len(arr)))


# Two-sided 95 % t critical values by degrees of freedom (avoids a scipy dependency).
T_95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
        11: 2.20, 12: 2.18, 13: 2.16, 14: 2.14, 15: 2.13, 16: 2.12, 17: 2.11, 18: 2.10, 19: 2.09,
        20: 2.09, 24: 2.06, 29: 2.05, 39: 2.02, 49: 2.01, 99: 1.98}
