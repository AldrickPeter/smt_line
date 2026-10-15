"""Pick-and-place machine.

Milestone 1: only its cycle time differs from the base station, because it
scales with how many components the board needs. Nozzles and feeders come in
milestone 4.
"""

from smt_sim.board import Board
from smt_sim.station import Station


class PickAndPlace(Station):
    def cycle_time_for(self, board: Board) -> float:
        handling_s = self.params["board_handling_s"]
        per_part_s = self.params["time_per_component_s"]
        return handling_s + per_part_s * board.component_count
