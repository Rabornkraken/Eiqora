"""
Orchestrator configuration presets.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class OrchestratorConfig:
    mode: str = "default"
    include_topdown: bool = True
    include_fundamental: bool = True
    include_supply_chain: bool = True
    enable_position_manager: bool = False
    use_cache: bool = True

    def override(self, **kwargs) -> "OrchestratorConfig":
        return replace(self, **kwargs)

    @classmethod
    def live(cls) -> "OrchestratorConfig":
        return cls(mode="live", enable_position_manager=True, use_cache=False)

    @classmethod
    def backtest(
        cls,
        *,
        include_fundamental: bool = True,
        use_cache: bool = True,
    ) -> "OrchestratorConfig":
        return cls(
            mode="backtest",
            include_fundamental=include_fundamental,
            use_cache=use_cache,
        )

    @classmethod
    def analysis(cls) -> "OrchestratorConfig":
        return cls(mode="analysis", include_fundamental=True, use_cache=True)
