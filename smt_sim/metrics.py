"""Metrics computed from the event log: time in state, OEE, Pareto, bottleneck.

Everything here is derived after the fact from the event log plus a few
counters, never from live station internals. That keeps the metrics honest:
a dashboard fed only by the event stream could compute exactly the same thing.

Step 1 turns the log into intervals: "station X was in state S, with reason R,
from t0 to t1". Every metric below is a sum or grouping over those intervals.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smt_sim.events import EventLog, StationState

if TYPE_CHECKING:   # only for type hints; avoids a circular import with sim.py
    from smt_sim.sim import Simulator

# States caused by *other* stations. Everything else is the station's own time.
WAITING_STATES = {StationState.STARVED, StationState.BLOCKED}

# States that are stops with a reason code and belong in the downtime Pareto.
STOP_STATES = {StationState.DOWN, StationState.STARVED_MATERIAL, StationState.SETUP, StationState.PM}


@dataclass(frozen=True)
class Interval:
    station: str
    state: StationState
    reason_code: str | None
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def state_intervals(event_log: EventLog, end_time_s: float) -> list[Interval]:
    """Convert state-change events into closed time intervals.

    Each event starts an interval that lasts until that station's next event,
    or until `end_time_s` for the last one.
    """
    by_station: dict[str, list] = defaultdict(list)
    for event in event_log.events:
        by_station[event.station].append(event)

    intervals = []
    for station, events in by_station.items():
        for this, nxt in zip(events, events[1:] + [None]):
            end_s = nxt.sim_time_s if nxt is not None else end_time_s
            if end_s > this.sim_time_s:   # skip zero-length intervals
                intervals.append(Interval(station, this.new_state, this.reason_code, this.sim_time_s, end_s))
    return intervals


def time_in_state(intervals: list[Interval]) -> dict[str, dict[StationState, float]]:
    """Seconds spent in each state, per station. Every state appears (0.0 if never entered)."""
    totals: dict[str, dict[StationState, float]] = defaultdict(lambda: {s: 0.0 for s in StationState})
    for iv in intervals:
        totals[iv.station][iv.state] += iv.duration_s
    return dict(totals)


# ---- OEE ------------------------------------------------------------------


@dataclass(frozen=True)
class StationOEE:
    planned_s: float
    running_s: float
    starved_s: float
    blocked_s: float
    boards_processed: int
    ideal_cycle_s: float
    availability: float
    performance: float
    quality: float
    oee: float


def _ratio(numerator: float, denominator: float) -> float:
    """Division that returns NaN instead of crashing when nothing was measured."""
    return numerator / denominator if denominator > 0 else math.nan


def station_oee(sim: Simulator) -> dict[str, StationOEE]:
    """OEE per station, judging each station only on its own losses.

    planned time  = observed time − planned maintenance (PM)
    availability  = running / (planned − starved − blocked)
                    Starved/blocked are caused by neighbours, so they are
                    removed from the denominator and reported separately.
    performance   = ideal cycle × boards processed / running
    quality       = 1.0 until inspection exists (milestone 5)

    Note for the reflow oven: it is RUNNING whenever any board is inside, and
    its ideal cycle is the entry interval. Its performance therefore reads as
    "fraction of loading slots used", which is low for a non-bottleneck oven.
    """
    tis = time_in_state(state_intervals(sim.event_log, sim.time_s))
    result = {}
    for st in sim.stations:
        t = tis[st.name]
        planned_s = sim.time_s - t[StationState.PM]
        running_s = t[StationState.RUNNING]
        starved_s = t[StationState.STARVED]
        blocked_s = t[StationState.BLOCKED]
        ideal_cycle_s = sim.ideal_cycle_time_s(st.name)

        availability = _ratio(running_s, planned_s - starved_s - blocked_s)
        performance = _ratio(ideal_cycle_s * st.part_count, running_s)
        quality = 1.0
        result[st.name] = StationOEE(
            planned_s=planned_s,
            running_s=running_s,
            starved_s=starved_s,
            blocked_s=blocked_s,
            boards_processed=st.part_count,
            ideal_cycle_s=ideal_cycle_s,
            availability=availability,
            performance=performance,
            quality=quality,
            oee=availability * performance * quality,
        )
    return result


@dataclass(frozen=True)
class LineOEE:
    planned_s: float
    boards_out: int
    bottleneck_ideal_cycle_s: float
    oee: float


def line_oee(sim: Simulator) -> LineOEE:
    """Line OEE measured at the end of the line.

    OEE = good boards out × bottleneck ideal cycle / planned time

    i.e. actual good output divided by what the line could make if the
    slowest station ran at its ideal speed the whole time. All boards count as
    good until defects exist (milestone 5).
    """
    bottleneck_cycle_s = max(sim.ideal_cycle_time_s(st.name) for st in sim.stations)
    planned_s = sim.time_s
    good_boards = sim.boards_completed
    return LineOEE(
        planned_s=planned_s,
        boards_out=good_boards,
        bottleneck_ideal_cycle_s=bottleneck_cycle_s,
        oee=_ratio(good_boards * bottleneck_cycle_s, planned_s),
    )


# ---- Downtime Pareto ------------------------------------------------------


@dataclass(frozen=True)
class ParetoRow:
    station: str
    reason_code: str
    state: StationState
    total_s: float
    occurrences: int


def downtime_pareto(intervals: list[Interval]) -> list[ParetoRow]:
    """Stop time grouped by (station, reason code), largest first.

    Only the station's own stops count (DOWN, STARVED_MATERIAL, SETUP, PM);
    starved/blocked waiting is a symptom, not a root cause.
    """
    totals: dict[tuple, float] = defaultdict(float)
    counts: dict[tuple, int] = defaultdict(int)
    for iv in intervals:
        if iv.state in STOP_STATES:
            key = (iv.station, iv.reason_code or "UNKNOWN", iv.state)
            totals[key] += iv.duration_s
            counts[key] += 1
    rows = []
    for (station, reason_code, state), total_s in totals.items():
        rows.append(ParetoRow(station, reason_code, state, total_s, counts[(station, reason_code, state)]))
    return sorted(rows, key=lambda r: r.total_s, reverse=True)


# ---- Bottleneck detection -------------------------------------------------


@dataclass(frozen=True)
class BottleneckResult:
    station: str
    mean_active_period_s: dict[str, float]   # per station
    confirmed: bool   # upstream mostly blocked AND downstream mostly starved


def active_periods(intervals: list[Interval], station: str) -> list[float]:
    """Durations of uninterrupted active periods for one station.

    "Active" means anything except waiting (STARVED or BLOCKED): running,
    and also being down, in setup, etc., because the station is busy with
    its own work and the rest of the line has to wait for it. Consecutive
    active intervals (e.g. RUNNING -> DOWN -> RUNNING) merge into one period.
    """
    periods = []
    current_s = 0.0
    for iv in sorted((iv for iv in intervals if iv.station == station), key=lambda iv: iv.start_s):
        if iv.state in WAITING_STATES:
            if current_s > 0:
                periods.append(current_s)
            current_s = 0.0
        else:
            current_s += iv.duration_s
    if current_s > 0:
        periods.append(current_s)
    return periods


def find_bottleneck(sim: Simulator) -> BottleneckResult:
    """Active period method (Roser et al.).

    The bottleneck is the station with the longest average active period:
    it rarely has to wait, so everyone else waits for it. As a cross-check,
    stations upstream of it should spend more time blocked than starved, and
    stations downstream more time starved than blocked.
    """
    intervals = state_intervals(sim.event_log, sim.time_s)
    names = [st.name for st in sim.stations]

    mean_active = {}
    for name in names:
        periods = active_periods(intervals, name)
        mean_active[name] = sum(periods) / len(periods) if periods else 0.0
    bottleneck = max(names, key=lambda n: mean_active[n])

    tis = time_in_state(intervals)
    idx = names.index(bottleneck)
    upstream_ok = all(tis[n][StationState.BLOCKED] >= tis[n][StationState.STARVED] for n in names[:idx])
    downstream_ok = all(tis[n][StationState.STARVED] >= tis[n][StationState.BLOCKED] for n in names[idx + 1:])
    return BottleneckResult(bottleneck, mean_active, upstream_ok and downstream_ok)


# ---- Text report ----------------------------------------------------------


def format_report(sim: Simulator) -> str:
    """Human-readable summary of all metrics (used by the headless script)."""
    lines = []
    line = line_oee(sim)
    lines.append(
        f"Line OEE {line.oee:6.1%}   boards out {line.boards_out}   "
        f"bottleneck ideal cycle {line.bottleneck_ideal_cycle_s:g} s   time {line.planned_s:g} s"
    )
    lines.append("")
    lines.append(f"{'station':10s} {'running':>8s} {'starved':>8s} {'blocked':>8s}"
                 f" {'avail':>7s} {'perf':>7s} {'OEE':>7s} {'boards':>7s}")
    for name, m in station_oee(sim).items():
        p = m.planned_s
        lines.append(
            f"{name:10s} {m.running_s / p:8.1%} {m.starved_s / p:8.1%} {m.blocked_s / p:8.1%}"
            f" {m.availability:7.1%} {m.performance:7.1%} {m.oee:7.1%} {m.boards_processed:7d}"
        )

    bn = find_bottleneck(sim)
    lines.append("")
    lines.append(f"Bottleneck: {bn.station} ({'confirmed' if bn.confirmed else 'NOT confirmed'} "
                 f"by upstream blocking / downstream starving)")
    lines.append("  mean active period: " + ", ".join(f"{n} {s:.0f} s" for n, s in bn.mean_active_period_s.items()))

    pareto = downtime_pareto(state_intervals(sim.event_log, sim.time_s))
    lines.append("")
    lines.append("Downtime Pareto:" + ("" if pareto else " (no stops)"))
    for row in pareto:
        lines.append(f"  {row.station:10s} {row.reason_code:16s} {row.total_s:8.1f} s  x{row.occurrences}")
    return "\n".join(lines)
