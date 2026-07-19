"""Rolling history of PSU power readings, for trend graphing.

The OWON PSU reports voltage and current; power is ``V * I``. :class:`PowerHistory`
keeps a bounded, time-stamped buffer of samples so the power panel can draw
amperage / voltage / wattage trends. It's pure data — the caller supplies the
timestamp (``time.monotonic()`` on the bench) so it stays deterministic in tests.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PowerSample:
    t: float  # seconds (monotonic clock)
    voltage: float
    current: float

    @property
    def power(self) -> float:
        return self.voltage * self.current


class PowerHistory:
    """A bounded, time-ordered buffer of :class:`PowerSample`."""

    def __init__(self, max_samples: int = 600) -> None:
        if max_samples <= 0:
            raise ValueError("max_samples must be positive")
        self._samples: deque[PowerSample] = deque(maxlen=max_samples)

    def add(self, voltage: float, current: float, t: float) -> PowerSample:
        """Append a sample (power computed) and return it. Oldest evicts at cap."""
        sample = PowerSample(t=t, voltage=voltage, current=current)
        self._samples.append(sample)
        return sample

    def samples(self) -> list[PowerSample]:
        return list(self._samples)

    def latest(self) -> PowerSample | None:
        return self._samples[-1] if self._samples else None

    def clear(self) -> None:
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)
