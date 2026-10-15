"""The board record that travels down the line.

Milestone 1 only tracks identity and per-station timestamps. Measurements,
true defects and detected defects are added in milestone 5.
"""

from dataclasses import dataclass, field


@dataclass
class Board:
    serial: str
    product: str
    component_count: int
    time_in_s: dict[str, float] = field(default_factory=dict)   # station name -> sim time entered
    time_out_s: dict[str, float] = field(default_factory=dict)  # station name -> sim time left


class BoardSource:
    """Bare-board loader at the head of the line: never runs out.

    It has the same `is_empty()` / `pop()` interface as a Buffer, so the
    printer can treat it exactly like any other input buffer.
    """

    def __init__(self, product: str, component_count: int) -> None:
        self.product = product
        self.component_count = component_count
        self.boards_created = 0

    def is_empty(self) -> bool:
        return False

    def pop(self) -> Board:
        self.boards_created += 1
        return Board(
            serial=f"SN{self.boards_created:06d}",
            product=self.product,
            component_count=self.component_count,
        )
