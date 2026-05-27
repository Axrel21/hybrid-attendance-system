"""Automatic fan control and temperature sampling (D5.5)."""

from edge.thermal.fan_controller import FanController, FanMode, load_thermal_config
from edge.thermal.temperature_reader import TemperatureReader

__all__ = [
    "FanController",
    "FanMode",
    "TemperatureReader",
    "load_thermal_config",
]
