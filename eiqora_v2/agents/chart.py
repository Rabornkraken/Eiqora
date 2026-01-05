"""
Chart Agent implementation.
Classifies chart setups and identifies entry/invalidation levels.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.chart import ChartOutput
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_prices, get_indicators, get_price_levels, get_hourly_indicators


class ChartAgent(BaseAgent[ChartOutput]):
    """
    Chart Agent: classifies setup type and identifies key levels.
    
    Uses a finite taxonomy of setup types:
    - BREAKOUT_20D, BREAKOUT_60D
    - PULLBACK_MA20, PULLBACK_MA50
    - BASE_BREAKOUT
    - REVERSAL_AFTER_SELL_OFF
    - RANGE_FADE_HIGH, RANGE_FADE_LOW
    - NO_SETUP
    """
    
    name = "chart"
    output_schema = ChartOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Fetch price data, daily indicators, and hourly indicators."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Get recent price bars
        prices = await get_prices(symbol, 60, asof_time)
        
        if len(prices) < 10:
            return {"error": f"Insufficient price data: {len(prices)} bars"}
        
        # Get daily indicators (trend/setup)
        indicators = await get_indicators(symbol, 60, asof_time)
        
        # Get hourly indicators (entry timing)
        hourly = await get_hourly_indicators(symbol, asof_time.date(), asof_time)
        
        # Get key levels
        levels = await get_price_levels(symbol, 60, asof_time)
        
        return {
            "prices": prices[-20:],  # Last 20 bars for prompt
            "indicators": indicators,
            "hourly": hourly,
            "levels": levels,
            "full_bar_count": len(prices),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt with multi-timeframe analysis."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Format recent bars
        bars_text = self._format_bars(data.get("prices", []))
        
        indicators = data.get("indicators", {})
        hourly = data.get("hourly", {})
        levels = data.get("levels", {})
        yesterday = levels.get("yesterday", {})
        
        # Build hourly section if available
        hourly_section = ""
        if not hourly.get("error"):
            vwap_dist = hourly.get('vwap_distance_pct', 0)
            vwap_status = "above" if vwap_dist > 0 else "below"
            hourly_section = f"""

HOURLY TIMING (Entry Precision):
- VWAP: ${hourly.get('vwap', 0):.2f} (price {vwap_status} by {abs(vwap_dist):.2f}%)
- Hourly RSI: {hourly.get('rsi_hourly', 50):.1f}
- Intraday Trend: {hourly.get('intraday_trend', 'NEUTRAL')}
- Position in Range: {hourly.get('position_in_range', 0.5):.1%} (0%=LOD, 100%=HOD)
- Today High/Low: ${hourly.get('today_high', 0):.2f} / ${hourly.get('today_low', 0):.2f}
- Hourly Tags: {', '.join(hourly.get('state_tags', []))}
"""
        
        return f"""
Analyze the chart for {symbol} as of {asof_time}.

RECENT PRICE BARS (last 20 days):
{bars_text}

KEY LEVELS:
- Yesterday Close: ${yesterday.get('close', 0):.2f}
- Yesterday High: ${yesterday.get('high', 0):.2f}
- Yesterday Low: ${yesterday.get('low', 0):.2f}
- 20-Day High: ${levels.get('high_20d', 0):.2f}
- 20-Day Low: ${levels.get('low_20d', 0):.2f}
- 60-Day High: ${levels.get('high_60d', 0):.2f}
- 60-Day Low: ${levels.get('low_60d', 0):.2f}
- 20-Day Range: {levels.get('range_20d_pct', 0):.1%}

DAILY INDICATORS (Setup Identification):
- Current Price: ${indicators.get('current_price', 0):.2f}
- MA20: ${indicators.get('ma20', 0):.2f} | MA50: ${indicators.get('ma50', 'N/A')}
- Trend: {indicators.get('trend', {})}
- RSI(14): {indicators.get('rsi14', 50):.1f}
- MACD: {indicators.get('macd', {}).get('histogram', 0):.2f}
- ADX(14): {indicators.get('adx14', 25):.1f}
- Bollinger: {indicators.get('bollinger', {}).get('price_position', 'N/A')}
- State Tags: {', '.join(indicators.get('state_tags', []))}{hourly_section}

Classify the setup type using daily timeframe. Use hourly data to assess entry timing quality.
If no clear setup exists, use setup_type="NO_SETUP" and direction="NEUTRAL".
"""
    
    def _format_bars(self, bars: list[dict]) -> str:
        """Format price bars for prompt."""
        if not bars:
            return "No data"
        
        lines = ["Date       | Open    | High    | Low     | Close   | Volume"]
        lines.append("-" * 65)
        
        for bar in bars[-10:]:  # Show last 10 in prompt
            date = bar.get("date", "N/A")
            if hasattr(date, "strftime"):
                date = date.strftime("%Y-%m-%d")
            lines.append(
                f"{date} | {bar.get('open', 0):7.2f} | {bar.get('high', 0):7.2f} | "
                f"{bar.get('low', 0):7.2f} | {bar.get('close', 0):7.2f} | {bar.get('volume', 0):>10,.0f}"
            )
        
        return "\n".join(lines)
    
    def _get_system_prompt(self) -> str:
        return """You are a Chart Agent that classifies technical setups for swing trading.

ALLOWED SETUP TYPES (must use exactly one):
- BREAKOUT_20D: Breaking above 20-day high
- BREAKOUT_60D: Breaking above 60-day high
- PULLBACK_MA20: Healthy pullback to 20-day MA in uptrend
- PULLBACK_MA50: Healthy pullback to 50-day MA in uptrend
- BASE_BREAKOUT: Breaking out of consolidation base
- REVERSAL_AFTER_SELL_OFF: V-bottom or reversal pattern after sharp decline
- RANGE_FADE_HIGH: Fading resistance in range-bound market
- RANGE_FADE_LOW: Buying support in range-bound market
- NO_SETUP: No actionable setup identified

OUTPUT SCHEMA:
{
  "setup_type": "<from list above>",
  "direction": "LONG|SHORT|NEUTRAL",
  "entry_trigger": {"type": "BREAK_YDAY_HIGH|CLOSE_ABOVE_LEVEL|NEXT_OPEN", "level": <float|null>},
  "invalidation": {"type": "CLOSE_BELOW_LEVEL|CLOSE_BELOW_20D_LOW|CLOSE_BELOW_50DMA", "level": <float>, "days_confirm": 1},
  "setup_quality": {"volume_confirm": <bool>, "compression": <bool>, "score": <0.0-1.0>},
  "key_levels": {"support": <float>, "resistance": <float>},
  "notes": "<brief technical notes, max 300 chars>"
}

For NO_SETUP, set direction="NEUTRAL", entry_trigger=null, invalidation=null.
Return ONLY valid JSON. No explanation."""
    
    def _build_state_update(self, state: SwingTradeState, result: ChartOutput) -> dict[str, Any]:
        """Build state update with chart output."""
        return {"chart": result.model_dump()}
