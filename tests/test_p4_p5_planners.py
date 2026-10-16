"""P4 dispatching rules and P5 exact optimizers (CP-SAT, MILP), checked against brute force."""

import pytest

from planning.bruteforce import score, solve_bruteforce
from planning.cpsat import solve_cpsat
from planning.generate import random_orders
from planning.instance import make_instance
from planning.milp import solve_milp
from planning.model import PlanningModel
from planning.plan import predict
from planning.rules import RULES, edd, fifo, min_setup
from smt_sim.orders import Order, load_orders

A, B, C = "CTRL-BOARD-A", "SENSOR-BOARD-B", "POWER-BOARD-C"


@pytest.fixture(scope="module")
def model() -> PlanningModel:
    return PlanningModel.load("config/planning_model.yaml")


def int_score(plan, orders, model):
    """The exact objective (weighted tardiness, makespan) every solver optimizes."""
    inst = make_instance(orders, model)
    index = {o.id: j for j, o in enumerate(orders)}
    return score(inst, [[index[lot.order_id] for lot in plan.lines[line]] for line in inst.lines])


# ---- dispatching rules --------------------------------------------------------


def test_every_rule_gives_a_valid_plan(model):
    for name in ("demo", "tight", "large"):
        orders = load_orders(f"config/orders/{name}_order.yaml")
        for rule in RULES.values():
            rule(orders, model).validate(orders)


def test_fifo_keeps_the_order_book_sequence_on_each_line(model):
    orders = load_orders("config/orders/tight_order.yaml")
    position = {o.id: i for i, o in enumerate(orders)}
    for lots in fifo(orders, model).lines.values():
        seq = [position[lot.order_id] for lot in lots]
        assert seq == sorted(seq)


def test_edd_runs_each_line_in_due_date_order(model):
    orders = load_orders("config/orders/tight_order.yaml")
    due = {o.id: o.due_s for o in orders}
    for lots in edd(orders, model).lines.values():
        dues = [due[lot.order_id] for lot in lots]
        assert dues == sorted(dues)


def test_list_scheduling_puts_a_job_where_it_finishes_first():
    """Hand-checkable: line F is twice as fast, so the first job goes there."""
    model = PlanningModel(
        lines=["S", "F"], products=[A],
        seconds_per_good_board={"S": {A: 20.0}, "F": {A: 10.0}},
        changeover_s={"S": {A: {A: 0}}, "F": {A: {A: 0}}},
        fill_s={"S": 0.0, "F": 0.0}, first_pass_yield={"S": {A: 1}, "F": {A: 1}},
    )
    orders = [Order("O1", A, 100, 10_000), Order("O2", A, 100, 10_000), Order("O3", A, 100, 10_000)]
    plan = fifo(orders, model)
    # O1: F finishes at 1000, S at 2000 -> F.  O2: F 2000, S 2000, a tie -> first line, S.
    # O3: F 2000, S 4000 -> F.  Makespan 2000.
    assert [lot.order_id for lot in plan.lines["F"]][0] == "O1"
    assert predict(plan, orders, model).makespan_s == 2000


def test_min_setup_makes_fewer_changeovers_than_fifo(model):
    orders = load_orders("config/orders/tight_order.yaml")
    assert predict(min_setup(orders, model), orders, model).changeovers < \
        predict(fifo(orders, model), orders, model).changeovers


# ---- exact methods ----------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_cpsat_and_milp_match_brute_force(model, seed):
    orders = random_orders(6, model, seed=seed, tightness=0.9)
    best = int_score(solve_bruteforce(orders, model), orders, model)
    assert int_score(solve_cpsat(orders, model, time_limit_s=20), orders, model) == best
    milp = int_score(solve_milp(orders, model, time_limit_s=20), orders, model)
    assert milp[0] == best[0] and milp[1] == pytest.approx(best[1], abs=2)   # float rounding


def test_optimizers_never_lose_to_the_rules(model):
    orders = load_orders("config/orders/tight_order.yaml")
    best_rule = min(int_score(rule(orders, model), orders, model) for rule in RULES.values())
    cpsat = solve_cpsat(orders, model, time_limit_s=20)
    assert int_score(cpsat, orders, model) <= best_rule
    assert "OPTIMAL" in cpsat.notes


def test_brute_force_refuses_big_instances(model):
    with pytest.raises(ValueError):
        solve_bruteforce(random_orders(12, model, seed=1), model)


def test_random_orders_are_reproducible(model):
    assert random_orders(8, model, seed=5) == random_orders(8, model, seed=5)
    assert random_orders(8, model, seed=5) != random_orders(8, model, seed=6)
