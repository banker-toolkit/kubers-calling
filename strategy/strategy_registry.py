"""
KUBER'S CALLING — strategy/strategy_registry.py
=================================================
Layer 3: Strategy lifecycle manager.

Maintains the list of active strategies, their live/shadow status.
Engine calls get_live_strategy() and get_shadow_strategies() each cycle.
Engine never holds direct references to strategies — always via registry.

Promotion of ML models is done by the owner via ML Workbench push.
No autonomous promotion logic exists here.
"""

import os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger("strategy_registry")


class StrategyRegistry:

    def __init__(self):
        self._live_strategy    = None
        self._shadow_strategies= []

    def register(self, strategy, mode: str = "shadow"):
        """Register a strategy. mode: 'live' or 'shadow'."""
        if mode == "live":
            if self._live_strategy is not None:
                log.info("[registry] Demoting '%s' from live to shadow",
                         self._live_strategy.name)
                self._live_strategy.is_live = False
                self._shadow_strategies.append(self._live_strategy)
            strategy.is_live = True
            self._live_strategy = strategy
            log.info("[registry] Live strategy set: %s v%s",
                     strategy.name, strategy.version)
        else:
            strategy.is_live = False
            self._shadow_strategies.append(strategy)
            log.info("[registry] Shadow strategy registered: %s v%s",
                     strategy.name, strategy.version)

    def promote(self, strategy_name: str):
        """
        Promote a shadow strategy to live.
        Called by the Push mechanism from ML Workbench.
        """
        for s in self._shadow_strategies:
            if s.name == strategy_name:
                self._shadow_strategies.remove(s)
                self.register(s, mode="live")
                return
        raise KeyError(f"Strategy '{strategy_name}' not found in shadow book")

    def retire(self, strategy_name: str):
        """Remove a strategy from the active list."""
        self._shadow_strategies = [
            s for s in self._shadow_strategies if s.name != strategy_name
        ]
        log.info("[registry] Retired strategy: %s", strategy_name)

    def get_live_strategy(self):
        return self._live_strategy

    def get_shadow_strategies(self) -> list:
        return list(self._shadow_strategies)

    def get_all_names(self) -> list:
        names = [self._live_strategy.name] if self._live_strategy else []
        names += [s.name for s in self._shadow_strategies]
        return names

    def status(self) -> dict:
        return {
            "live": self._live_strategy.name if self._live_strategy else None,
            "shadow_count": len(self._shadow_strategies),
            "shadow_names": [s.name for s in self._shadow_strategies],
        }


# ── Module-level singleton ───────────────────────────────────────────
registry = StrategyRegistry()
