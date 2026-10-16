"""Brute force: try every possible plan. Only feasible for a handful of orders.

Every assignment of jobs to lines, and every order of the jobs on each line,
is scored with the same integer data and objective as CP-SAT and MILP. With
n jobs and 2 lines that is (n + 1) * n! plans: 5,040 for 6 jobs, 3.6 million
for 9. It exists to PROVE on small cases that the optimizers find the optimum,
and to show in the scaling chart why we need smarter methods.
"""

import itertools
import time

from planning.instance import Instance, make_instance
from planning.model import PlanningModel
from planning.plan import Lot, Plan
from smt_sim.orders import Order


def score(inst: Instance, sequences: list[list[int]]) -> tuple[int, int]:
    """(weighted tardiness, makespan) of a plan: one job sequence per line, back to back."""
    weighted, makespan = 0, 0
    for l, seq in enumerate(sequences):
        t, previous = inst.fill[l], None
        for j in seq:
            if previous is not None:
                t += inst.setup_between(l, previous, j)
            t += inst.duration[j][l]
            weighted += inst.weight[j] * max(0, t - inst.due[j])
            makespan = max(makespan, t)
            previous = j
    return weighted, makespan


def solve_bruteforce(orders: list[Order], model: PlanningModel, max_jobs: int = 8) -> Plan:
    if len(orders) > max_jobs:
        raise ValueError(f"Brute force with {len(orders)} jobs would take too long (max {max_jobs})")
    inst = make_instance(orders, model)
    clock = time.perf_counter()
    n_lines = len(inst.lines)
    best, best_sequences = None, None
    for assignment in itertools.product(range(n_lines), repeat=inst.n):
        groups = [[j for j in range(inst.n) if assignment[j] == l] for l in range(n_lines)]
        for sequences in itertools.product(*(itertools.permutations(g) for g in groups)):
            s = score(inst, [list(seq) for seq in sequences])
            if best is None or s < best:   # tuples compare lexicographically
                best, best_sequences = s, sequences
    lines = {line: [Lot(inst.orders[j].id, inst.orders[j].product, inst.orders[j].quantity)
                    for j in best_sequences[l]] for l, line in enumerate(inst.lines)}
    return Plan("BRUTE_FORCE", lines, solve_time_s=time.perf_counter() - clock)
