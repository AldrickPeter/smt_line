"""Pick-and-place machine: feeders, reels, splicing, nozzles.

Cycle time scales with the board's component count. On top of that, each
board's parts are picked one by one:

  - Parts come from the feeder holding that part number. Every pick, good or
    failed, uses up one part from the reel.
  - Nozzles take turns (round robin). A worn or clogged nozzle places parts
    with a larger offset (µm) and fails more picks. A failed pick is retried,
    which costs one extra component time.
  - The machine's vision sees failed picks. After K failures in a row on the
    same nozzle it stops: DOWN / NOZZLE_CLOG, and the nozzle is cleaned.
    Until then, a clogged nozzle keeps placing parts badly: those boards carry
    the large offsets, which become defects in milestone 5.

Reels and the operator:
  - When a reel drops below its low-reel warning (enough parts for N more
    boards) the operator is called. With some probability the call is missed.
  - If the operator arrives before the reel is empty, they splice a new reel
    on: no stop.
  - If the reel runs empty first: STARVED_MATERIAL / FEEDER_EMPTY until the
    operator arrives, plus the time to load a new reel.

Simplification: all picks for a board happen at the moment it starts, then
the cycle time runs. If a reel empties part-way, the board waits in the
machine and the remaining picks happen after the reel change.
"""

from typing import Any

from smt_sim.board import Board
from smt_sim.components import Feeder, Nozzle
from smt_sim.events import ReasonCode, StationState
from smt_sim.station import Station


class PickAndPlace(Station):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        nozzle_cfg = self.params.get("nozzles")
        self.nozzles = [Nozzle(i, nozzle_cfg, self.rng) for i in range(nozzle_cfg["count"])] if nozzle_cfg else []
        self.clog_detect_fails = int(nozzle_cfg["clog_detect_consecutive_fails"]) if nozzle_cfg else 0
        self.clean_time_s = float(nozzle_cfg["clean_time_s"]) if nozzle_cfg else 0.0

        feeder_cfg = self.params.get("feeders")
        self.feeders: dict[str, Feeder] = {}   # part number -> feeder
        if feeder_cfg:
            for slot_cfg in feeder_cfg["slots"]:
                feeder = Feeder(slot_cfg)
                self.feeders[feeder.part_number] = feeder
            self.low_reel_warning_boards = int(feeder_cfg["low_reel_warning_boards"])
            self.splice_miss_probability = float(feeder_cfg["splice_miss_probability"])
            self.reel_change_time_s = float(feeder_cfg["reel_change_time_s"])

        self.mispick_count = 0   # total failed picks (parts wasted)
        self._pick_counter = 0   # drives the nozzle round robin
        # Picking progress for the current board (kept across a mid-board stop).
        self._picking_board: Board | None = None
        self._picks_todo: list[str] = []         # part numbers still to place
        self._placements: list[tuple[str, int, float]] = []
        self._board_mispicks = 0

    def cycle_time_for(self, board: Board) -> float:
        handling_s = self.params["board_handling_s"]
        per_part_s = self.params["time_per_component_s"]
        return handling_s + per_part_s * board.component_count

    # ---- per-board picking --------------------------------------------------

    def _prepare_board(self, board: Board, dt_s: float) -> bool:
        if not self.nozzles or not self.feeders:
            return True

        if self._picking_board is not board:   # a new board: list every part to place
            self._picking_board = board
            self._placements = []
            self._picks_todo = []
            for part, qty in board.bom.items():
                self._picks_todo.extend([part] * qty)
            self._picks_todo.reverse()   # pop() from the end keeps BOM order
            self._board_mispicks = 0

        while self._picks_todo:
            part = self._picks_todo[-1]
            feeder = self._feeder_for(part)
            self._maybe_splice(feeder)
            if feeder.qty == 0:
                self._reel_empty_stop(feeder, dt_s)
                return False

            nozzle = self.nozzles[self._pick_counter % len(self.nozzles)]
            self._pick_counter += 1
            offset_um, nozzle_failed = nozzle.pick()
            feeder_failed = self.rng.random() < feeder.mispick_rate
            feeder.qty -= 1
            self._maybe_call_operator(feeder, board)

            if nozzle_failed or feeder_failed:
                self.mispick_count += 1
                self._board_mispicks += 1
                self.remaining_s += self.params["time_per_component_s"]   # retry costs a pick
                nozzle.consecutive_fails += 1
                if nozzle.consecutive_fails >= self.clog_detect_fails:
                    nozzle.clean()
                    self._stop_now(StationState.DOWN, ReasonCode.NOZZLE_CLOG, self.clean_time_s, dt_s)
                    return False
                continue   # retry the same part

            nozzle.consecutive_fails = 0
            self._placements.append((part, nozzle.nozzle_id, offset_um))
            self._picks_todo.pop()

        board.measurements[self.name] = {
            "placements": self._placements,   # (part number, nozzle id, offset µm)
            "max_abs_offset_um": max(abs(p[2]) for p in self._placements),
            "mispicks": self._board_mispicks,
        }
        return True

    def _feeder_for(self, part: str) -> Feeder:
        try:
            return self.feeders[part]
        except KeyError:
            raise KeyError(f"{self.name}: no feeder loaded with part {part!r}") from None

    # ---- reels and operator -------------------------------------------------

    def _maybe_call_operator(self, feeder: Feeder, board: Board) -> None:
        """Low-reel warning: call the operator once per reel."""
        warning_qty = board.bom[feeder.part_number] * self.low_reel_warning_boards
        if feeder.operator_called or feeder.qty > warning_qty:
            return
        feeder.operator_called = True
        if self.rng.random() >= self.splice_miss_probability:
            feeder.splice_at_s = self._time_s + self.operator.response_time_s()

    def _maybe_splice(self, feeder: Feeder) -> None:
        """The operator has arrived in time: splice without stopping."""
        if feeder.splice_at_s is not None and self._time_s >= feeder.splice_at_s and feeder.qty > 0:
            feeder.splice()

    def _reel_empty_stop(self, feeder: Feeder, dt_s: float) -> None:
        if feeder.splice_at_s is not None:   # operator already on the way
            wait_s = max(0.0, feeder.splice_at_s - self._time_s)
        else:                                # nobody coming: call now
            wait_s = self.operator.response_time_s()
        feeder.load_new_reel()   # done by the time the stop ends
        self._stop_now(StationState.STARVED_MATERIAL, ReasonCode.FEEDER_EMPTY,
                       wait_s + self.reel_change_time_s, dt_s)

    def extra_tags(self) -> dict[str, Any]:
        # Only what the real machine knows: reel counts and failed picks.
        # Whether a nozzle is actually clogged is hidden ground truth.
        tags: dict[str, Any] = {f"slot{f.slot}_qty": f.qty for f in self.feeders.values()}
        tags["mispick_count"] = self.mispick_count
        return tags
