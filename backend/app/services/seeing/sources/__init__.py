"""Weather sources. Each parses a provider response into HourlyFrame objects (no coordinates kept)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


class SourceError(Exception):
    """A provider failure. The message has already been through privacy.redact_url."""


@dataclass
class HourlyFrame:
    """Hourly series for one model/source. times are naive UTC; values[var] is aligned with times (None = missing)."""
    times: List[datetime]
    values: Dict[str, List[Optional[float]]] = field(default_factory=dict)
    _index: Optional[Dict[datetime, int]] = field(default=None, repr=False, compare=False)

    def at(self, var: str, t: datetime) -> Optional[float]:
        series = self.values.get(var)
        if series is None:
            return None
        if self._index is None:
            self._index = {ts: i for i, ts in enumerate(self.times)}
        i = self._index.get(t)
        return series[i] if i is not None else None

    def has_data(self) -> bool:
        return any(v is not None for series in self.values.values() for v in series)
