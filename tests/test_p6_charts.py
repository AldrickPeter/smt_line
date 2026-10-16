"""P6: every comparison chart renders from a tiny comparison (2 seeds, 3 orders)."""

import pytest

pytest.importorskip("matplotlib")

from planning import charts  # noqa: E402
from planning.evaluate import evaluate  # noqa: E402
from planning.model import PlanningModel  # noqa: E402
from planning.plan import load_line_configs, predict  # noqa: E402
from planning.rules import edd, fifo  # noqa: E402
from smt_sim.orders import Order  # noqa: E402

A, B, C = "CTRL-BOARD-A", "SENSOR-BOARD-B", "POWER-BOARD-C"


@pytest.fixture(scope="module")
def comparison():
    model = PlanningModel.load("config/planning_model.yaml")
    orders = [Order("SO-1", A, 40, 3600), Order("SO-2", B, 30, 1800, 2.0), Order("SO-3", C, 30, 5400)]
    base, _ = load_line_configs()
    plans = [fifo(orders, model), edd(orders, model)]
    predictions = {p.method: predict(p, orders, model) for p in plans}
    results = {p.method: evaluate(p, orders, base, seeds=[1, 2], planned_yield=model.first_pass_yield)
               for p in plans}
    return model, orders, plans, predictions, results


def test_all_charts_render(comparison, tmp_path):
    model, orders, plans, predictions, results = comparison
    charts.gantt(plans, predictions, orders, model, "t", tmp_path / "gantt.png")
    charts.kpi_comparison(results, predictions, "t", tmp_path / "kpi.png")
    charts.distribution(results, "weighted_tardiness_h", "h", "t", tmp_path / "spread.png")
    charts.paired_vs_baseline(results, "FIFO", "weighted_tardiness_h", "h", "t", tmp_path / "vs.png")
    charts.planned_vs_simulated([("t", predictions, results)], tmp_path / "pvs.png")
    rows = [{"n": n, "instance": 0, "method": m, "solve_time_s": t, "hit_limit": t >= 10,
             "weighted_tardiness_h": 1.0, "makespan_h": 5.0}
            for n in (4, 6) for m, t in (("CP-SAT", 0.1 * n), ("MILP", 2.0 * n), ("EDD", 0.0), ("BRUTE_FORCE", 0.5))]
    charts.solver_scaling(rows, 10, tmp_path / "scaling.png")
    for name in ("gantt", "kpi", "spread", "vs", "pvs", "scaling"):
        assert (tmp_path / f"{name}.png").stat().st_size > 2_000


def test_late_labels():
    assert charts._late_text(10) == ""
    assert charts._late_text(600) == "  late 10 min"
    assert charts._late_text(5400) == "  late 1.5 h"
