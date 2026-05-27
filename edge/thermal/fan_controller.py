"""GPIO fan control with hysteresis (no hardcoded pins)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from config.logging_setup import LOG_RUNTIME

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_YAML = _PROJECT_ROOT / "config" / "settings.yaml"

HYSTERESIS_C = 2.0
_THRESH_UP = (55.0, 65.0, 72.0)  # OFF→LOW, LOW→HIGH, HIGH→MAX
_THRESH_DOWN = tuple(t - HYSTERESIS_C for t in _THRESH_UP)  # 53, 63, 70

_PWM_DUTY = {
    "OFF": 0,
    "LOW": 40,
    "HIGH": 70,
    "MAX": 100,
}


class FanMode(str, Enum):
    OFF = "OFF"
    LOW = "LOW"
    HIGH = "HIGH"
    MAX = "MAX"


_ORDER = (FanMode.OFF, FanMode.LOW, FanMode.HIGH, FanMode.MAX)


@dataclass(frozen=True)
class ThermalConfig:
    enabled: bool = True
    gpio_pin: int = 18
    pwm: bool = False


def load_thermal_config(
    yaml_path: Path | str | None = None,
) -> ThermalConfig:
    """Load ``thermal:`` block from config/settings.yaml; safe defaults on error."""
    path = Path(yaml_path) if yaml_path else _DEFAULT_YAML
    defaults = ThermalConfig()
    if not path.is_file():
        return defaults
    try:
        import yaml  # type: ignore[import-untyped]

        with open(path, encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
        block = data.get("thermal") or {}
        if not isinstance(block, dict):
            return defaults
        return ThermalConfig(
            enabled=bool(block.get("enabled", defaults.enabled)),
            gpio_pin=int(block.get("gpio_pin", defaults.gpio_pin)),
            pwm=bool(block.get("pwm", defaults.pwm)),
        )
    except Exception as exc:
        LOG_RUNTIME.warning(
            "Could not load thermal config from %s (%s); using defaults",
            path,
            exc,
        )
        return defaults


class FanController:
    """Thermal fan state machine; GPIO best-effort when enabled."""

    def __init__(self, config: ThermalConfig | None = None) -> None:
        self._config = config or load_thermal_config()
        self._state = FanMode.OFF
        self._gpio = None
        self._pwm = None
        self._log = logging.getLogger("attendance.runtime")
        if self._config.enabled:
            self._init_gpio()

    def get_state(self) -> str:
        return self._state.value

    def update(self, temp_c: float | None) -> str:
        if temp_c is not None:
            self._apply_hysteresis(float(temp_c))
        if self._config.enabled:
            self._apply_hardware()
        return self.get_state()

    def cleanup(self) -> None:
        if self._gpio is None:
            return
        try:
            if self._pwm is not None:
                self._pwm.stop()
            self._gpio.cleanup(self._config.gpio_pin)
        except Exception:
            pass
        self._gpio = None
        self._pwm = None

    def _apply_hysteresis(self, temp_c: float) -> None:
        idx = _ORDER.index(self._state)
        while idx < len(_ORDER) - 1 and temp_c >= _THRESH_UP[idx]:
            idx += 1
        while idx > 0 and temp_c < _THRESH_DOWN[idx - 1]:
            idx -= 1
        self._state = _ORDER[idx]

    def _init_gpio(self) -> None:
        try:
            import RPi.GPIO as GPIO  # type: ignore[import-untyped]
        except Exception as exc:
            self._log.warning("GPIO unavailable for fan control: %s", exc)
            return
        try:
            GPIO.setmode(GPIO.BCM)
            pin = self._config.gpio_pin
            GPIO.setup(pin, GPIO.OUT)
            self._gpio = GPIO
            if self._config.pwm:
                self._pwm = GPIO.PWM(pin, 25000)
                self._pwm.start(0)
        except Exception as exc:
            self._log.warning(
                "Fan GPIO init failed on pin %s: %s",
                self._config.gpio_pin,
                exc,
            )
            self._gpio = None
            self._pwm = None

    def _apply_hardware(self) -> None:
        if self._gpio is None:
            return
        pin = self._config.gpio_pin
        mode = self._state
        try:
            if self._pwm is not None:
                self._pwm.ChangeDutyCycle(_PWM_DUTY[mode.value])
            else:
                self._gpio.output(
                    pin,
                    self._gpio.HIGH if mode != FanMode.OFF else self._gpio.LOW,
                )
        except Exception as exc:
            self._log.warning("Fan GPIO update failed: %s", exc)
