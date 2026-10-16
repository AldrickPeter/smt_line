"""P2 planning model (calibrated from the simulator) and P3 plans + evaluator."""

import pytest

from planning.evaluate import evaluate, mean_ci95
from planning.model import PlanningModel, calibrate
from planning.plan import Lot, Plan, line_configs_for_plan, load_line_configs, predict
from smt_sim.config import make_deterministic
from smt_sim.orders import Order, load_orders

A, B, C = "CTRL-BOARD-A", "SENSOR-BOARD-B", "POWER-BOARD-C"
PRODUCTS = [A, B, C]


@pytest.fixture(scope="module")
def det_configs():
    base, _ = load_line_configs()
    return {name: make_deterministic(cfg) for name, cfg in base.items()}


@pytest.fixture(scope="module")
def det_model(det_configs):
    """Planning model of the deterministic lines (pace from deterministic runs)."""
    return calibrate(det_configs, PRODUCTS, seeds=[1], deterministic_pace=True)


def test_changeover_matrix_shape(det_model):
    for line in det_model.lines:
        for i in PRODUCTS:
            assert det_model.changeover_s[line][i][i] == 0
            for j in PRODUCTS:
                if i != j:
                    assert 1500 < det_model.changeover_s[line][i][j] < 4000   # ~25-65 min
        assert 300 < det_model.fill_s[line] < 600


def test_deterministic_pace_is_the_bottleneck_cycle(det_model):
    assert det_model.seconds_per_good_board["line1"] == pytest.approx({A: 38.0, B: 34.0, C: 32.0}, abs=0.5)
    assert all(v == pytest.approx(30.0, abs=0.5) for v in det_model.seconds_per_good_board["line2"].values())


def test_model_predicts_a_deterministic_run(det_model, det_configs):
    """The planning model's timeline matches the deterministic simulator within 2 %."""
    orders = [Order("O1", A, 60, 2 * 3600), Order("O2", C, 50, 5 * 3600), Order("O3", B, 40, 3 * 3600)]
    plan = Plan("hand", {"line1": [Lot("O1", A, 60), Lot("O2", C, 50)], "line2": [Lot("O3", B, 40)]})
    predicted = predict(plan, orders, det_model)
    [result] = evaluate(plan, orders, det_configs, seeds=[1])
    for o in orders:
        assert result.completion_s[o.id] == pytest.approx(predicted.completion_s[o.id], rel=0.02)
    assert predicted.changeovers == result.changeovers == 1


def test_model_save_and_load(det_model, tmp_path):
    path = tmp_path / "model.yaml"
    det_model.save(path)
    assert PlanningModel.load(path) == det_model


def test_predict_arithmetic():
    model = PlanningModel(
        lines=["L"], products=[A, B],
        seconds_per_good_board={"L": {A: 10.0, B: 20.0}},
        changeover_s={"L": {A: {A: 0, B: 100}, B: {A: 100, B: 0}}},
        fill_s={"L": 50.0},
        first_pass_yield={"L": {A: 1.0, B: 1.0}},
    )
    orders = [Order("O1", A, 10, 200), Order("O2", B, 5, 100, weight=2)]
    p = predict(Plan("t", {"L": [Lot("O1", A, 10), Lot("O2", B, 5)]}), orders, model)
    assert p.completion_s == {"O1": 50 + 100, "O2": 150 + 100 + 100}
    assert p.weighted_tardiness_h == pytest.approx(2 * (350 - 100) / 3600)
    assert p.late_orders == 1 and p.changeovers == 1 and p.makespan_s == 350


def test_plan_validation():
    orders = [Order("O1", A, 10, 100), Order("O2", B, 5, 100)]
    Plan("ok", {"L": [Lot("O1", A, 10), Lot("O2", B, 5)]}).validate(orders)
    with pytest.raises(ValueError):
        Plan("missing", {"L": [Lot("O1", A, 10)]}).validate(orders)
    with pytest.raises(ValueError):
        Plan("twice", {"L": [Lot("O1", A, 10), Lot("O1", A, 10), Lot("O2", B, 5)]}).validate(orders)
    with pytest.raises(ValueError):
        Plan("wrong qty", {"L": [Lot("O1", A, 9), Lot("O2", B, 5)]}).validate(orders)


def test_line_configs_carry_jobs_orders_and_planned_yield():
    base, _ = load_line_configs()
    orders = load_orders("config/orders/demo_order.yaml")
    lots = {"line1": [Lot(o.id, o.product, o.quantity) for o in orders[:3]],
            "line2": [Lot(o.id, o.product, o.quantity) for o in orders[3:]]}
    cfgs = line_configs_for_plan(Plan("t", lots), orders, base, {"line1": {A: 0.9, B: 0.9, C: 0.9}})
    jobs = cfgs["line1"]["production_plan"]["jobs"]
    assert [j["order"] for j in jobs] == [o.id for o in orders[:3]]
    assert all(j["planned_yield"] == 0.9 for j in jobs)
    assert all(j["planned_yield"] == 1.0 for j in cfgs["line2"]["production_plan"]["jobs"])
    assert {o["id"] for o in cfgs["line2"]["orders"]} == {o.id for o in orders[3:]}
    assert base["line1"].get("orders") is None   # base configs untouched


def test_planned_yield_cuts_top_ups():
    base, _ = load_line_configs()
    orders = load_orders("config/orders/demo_order.yaml")[:4]
    plan = Plan("t", {"line1": [Lot(o.id, o.product, o.quantity) for o in orders[:2]],
                      "line2": [Lot(o.id, o.product, o.quantity) for o in orders[2:]]})
    seeds = [1, 2, 3]
    without = evaluate(plan, orders, base, seeds)
    allowance = {line: {p: 0.88 for p in PRODUCTS} for line in base}
    with_allowance = evaluate(plan, orders, base, seeds, planned_yield=allowance)
    assert sum(r.top_ups for r in with_allowance) < sum(r.top_ups for r in without)
    assert all(r.finished for r in with_allowance + without)


def test_mean_ci95():
    mean, half = mean_ci95([1.0, 2.0, 3.0])
    assert mean == 2.0 and half == pytest.approx(4.30 * 1.0 / 3 ** 0.5, rel=1e-3)
