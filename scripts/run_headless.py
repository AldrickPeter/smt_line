"""Run the line headless and print a metrics report.

Usage:
    python scripts/run_headless.py [--hours 8] [--config path.yaml] [--seed 42]
                                   [--events-csv out/events.csv] [--boards-csv out/boards.csv]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # allow running without install

from smt_sim import Simulator  # noqa: E402
from smt_sim.export import boards_to_csv  # noqa: E402
from smt_sim.metrics import finished_boards, format_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--config", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--events-csv", default=None, help="write the event log to this CSV file")
    parser.add_argument("--boards-csv", default=None, help="write finished board records to this CSV file")
    args = parser.parse_args()

    sim = Simulator(args.config, seed=args.seed)
    sim_s = args.hours * 3600

    wall_start = time.perf_counter()
    sim.run(sim_s)
    wall_s = time.perf_counter() - wall_start

    print(f"Simulated {args.hours:g} h in {wall_s:.2f} s wall time "
          f"({sim_s / wall_s:,.0f}x real time)")
    print(f"Boards completed: {sim.boards_completed}  "
          f"({sim.boards_completed / args.hours:.1f} boards/h)")
    print(f"Event log entries: {len(sim.event_log)}\n")
    print(format_report(sim))

    if args.events_csv:
        Path(args.events_csv).parent.mkdir(parents=True, exist_ok=True)
        sim.event_log.to_csv(args.events_csv)
        print(f"\nEvent log written to {args.events_csv}")
    if args.boards_csv:
        Path(args.boards_csv).parent.mkdir(parents=True, exist_ok=True)
        boards_to_csv(finished_boards(sim), args.boards_csv)
        print(f"Board records written to {args.boards_csv}")


if __name__ == "__main__":
    main()
