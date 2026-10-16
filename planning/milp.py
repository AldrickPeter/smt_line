"""Exact optimization as a mixed-integer linear program (MILP), solved with SCIP via OR-Tools.

The same problem as cpsat.py, written the "textbook" way with only linear
constraints, so it can be explained in a report:

Variables
    x[j,l]  in {0,1}   job j runs on line l
    y[i,j]  in {0,1}   (i < j) job i comes before job j, IF they share a line
    S_j, E_j >= 0      start and end of job j (seconds)
    T_j >= 0           tardiness of job j
    C                  makespan

Constraints
    sum_l x[j,l] = 1                                   each job on one line
    E_j = S_j + sum_l d[j,l] x[j,l]                    duration depends on the line
    S_j >= sum_l fill_l x[j,l]                          an empty line needs `fill` first
    For each pair i < j and line l ("big-M disjunction"):
      S_j >= E_i + s_l(i,j) - M (3 - x[i,l] - x[j,l] - y[i,j])    i before j
      S_i >= E_j + s_l(j,i) - M (2 - x[i,l] - x[j,l] + y[i,j])    j before i
    The "- M (...)" term switches a constraint off unless both jobs are on line l
    and the order matches y. M is a safe upper bound on any time (the horizon).
    T_j >= E_j - due_j,   C >= E_j

Note: this applies the changeover between ANY two jobs on a line in sequence,
not only neighbours. That is exact when a changeover is never longer than
going via a third product (setup(a,c) <= setup(a,b) + lot b + setup(b,c)),
which holds here because every lot takes far longer than a changeover.

Objective in two passes, like CP-SAT: weighted tardiness, then makespan.

Why compare with CP-SAT: big-M models have weak LP relaxations, so the
solver's bounds are poor and solve time grows quickly with the number of
jobs. That growth is one of the comparison charts.
"""

import time

from ortools.linear_solver import pywraplp

from planning.instance import make_instance
from planning.model import PlanningModel
from planning.plan import Lot, Plan
from smt_sim.orders import Order


def solve_milp(orders: list[Order], model: PlanningModel, time_limit_s: float = 30.0,
               backend: str = "SCIP") -> Plan:
    inst = make_instance(orders, model)
    clock = time.perf_counter()
    solver = pywraplp.Solver.CreateSolver(backend)
    if solver is None:
        raise RuntimeError(f"MILP backend {backend} not available")
    n, n_lines, M = inst.n, len(inst.lines), inst.horizon

    x = [[solver.BoolVar(f"x_{j}_{l}") for l in range(n_lines)] for j in range(n)]
    S = [solver.NumVar(0, M, f"S_{j}") for j in range(n)]
    E = [solver.NumVar(0, M, f"E_{j}") for j in range(n)]
    T = [solver.NumVar(0, M, f"T_{j}") for j in range(n)]
    C = solver.NumVar(0, M, "C")

    for j in range(n):
        solver.Add(sum(x[j]) == 1)
        solver.Add(E[j] == S[j] + sum(inst.duration[j][l] * x[j][l] for l in range(n_lines)))
        solver.Add(S[j] >= sum(inst.fill[l] * x[j][l] for l in range(n_lines)))
        solver.Add(T[j] >= E[j] - inst.due[j])
        solver.Add(C >= E[j])

    for i in range(n):
        for j in range(i + 1, n):
            y = solver.BoolVar(f"y_{i}_{j}")
            for l in range(n_lines):
                solver.Add(S[j] >= E[i] + inst.setup_between(l, i, j) - M * (3 - x[i][l] - x[j][l] - y))
                solver.Add(S[i] >= E[j] + inst.setup_between(l, j, i) - M * (2 - x[i][l] - x[j][l] + y))

    weighted = sum(inst.weight[j] * T[j] for j in range(n))

    # Pass 1: weighted tardiness.
    solver.SetTimeLimit(int(time_limit_s / 2 * 1000))
    solver.Minimize(weighted)
    status1 = solver.Solve()
    if status1 not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError(f"MILP found no plan (status {status1})")
    best_weighted = solver.Objective().Value()

    # Pass 2: keep that tardiness (small tolerance for floating point), minimize makespan.
    solver.Add(weighted <= best_weighted + 0.5)
    solver.Minimize(C)
    status2 = solver.Solve()

    lines: dict[str, list[Lot]] = {line: [] for line in inst.lines}
    for l, line in enumerate(inst.lines):
        jobs = [j for j in range(n) if x[j][l].solution_value() > 0.5]
        for j in sorted(jobs, key=lambda j: S[j].solution_value()):
            o = inst.orders[j]
            lines[line].append(Lot(o.id, o.product, o.quantity))
    names = {pywraplp.Solver.OPTIMAL: "OPTIMAL", pywraplp.Solver.FEASIBLE: "FEASIBLE"}
    return Plan("MILP", lines, solve_time_s=time.perf_counter() - clock,
                notes=f"pass1 {names.get(status1, status1)}, pass2 {names.get(status2, status2)}")
