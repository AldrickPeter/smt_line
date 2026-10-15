"""Base station: a single-board server with a small state machine.

Each tick a station does at most these things, in order:
  1. If it is DOWN, count down the repair time and do nothing else.
  2. If it holds a finished board, push it to the output buffer
     (or go BLOCKED if the buffer is full).
  3. If it is empty, pull the next board from the input buffer
     (or go STARVED if there is none).
  4. Work on the current board for one tick (RUNNING).

Timing note: a board's cycle starts on the tick it is pulled and finishes once
`dt` has been subtracted enough times, so cycle times are effectively rounded
up to a whole number of ticks. With the default 0.5 s tick this error is small.
"""

from typing import Any, Protocol

import numpy as np

from smt_sim.board import Board
from smt_sim.events import Event, EventLog, StationState


class BoardInput(Protocol):
    """Anything a station can pull boards from (a Buffer or the BoardSource)."""

    def is_empty(self) -> bool: ...
    def pop(self) -> Board: ...


class Station:
    def __init__(
        self,
        name: str,
        params: dict[str, Any],
        input_buffer: BoardInput,
        output_buffer: Any,          # a Buffer
        event_log: EventLog,
        rng: np.random.Generator,
    ) -> None:
        self.name = name
        self.params = params
        self.input_buffer = input_buffer
        self.output_buffer = output_buffer
        self.event_log = event_log
        self.rng = rng   # unused until milestone 3, but every component gets the shared RNG

        self.state = StationState.STARVED
        self.reason_code: str | None = None
        self.part_count = 0          # boards completed and handed downstream

        self.current_board: Board | None = None
        self.remaining_s = 0.0       # work left on current_board

        self._down_remaining_s = 0.0
        self._down_reason: str | None = None

        # Clock of the tick being processed, so _set_state can timestamp events.
        self._tick = 0
        self._time_s = 0.0

        self.event_log.record(Event(0, 0.0, name, None, self.state, None))

    # ---- public API -------------------------------------------------------

    def cycle_time_for(self, board: Board) -> float:
        """Ideal processing time for one board, in seconds."""
        return float(self.params["cycle_time_s"])

    def force_down(self, reason_code: str, duration_s: float) -> None:
        """Stop the station for `duration_s`. Used by tests and scenario hooks.

        A board being worked on stays in the station and resumes afterwards.
        """
        self._down_remaining_s = duration_s
        self._down_reason = reason_code

    def step(self, tick: int, time_s: float, dt_s: float) -> None:
        """Advance this station by one tick."""
        self._tick, self._time_s = tick, time_s

        if self._down_remaining_s > 0:
            self._set_state(StationState.DOWN, self._down_reason)
            self._down_remaining_s -= dt_s
            return

        self._process(dt_s)

    # ---- internals --------------------------------------------------------

    def _process(self, dt_s: float) -> None:
        """Single-board server logic: push finished, pull next, work."""
        # 1. Hand off a finished board.
        if self.current_board is not None and self.remaining_s <= 0:
            if not self.output_buffer.has_space():
                self._set_state(StationState.BLOCKED)
                return
            self._finish_board(self.current_board)
            self.current_board = None

        # 2. Pull the next board.
        if self.current_board is None:
            if self.input_buffer.is_empty():
                self._set_state(StationState.STARVED)
                return
            self.current_board = self.input_buffer.pop()
            self.current_board.time_in_s[self.name] = self._time_s
            self.remaining_s = self.cycle_time_for(self.current_board)

        # 3. Work on it.
        self._set_state(StationState.RUNNING)
        self.remaining_s -= dt_s

    def _finish_board(self, board: Board) -> None:
        board.time_out_s[self.name] = self._time_s
        self.output_buffer.push(board)
        self.part_count += 1

    def _set_state(self, new_state: StationState, reason_code: str | None = None) -> None:
        """Change state and log it. Repeated calls with the same state are ignored."""
        if new_state == self.state and reason_code == self.reason_code:
            return
        self.event_log.record(
            Event(self._tick, self._time_s, self.name, self.state, new_state, reason_code)
        )
        self.state = new_state
        self.reason_code = reason_code
