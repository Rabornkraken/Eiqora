"""
Signal management for live trading.

Stores trade signals to database and sends notifications.
"""

import json
import logging
from datetime import date
from typing import Any
from uuid import uuid4

import psycopg

_logger = logging.getLogger(__name__)


class SignalManager:
    """Manages trade signal storage and notifications."""
    
    def __init__(self, db_url: str):
        self.db_url = db_url
    
    def store_signals(self, signals: list[dict[str, Any]]) -> list[str]:
        """
        Store trade signals to database.
        
        Returns:
            List of signal IDs
        """
        if not signals:
            return []
        
        signal_ids = []
        
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                for signal in signals:
                    signal_id = str(uuid4())
                    
                    # Convert agent_outputs and trigger_detail to JSON
                    agent_outputs_json = json.dumps(signal.get("agent_outputs", {}))
                    trigger_detail_json = json.dumps(signal.get("trigger_detail", {}))
                    
                    cur.execute(
                        """
                        INSERT INTO trade_signal (
                            id, symbol, signal_date, trigger_type, action,
                            entry_price, stop_loss, take_profit, conviction,
                            reasoning, agent_outputs, trigger_detail
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                        (
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
                        ),
                    )
                    
                    result = cur.fetchone()
                    actual_id = result[0] if result else signal_id
                    signal_ids.append(str(actual_id))
                    
                    _logger.info(
                        f"Stored signal: {signal['symbol']} {signal['trigger_type']} "
                        f"@ ${signal.get('entry_price', 0):.2f}"
                    )
                
                conn.commit()
        
        return signal_ids
    
    def get_signals_for_date(self, signal_date: date) -> list[dict[str, Any]]:
        """Retrieve all signals for a specific date."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 
                        id, symbol, signal_date, trigger_type, action,
                        entry_price, stop_loss, take_profit, conviction,
                        reasoning, created_at
                    FROM trade_signal
                    WHERE signal_date = %s
                    ORDER BY created_at DESC
                    """,
                    (signal_date,),
                )
                
                rows = cur.fetchall()
                
                signals = []
                for row in rows:
                    signals.append({
                        "id": str(row[0]),
                        "symbol": row[1],
                        "signal_date": row[2],
                        "trigger_type": row[3],
                        "action": row[4],
                        "entry_price": float(row[5]) if row[5] else None,
                        "stop_loss": float(row[6]) if row[6] else None,
                        "take_profit": float(row[7]) if row[7] else None,
                        "conviction": float(row[8]) if row[8] else None,
                        "reasoning": row[9],
                        "created_at": row[10],
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
    
    def generate_daily_report(self, signal_date: date) -> str:
        """Generate a formatted daily signal report."""
        signals = self.get_signals_for_date(signal_date)
        
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
