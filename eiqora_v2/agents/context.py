"""
Context Agent implementation.
Provides stock-level context features for swing planning.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.context import ContextOutput, VolBasis, TrendStatus, MomentumMetrics
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_indicators


class ContextAgent(BaseAgent[ContextOutput]):
    """
    Context Agent: provides deterministic context features.
    
    Gathers:
    - Volatility (RV20)
    - Trend status (vs MA20, MA50, MA200)
    - Momentum (20d, 60d returns)
    - Volume profile
    - State tags
    """
    
    name = "context"
    output_schema = ContextOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Fetch technical indicators from price data."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        indicators = await get_indicators(symbol, 60, asof_time)
        return indicators
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt with indicator data."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Check for data issues
        if data.get("error"):
            return f"""
Symbol: {symbol}
As of: {asof_time}

ERROR: {data.get('error')}
Data points available: {data.get('data_points', 0)}

Based on limited data, provide best-effort context analysis.
Set data_quality to "SPARSE" or "STALE" as appropriate.
"""
        
        return f"""
Analyze the following technical indicators for {symbol} as of {asof_time}.

INDICATOR DATA:
- Current Price: ${data.get('current_price', 0):.2f}
- MA20: ${data.get('ma20', 0):.2f}
- MA50: ${data.get('ma50', 'N/A')}
- MA200: ${data.get('ma200', 'N/A')}
- Trend Status: {data.get('trend', {})}
- RV20 (Realized Vol): {data.get('rv20', 0):.4f}
- ATR14: ${data.get('atr14', 0):.2f}
- 20d Return: {data.get('ret_20d', 0):.2%} if data.get('ret_20d') else "N/A"
- 60d Return: {data.get('ret_60d', 0):.2%} if data.get('ret_60d') else "N/A"
- Volume Z-Score (20d): {data.get('volume_z_20d', 0):.2f}
- State Tags: {data.get('state_tags', [])}
- Data Points: {data.get('data_points', 0)}

Provide a structured context analysis following the schema.
Use RV20 as the vol_basis type. Set appropriate state_tags based on trend, volatility, and volume.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Context Agent that provides deterministic technical context for swing trade analysis.

OUTPUT SCHEMA:
{
  "vol_basis": {"type": "RV20", "value": <float>},
  "trend": {"ma20": "ABOVE|BELOW", "ma50": "ABOVE|BELOW|null", "ma200": "ABOVE|BELOW|null"},
  "momentum": {"ret_20d": <float|null>, "ret_60d": <float|null>},
  "volume_z_20d": <float>,
  "state_tags": ["UPTREND", "HIGH_VOL", etc.],
  "current_price": <float>,
  "data_quality": "GOOD|SPARSE|STALE"
}

STATE_TAGS options:
- UPTREND, DOWNTREND, MIXED (trend)
- HIGH_VOL, LOW_VOL (volatility)
- HIGH_VOLUME (unusual volume)

Return ONLY valid JSON. No explanation."""

    def _build_state_update(self, state: SwingTradeState, result: ContextOutput) -> dict[str, Any]:
        """Build state update with context output."""
        return {"context": result.model_dump()}
