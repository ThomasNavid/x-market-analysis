"""Built-in signal definitions for backtesting."""

from dataclasses import dataclass
from typing import Any, Literal

SignalDirection = Literal[1, -1]


@dataclass(frozen=True)
class BuiltInSignal:
    """A named v1 signal with simple sentiment/confidence thresholds."""

    name: str
    description: str
    direction: SignalDirection
    sentiment_min: float | None
    sentiment_max: float | None
    ticker_confidence_min: float

    @property
    def conditions(self) -> dict[str, Any]:
        """JSON-serializable conditions for storing in the `signals` table."""
        return {
            "type": "builtin",
            "name": self.name,
            "direction": self.direction,
            "sentiment_min": self.sentiment_min,
            "sentiment_max": self.sentiment_max,
            "ticker_confidence_min": self.ticker_confidence_min,
        }


BUILT_IN_SIGNALS: dict[str, BuiltInSignal] = {
    "positive_high": BuiltInSignal(
        name="positive_high",
        description="Bullish posts with sentiment score >= 0.6 and ticker confidence >= 0.6.",
        direction=1,
        sentiment_min=0.6,
        sentiment_max=None,
        ticker_confidence_min=0.6,
    ),
    "negative_high": BuiltInSignal(
        name="negative_high",
        description="Bearish posts with sentiment score <= -0.6 and ticker confidence >= 0.6.",
        direction=-1,
        sentiment_min=None,
        sentiment_max=-0.6,
        ticker_confidence_min=0.6,
    ),
}


def get_builtin_signal(name: str) -> BuiltInSignal:
    """Return a built-in signal or raise a clear error."""
    normalized = name.strip().lower()
    try:
        return BUILT_IN_SIGNALS[normalized]
    except KeyError as exc:
        names = ", ".join(sorted(BUILT_IN_SIGNALS))
        raise ValueError(f"Unknown signal '{name}'. Available signals: {names}.") from exc
