"""Load the YAML line configuration."""

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read a YAML config file. With no path, read config/default.yaml."""
    with open(path or DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_deterministic(config: dict[str, Any]) -> dict[str, Any]:
    """Copy of `config` with all randomness switched off.

    No cycle-time jitter, no failures, no consumables (paste, stencil,
    nozzles, feeders), no oven temperature drift and no false calls, so no
    board is ever rejected and the line behaves exactly as the hand
    calculations predict. Changeovers stay (they are deterministic).
    """
    cfg = copy.deepcopy(config)
    random_keys = ("failures", "paste", "stencil", "nozzles", "feeders",
                   "temperature", "solder_defects", "false_calls_per_board")
    for st in cfg["line"]["stations"]:
        st["cycle_time_sd_s"] = 0.0
        for key in random_keys:
            st.pop(key, None)
    return cfg
