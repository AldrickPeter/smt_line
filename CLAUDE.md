# SMT Line Simulator — Project Context

## What this is

A tick-based Python simulator of a six-station surface-mount (SMT) printed circuit board assembly line. It is phase 1 of a larger IIoT project that will measure OEE (Overall Equipment Effectiveness) and trace losses to their root cause. Later phases (not in scope now) add OPC UA tags, a Node-RED edge gateway, MQTT/TLS to a cloud time-series database, a web dashboard, and production scheduling optimization (dispatching rules + OR-Tools CP-SAT).

The developer is building this for a class and as a portfolio piece for an intelligent manufacturing engineering role. **They need to understand every part of the code.** Prefer readable, well-named, plainly structured code over clever code. Briefly explain non-obvious design choices when you introduce them.

## Scope right now

The simulator, a pygame visualization, and (now in progress) order-based production planning: dispatching rules vs exact optimization, evaluated in the simulator (see "Planning milestones" below). Do not build OPC UA, MQTT, databases or dashboards unless asked. But design so those layers can attach cleanly later (see "Interfaces for later phases").

## Tech stack

- Python 3.11+
- numpy (randomness, math), pyyaml (config), pygame (visualization only), pytest (tests)
- No SimPy — the simulator is deliberately tick-based so it can stream state in real time later.

## Line layout

```
Stencil Printer → [buf] → SPI → [buf] → Pick-and-Place → [buf] → Reflow Oven → [buf] → AOI → [buf] → ICT
```

Buffers are finite (`collections.deque` with a capacity check). A stoppage must propagate: downstream stations go STARVED, upstream stations go BLOCKED.

## Core modeling principles (most important section)

1. **Randomness lives in physical quantities, not in defect decisions.** Never write `if rng.normal() > k: reject()`. Instead: wear/state → distribution of a measured quantity (paste volume %, placement offset µm, zone temperature) → defect exists if the quantity is out of spec → inspection detects it with imperfect probability. Every defect must have a traceable cause.
2. **Normal noise for continuous quantities** (cycle time jitter, paste volume, placement offset, temperatures). **Hazard rates for discrete events** (clogs, faults, jams), rising with wear. Use Weibull hazards (shape > 1) for wear-out, exponential for random faults, lognormal for repair durations.
3. **Ground truth is separate from observation.** Each board stores its true defects and, separately, what each inspection station detected. Validation compares dashboard conclusions to ground truth.
4. **Determinism.** A single `np.random.default_rng(seed)` is created per run and passed down to every component. No `random` module, no unseeded generators. Same seed + same config = identical run.
5. **All parameters live in YAML config**, not hardcoded. Defaults below are placeholders meant to be tuned.
6. **The simulation core never imports pygame.** The renderer only reads state. The sim must run headless at thousands of times real time (needed later for optimization experiments over many seeds).

## Simulation mechanics

- Tick size: configurable, default 0.5 s.
- Each tick, update stations **downstream to upstream** so space freed in a buffer is usable upstream the same tick.
- Cycle times: sampled per board from a normal distribution, clipped to a sensible minimum.
- Reflow is a **conveyor pipeline**, not a single-board server: multiple boards in transit, fixed transit time (default ~300 s), capacity set by conveyor length / board spacing.
- Pick-and-place cycle time scales with the board's component count; it should usually be the bottleneck.

## Station states

`RUNNING`, `STARVED` (input buffer empty), `BLOCKED` (finished board, output buffer full), `DOWN` (fault, with reason code), `STARVED_MATERIAL` (out of consumable — distinct from upstream starvation), `SETUP` (changeover), `PM` (planned maintenance).

Every state change appends to an event log: `(tick, sim_time_s, station, old_state, new_state, reason_code)`. Reason codes are short strings in a central enum/constants module (e.g. `NOZZLE_CLOG`, `FEEDER_EMPTY`, `PASTE_LOW`, `STENCIL_WIPE`, `CONVEYOR_JAM`).

## Station behavior

| Station | Tracked state | Behavior |
|---|---|---|
| Stencil printer | paste grams on stencil, prints since underside wipe, stencil cycle count, paste open time | Each print consumes normal-distributed paste. Below minimum → short stop `PASTE_LOW`. Aperture clogging grows with prints since wipe → lower transfer efficiency. Stencil wear → slow downward drift. Output: paste volume % per pad (or per board summary) stored on the board. Wipe every N prints is a short planned stop. |
| SPI | measurement noise | Measures paste volume (+ small noise). Fails boards outside spec (default 70–130%). Attributes defect to the printer. |
| Pick-and-place | feeder slots (part number, reel qty, mispick rate), nozzles (Weibull clog model) | Consumes parts per board BOM; mispicks consume without placing. Low-reel threshold → splice opportunity (no stop if done in time, config-driven operator response). Reel empty → `STARVED_MATERIAL` / `FEEDER_EMPTY` + reel change time. Nozzle wear and clogs increase placement offset sigma and mispick rate. Placement offsets stored on the board. |
| Reflow oven | zone temperatures (slow drift + noise) | Profile deviation raises probability of solder defects (tombstone, voids, cold joint). Store peak temp / deviation on the board. |
| AOI | detection rate and false-call rate per defect type | Detects placement and solder defects imperfectly. Records detected defects and false calls on the board. |
| ICT | detection rate | Catches electrical defects AOI missed (escapes). |

Example of the intended pattern (nozzle):

```python
def hazard(self):
    n = self.picks_since_clean
    return (self.beta / self.eta) * (n / self.eta) ** (self.beta - 1)

def pick(self):
    self.picks_since_clean += 1
    if not self.clogged and self.rng.random() < self.hazard():
        self.clogged = True
    wear = self.picks_since_clean / self.eta
    sigma_um = 15 + 25 * wear + (60 if self.clogged else 0)
    offset_um = self.rng.normal(0, sigma_um)
    pick_fail = self.rng.random() < (0.30 if self.clogged else 0.0005)
    return offset_um, pick_fail
```

## Board record

Each board: serial number, product type, BOM reference, timestamps in/out per station, per-station measurements, `true_defects` (list of {type, origin_station}), `detected_defects` (list of {type, detected_at, attributed_origin}), `false_calls`, final disposition (good / scrap / rework).

## Metrics

- Accumulate time-in-state per station from the event log.
- **Station availability** (own losses) = running / (planned time − starved − blocked). Starved and blocked are reported separately as line-interaction losses.
- **Performance** = (ideal cycle time × boards processed) / running time.
- **Quality** = first-pass yield: boards passing all inspections with no defect detected on the first pass / boards started.
- **Line OEE** computed at end-of-line output against the bottleneck's ideal cycle time.
- Downtime Pareto by reason code.
- **Bottleneck detection**: active period method (Roser) — the station with the longest uninterrupted active periods (RUNNING or DOWN); confirm with blocked time accumulating upstream and starved time downstream.

## Interfaces for later phases

- `Simulator.step()` advances one tick. `Simulator.run(seconds)` runs headless.
- `Simulator.get_tags()` returns a flat dict per station: `{state, reason_code, part_count, reject_count, ...}`. This will become OPC UA tags later, so keep it flat and primitive-typed.
- Event log and board records exportable to CSV/Parquet.
- Scenario hooks: a scenario can schedule interventions at sim times (e.g. accelerate stencil wear, set a feeder's reel to near-empty, force a nozzle clog at t=1800 s).

## Suggested layout

```
smt_sim/
  config/default.yaml
  config/scenarios/{stencil_wear,feeder_depletion,nozzle_fault}.yaml
  smt_sim/
    __init__.py
    sim.py            # Simulator, tick loop, update ordering
    station.py        # base Station + state machine
    stations/         # printer.py, spi.py, pnp.py, reflow.py, aoi.py, ict.py
    components.py     # Nozzle, Feeder, Stencil, etc.
    board.py
    buffer.py
    events.py         # event log, reason codes
    metrics.py        # OEE, FPY, Pareto, bottleneck
    scenarios.py
  viz/pygame_view.py  # read-only renderer, speed multiplier
  scripts/run_headless.py
  tests/
```

## Build milestones (do one at a time, stop and summarize after each)

1. **Deterministic line**: fixed cycle times, buffers, states, update order. Test: steady-state throughput equals the bottleneck rate; stoppage propagates as STARVED/BLOCKED.
2. **Event log + metrics**: time-in-state, OEE per station and line. Test: hand-computable small case.
3. **Stochastic faults and repairs**: exponential/Weibull failures, lognormal repairs. Test: same seed → identical log.
4. **Consumables**: paste, stencil wipe/wear, feeders/reels/splicing, nozzles.
4b. **Products and changeovers**: several products in config (BOM, part numbers per feeder slot), a production plan feeding the line (e.g. 50 × A then 30 × B), and per-station changeovers in `SETUP` with reason codes: printer stencil swap, SPI/AOI program load, pick-and-place feeder/cart swap (time depends on how many parts differ between the two products), reflow profile change + thermal settling, ICT fixture swap. Each station changes over when the first board of the new product reaches it, so the changeover ripples down the line. Test: changeover time appears as SETUP in the right order per station; a product pair sharing more parts changes over faster.
5. **Boards and defects**: measurements, true vs detected defects, AOI/ICT detection, FPY, attribution.
6. **Pygame view**: stations colored by state, buffer fill, counts, live OEE, speed control.
7. **Scenarios**: stencil wear, feeder depletion, injected nozzle fault. Test: metrics point to the correct root cause.
8. **Multiple lines** (done): `smt_sim/factory.py` runs several independent `Simulator`s on one clock from `config/factory.yaml` (base config + per-line `config_overrides` / `station_overrides`), each with its own seed derived from one factory seed via `SeedSequence.spawn`. Tags are keyed `line/entity`. Lines share nothing yet; a shared operator or one order book dispatched across lines would live in `Factory`.
## Planning milestones (order-based scheduling)

Idea: a planner decides which order runs on which line and in what sequence, using a simple deterministic **planning model** (time per good board per line/product, sequence-dependent changeover matrix, both calibrated from the simulator). The **simulator** then executes the plan under randomness, over many seeds with common random numbers (same seeds for every method), and measures what really happened. The planning code lives in a separate `planning/` package; the simulation core never imports OR-Tools.

Decisions: objective = weighted tardiness against due dates (makespan as tie-breaker); both lines, any product on either line; no lot splitting (one lot per order); rejected boards are replaced while the order's lot is running, and any later shortfall gets a top-up lot at the end of that line's plan. Solvers: OR-Tools CP-SAT, and a MILP via OR-Tools `pywraplp` (SCIP).

- **P1 Orders in the simulator**: order YAML, `order_id` on boards, jobs tied to orders, reject replacement + top-ups, per-order completion times, run until all orders are done.
- **P2 Planning model**: time per good board per (line, product) and changeover matrix per line, calibrated by simulation.
- **P3 Plan + evaluator**: `Plan` (per line: sequence of lots) -> per-line configs -> factory run over seeds -> KPIs (makespan, weighted tardiness, late orders, setup time, OEE, FPY, top-ups).
- **P4 Dispatching rules**: FIFO, EDD, SPT, minimum changeover, with earliest-completion line assignment.
- **P5 Optimizers**: CP-SAT (circuit per line with setup times) and MILP (big-M precedence); checked against brute force on small orders.
- **P6 Comparison + charts**: Gantt per method, KPI mean ± 95 % CI, distribution over seeds, paired improvement vs FIFO, planned vs simulated, solver scaling.
- **P7 (optional)** Re-planning after disruptions.

## Extensions after the simulator milestones

9. **Equipment speed** (done): `speed_pct` per station (config, `Station.set_speed()`, `set_speed` scenario action, `speed_pct` tag, Tab + Left/Right in the viewer). Cycle time = rated / speed; OEE stays measured against rated speed, so slow running is a performance loss. Pick-and-place offset spread and pick failures scale as speed^exponent (`speed_effects`), clog effects don't. The reflow oven's speed is fixed by its profile.

## Conventions

- Type hints and dataclasses where they help clarity.
- Docstrings on classes and non-trivial functions; units in names or comments (`_s`, `_um`, `_g`).
- pytest for every milestone; tests use fixed seeds.
- Keep functions short; no premature abstraction or plugin frameworks.
