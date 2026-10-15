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

    No cycle-time jitter, no failures and no consumables (paste, stencil,
    nozzles, feeders), so the line behaves exactly as the hand calculations
    predict. Useful for tests and for checking the line's ideal capacity.
    """
    cfg = copy.deepcopy(config)
    for st in cfg["line"]["stations"]:
        st["cycle_time_sd_s"] = 0.0
        for key in ("failures", "paste", "stencil", "nozzles", "feeders"):
            st.pop(key, None)
    return cfg
