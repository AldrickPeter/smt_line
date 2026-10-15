"""The board record that travels down the line, and the plan that releases boards.

A board carries its product spec (BOM, reflow profile, ...) so every station
can see what it is working on. `measurements` holds what each station
physically did to the board plus the cause values behind it.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Board:
    serial: str
    product: str
    spec: dict[str, Any]   # this product's config (shared, not copied)
    time_in_s: dict[str, float] = field(default_factory=dict)   # station name -> sim time entered
    time_out_s: dict[str, float] = field(default_factory=dict)  # station name -> sim time left
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)  # station name -> values

    @property
    def bom(self) -> dict[str, int]:
        """Part number -> quantity per board."""
        return self.spec["bom"]

    @property
    def component_count(self) -> int:
        return sum(self.bom.values())


class ProductionPlan:
    """Releases bare boards into the line, job by job.

    plan config:
        jobs:   [{product: NAME, quantity: N}, ...]   quantity omitted = run forever
        repeat: true -> start again at the first job after the last one

    It has the same `is_empty()` / `peek()` / `pop()` interface as a Buffer,
    so the printer treats it like any other input. When a non-repeating plan
    is finished it reports empty and the printer goes STARVED.
    """

    def __init__(self, plan_cfg: dict[str, Any], products: dict[str, dict[str, Any]]) -> None:
        self.jobs: list[dict[str, Any]] = plan_cfg["jobs"]
        self.repeat: bool = plan_cfg.get("repeat", False)
        self.products = products
        for job in self.jobs:
            if job["product"] not in products:
                raise ValueError(f"Production plan names unknown product {job['product']!r}")
            if job.get("quantity") is not None and job["quantity"] < 1:
                raise ValueError("Job quantity must be >= 1 (or omitted for 'forever')")

        self.boards_created = 0
        self._job_index = 0
        self._released_in_job = 0
        self._next: Board | None = None   # created on first peek, handed out on pop

    def is_empty(self) -> bool:
        return self._upcoming() is None

    def peek(self) -> Board:
        board = self._upcoming()
        if board is None:
            raise IndexError("Production plan is finished")
        return board

    def pop(self) -> Board:
        board = self.peek()
        self._next = None
        return board

    def _upcoming(self) -> Board | None:
        if self._next is None:
            self._next = self._make_next_board()
        return self._next

    def _make_next_board(self) -> Board | None:
        while True:
            if self._job_index >= len(self.jobs):
                if not self.repeat:
                    return None
                self._job_index = 0
            job = self.jobs[self._job_index]
            quantity = job.get("quantity")
            if quantity is None or self._released_in_job < quantity:
                self._released_in_job += 1
                self.boards_created += 1
                product = job["product"]
                return Board(f"SN{self.boards_created:06d}", product, self.products[product])
            self._job_index += 1
            self._released_in_job = 0
