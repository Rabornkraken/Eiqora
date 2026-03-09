"""
Signal management for live trading.

Stores trade signals to database using async connections.
"""

import json
import logging
from datetime import date
from typing import Any
from uuid import uuid4

from eiqora_v2.tools.db import get_connection

_logger = logging.getLogger(__name__)


class SignalManager:
    """Manages trade signal storage and notifications."""
    
    def __init__(self):
        pass  # Uses async db connection from tools.db
    
    async def store_signal(self, signal: dict[str, Any]) -> str:
        """
        Store a single trade signal to database.
        
        Returns:
            Signal ID
        """
        signal_id = str(uuid4())

        async with get_connection() as conn:
            # Convert agent_outputs and trigger_detail to JSON
            agent_outputs_json = json.dumps(signal.get("agent_outputs", {}), default=str)
            trigger_detail_json = json.dumps(signal.get("trigger_detail", {}), default=str)

            # Use RETURNING to get the actual row id (handles upsert correctly)
            row = await conn.fetchrow("""
                INSERT INTO trade_signal (
                    id, symbol, signal_date, trigger_type, action,
                    entry_price, stop_loss, take_profit, conviction,
                    reasoning, agent_outputs, trigger_detail
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                ON CONFLICT (symbol, signal_date, trigger_type)
                DO UPDATE SET
                    action = EXCLUDED.action,
                    entry_price = EXCLUDED.entry_price,
                    stop_loss = EXCLUDED.stop_loss,
                    take_profit = EXCLUDED.take_profit,
                    conviction = EXCLUDED.conviction,
                    reasoning = EXCLUDED.reasoning,
                    agent_outputs = EXCLUDED.agent_outputs,
                    trigger_detail = EXCLUDED.trigger_detail
                RETURNING id
            """,
                signal_id,
                signal["symbol"],
                signal["signal_date"],
                signal["trigger_type"],
                signal["action"],
                signal.get("entry_price"),
                signal.get("stop_loss"),
                signal.get("take_profit"),
                signal.get("conviction"),
                signal.get("reasoning"),
                agent_outputs_json,
                trigger_detail_json,
            )

            actual_id = str(row["id"]) if row else signal_id

            # Notify Telegram bot of GO signals
            if signal.get("action") == "GO":
                await conn.execute(
                    "SELECT pg_notify($1, $2)",
                    "eiqora_signal",
                    json.dumps({"signal_id": actual_id, "symbol": signal["symbol"]}),
                )

            _logger.info(
                f"Stored signal: {signal['symbol']} {signal['trigger_type']} "
                f"@ ${signal.get('entry_price', 0):.2f}"
            )

        return actual_id
    
    async def store_signals(self, signals: list[dict[str, Any]]) -> list[str]:
        """
        Store multiple trade signals to database.
        
        Returns:
            List of signal IDs
        """
        if not signals:
            return []
        
        signal_ids = []
        for signal in signals:
            signal_id = await self.store_signal(signal)
            signal_ids.append(signal_id)
        
        return signal_ids
    
    async def get_signals_for_date(self, signal_date: date) -> list[dict[str, Any]]:
        """Retrieve all signals for a specific date."""
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT 
                    id, symbol, signal_date, trigger_type, action,
                    entry_price, stop_loss, take_profit, conviction,
                    reasoning, created_at
                FROM trade_signal
                WHERE signal_date = $1
                ORDER BY created_at DESC
            """, signal_date)
            
            signals = []
            for row in rows:
                signals.append({
                    "id": str(row["id"]),
                    "symbol": row["symbol"],
                    "signal_date": row["signal_date"],
                    "trigger_type": row["trigger_type"],
                    "action": row["action"],
                    "entry_price": float(row["entry_price"]) if row["entry_price"] else None,
                    "stop_loss": float(row["stop_loss"]) if row["stop_loss"] else None,
                    "take_profit": float(row["take_profit"]) if row["take_profit"] else None,
                    "conviction": float(row["conviction"]) if row["conviction"] else None,
                    "reasoning": row["reasoning"],
                    "created_at": row["created_at"],
                })
            
            return signals
    
    def send_notifications(self, signals: list[dict[str, Any]]) -> None:
        """
        Send notifications for new signals.
        
        Currently logs to console. Can be extended to:
        - Send Slack messages
        - Send email
        - Trigger webhooks
        """
        if not signals:
            _logger.info("No signals to notify")
            return
        
        go_signals = [s for s in signals if s["action"] == "GO"]
        
        if not go_signals:
            _logger.info("No GO signals to notify")
            return
        
        _logger.info(f"\n{'='*60}")
        _logger.info(f"🔔 TRADE SIGNALS ({len(go_signals)} GO)")
        _logger.info(f"{'='*60}")
        
        for signal in go_signals:
            _logger.info(
                f"\n{signal['symbol']} - {signal['trigger_type']}\n"
                f"  Entry:      ${signal.get('entry_price', 0):.2f}\n"
                f"  Stop Loss:  ${signal.get('stop_loss', 0):.2f}\n"
                f"  Take Profit: ${signal.get('take_profit', 0):.2f}\n"
                f"  Conviction: {signal.get('conviction', 0):.0%}\n"
                f"  Reasoning:  {signal.get('reasoning', 'N/A')[:100]}..."
            )
        
        _logger.info(f"{'='*60}\n")
    
    async def generate_daily_report(self, signal_date: date) -> str:
        """Generate a formatted daily signal report."""
        signals = await self.get_signals_for_date(signal_date)
        
        go_count = sum(1 for s in signals if s["action"] == "GO")
        no_go_count = sum(1 for s in signals if s["action"] == "NO_GO")
        
        report = f"""
Daily Signal Report - {signal_date}
{'='*60}

Total Signals: {len(signals)}
GO Signals:    {go_count}
NO_GO Signals: {no_go_count}

"""
        
        if go_count > 0:
            report += "GO SIGNALS:\n"
            report += "-" * 60 + "\n"
            
            go_signals = [s for s in signals if s["action"] == "GO"]
            for signal in go_signals:
                report += f"""
{signal['symbol']} ({signal['trigger_type']})
  Entry: ${signal.get('entry_price', 0):.2f}
  SL: ${signal.get('stop_loss', 0):.2f} | TP: ${signal.get('take_profit', 0):.2f}
  Conviction: {signal.get('conviction', 0):.0%}
"""
        
        return report
