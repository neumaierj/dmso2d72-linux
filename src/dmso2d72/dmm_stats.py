"""Running min/max over a stream of multimeter readings.

Pure logic, no Qt, so it can be tested against decoded frames directly.
"""

from __future__ import annotations

from .protocol import DmmReading


class DmmStats:
    """Tracks the extremes of a reading stream until reset.

    Over-range ("OL") readings carry no value, so they are counted but never
    widen the range — otherwise a single open-leads sample would destroy the
    min/max of an otherwise valid run.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.min: float | None = None
        self.max: float | None = None
        self.count = 0
        self.overloads = 0
        self.unit = ""
        self.decimals = 0

    def update(self, reading: DmmReading | None) -> None:
        if reading is None:
            return
        self.count += 1
        self.unit = reading.unit
        self.decimals = reading.decimals
        if reading.overload or reading.value is None:
            self.overloads += 1
            return
        if self.min is None or reading.value < self.min:
            self.min = reading.value
        if self.max is None or reading.value > self.max:
            self.max = reading.value

    def format(self, value: float | None) -> str:
        """Format like the device screen, so min/max line up under the reading."""
        if value is None:
            return "--"
        return f"{value:.{self.decimals}f} {self.unit}".strip()
