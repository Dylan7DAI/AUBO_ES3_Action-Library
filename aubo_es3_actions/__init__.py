"""Small, expandable action library for an AUBO ES3 arm."""

from .actions import ACTIONS, run_action
from .config import RobotConfig, load_config

__all__ = ["ACTIONS", "RobotConfig", "load_config", "run_action"]
