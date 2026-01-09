"""
Analysis Logger - Records all pipeline analysis for auditing.

Logs both GO and NO_GO decisions with full agent outputs,
and tracks processed triggers to avoid duplicates.
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from eiqora_v2.tools.db import get_connection

_logger = logging.getLogger(__name__)


def _compute_trigger_hash(trigger_detail: dict) -> str:
    """Compute a unique hash for a trigger to detect duplicates."""
    # For news triggers, use news_id
    if trigger_detail.get("news_id"):
        key = f"news:{trigger_detail['news_id']}"
    elif trigger_detail.get("title"):
        # Use title + published_at for deduplication
        key = f"title:{trigger_detail.get('title', '')}:{trigger_detail.get('published_at', '')}"
    else:
        # Generic hash of all details
        key = json.dumps(trigger_detail, sort_keys=True, default=str)
    
    return hashlib.sha256(key.encode()).hexdigest()


async def is_trigger_processed(trigger_detail: dict, symbol: str) -> bool:
    """Check if a trigger has already been processed."""
    trigger_hash = _compute_trigger_hash(trigger_detail)
    
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            SELECT trigger_hash, result FROM processed_trigger
            WHERE trigger_hash = $1 AND symbol = $2
        """, trigger_hash, symbol)
        
        if row:
            _logger.debug(f"Trigger already processed: {symbol} hash={trigger_hash[:16]}...")
            return True
        
        return False


async def log_analysis(
    symbol: str,
    trigger_type: str,
    trigger_detail: dict,
    final_decision: str,
    final_state: dict[str, Any],
    processing_time_ms: int = 0,
    profile_score: float | None = None,
    technical_score: float | None = None,
) -> str:
    """
    Log a complete pipeline analysis to the database.
    
    Records all agent outputs even for NO_GO decisions.
    
    Returns:
        analysis_id (UUID string)
    """
    analysis_id = str(uuid4())
    trigger_hash = _compute_trigger_hash(trigger_detail)
    
    # Extract decision reason
    decision = final_state.get("decision", {})
    decision_reason = decision.get("reason") or decision.get("reasoning") or ""
    
    # If idea_generator skipped, note that
    ideas = final_state.get("ideas", {})
    if ideas.get("skip_reason"):
        decision_reason = f"[Idea Skip] {ideas['skip_reason']}. {decision_reason}"

    # Append red-team summary when available
    red_team = final_state.get("red_team", {})
    if isinstance(red_team, dict) and red_team.get("summary"):
        red_team_label = red_team.get("decision", "N/A")
        red_team_line = f"[RedTeam {red_team_label}] {red_team.get('summary')}"
        decision_reason = f"{decision_reason} | {red_team_line}".strip(" |")
    
    async with get_connection() as conn:
        # 1. Log the analysis
        await conn.execute("""
            INSERT INTO analysis_log (
                analysis_id, symbol, analysis_date, analysis_time,
                trigger_type, trigger_id, trigger_detail,
                final_decision, decision_reason,
                topdown_output, context_output, chart_output,
                fundamental_output, idea_generator_output, exit_policy_output,
                red_team_output, decision_output, position_manager_output, sanity_veto_output,
                risk_model_output, profile_score, technical_score, processing_time_ms
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12, $13, $14, $15, $16, $17, $18,
                $19, $20, $21, $22, $23
            )
        """,
            analysis_id,
            symbol,
            datetime.now(timezone.utc).date(),
            datetime.now(timezone.utc),
            trigger_type,
            trigger_detail.get("news_id") or trigger_detail.get("id"),
            json.dumps(trigger_detail, default=str),
            final_decision,
            decision_reason if decision_reason else None,
            json.dumps(final_state.get("topdown", {}), default=str),
            json.dumps(final_state.get("context", {}), default=str),
            json.dumps(final_state.get("chart", {}), default=str),
            json.dumps(final_state.get("fundamental", {}), default=str),
            json.dumps(final_state.get("ideas", {}), default=str),
            json.dumps(final_state.get("exit_policy", {}), default=str),
            json.dumps(final_state.get("red_team", {}), default=str),
            json.dumps(final_state.get("decision", {}), default=str),
            json.dumps(final_state.get("position_manager", {}), default=str),
            json.dumps(final_state.get("sanity_veto", {}), default=str),
            json.dumps(final_state.get("risk_model", {}), default=str),
            profile_score,
            technical_score,
            processing_time_ms,
        )
        
        # 2. Mark trigger as processed
        await conn.execute("""
            INSERT INTO processed_trigger (
                trigger_hash, symbol, trigger_type, trigger_source_id,
                analysis_id, result, news_id, news_title
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (trigger_hash) DO NOTHING
        """,
            trigger_hash,
            symbol,
            trigger_type,
            trigger_detail.get("news_id"),
            analysis_id,
            final_decision,
            trigger_detail.get("news_id"),
            trigger_detail.get("title", "")[:500] if trigger_detail.get("title") else None,
        )
        
        _logger.info(f"Logged analysis: {symbol} {trigger_type} -> {final_decision} (id={analysis_id[:8]}...)")
    
    return analysis_id


async def get_analysis_summary(symbol: str, days: int = 7) -> dict[str, Any]:
    """Get summary of recent analyses for a symbol."""
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT 
                final_decision, 
                COUNT(*) as count,
                MAX(analysis_time) as latest
            FROM analysis_log
            WHERE symbol = $1 
              AND analysis_date > CURRENT_DATE - $2
            GROUP BY final_decision
        """, symbol, days)
        
        summary = {
            "symbol": symbol,
            "period_days": days,
            "total": 0,
            "decisions": {},
        }
        
        for row in rows:
            summary["decisions"][row["final_decision"]] = {
                "count": row["count"],
                "latest": row["latest"],
            }
            summary["total"] += row["count"]
        
        return summary


async def get_recent_no_go_reasons(symbol: str, limit: int = 5) -> list[dict]:
    """Get recent NO_GO reasons for a symbol."""
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT 
                analysis_time, trigger_type, decision_reason,
                idea_generator_output, decision_output
            FROM analysis_log
            WHERE symbol = $1 AND final_decision = 'NO_GO'
            ORDER BY analysis_time DESC
            LIMIT $2
        """, symbol, limit)
        
        reasons = []
        for row in rows:
            reasons.append({
                "time": row["analysis_time"],
                "trigger_type": row["trigger_type"],
                "reason": row["decision_reason"],
            })
        
        return reasons
