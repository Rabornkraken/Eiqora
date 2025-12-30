"""
Portfolio Coordinator Agent implementation.
Enforces portfolio-level constraints on GO signals.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.portfolio import (
    PortfolioCoordinatorOutput,
    PortfolioConstraints,
    SignalSummary,
    ApprovedSignal,
    RejectedSignal,
)
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.config.universe import get_ticker_clusters


# Default constraints
DEFAULT_CONSTRAINTS = PortfolioConstraints()


class PortfolioCoordinatorAgent(BaseAgent[PortfolioCoordinatorOutput]):
    """
    Portfolio Coordinator: enforces portfolio-level constraints.
    
    Runs ONCE after all individual ticker analyses.
    Takes all GO signals and filters/ranks them based on:
    - Correlation cluster limits
    - Sector concentration limits
    - Total position limits
    
    This is a deterministic agent (minimal LLM involvement).
    """
    
    name = "portfolio_coordinator"
    output_schema = PortfolioCoordinatorOutput
    
    def __init__(self, constraints: PortfolioConstraints | None = None):
        super().__init__()
        self.constraints = constraints or DEFAULT_CONSTRAINTS
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """
        Gather all GO signals from the sweep.
        
        In batch mode, state["all_signals"] contains SignalSummary for each GO.
        """
        return {
            "signals": state.get("all_signals", []),
            "topdown": state.get("topdown", {}),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for portfolio coordination."""
        signals = data.get("signals", [])
        topdown = data.get("topdown", {})
        
        if not signals:
            return """
No GO signals to coordinate.
Return empty approved and rejected lists.
"""
        
        # Format signals
        signals_text = "\n".join([
            f"  {i+1}. {s.get('symbol')}: {s.get('direction')} {s.get('setup_type')}, "
            f"conviction={s.get('conviction')}, risk={s.get('risk_score', 0):.2f}, "
            f"WR={s.get('win_rate', 0):.1%}, clusters={s.get('clusters', [])}"
            for i, s in enumerate(signals)
        ])
        
        regime = topdown.get("regime", "UNKNOWN")
        bias = topdown.get("bias", "NEUTRAL")
        
        return f"""
Coordinate portfolio from {len(signals)} GO signals.

MARKET CONTEXT:
- Regime: {regime}
- Bias: {bias}

SIGNALS (ranked by conviction + stats):
{signals_text}

CONSTRAINTS:
- Max positions: {self.constraints.max_positions}
- Max per sector: {self.constraints.max_sector_pct:.0%}
- Cluster limits: {self.constraints.max_cluster_positions}

Apply constraints and return approved signals in priority order.
Explain any rejections.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Portfolio Coordinator that enforces position limits.

RANKING CRITERIA (in priority order):
1. HIGH conviction signals first
2. Higher win rate / expected return
3. Lower risk score
4. Diversification across clusters

CONSTRAINT APPLICATION:
1. Sort signals by quality (conviction + stats)
2. Add best signal if no constraint violated
3. Check cluster limits before adding
4. Check sector limits before adding
5. Stop when max_positions reached

OUTPUT SCHEMA:
{
  "approved": [
    {"symbol": "NVDA", "direction": "LONG", "priority": 1, "allocation_pct": 0.15, "notes": "Top semi pick"}
  ],
  "rejected": [
    {"symbol": "AMD", "reason": "Cluster limit reached", "constraint": "SEMIS"}
  ],
  "portfolio_summary": {
    "total_positions": 5,
    "sector_breakdown": {"Technology": 0.3, "Financials": 0.2},
    "cluster_usage": {"SEMIS": 2, "FANMAG": 1}
  },
  "constraints_applied": ["SEMIS cluster limit", "max_positions"]
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: PortfolioCoordinatorOutput) -> dict[str, Any]:
        """Build state update with portfolio output."""
        return {"portfolio": result.model_dump()}


def coordinate_signals(
    signals: list[SignalSummary],
    constraints: PortfolioConstraints | None = None,
) -> PortfolioCoordinatorOutput:
    """
    Deterministic portfolio coordination (no LLM).
    
    For simple cases, this can be used instead of the full agent.
    """
    constraints = constraints or DEFAULT_CONSTRAINTS
    
    # Sort signals by quality
    sorted_signals = sorted(
        signals,
        key=lambda s: (
            0 if s.conviction == "HIGH" else 1 if s.conviction == "MEDIUM" else 2,
            -(s.win_rate or 0),
            s.risk_score,
        )
    )
    
    approved = []
    rejected = []
    cluster_counts: dict[str, int] = {}
    sector_counts: dict[str, int] = {}
    constraints_applied = set()
    
    for signal in sorted_signals:
        # Check max positions
        if len(approved) >= constraints.max_positions:
            rejected.append(RejectedSignal(
                symbol=signal.symbol,
                reason="Maximum position limit reached",
                constraint="max_positions",
            ))
            constraints_applied.add("max_positions")
            continue
        
        # Check cluster limits
        cluster_violation = None
        for cluster in signal.clusters:
            max_allowed = constraints.max_cluster_positions.get(cluster, 10)
            current = cluster_counts.get(cluster, 0)
            if current >= max_allowed:
                cluster_violation = cluster
                break
        
        if cluster_violation:
            rejected.append(RejectedSignal(
                symbol=signal.symbol,
                reason=f"Cluster limit reached for {cluster_violation}",
                constraint=f"{cluster_violation} cluster",
            ))
            constraints_applied.add(f"{cluster_violation} cluster limit")
            continue
        
        # Check sector limits (approximate)
        sector = signal.sector_etf
        current_sector = sector_counts.get(sector, 0)
        total = len(approved) + 1
        if total > 0 and (current_sector + 1) / total > constraints.max_sector_pct:
            rejected.append(RejectedSignal(
                symbol=signal.symbol,
                reason=f"Sector concentration limit for {sector}",
                constraint="sector_concentration",
            ))
            constraints_applied.add("sector_concentration")
            continue
        
        # Approve signal
        approved.append(ApprovedSignal(
            symbol=signal.symbol,
            direction=signal.direction,
            priority=len(approved) + 1,
            allocation_pct=round(1.0 / constraints.max_positions, 2),
            notes=f"{signal.setup_type} - {signal.conviction} conviction",
        ))
        
        # Update counts
        for cluster in signal.clusters:
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
    
    # Build summary
    portfolio_summary = {
        "total_positions": len(approved),
        "cluster_usage": dict(cluster_counts),
        "sector_usage": dict(sector_counts),
    }
    
    return PortfolioCoordinatorOutput(
        approved=approved,
        rejected=rejected,
        portfolio_summary=portfolio_summary,
        constraints_applied=list(constraints_applied),
    )
