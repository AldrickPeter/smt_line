"""Reflow oven, modeled as a conveyor pipeline rather than a single-board server.

Many boards are inside the oven at once. Each one needs `transit_time_s` to
cross it. Boards must be spaced at least `board_pitch_mm` apart, so:

    capacity          = conveyor_length_mm / board_pitch_mm        (boards)
    entry_interval_s  = transit_time_s / capacity                  (seconds)

The entry interval is the oven's effective cycle time: it can accept one new
board every `entry_interval_s`, no matter how long each board stays inside.

Simplification: if the board at the exit cannot leave (output buffer full), the
whole conveyor halts. A real oven cannot stop its belt without overbaking the
boards; that consequence can be modeled later as a solder-defect cause.
"""

from collections import deque
from dataclasses import dataclass

from smt_sim.board import Board
from smt_sim.events import StationState
from smt_sim.station import Station


@dataclass
class _BoardInOven:
    board: Board
    remaining_s: float   # time left until it reaches the exit


class ReflowOven(Station):
    is_pipeline = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.transit_time_s = float(self.params["transit_time_s"])
        self.capacity = int(self.params["conveyor_length_mm"] // self.params["board_pitch_mm"])
        self.entry_interval_s = self.transit_time_s / self.capacity
        self.conveyor: deque[_BoardInOven] = deque()   # leftmost = closest to exit
        self._since_last_entry_s = self.entry_interval_s  # first board may enter at once
        # Sim time each board entered, like a board-in sensor at the entrance.
        # Used to judge the oven by its entrance for bottleneck detection.
        self.entry_times_s: list[float] = []

    def cycle_time_for(self, board: Board) -> float:
        return self.entry_interval_s

    def boards_inside(self) -> list[Board]:
        return [item.board for item in self.conveyor]

    def _process(self, dt_s: float) -> None:
        # 1. Board at the exit leaves, or the conveyor halts.
        if self.conveyor and self.conveyor[0].remaining_s <= 0:
            if not self.output_buffer.has_space():
                self._set_state(StationState.BLOCKED)
                return
            self._finish_board(self.conveyor.popleft().board)

        # 2. Load a new board if there is room and enough spacing.
        can_load = (
            len(self.conveyor) < self.capacity
            and self._since_last_entry_s >= self.entry_interval_s
            and not self.input_buffer.is_empty()
        )
        if can_load:
            board = self.input_buffer.pop()
            board.time_in_s[self.name] = self._time_s
            self.conveyor.append(_BoardInOven(board, self.transit_time_s))
            self._since_last_entry_s = 0.0
            self.entry_times_s.append(self._time_s)

        # 3. Move the belt.
        self._since_last_entry_s += dt_s
        if not self.conveyor:
            self._set_state(StationState.STARVED)
            return
        for item in self.conveyor:
            item.remaining_s -= dt_s
        self._set_state(StationState.RUNNING)
