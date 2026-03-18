"""
KUBER'S CALLING — strategy/strategy_base.py
=============================================
Layer 3: Abstract strategy contract.

Every strategy — rule-based, shadow, or ML model — implements this
interface. The engine calls evaluate() and handles the result.
It never knows or cares what is inside a strategy.

This is what makes the strategy layer swappable across the ML lifecycle.
"""

import os, sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class SignalResult:
    """
    Returned by every strategy.evaluate() call.
    Contains both the decision and full telemetry for logging.
    """
    signal:      Literal["BUY", "SELL", "PASS"]
    direction:   Optional[Literal["LONG", "SHORT"]] = None
    confidence:  float  = 1.0           # 0.0–1.0. Rules=1.0, ML=probability.
    limit_price: float  = 0.0
    sl_price:    float  = 0.0
    target_price:float  = 0.0
    metadata:    dict   = field(default_factory=dict)  # full telemetry → signal_log

    @property
    def is_trade(self) -> bool:
        return self.signal in ("BUY", "SELL")

    def __repr__(self):
        return (f"SignalResult({self.signal} dir={self.direction} "
                f"conf={self.confidence:.2f} limit={self.limit_price:.2f})")


class Strategy(ABC):
    """
    Abstract base class for all strategies.

    name:     Unique identifier — e.g. 'RULE_V1', 'SH-28', 'ML_GBT_V3'
    version:  Semantic version string
    is_live:  True = real money. False = shadow only.
    """
    name:    str = "BASE"
    version: str = "0.0"
    is_live: bool = False

    @abstractmethod
    def evaluate(self, snapshot) -> SignalResult:
        """
        Evaluate the current MarketSnapshot and return a SignalResult.

        snapshot: MarketSnapshot (from features/feature_engine.py)

        RULES:
          - Must return a SignalResult — never raise, never return None
          - Rules strategies: confidence = 1.0
          - ML strategies:    confidence = model probability
          - PASS is a valid and expected result
          - Include full reasoning in metadata for transparency
        """
        ...

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name}, live={self.is_live})"
