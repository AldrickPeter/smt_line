"""Calibrate the planning model from the simulator and save it.

Usage:
    python scripts/calibrate_planning_model.py [--factory config/factory.yaml]
                                               [--out config/planning_model.yaml]
                                               [--seeds 3] [--hours 12]

Takes about a minute: deterministic runs for the changeover matrix, then
randomized single-product runs per line and product for the pace.
Re-run it whenever the line config changes.
"""

import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # allow running without install

from planning.model import calibrate  # noqa: E402
from smt_sim.config import load_config  # noqa: E402
from smt_sim.factory import DEFAULT_FACTORY_PATH, build_line_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factory", default=str(DEFAULT_FACTORY_PATH))
    parser.add_argument("--out", default=str(ROOT / "config" / "planning_model.yaml"))
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--hours", type=float, default=12.0)
    args = parser.parse_args()

    with open(args.factory, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    line_configs = {name: build_line_config(cfg or {}) for name, cfg in raw["lines"].items()}
    products = list(load_config()["products"])

    start = time.perf_counter()
    model = calibrate(line_configs, products, seeds=list(range(1, args.seeds + 1)), hours=args.hours)
    model.save(args.out)
    print(f"Calibrated in {time.perf_counter() - start:.0f} s -> {args.out}\n")
    for line in model.lines:
        print(f"{line}: fill {model.fill_s[line]:.0f} s")
        for p in products:
            co = ", ".join(f"->{q} {model.changeover_s[line][p][q]:.0f}" for q in products if q != p)
            print(f"  {p:15s} {model.seconds_per_good_board[line][p]:6.1f} s/good board   changeover {co} s")


if __name__ == "__main__":
    main()
