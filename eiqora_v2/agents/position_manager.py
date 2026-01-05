"""
Position Manager Agent - LLM-based portfolio position management.

Instead of hard-coded rules, uses LLM to interpret portfolio state contextually
and make intelligent decisions about new trades based on:
- Current positions and performance
- Exposure levels and diversification
- Cluster/sector concentration
- Market context
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.state import SwingTradeState


class Position(BaseModel):
    """Current open position."""
    symbol: str
    entry_price: float
    current_price: float
    pnl_pct: float
    size_pct: float = Field(description="% of total capital")
    days_held: int
    cluster: str | None = None
    sector: str | None = None


class PortfolioState(BaseModel):
    """Current portfolio state."""
    positions: list[Position]
    total_exposure_pct: float = Field(description="Total % of capital deployed")
    available_capital_pct: float = Field(description="Cash available %")
    sector_breakdown: dict[str, float] = Field(default_factory=dict)
    cluster_exposure: dict[str, float] = Field(default_factory=dict)


class PositionManagerOutput(BaseModel):
    """Position manager decision output."""
    decision: str = Field(description="APPROVE, REDUCE_SIZE, or REJECT")
    reasoning: str = Field(description="Contextual explanation for decision")
    approved_size_pct: float | None = Field(default=None, description="Approved position size % if APPROVE/REDUCE_SIZE")
    portfolio_impact: dict[str, Any] = Field(default_factory=dict, description="Expected portfolio changes")


class PositionManagerAgent(BaseAgent[PositionManagerOutput]):
    """
    Position Manager: Makes contextual decisions about new trades based on portfolio state.
    
    Uses LLM to interpret:
    - Is this trade additive given current positions?
    - Does it create unwanted concentration?
    - Should we size differently based on existing exposure?
    - Is timing right given current P&L?
    """
    
    name = "position_manager"
    output_schema = PositionManagerOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """
        Gather current portfolio state and proposed trade.
        
        In live mode, queries Alpaca for open positions.
        In backtest mode, uses state['current_positions'].
        """
        from eiqora_v2.tools.db import get_connection
        
        # Get proposed trade details
        decision = state.get("decision", {})
        symbol = state.get("symbol")
        
        proposed_trade = {
            "symbol": symbol,
            "direction": decision.get("direction", "LONG"),
            "conviction": decision.get("conviction", "MEDIUM"),
            "setup_type": decision.get("setup_type", ""),
            "proposed_size_pct": decision.get("position_size_pct", 10.0),
            "entry_price": decision.get("entry_price", 0),
        }
        
        # Get current positions
        # In production, this would query Alpaca API
        # For now, query from signal table
        positions = []
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT symbol, entry_price, stop_loss, take_profit,
                       conviction, created_at
                FROM signal
                WHERE action = 'GO'
                  AND created_at > NOW() - INTERVAL '30 days'
                ORDER BY created_at DESC
                LIMIT 10
            """)
            
            for row in rows:
                # Simplified: assume all are still open
                # In production, would check Alpaca positions
                positions.append({
                    "symbol": row["symbol"],
                    "entry_price": float(row["entry_price"]) if row["entry_price"] else 0,
                    "current_price": 0,  # Would fetch from market
                    "pnl_pct": 0,  # Would calculate
                    "size_pct": 10.0,  # Would get from actual position
                    "days_held": (datetime.utcnow() - row["created_at"]).days,
                })
        
        # Calculate portfolio metrics
        total_exposure = sum(p["size_pct"] for p in positions)
        
        return {
            "proposed_trade": proposed_trade,
            "current_positions": positions,
            "total_exposure_pct": total_exposure,
            "available_capital_pct": 100 - total_exposure,
            "topdown": state.get("topdown", {}),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build contextual prompt for position management."""
        proposed = data["proposed_trade"]
        positions = data["current_positions"]
        exposure = data["total_exposure_pct"]
        available = data["available_capital_pct"]
        topdown = data.get("topdown", {})
        
        # MACRO SAFEGUARD: Check for market stress (SPY drawdown)
        spy_indicators = topdown.get("spy", {})
        spy_drawdown = spy_indicators.get("drawdown_pct", 0) if isinstance(spy_indicators, dict) else 0
        market_stress = spy_drawdown < -10  # SPY down 10%+
        
        # Format current positions
        if positions:
            positions_text = "\n".join([
                f"  - {p['symbol']}: {p['size_pct']:.1f}% exposure, "
                f"{p['days_held']}d held, P&L: {p['pnl_pct']:+.1f}%"
                for p in positions
            ])
        else:
            positions_text = "  (No open positions)"
        
        market_regime = topdown.get("regime", "UNKNOWN")
        market_bias = topdown.get("bias", "NEUTRAL")
        
        # Build stress warning if applicable
        stress_warning = ""
        if market_stress:
            stress_warning = f"""
⚠️ MARKET STRESS ALERT:
- SPY drawdown: {spy_drawdown:.1f}%
- CORRELATION RISK: All stocks may move together
- Consider: Smaller position sizes or pause
"""
        
        return f"""
Evaluate whether to accept new trade given current portfolio state.

MARKET CONTEXT:
- Regime: {market_regime}
- Bias: {market_bias}

{stress_warning}

CURRENT PORTFOLIO:
- Open positions: {len(positions)}
- Total exposure: {exposure:.1f}%
- Available capital: {available:.1f}%

Positions:
{positions_text}

PROPOSED TRADE:
- Symbol: {proposed['symbol']}
- Direction: {proposed['direction']}
- Setup: {proposed['setup_type']}
- Conviction: {proposed['conviction']}
- Proposed size: {proposed['proposed_size_pct']:.1f}%

GUIDELINES (interpret contextually, not hard rules):
- Generally prefer 3-4 positions max
- Generally prefer <50% total exposure
- Watch for cluster/sector concentration
- Consider existing position performance
- IN MARKET STRESS: Be VERY cautious, consider smaller sizes or pause

CONTEXTUAL CONSIDERATIONS:
- Exceptional setup in new sector → might allow 4th+ position
- Same cluster as existing winner → might size smaller
- Multiple losing positions → might be more selective
- Low total exposure → more room for new position
- High conviction + diversifying → override position count
- Market stress (correlation breakdown) → extra caution warranted

Make a contextual decision: APPROVE, REDUCE_SIZE, or REJECT.
If REDUCE_SIZE, suggest appropriate size.
Explain reasoning based on portfolio context.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Portfolio Position Manager.

Your job: Make intelligent, contextual decisions about new trades.

KEY PRINCIPLES:
1. **Context over rules**: Guidelines are flexible based on situation
2. **Quality over quantity**: Better to have 3 great positions than 5 mediocre
3. **Diversification matters**: Avoid clustering in same sector/theme
4. **Risk management**: Consider existing exposure and P&L
5. **Conviction-weighted**: High conviction setups deserve more flexibility

DECISION FRAMEWORK:
- APPROVE: Trade fits well, accept at proposed size
- REDUCE_SIZE: Trade is good but size should be adjusted for portfolio fit
- REJECT: Trade doesn't fit current portfolio state

REASONING QUALITY:
- Explain HOW portfolio context influenced decision
- Reference specific positions if relevant
- Justify any guideline overrides
- Be concrete about portfolio impact

OUTPUT SCHEMA:
{
  "decision": "APPROVE" | "REDUCE_SIZE" | "REJECT",
  "reasoning": "Contextual explanation...",
  "approved_size_pct": 10.0,  // If APPROVE/REDUCE_SIZE
  "portfolio_impact": {
    "new_total_exposure": 45.0,
    "new_position_count": 4,
    "diversification_score": "GOOD" | "MODERATE" | "POOR"
  }
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: PositionManagerOutput) -> dict[str, Any]:
        """Update state with position manager decision."""
        update = {"position_manager": result.model_dump()}
        
        # If rejected, override final decision
        if result.decision == "REJECT":
            update["decision"] = {
                **state.get("decision", {}),
                "final_call": "NO_GO",
                "override_reason": f"Position Manager: {result.reasoning}",
            }
        
        # If size reduced, update decision
        elif result.decision == "REDUCE_SIZE" and result.approved_size_pct:
            update["decision"] = {
                **state.get("decision", {}),
                "position_size_pct": result.approved_size_pct,
                "size_adjusted_reason": result.reasoning,
            }
        
        return update
