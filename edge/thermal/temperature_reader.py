"""CPU temperature from sysfs (never raises)."""

from __future__ import annotations

from pathlib import Path

_THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


class TemperatureReader:
    def read_temp_c(self) -> float | None:
        try:
            raw = _THERMAL_ZONE.read_text(encoding="ascii").strip()
            return int(raw) / 1000.0
        except Exception:
            return None
