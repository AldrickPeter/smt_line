"""Compare planning methods on order books, in the simulator, over many seeds.

Usage:
    python scripts/compare_planners.py [--orders demo tight large] [--seeds 20]
                                       [--time-limit 30] [--workers 8] [--out out/planning]
    python scripts/compare_planners.py --scaling [--sizes 4 5 6 7 8 10 12 14 16]

For every order book: build a plan with each method (FIFO, EDD, SPT, MIN_SETUP,
CP-SAT, MILP), simulate every plan on the same seeds, and write
    results.csv          one row per (order book, method, seed)
    summary.md           a table per order book
    gantt_<book>.png, kpi_<book>.png, spread_<book>.png, vs_fifo_<book>.png,
    planned_vs_simulated.png
With --scaling: solve time and plan quality vs number of orders (random order
books), written to scaling.csv and solver_scaling.png. Run separately because
the solvers must not compete for CPU with simulations while being timed.
"""

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # allow running without install

from planning import charts  # noqa: E402
from planning.bruteforce import solve_bruteforce  # noqa: E402
from planning.cpsat import solve_cpsat  # noqa: E402
from planning.evaluate import RunResult, mean_ci95, simulate_plan  # noqa: E402
from planning.generate import random_orders  # noqa: E402
from planning.milp import solve_milp  # noqa: E402
from planning.model import PlanningModel  # noqa: E402
from planning.plan import Plan, predict, load_line_configs  # noqa: E402
from planning.rules import RULES, edd  # noqa: E402
from smt_sim.orders import load_orders  # noqa: E402

MODEL_PATH = ROOT / "config" / "planning_model.yaml"


def build_plans(orders, model: PlanningModel, time_limit_s: float) -> list[Plan]:
    plans = [rule(orders, model) for rule in RULES.values()]
    plans.append(solve_cpsat(orders, model, time_limit_s))
    plans.append(solve_milp(orders, model, time_limit_s))
    for p in plans:
        p.validate(orders)
    return plans


def simulate_all(plans, orders, model, base_configs, seeds, workers) -> dict[str, list[RunResult]]:
    """Every plan on every seed, spread over worker processes. Same seeds for every plan."""
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {p.method: [pool.submit(simulate_plan, p, orders, base_configs, s, model.first_pass_yield)
                              for s in seeds] for p in plans}
        return {method: [f.result() for f in fs] for method, fs in futures.items()}


def summary_table(name, plans, predictions, results) -> str:
    rows = [f"### {name}\n",
            "| method | solve time | planned weighted lateness | simulated weighted lateness (95 % CI) | "
            "simulated makespan | late orders | changeovers | top-ups | factory OEE | FPY |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for p in plans:
        runs = results[p.method]
        wt, wt_ci = mean_ci95([r.weighted_tardiness_h for r in runs])
        mk, mk_ci = mean_ci95([r.makespan_h for r in runs])

        def avg(key):
            return sum(getattr(r, key) for r in runs) / len(runs)

        rows.append(f"| {p.method} | {p.solve_time_s:.2f} s | {predictions[p.method].weighted_tardiness_h:.2f} h "
                    f"| {wt:.2f} ± {wt_ci:.2f} h | {mk:.2f} ± {mk_ci:.2f} h | {avg('late_orders'):.1f} "
                    f"| {avg('changeovers'):.1f} | {avg('top_ups'):.1f} | {avg('factory_oee'):.1%} "
                    f"| {avg('first_pass_yield'):.1%} |")
    unfinished = sum(not r.finished for runs in results.values() for r in runs)
    if unfinished:
        rows.append(f"\n{unfinished} runs hit the time cap and count as finished at the cap.")
    return "\n".join(rows) + "\n"


def run_comparison(args, model) -> None:
    out = Path(args.out)
    base_configs, _ = load_line_configs()
    seeds = list(range(1, args.seeds + 1))
    csv_rows, md, panels = [], ["# Planning method comparison\n",
                                f"{len(seeds)} seeds per method (the same seeds for every method). "
                                f"Solver time limit {args.time_limit:g} s.\n"], []
    for name in args.orders:
        orders = load_orders(ROOT / "config" / "orders" / f"{name}_order.yaml")
        print(f"\n== {name}: {len(orders)} orders ==")
        plans = build_plans(orders, model, args.time_limit)
        predictions = {p.method: predict(p, orders, model) for p in plans}
        for p in plans:
            print(f"  planned {p.method:10s} weighted lateness {predictions[p.method].weighted_tardiness_h:6.2f} h"
                  f"  ({p.solve_time_s:.2f} s) {p.notes}")
        clock = time.perf_counter()
        results = simulate_all(plans, orders, model, base_configs, seeds, args.workers)
        print(f"  simulated {len(plans)} plans x {len(seeds)} seeds in {time.perf_counter() - clock:.0f} s")

        for method, runs in results.items():
            for r in runs:
                csv_rows.append({"order_book": name, "method": method, "seed": r.seed, "finished": r.finished,
                                 "makespan_h": round(r.makespan_h, 4),
                                 "weighted_tardiness_h": round(r.weighted_tardiness_h, 4),
                                 "late_orders": r.late_orders, "changeovers": r.changeovers, "top_ups": r.top_ups,
                                 "factory_oee": round(r.factory_oee, 4),
                                 "first_pass_yield": round(r.first_pass_yield, 4)})
        table = summary_table(name, plans, predictions, results)
        print(table)
        md.append(table)
        panels.append((name, predictions, results))

        title = f"{name} order book ({len(orders)} orders)"
        charts.gantt(plans, predictions, orders, model, f"Plans for the {title}", out / f"gantt_{name}.png")
        charts.kpi_comparison(results, predictions, f"Simulated results, {title}, {len(seeds)} seeds",
                              out / f"kpi_{name}.png")
        charts.distribution(results, "weighted_tardiness_h", "Weighted lateness (h)",
                            f"Weighted lateness per seed, {title}", out / f"spread_{name}.png")
        charts.paired_vs_baseline(results, "FIFO", "weighted_tardiness_h",
                                  "Weighted lateness minus FIFO's on the same seed (h), negative = better",
                                  f"Improvement over FIFO, {title}", out / f"vs_fifo_{name}.png")

    charts.planned_vs_simulated(panels, out / "planned_vs_simulated.png")
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Written to {out}")


def run_scaling(args, model) -> None:
    out = Path(args.out)
    rows = []
    for n in args.sizes:
        for instance in range(args.instances):
            orders = random_orders(n, model, seed=1000 * n + instance, tightness=1.0)
            solvers = [("CP-SAT", lambda: solve_cpsat(orders, model, args.time_limit)),
                       ("MILP", lambda: solve_milp(orders, model, args.time_limit)),
                       ("EDD", lambda: edd(orders, model))]
            if n <= args.brute_force_max:
                solvers.append(("BRUTE_FORCE", lambda: solve_bruteforce(orders, model, max_jobs=args.brute_force_max)))
            for method, solve in solvers:
                plan = solve()
                pred = predict(plan, orders, model)
                rows.append({"n": n, "instance": instance, "method": method,
                             "solve_time_s": round(plan.solve_time_s, 3),
                             "hit_limit": "FEASIBLE" in plan.notes,
                             "weighted_tardiness_h": round(pred.weighted_tardiness_h, 4),
                             "makespan_h": round(pred.makespan_s / 3600, 4)})
                print(f"  n={n:2d} #{instance} {method:11s} {plan.solve_time_s:7.2f} s  "
                      f"weighted lateness {pred.weighted_tardiness_h:6.2f} h  {plan.notes}")
    with open(out / "scaling.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    charts.solver_scaling(rows, args.time_limit, out / "solver_scaling.png")
    print(f"Written to {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", nargs="+", default=["demo", "tight", "large"])
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--time-limit", type=float, default=30.0, help="per solver, seconds")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--out", default=str(ROOT / "out" / "planning"))
    parser.add_argument("--scaling", action="store_true", help="run the solver scaling study instead")
    parser.add_argument("--sizes", type=int, nargs="+", default=[4, 5, 6, 7, 8, 10, 12, 14, 16])
    parser.add_argument("--instances", type=int, default=2, help="random order books per size (scaling)")
    parser.add_argument("--brute-force-max", type=int, default=7)
    args = parser.parse_args()

    Path(args.out).mkdir(parents=True, exist_ok=True)
    model = PlanningModel.load(MODEL_PATH)
    if args.scaling:
        run_scaling(args, model)
    else:
        run_comparison(args, model)


if __name__ == "__main__":
    main()
