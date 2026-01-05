"""
Context Agent implementation.
Provides stock-level context features for swing planning.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.context import ContextOutput, VolBasis, TrendStatus, MomentumMetrics
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_indicators, get_hourly_indicators


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
        """Fetch daily and hourly technical indicators."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Get daily indicators
        indicators = await get_indicators(symbol, 60, asof_time)
        
        # Get hourly indicators for entry timing
        hourly = await get_hourly_indicators(symbol, asof_time.date(), asof_time)
        
        return {
            "daily": indicators,
            "hourly": hourly,
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt with daily and hourly indicator data."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})
        
        # Check for data issues
        if daily.get("error"):
            return f"""
Symbol: {symbol}
As of: {asof_time}

ERROR: {daily.get('error')}
Data points available: {daily.get('data_points', 0)}

Based on limited data, provide best-effort context analysis.
Set data_quality to "SPARSE" or "STALE" as appropriate.
"""
        
        # Build hourly timing section
        hourly_section = ""
        if not hourly.get("error"):
            vwap_dist = hourly.get('vwap_distance_pct', 0)
            hourly_section = f"""

HOURLY TIMING:
- VWAP: ${hourly.get('vwap', 0):.2f} (distance: {vwap_dist:+.2f}%)
- Hourly RSI: {hourly.get('rsi_hourly', 50):.1f}
- Intraday Trend: {hourly.get('intraday_trend', 'NEUTRAL')}
- Position in Range: {hourly.get('position_in_range', 0.5):.1%}
- Hourly Tags: {', '.join(hourly.get('state_tags', []))}
"""
        
        return f"""
Analyze the following technical indicators for {symbol} as of {asof_time}.

DAILY INDICATORS:
- Current Price: ${daily.get('current_price', 0):.2f}
- MA20: ${daily.get('ma20', 0):.2f}
- MA50: ${daily.get('ma50', 'N/A')}
- MA200: ${daily.get('ma200', 'N/A')}
- Trend Status: {daily.get('trend', {})}
- RSI(14): {daily.get('rsi14', 50):.1f}
- MACD Histogram: {daily.get('macd', {}).get('histogram', 0):.2f}
- ADX(14): {daily.get('adx14', 25):.1f}
- RV20 (Realized Vol): {daily.get('rv20', 0):.4f}
- ATR14: ${daily.get('atr14', 0):.2f}
- 20d Return: {daily.get('ret_20d', 0):.2%} if daily.get('ret_20d') else "N/A"
- 60d Return: {daily.get('ret_60d', 0):.2%} if daily.get('ret_60d') else "N/A"
- Volume Z-Score (20d): {daily.get('volume_z_20d', 0):.2f}
- State Tags: {daily.get('state_tags', [])}
- Data Points: {daily.get('data_points', 0)}{hourly_section}

Provide a structured context analysis. Use hourly timing to assess if NOW is a good entry point.
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
