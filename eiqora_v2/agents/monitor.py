"""
Monitor Agent implementation.
Tracks open positions and generates alerts.
"""

from datetime import datetime, timedelta
from typing import Any
import uuid

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.monitor import MonitorOutput, PositionAlert, OpenPosition
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_prices


class MonitorAgent(BaseAgent[MonitorOutput]):
    """
    Monitor Agent: tracks open positions and generates alerts.
    
    Runs periodically (not per-trade) to check all open positions.
    Generates alerts for:
    - Approaching TP/SL levels
    - Invalidation conditions
    - Time stop warnings
    - Unusual price/volume activity
    """
    
    name = "monitor"
    output_schema = MonitorOutput
    
    def __init__(self, positions: list[OpenPosition] | None = None):
        super().__init__()
        self.positions = positions or []
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather current prices for all open positions."""
        asof_time = state["asof_time"]
        price_data = {}
        
        for position in self.positions:
            try:
                prices = await get_prices(position.symbol, 5, asof_time)
                if prices:
                    last_bar = prices[-1]
                    price_data[position.symbol] = {
                        "current_price": float(last_bar["close"]),
                        "high": float(last_bar["high"]),
                        "low": float(last_bar["low"]),
                        "volume": last_bar.get("volume"),
                    }
            except Exception as e:
                self.logger.error(f"Failed to get price for {position.symbol}: {e}")
        
        return {"price_data": price_data, "asof_time": asof_time}
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for position monitoring."""
        price_data = data.get("price_data", {})
        asof_time = data.get("asof_time")
        
        if not self.positions:
            return "No open positions to monitor."
        
        positions_text = []
        for pos in self.positions:
            current = price_data.get(pos.symbol, {}).get("current_price", "N/A")
            pnl = ((current / pos.entry_price - 1) * 100) if isinstance(current, float) else None
            
            positions_text.append(
                f"- {pos.symbol} ({pos.direction}): entry=${pos.entry_price:.2f}, "
                f"current=${current}, TP=${pos.tp_level:.2f}, SL=${pos.sl_level:.2f}, "
                f"P&L={pnl:.1f}%" if pnl else f"- {pos.symbol}: data unavailable"
            )
        
        return f"""
Monitor {len(self.positions)} open positions as of {asof_time}.

POSITIONS:
{chr(10).join(positions_text)}

Generate alerts for:
1. Price within 10% of TP/SL
2. Time stop < 5 days away
3. Any invalidation conditions met
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Monitor Agent that tracks open trading positions.

ALERT TYPES:
- APPROACHING_TP: Price within 10% of take profit
- APPROACHING_SL: Price within 10% of stop loss  
- AT_INVALIDATION: Invalidation level breached
- TIME_WARNING: Time stop < 5 days away
- UNUSUAL_VOLUME: Volume > 3x average
- GAP_DOWN/GAP_UP: Significant gap overnight

SEVERITY:
- INFO: Normal updates, approaching levels
- WARNING: Close to exits, time running out
- CRITICAL: Invalidation hit, immediate action needed

OUTPUT SCHEMA:
{
  "positions_checked": 3,
  "position_updates": [{"position_id": "...", "current_price": 150.0, "status": "ACTIVE"}],
  "alerts": [
    {"alert_id": "...", "position_id": "...", "symbol": "NVDA", "alert_type": "APPROACHING_TP", 
     "severity": "INFO", "message": "...", "triggered_at": "..."}
  ],
  "summary": {"total_pnl_pct": 2.5, "alerts_count": 1}
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: MonitorOutput) -> dict[str, Any]:
        """Build state update."""
        return {"monitor": result.model_dump()}


async def check_positions(
    positions: list[OpenPosition],
    asof_time: datetime,
) -> MonitorOutput:
    """
    Deterministic position checking (no LLM).
    
    For simple monitoring, this can be used instead of the full agent.
    """
    from eiqora_v2.tools.prices import get_prices
    
    position_updates = []
    alerts = []
    total_pnl = 0.0
    
    for pos in positions:
        try:
            prices = await get_prices(pos.symbol, 5, asof_time)
            if not prices:
                continue
            
            last_bar = prices[-1]
            current_price = float(last_bar["close"])
            
            # Calculate P&L
            if pos.direction == "LONG":
                pnl_pct = (current_price / pos.entry_price - 1)
            else:
                pnl_pct = (pos.entry_price / current_price - 1)
            
            total_pnl += pnl_pct
            
            # Calculate days held
            days_held = (asof_time.date() - pos.entry_date.date()).days
            
            # Determine status
            status = "ACTIVE"
            if pos.direction == "LONG":
                if current_price >= pos.tp_level:
                    status = "AT_TP"
                elif current_price <= pos.sl_level:
                    status = "AT_SL"
                elif pos.invalidation_level and current_price <= pos.invalidation_level:
                    status = "AT_INVALIDATION"
            else:  # SHORT
                if current_price <= pos.tp_level:
                    status = "AT_TP"
                elif current_price >= pos.sl_level:
                    status = "AT_SL"
            
            if asof_time >= pos.time_stop_date - timedelta(days=5):
                if status == "ACTIVE":
                    status = "TIME_WARNING"
            
            position_updates.append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "current_price": current_price,
                "unrealized_pnl_pct": pnl_pct * 100,
                "days_held": days_held,
                "status": status,
            })
            
            # Generate alerts
            if status == "AT_TP":
                alerts.append(PositionAlert(
                    alert_id=str(uuid.uuid4())[:8],
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    alert_type="APPROACHING_TP",
                    severity="INFO",
                    message=f"{pos.symbol} hit take profit at ${current_price:.2f}",
                    triggered_at=asof_time,
                ))
            elif status == "AT_SL":
                alerts.append(PositionAlert(
                    alert_id=str(uuid.uuid4())[:8],
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    alert_type="APPROACHING_SL",
                    severity="CRITICAL",
                    message=f"{pos.symbol} hit stop loss at ${current_price:.2f}",
                    triggered_at=asof_time,
                ))
            elif status == "AT_INVALIDATION":
                alerts.append(PositionAlert(
                    alert_id=str(uuid.uuid4())[:8],
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    alert_type="AT_INVALIDATION",
                    severity="CRITICAL",
                    message=f"{pos.symbol} invalidation level breached",
                    triggered_at=asof_time,
                ))
            elif status == "TIME_WARNING":
                alerts.append(PositionAlert(
                    alert_id=str(uuid.uuid4())[:8],
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    alert_type="TIME_WARNING",
                    severity="WARNING",
                    message=f"{pos.symbol} time stop in < 5 days",
                    triggered_at=asof_time,
                ))
        
        except Exception as e:
            position_updates.append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "error": str(e),
            })
    
    avg_pnl = (total_pnl / len(positions) * 100) if positions else 0
    
    return MonitorOutput(
        positions_checked=len(positions),
        position_updates=position_updates,
        alerts=alerts,
        summary={
            "total_positions": len(positions),
            "avg_pnl_pct": round(avg_pnl, 2),
            "alerts_count": len(alerts),
        },
    )
