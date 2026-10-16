"""Exact optimization with OR-Tools CP-SAT (constraint programming).

Variables, for every job j (one order = one lot) and line l:
    on[j][l]        1 if job j runs on line l              (each job: exactly one line)
    start[j], end[j]  whole seconds
Per line, the jobs on it form a ROUTE through the line, like a delivery van
visiting addresses: a "circuit" from a dummy depot node 0, through each job on
that line once, and back to 0. Arc i -> j being chosen means "j runs right
after i on this line", and then
    start[j] >= end[i] + changeover(product i -> product j)
Arc 0 -> j means "j is first on this line": start[j] >= fill time.
A job not on line l is skipped in l's circuit (a self-loop arc on j, which
is allowed only when on[j][l] = 0).

CP-SAT's AddCircuit constraint handles all of this; it is the standard way to
model sequence-dependent setup times in CP-SAT.

Objective, in two passes (see instance.py):
    pass 1: minimize total weighted tardiness
    pass 2: keep that optimal tardiness, minimize the makespan
"""

import time

from ortools.sat.python import cp_model

from planning.instance import Instance, make_instance
from planning.model import PlanningModel
from planning.plan import Lot, Plan
from smt_sim.orders import Order


def solve_cpsat(orders: list[Order], model: PlanningModel, time_limit_s: float = 30.0,
                workers: int = 8) -> Plan:
    inst = make_instance(orders, model)
    start_clock = time.perf_counter()
    m, start, end, on, tardiness, makespan = _build(inst)
    weighted = sum(inst.weight[j] * tardiness[j] for j in range(inst.n))

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = workers

    # Pass 1: weighted tardiness.
    m.Minimize(weighted)
    solver.parameters.max_time_in_seconds = time_limit_s / 2
    status1 = solver.Solve(m)
    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT found no plan (status {solver.StatusName(status1)})")
    best_weighted = round(solver.ObjectiveValue())

    # Pass 2: same tardiness, shortest makespan. Start from the pass-1 plan.
    m.Add(weighted <= best_weighted)
    m.Minimize(makespan)
    _hint_current_solution(m, solver, start, end, on)
    solver.parameters.max_time_in_seconds = time_limit_s / 2
    status2 = solver.Solve(m)

    plan = _extract(inst, solver, start, on, "CP-SAT")
    plan.solve_time_s = time.perf_counter() - start_clock
    plan.notes = f"pass1 {solver.StatusName(status1)}, pass2 {solver.StatusName(status2)}"
    return plan


def _build(inst: Instance):
    m = cp_model.CpModel()
    n, n_lines, H = inst.n, len(inst.lines), inst.horizon
    start = [m.NewIntVar(0, H, f"start_{j}") for j in range(n)]
    end = [m.NewIntVar(0, H, f"end_{j}") for j in range(n)]
    on = [[m.NewBoolVar(f"on_{j}_{l}") for l in range(n_lines)] for j in range(n)]

    for j in range(n):
        m.AddExactlyOne(on[j])
        for l in range(n_lines):
            m.Add(end[j] == start[j] + inst.duration[j][l]).OnlyEnforceIf(on[j][l])

    for l in range(n_lines):
        arcs = [(0, 0, m.NewBoolVar(f"line_{l}_empty"))]   # line with no jobs at all
        for j in range(n):
            node = j + 1
            first = m.NewBoolVar(f"first_{j}_{l}")
            arcs.append((0, node, first))
            m.Add(start[j] >= inst.fill[l]).OnlyEnforceIf(first)
            arcs.append((node, 0, m.NewBoolVar(f"last_{j}_{l}")))
            arcs.append((node, node, on[j][l].Not()))      # skip j on this line
            for i in range(n):
                if i != j:
                    follows = m.NewBoolVar(f"{i}_then_{j}_on_{l}")
                    arcs.append((i + 1, node, follows))
                    m.Add(start[j] >= end[i] + inst.setup_between(l, i, j)).OnlyEnforceIf(follows)
        m.AddCircuit(arcs)

    tardiness = []
    for j in range(n):
        t = m.NewIntVar(0, H, f"tardiness_{j}")
        m.AddMaxEquality(t, [0, end[j] - inst.due[j]])
        tardiness.append(t)
    makespan = m.NewIntVar(0, H, "makespan")
    m.AddMaxEquality(makespan, end)
    return m, start, end, on, tardiness, makespan


def _hint_current_solution(m, solver, start, end, on) -> None:
    for var in start + end + [b for row in on for b in row]:
        m.AddHint(var, solver.Value(var))


def _extract(inst: Instance, solver, start, on, method: str) -> Plan:
    lines: dict[str, list[Lot]] = {line: [] for line in inst.lines}
    for l, line in enumerate(inst.lines):
        jobs = [j for j in range(inst.n) if solver.Value(on[j][l])]
        for j in sorted(jobs, key=lambda j: solver.Value(start[j])):
            o = inst.orders[j]
            lines[line].append(Lot(o.id, o.product, o.quantity))
    return Plan(method, lines)
