"""Integer data for the exact methods (CP-SAT, MILP, brute force).

Solvers work best with whole numbers, so every time is rounded to whole
seconds here, once, and every exact method reads the same rounded data.

Objective (shared by every method, see plan.score):
    1. minimize weighted tardiness  sum_j w_j * max(0, completion_j - due_j)
    2. among plans equally good on 1, minimize the makespan
This "lexicographic" objective is solved in two passes by CP-SAT and MILP.
"""

from dataclasses import dataclass

from planning.model import PlanningModel
from smt_sim.orders import Order

WEIGHT_SCALE = 100   # order weights are multiplied by this and rounded, to keep the objective integer


@dataclass
class Instance:
    orders: list[Order]
    lines: list[str]
    duration: list[list[int]]       # [job][line] seconds for the whole lot
    setup: dict[str, dict[str, dict[str, int]]]   # [line][from product][to product] seconds
    fill: list[int]                 # [line] seconds before an empty line delivers its first board
    due: list[int]                  # [job] seconds
    weight: list[int]               # [job] scaled weight
    horizon: int                    # an upper bound on any completion time

    @property
    def n(self) -> int:
        return len(self.orders)

    def setup_between(self, line_index: int, i: int, j: int) -> int:
        return self.setup[self.lines[line_index]][self.orders[i].product][self.orders[j].product]


def make_instance(orders: list[Order], model: PlanningModel) -> Instance:
    lines = list(model.lines)
    duration = [[round(model.lot_duration_s(line, o.product, o.quantity)) for line in lines] for o in orders]
    setup = {line: {a: {b: round(v) for b, v in row.items()} for a, row in model.changeover_s[line].items()}
             for line in lines}
    fill = [round(model.fill_s[line]) for line in lines]
    max_setup = max(v for line in lines for row in setup[line].values() for v in row.values())
    horizon = max(fill) + sum(max(row) for row in duration) + len(orders) * max_setup
    return Instance(
        orders=list(orders),
        lines=lines,
        duration=duration,
        setup=setup,
        fill=fill,
        due=[round(o.due_s) for o in orders],
        weight=[round(o.weight * WEIGHT_SCALE) for o in orders],
        horizon=horizon,
    )
