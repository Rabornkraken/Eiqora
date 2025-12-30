"""
Invalidation Agent implementation.
Checks if trade thesis is invalidated.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.monitor import InvalidationOutput
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_prices, get_indicators


class InvalidationAgent(BaseAgent[InvalidationOutput]):
    """
    Invalidation Agent: checks if trade thesis is invalidated.
    
    Evaluates:
    - Price vs invalidation level
    - Trend reversal signals
    - Time stop conditions
    - Fundamental changes (from Event Extractor)
    """
    
    name = "invalidation"
    output_schema = InvalidationOutput
    
    def __init__(self, position_id: str, invalidation_level: float | None = None):
        super().__init__()
        self.position_id = position_id
        self.invalidation_level = invalidation_level
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather price and context data."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Get recent prices
        prices = await get_prices(symbol, 10, asof_time)
        
        # Get indicators
        indicators = await get_indicators(symbol, 30, asof_time)
        
        return {
            "prices": prices,
            "indicators": indicators,
            "position_id": self.position_id,
            "invalidation_level": self.invalidation_level,
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for invalidation check."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        prices = data.get("prices", [])
        indicators = data.get("indicators", {})
        invalidation_level = data.get("invalidation_level")
        
        if not prices:
            return f"""
No price data available for {symbol}.
Return is_invalidated=false with action=HOLD.
"""
        
        last_price = float(prices[-1]["close"]) if prices else 0
        prev_price = float(prices[-2]["close"]) if len(prices) > 1 else last_price
        
        return f"""
Check invalidation for position on {symbol} as of {asof_time}.

POSITION:
- Position ID: {data.get('position_id')}
- Invalidation Level: ${invalidation_level:.2f if invalidation_level else 'Not set'}

CURRENT PRICE DATA:
- Current Close: ${last_price:.2f}
- Previous Close: ${prev_price:.2f}
- Change: {((last_price/prev_price) - 1) * 100:.2f}%

INDICATORS:
- MA20 Trend: {indicators.get('trend', {}).get('ma20', 'N/A')}
- MA50 Trend: {indicators.get('trend', {}).get('ma50', 'N/A')}
- State Tags: {indicators.get('state_tags', [])}

Check if:
1. Price closed below invalidation level (for LONG)
2. Trend has reversed (MA alignment changed)
3. Any other conditions warrant exit
"""
    
    def _get_system_prompt(self) -> str:
        return """You are an Invalidation Agent that checks if trade thesis is invalidated.

INVALIDATION TYPES:
- CLOSE_BELOW_LEVEL: Price closed below invalidation level (LONG)
- CLOSE_ABOVE_LEVEL: Price closed above invalidation level (SHORT)
- TREND_REVERSAL: MA alignment flipped against position
- TIME_STOP: Time stop triggered
- NOT_INVALIDATED: Thesis still valid

ACTION MAPPING:
- HOLD: Thesis valid, keep position
- EXIT_NOW: Critical invalidation, exit immediately
- EXIT_NEXT_OPEN: Moderate invalidation, exit on next open
- REDUCE: Partial invalidation, reduce position size

OUTPUT SCHEMA:
{
  "position_id": "<id>",
  "symbol": "AAPL",
  "is_invalidated": true|false,
  "invalidation_type": "CLOSE_BELOW_LEVEL|...|NOT_INVALIDATED",
  "current_price": 150.0,
  "invalidation_level": 145.0,
  "action": "HOLD|EXIT_NOW|EXIT_NEXT_OPEN|REDUCE",
  "reason": "<brief explanation>"
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: InvalidationOutput) -> dict[str, Any]:
        """Build state update."""
        return {"invalidation": result.model_dump()}
