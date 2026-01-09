"""
Position Manager Agent - LLM-based portfolio position management.

Instead of hard-coded rules, uses LLM to interpret portfolio state contextually
and make intelligent decisions about new trades based on:
- Current positions and performance
- Exposure levels and diversification
- Cluster/sector concentration
- Market context
"""

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
        
        Uses database-tracked positions for live mode.
        In backtest mode, uses state['current_positions'].
        """
        from eiqora_v2.tools.positions import get_open_positions
        from eiqora_v2.services.risk_model import size_position
        
        # Get proposed trade details
        decision = state.get("decision", {})
        symbol = state.get("symbol")
        ideas = state.get("ideas", {}) or {}
        context = state.get("context", {}) or {}
        rule = decision.get("rule", {}) or {}

        ideas_list = ideas.get("ideas", []) or []
        primary_idea_id = ideas.get("primary_idea_id")
        primary_idea = next(
            (idea for idea in ideas_list if idea.get("idea_id") == primary_idea_id),
            ideas_list[0] if ideas_list else {},
        )

        entry_price = rule.get("entry_level") or context.get("current_price") or 0
        sl_mult = rule.get("sl_mult", 2.0)
        atr = context.get("atr14")
        if not atr and entry_price:
            atr = entry_price * 0.02
        direction = rule.get("direction", "LONG")
        stop_loss = None
        if entry_price and atr:
            if direction == "SHORT":
                stop_loss = entry_price + (atr * sl_mult)
            else:
                stop_loss = entry_price - (atr * sl_mult)

        conviction = primary_idea.get("conviction") or "MEDIUM"

        risk_model_output: dict[str, Any] = {}
        if entry_price:
            try:
                risk_model_output = await size_position(
                    symbol=symbol,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    conviction=conviction,
                )
            except Exception as exc:
                risk_model_output = {"error": str(exc)}
        
        proposed_trade = {
            "symbol": symbol,
            "direction": rule.get("direction", decision.get("direction", "LONG")),
            "conviction": conviction,
            "setup_type": decision.get("setup_type", ""),
            "proposed_size_pct": risk_model_output.get("position_size_pct", decision.get("position_size_pct", 10.0)),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
        }
        
        # Get current positions from database
        positions = []
        try:
            open_positions = await get_open_positions()
            for pos in open_positions:
                positions.append({
                    "symbol": pos["symbol"],
                    "entry_price": float(pos.get("entry_price") or 0),
                    "current_price": float(pos.get("current_price") or pos.get("entry_price") or 0),
                    "pnl_pct": float(pos.get("unrealized_pnl_pct") or 0),
                    "size_pct": float(pos.get("size_pct") or 10.0),
                    "days_held": int(pos.get("days_held") or 0),
                })
        except Exception as e:
            self._logger.debug(f"No existing positions found: {e}")
            positions = []
        
        # Calculate portfolio metrics
        total_exposure = sum(p["size_pct"] for p in positions)
        
        return {
            "proposed_trade": proposed_trade,
            "current_positions": positions,
            "total_exposure_pct": total_exposure,
            "available_capital_pct": 100 - total_exposure,
            "topdown": state.get("topdown", {}),
            "risk_model": risk_model_output,
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build contextual prompt for position management."""
        proposed = data["proposed_trade"]
        positions = data["current_positions"]
        exposure = data["total_exposure_pct"]
        available = data["available_capital_pct"]
        topdown = data.get("topdown", {})
        risk_model = data.get("risk_model", {}) or {}
        
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
- Entry Price: {proposed.get('entry_price', 0)}
- Stop Loss: {proposed.get('stop_loss', 'N/A')}

RISK MODEL (deterministic caps):
- Recommended size %: {risk_model.get('position_size_pct', 'N/A')}
- Portfolio heat cap: {risk_model.get('portfolio_heat_cap', 'N/A')}
- Available exposure %: {risk_model.get('available_exposure_pct', 'N/A')}
- Position count: {risk_model.get('position_count', 'N/A')}
- Sector exposure: {risk_model.get('sector_exposure', {})}
- Notes: {risk_model.get('notes', [])}

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
If REDUCE_SIZE, suggest appropriate size. Do not exceed the risk model recommended size.
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
