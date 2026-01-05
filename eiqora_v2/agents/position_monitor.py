"""
Position Monitor Agent implementation.
Monitors open positions and recommends adjustments based on Tier 2 events.

READ-ONLY: Does not trigger data collection.
Reads from: economic_indicator, market_bar_daily, earnings_event

Tier 2 Events Monitored:
- VIX spikes (>25% above 20d average)
- FOMC within 3 days
- Earnings approaching
- Trend invalidation (price < MA20)
"""

from datetime import datetime, timedelta, date
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.monitor import PositionMonitorOutput, MarketContext
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.events import get_economic_calendar
from eiqora_v2.tools.prices import get_indicators


class PositionMonitorAgent(BaseAgent[PositionMonitorOutput]):
    """
    Position Monitor Agent: checks open positions daily for Tier 2 risk events.
    
    Recommends:
    - HOLD: Continue with current TP/SL
    - TIGHTEN: Move stop loss closer (lock in profits)
    - WIDEN: Give more room (temporary volatility)
    - EXIT: Close position early (material risk event)
    
    READ-ONLY - does NOT trigger data collection.
    """
    
    name = "position_monitor"
    output_schema = PositionMonitorOutput
    
    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """
        Monitor a position and recommend actions.
        
        State should include:
        - symbol: ticker being monitored
        - asof_time: current date
        - position: dict with entry_price, stop_loss, take_profit, direction, entry_date
        """
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        position = state.get("position", {})
        
        self.logger.info(f"Running {self.name} for {symbol}")
        
        try:
            # Gather market context
            market_ctx = await self._gather_market_context(symbol, asof_time)
            
            # Gather position context
            position_ctx = self._get_position_context(position, asof_time)
            
            # Check for risk events
            risk_factors = []
            action = "HOLD"
            reason = "No significant risk events detected"
            new_sl = None
            new_tp = None
            urgency = "LOW"
            
            # === TIER 2 EVENT CHECKS ===
            
            # 1. VIX Spike - Widen stops or reduce size
            if market_ctx.get("vix_spike"):
                risk_factors.append("VIX_SPIKE")
                if position_ctx["unrealized_pnl_pct"] < -0.02:  # Losing position
                    action = "EXIT"
                    reason = "VIX spike with losing position - cut losses"
                    urgency = "HIGH"
                else:
                    action = "WIDEN"
                    new_sl = self._widen_stop(position, 1.5)  # 50% wider
                    reason = "VIX spike - widening stops to avoid whipsaw"
                    urgency = "MEDIUM"
            
            # 2. FOMC within 3 days - Exit or tighten
            days_to_fomc = market_ctx.get("days_to_fomc")
            if days_to_fomc is not None and days_to_fomc <= 3:
                risk_factors.append(f"FOMC_IN_{days_to_fomc}D")
                if position_ctx["unrealized_pnl_pct"] > 0.03:  # Profitable
                    action = "TIGHTEN"
                    new_sl = self._tighten_stop_to_breakeven(position)
                    reason = f"FOMC in {days_to_fomc} days - locking in profits"
                    urgency = "MEDIUM"
                elif days_to_fomc <= 1:
                    action = "EXIT"
                    reason = "FOMC tomorrow - reducing event risk"
                    urgency = "HIGH"
            
            # 3. Earnings approaching - Exit before event
            if market_ctx.get("ticker_has_earnings_soon"):
                days_to_earnings = market_ctx.get("days_to_earnings", 99)
                risk_factors.append(f"EARNINGS_IN_{days_to_earnings}D")
                if days_to_earnings <= 1:
                    action = "EXIT"
                    reason = "Earnings tomorrow - exiting to avoid binary event"
                    urgency = "HIGH"
                elif days_to_earnings <= 3:
                    action = "TIGHTEN"
                    new_sl = self._tighten_stop_to_breakeven(position)
                    reason = f"Earnings in {days_to_earnings} days - protecting profits"
                    urgency = "MEDIUM"
            
            # 4. Trend Invalidation - Tighten or exit
            if market_ctx.get("daily_trend") == "DOWNTREND" and position.get("direction") == "long":
                risk_factors.append("TREND_REVERSAL")
                if position_ctx["unrealized_pnl_pct"] < 0:
                    action = "EXIT"
                    reason = "Daily trend reversed to downtrend - invalidating long thesis"
                    urgency = "HIGH"
                else:
                    action = "TIGHTEN"
                    new_sl = self._tighten_stop_to_breakeven(position)
                    reason = "Trend weakening - tightening stop"
                    urgency = "MEDIUM"
            
            # 5. Large Profit - Trailing stop
            if position_ctx["unrealized_pnl_pct"] > 0.08:  # >8% profit
                if action == "HOLD":
                    action = "TIGHTEN"
                    new_sl = self._trail_stop(position, 0.5)  # 50% of gains
                    reason = "Large profit - trailing stop to lock in gains"
                    urgency = "LOW"
            
            # 6. Time decay - Exit warning
            if position_ctx["days_held"] > position.get("max_holding_days", 30) - 5:
                risk_factors.append("TIME_STOP_APPROACHING")
                if action == "HOLD":
                    action = "TIGHTEN"
                    reason = f"Time stop approaching in {position.get('max_holding_days', 30) - position_ctx['days_held']} days"
                    urgency = "LOW"
            
            output = PositionMonitorOutput(
                action=action,
                reason=reason,
                new_stop_loss=new_sl,
                new_take_profit=new_tp,
                urgency=urgency,
                risk_factors=risk_factors,
            )
            
            self.logger.info(f"{self.name} completed: {action} - {reason}")
            return {"position_monitor": output.model_dump()}
            
        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                "position_monitor": PositionMonitorOutput(
                    action="HOLD",
                    reason=f"Monitor failed: {e}",
                    urgency="LOW",
                    risk_factors=["MONITOR_ERROR"],
                ).model_dump()
            }
    
    async def _gather_market_context(self, symbol: str, asof_time: datetime) -> dict:
        """Gather current market conditions."""
        ctx = {}
        
        # Get VIX data
        try:
            vix_indicators = await get_indicators("IDX_VIX", 30, asof_time)
            if not vix_indicators.get("error"):
                ctx["vix_current"] = vix_indicators.get("current_price", 0)
                ctx["vix_20d_avg"] = vix_indicators.get("ma20", 0)
                if ctx["vix_20d_avg"] > 0:
                    ctx["vix_spike"] = ctx["vix_current"] > ctx["vix_20d_avg"] * 1.25
        except Exception:
            pass
        
        # Get economic calendar
        try:
            econ = await get_economic_calendar(asof_time, 7, 7)
            ctx["days_to_fomc"] = econ.get("days_to_fomc")
            ctx["days_to_cpi"] = None  # Could add
        except Exception:
            pass
        
        # Check for earnings
        try:
            async with get_connection() as conn:
                row = await conn.fetchrow("""
                    SELECT earnings_date 
                    FROM earnings_event
                    WHERE symbol = $1 AND earnings_date > $2::date
                    ORDER BY earnings_date ASC
                    LIMIT 1
                """, symbol, asof_time)
                if row:
                    days_to_earnings = (row["earnings_date"] - asof_time.date()).days
                    if days_to_earnings <= 5:
                        ctx["ticker_has_earnings_soon"] = True
                        ctx["days_to_earnings"] = days_to_earnings
        except Exception:
            pass
        
        # Get ticker daily trend
        try:
            indicators = await get_indicators(symbol, 30, asof_time)
            if not indicators.get("error"):
                ctx["price_vs_ma20"] = "ABOVE" if indicators.get("trend", {}).get("ma20") == "ABOVE" else "BELOW"
                ctx["daily_trend"] = "UPTREND" if "UPTREND" in indicators.get("state_tags", []) else \
                                     "DOWNTREND" if "DOWNTREND" in indicators.get("state_tags", []) else "SIDEWAYS"
        except Exception:
            pass
        
        return ctx
    
    def _get_position_context(self, position: dict, asof_time: datetime) -> dict:
        """Calculate position metrics."""
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", entry_price)
        entry_date = position.get("entry_date")
        
        # Calculate unrealized P&L
        if position.get("direction") == "long":
            unrealized_pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0
        else:
            unrealized_pnl_pct = (entry_price - current_price) / entry_price if entry_price else 0
        
        # Calculate days held
        days_held = 0
        if entry_date:
            if isinstance(entry_date, datetime):
                entry_d = entry_date.date()
            else:
                entry_d = entry_date
            asof_d = asof_time.date() if isinstance(asof_time, datetime) else asof_time
            days_held = (asof_d - entry_d).days
        
        return {
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "days_held": days_held,
        }
    
    def _tighten_stop_to_breakeven(self, position: dict) -> float:
        """Move stop loss to breakeven (entry price)."""
        return position.get("entry_price", 0)
    
    def _widen_stop(self, position: dict, factor: float) -> float:
        """Widen stop loss by a factor."""
        entry = position.get("entry_price", 0)
        current_sl = position.get("stop_loss", 0)
        distance = abs(entry - current_sl)
        
        if position.get("direction") == "long":
            return entry - (distance * factor)
        else:
            return entry + (distance * factor)
    
    def _trail_stop(self, position: dict, pnl_percent_to_lock: float) -> float:
        """Trail stop to lock in a percentage of profits."""
        entry = position.get("entry_price", 0)
        current = position.get("current_price", entry)
        
        if position.get("direction") == "long":
            profit = current - entry
            return entry + (profit * pnl_percent_to_lock)
        else:
            profit = entry - current
            return entry - (profit * pnl_percent_to_lock)

    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Not used - run() is overridden."""
        return {}
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Not used - run() is overridden."""
        return ""
    
    def _build_state_update(self, state: SwingTradeState, result: PositionMonitorOutput) -> dict[str, Any]:
        """Build state update."""
        return {"position_monitor": result.model_dump()}
