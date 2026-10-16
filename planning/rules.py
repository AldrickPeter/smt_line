"""Dispatching rules: simple, instant planning methods, as used in most factories.

Two parts to every rule:

  1. In what ORDER to consider the jobs (the rule itself):
       FIFO  first in, first out: the order book as received
       EDD   earliest due date first (classic rule for on-time delivery)
       SPT   shortest processing time first (finishes many orders early)
  2. Which LINE gets each job: the line where it would finish earliest,
     counting that line's current workload and the changeover from the
     product it ran last ("list scheduling").

MIN_SETUP is a different kind of rule: instead of a fixed list, whenever a
line becomes free it picks the waiting job with the cheapest changeover from
that line's last product (ties broken by due date). It batches same-product
jobs together and so makes few changeovers.

All rules use only the planning model, never the simulator.
"""

import time
from collections.abc import Callable

from planning.model import PlanningModel
from planning.plan import Lot, Plan
from smt_sim.orders import Order


class _LineState:
    """What a rule needs to know about each line while building a plan."""

    def __init__(self, model: PlanningModel, line: str) -> None:
        self.model, self.line = model, line
        self.lots: list[Lot] = []
        self.free_at_s = model.fill_s[line]   # an empty line delivers its first board after `fill`
        self.last_product: str | None = None

    def finish_if_added(self, order: Order) -> float:
        return (self.free_at_s + self.model.setup_s(self.line, self.last_product, order.product)
                + self.model.lot_duration_s(self.line, order.product, order.quantity))

    def add(self, order: Order) -> None:
        self.free_at_s = self.finish_if_added(order)
        self.last_product = order.product
        self.lots.append(Lot(order.id, order.product, order.quantity))


def list_schedule(method: str, ordered: list[Order], model: PlanningModel) -> Plan:
    """Give each job, in the given order, to the line where it finishes earliest."""
    start = time.perf_counter()
    lines = [_LineState(model, line) for line in model.lines]
    for order in ordered:
        best = min(lines, key=lambda ls: ls.finish_if_added(order))
        best.add(order)
    return Plan(method, {ls.line: ls.lots for ls in lines}, solve_time_s=time.perf_counter() - start)


def fifo(orders: list[Order], model: PlanningModel) -> Plan:
    return list_schedule("FIFO", list(orders), model)


def edd(orders: list[Order], model: PlanningModel) -> Plan:
    return list_schedule("EDD", sorted(orders, key=lambda o: (o.due_s, o.id)), model)


def spt(orders: list[Order], model: PlanningModel) -> Plan:
    def fastest_time(o: Order) -> float:
        return min(model.lot_duration_s(line, o.product, o.quantity) for line in model.lines)
    return list_schedule("SPT", sorted(orders, key=lambda o: (fastest_time(o), o.id)), model)


def min_setup(orders: list[Order], model: PlanningModel) -> Plan:
    """When a line becomes free, give it the waiting job with the cheapest changeover."""
    start = time.perf_counter()
    lines = [_LineState(model, line) for line in model.lines]
    waiting = sorted(orders, key=lambda o: (o.due_s, o.id))
    while waiting:
        line = min(lines, key=lambda ls: (ls.free_at_s, ls.line))   # the line free first
        job = min(waiting, key=lambda o: (model.setup_s(line.line, line.last_product, o.product), o.due_s))
        line.add(job)
        waiting.remove(job)
    return Plan("MIN_SETUP", {ls.line: ls.lots for ls in lines}, solve_time_s=time.perf_counter() - start)


RULES: dict[str, Callable[[list[Order], PlanningModel], Plan]] = {
    "FIFO": fifo,
    "EDD": edd,
    "SPT": spt,
    "MIN_SETUP": min_setup,
}
