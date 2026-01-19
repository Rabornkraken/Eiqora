"""
Event Risk Checking Utilities.

Checks for upcoming events that could create volatility:
- Earnings releases
- Ex-dividend dates  
- Major economic events
"""

from datetime import datetime, timedelta
from typing import Any

from eiqora_v2.tools.db import get_connection


async def check_upcoming_events(
    symbol: str,
    asof_time: datetime,
    lookforward_days: int = 7
) -> dict[str, Any]:
    """
    Check for upcoming events that could affect entry timing.
    
    Args:
        symbol: Stock symbol
        asof_time: Current time (for backtesting compatibility)
        lookforward_days: How many days ahead to check
        
    Returns:
        Dictionary with event warnings and risk level
    """
    events = []
    risk_level = "LOW"  # LOW, MEDIUM, HIGH
    
    end_date = asof_time + timedelta(days=lookforward_days)
    
    async with get_connection() as conn:
        # 1. Check for upcoming earnings
        earnings_row = await conn.fetchrow("""
            SELECT earnings_date, time_of_day
            FROM earnings_event
            WHERE symbol = $1
              AND earnings_date >= $2
              AND earnings_date <= $3
            ORDER BY earnings_date
            LIMIT 1
        """, symbol, asof_time.date(), end_date.date())
        
        if earnings_row:
            earnings_date = earnings_row["earnings_date"]
            days_until = (earnings_date - asof_time.date()).days
            time_of_day = earnings_row["time_of_day"] or "Unknown"
            
            events.append({
                "type": "EARNINGS",
                "date": str(earnings_date),
                "days_until": days_until,
                "time_of_day": time_of_day,
                "severity": "HIGH" if days_until <= 2 else "MEDIUM",
                "message": f"Earnings in {days_until} day{'s' if days_until != 1 else ''} ({time_of_day})"
            })
            
            # Set overall risk level
            if days_until <= 1:
                risk_level = "HIGH"
            elif days_until <= 3:
                risk_level = "MEDIUM"
        
        # 2. Check for upcoming ex-dividend dates
        div_row = await conn.fetchrow("""
            SELECT ex_date, cash_amount, pay_date
            FROM corporate_action
            WHERE ticker = $1
              AND action_type = 'DIVIDEND'
              AND ex_date >= $2
              AND ex_date <= $3
            ORDER BY ex_date
            LIMIT 1
        """, symbol, asof_time.date(), end_date.date())
        
        if div_row:
            ex_date = div_row["ex_date"]
            days_until = (ex_date - asof_time.date()).days
            amount = div_row["cash_amount"]
            
            events.append({
                "type": "EX_DIVIDEND",
                "date": str(ex_date),
                "days_until": days_until,
                "amount": float(amount) if amount else None,
                "severity": "MEDIUM" if days_until <= 1 else "LOW",
                "message": f"Ex-dividend in {days_until} day{'s' if days_until != 1 else ''} (${amount if amount else 'TBD'})"
            })
            
            # Ex-dividend is generally low risk unless very close
            if days_until == 0 and risk_level == "LOW":
                risk_level = "MEDIUM"
    
    return {
        "has_upcoming_events": len(events) > 0,
        "risk_level": risk_level,
        "events": events,
        "warning_count": sum(1 for e in events if e["severity"] in ["HIGH", "MEDIUM"]),
    }


def format_event_warnings(event_data: dict[str, Any]) -> str:
    """
    Format upcoming events into a readable warning string.
    
    Args:
        event_data: Output from check_upcoming_events()
        
    Returns:
        Formatted string for agent prompts
    """
    if not event_data.get("has_upcoming_events"):
        return "No upcoming events in next 7 days"
    
    lines = []
    risk_level = event_data.get("risk_level", "LOW")
    
    lines.append(f"⚠️  EVENT RISK: {risk_level}")
    lines.append("")
    
    for event in event_data.get("events", []):
        severity_icon = "🔴" if event["severity"] == "HIGH" else "🟡" if event["severity"] == "MEDIUM" else "🟢"
        lines.append(f"{severity_icon} {event['message']}")
        
        # Add recommendations
        if event["type"] == "EARNINGS" and event["days_until"] <= 2:
            lines.append("   → Consider waiting until after earnings for entry")
        elif event["type"] == "EARNINGS" and event["days_until"] <= 5:
            lines.append("   → Entry timing is risky, expect higher volatility")
        elif event["type"] == "EX_DIVIDEND" and event["days_until"] <= 1:
            lines.append("   → Expect price drop by dividend amount on ex-date")
    
    return "\n".join(lines)
