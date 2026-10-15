"""The board record that travels down the line.

Milestone 4 adds `measurements`: what each station physically did to the
board (paste volume, placement offsets), plus the state of the cause behind
it (prints since wipe, nozzle id). Milestone 5 turns these into true and
detected defects.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Board:
    serial: str
    product: str
    bom: dict[str, int]   # part number -> quantity per board (shared, not copied)
    time_in_s: dict[str, float] = field(default_factory=dict)   # station name -> sim time entered
    time_out_s: dict[str, float] = field(default_factory=dict)  # station name -> sim time left
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)  # station name -> values

    @property
    def component_count(self) -> int:
        return sum(self.bom.values())


class BoardSource:
    """Bare-board loader at the head of the line: never runs out.

    It has the same `is_empty()` / `pop()` interface as a Buffer, so the
    printer can treat it exactly like any other input buffer.
    """

    def __init__(self, product: str, bom: dict[str, int]) -> None:
        self.product = product
        self.bom = bom
        self.boards_created = 0

    def is_empty(self) -> bool:
        return False

    def pop(self) -> Board:
        self.boards_created += 1
        return Board(serial=f"SN{self.boards_created:06d}", product=self.product, bom=self.bom)
