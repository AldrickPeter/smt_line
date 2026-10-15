"""Simulator: builds the line from config and runs the tick loop."""

from pathlib import Path
from typing import Any

import numpy as np

from smt_sim.board import Board, BoardSource
from smt_sim.buffer import Buffer
from smt_sim.config import load_config
from smt_sim.events import EventLog
from smt_sim.station import Station
from smt_sim.stations.pnp import PickAndPlace
from smt_sim.stations.reflow import ReflowOven

# Station `type` in the config -> class. Types not listed use the base Station
# (fixed cycle time) until their own behavior is added in later milestones.
STATION_CLASSES: dict[str, type[Station]] = {
    "pnp": PickAndPlace,
    "reflow": ReflowOven,
}


class Simulator:
    """A six-station SMT line advanced in fixed time steps (ticks).

    Layout: source -> st1 -> buf -> st2 -> buf -> ... -> st6 -> sink
    """

    def __init__(self, config: dict[str, Any] | str | Path | None = None, seed: int | None = None) -> None:
        if config is None or isinstance(config, (str, Path)):
            config = load_config(config)
        self.config = config

        self.dt_s: float = float(config["sim"]["tick_s"])
        self.seed: int = config["sim"]["seed"] if seed is None else seed
        self.rng = np.random.default_rng(self.seed)   # the ONE generator for this run

        self.tick = 0
        self.time_s = 0.0
        self.event_log = EventLog()

        product = config["product"]
        self.source = BoardSource(product["name"], product["component_count"])
        self.sink = Buffer("sink", capacity=None)   # finished boards collect here

        self.stations: list[Station] = []
        self.buffers: list[Buffer] = []
        self._build_line(config["line"])

    def _build_line(self, line_cfg: dict[str, Any]) -> None:
        station_cfgs = line_cfg["stations"]
        upstream: Any = self.source
        for i, st_cfg in enumerate(station_cfgs):
            is_last = i == len(station_cfgs) - 1
            if is_last:
                downstream = self.sink
            else:
                next_name = station_cfgs[i + 1]["name"]
                downstream = Buffer(f"{st_cfg['name']}->{next_name}", line_cfg["buffer_capacity"])
                self.buffers.append(downstream)

            cls = STATION_CLASSES.get(st_cfg["type"], Station)
            station = cls(st_cfg["name"], st_cfg, upstream, downstream, self.event_log, self.rng)
            self.stations.append(station)
            upstream = downstream

    # ---- running ----------------------------------------------------------

    def step(self) -> None:
        """Advance the whole line by one tick.

        Stations update downstream -> upstream. That way, when a station pulls
        a board from its input buffer, the station before it sees the freed
        slot in the same tick instead of waiting an extra tick.
        """
        for station in reversed(self.stations):
            station.step(self.tick, self.time_s, self.dt_s)
        self.tick += 1
        self.time_s = self.tick * self.dt_s   # multiply, don't accumulate, to avoid float drift

    def run(self, seconds: float) -> None:
        """Run headless for `seconds` of simulated time."""
        n_ticks = round(seconds / self.dt_s)
        for _ in range(n_ticks):
            self.step()

    # ---- inspection -------------------------------------------------------

    def station(self, name: str) -> Station:
        for st in self.stations:
            if st.name == name:
                return st
        raise KeyError(name)

    def ideal_cycle_time_s(self, station_name: str) -> float:
        """Ideal cycle time of a station for the configured product.

        Uses a throwaway reference board so stations whose cycle time depends
        on the board (pick-and-place: component count) answer correctly.
        """
        reference = Board("REFERENCE", self.source.product, self.source.component_count)
        return self.station(station_name).cycle_time_for(reference)

    @property
    def boards_completed(self) -> int:
        return len(self.sink)

    def get_tags(self) -> dict[str, dict[str, Any]]:
        """Flat, primitive-typed snapshot per station and buffer.

        Shaped so it can later be mapped 1:1 onto OPC UA tags.
        """
        tags: dict[str, dict[str, Any]] = {}
        for st in self.stations:
            tags[st.name] = {
                "state": st.state.value,
                "reason_code": st.reason_code or "",
                "part_count": st.part_count,
                "reject_count": 0,   # inspection arrives in milestone 5
            }
        for buf in self.buffers:
            tags[buf.name] = {"level": len(buf), "capacity": buf.capacity}
        return tags
