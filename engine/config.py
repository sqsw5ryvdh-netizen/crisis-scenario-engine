"""Loading and validating the YAML config files.

This is the only engine module that reads files. Model functions receive
validated objects and never touch the file system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from engine.schema import Config

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")
    return data


def load_config(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> Config:
    """Load banks.yaml, model.yaml, scenarios.yaml and injects.yaml and validate them."""
    config_dir = Path(config_dir)
    return Config(
        banks=_read_yaml(config_dir / "banks.yaml"),
        model=_read_yaml(config_dir / "model.yaml"),
        scenarios=_read_yaml(config_dir / "scenarios.yaml"),
        injects=_read_yaml(config_dir / "injects.yaml"),
    )
