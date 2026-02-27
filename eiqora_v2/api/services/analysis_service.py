"""
Analysis Service - Business logic for stock analysis
Integrates with eiqora_v2 orchestrators
"""
import uuid
import json
from uuid import UUID
from datetime import date, datetime
from typing import Dict, Any, Optional
import asyncio


def parse_json_field(value):
    """Parse a JSON field that might be a string or dict"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value

from ..models.analysis import AnalysisResponse, AnalysisDetail
from eiqora_v2.config.orchestrator import OrchestratorConfig


class AnalysisService:
    """Service for managing stock analyses"""

    def __init__(self):
        # In-memory storage for running analyses
        # In production, this should be replaced with Redis or database
        self.running_analyses: Dict[str, Dict[str, Any]] = {}
        self.completed_analyses: Dict[str, Dict[str, Any]] = {}

    async def start_analysis(
        self, symbol: str, trigger_type: str, asof_time: Optional[datetime] = None
    ) -> str:
        """
        Start a new stock analysis

        Args:
            symbol: Stock ticker symbol
            trigger_type: Type of trigger for analysis
            asof_time: Analysis as-of time (defaults to now)

        Returns:
            analysis_id: Unique identifier for the analysis
        """
        analysis_id = str(uuid.uuid4())

        # Set default asof_time if not provided
        if asof_time is None:
            asof_time = datetime.utcnow()

        # Store analysis metadata
        self.running_analyses[analysis_id] = {
            "analysis_id": analysis_id,
            "symbol": symbol.upper(),
            "status": "running",
            "created_at": asof_time,
            "completed_at": None,
            "result": None,
            "error": None,
            "trigger_type": trigger_type,
            "steps": ["started"],
        }

        # Run analysis in background
        asyncio.create_task(self._run_analysis_task(analysis_id))

        return analysis_id

    async def _run_analysis_task(self, analysis_id: str):
        """
        Background task to run the actual analysis
        This will integrate with eiqora_v2 orchestrator
        """
        try:
            analysis = self.running_analyses[analysis_id]
            symbol = analysis["symbol"]
            trigger_type = analysis["trigger_type"]
            asof_time = analysis["created_at"]

            # Import orchestrator (lazy import to avoid circular dependency)
            from eiqora_v2.orchestrator import BacktestOrchestrator

            # Create orchestrator
            orchestrator = BacktestOrchestrator(config=OrchestratorConfig.analysis())

            # Update status - starting
            analysis["steps"].append("orchestrator_initialized")

            # Run analysis
            trigger = {"type": trigger_type, "signal": "manual_analysis"}
            result = await orchestrator.run(symbol, asof_time, trigger)

            # Update status - completed
            analysis["steps"].append("analysis_completed")
            analysis["status"] = "completed"
            analysis["completed_at"] = datetime.utcnow()
            analysis["result"] = result

            # Move to completed storage
            self.completed_analyses[analysis_id] = analysis
            del self.running_analyses[analysis_id]

        except Exception as e:
            # Handle errors
            analysis = self.running_analyses.get(analysis_id)
            if analysis:
                analysis["status"] = "failed"
                analysis["completed_at"] = datetime.utcnow()
                analysis["error"] = str(e)
                analysis["steps"].append("analysis_failed")

                # Move to completed storage (even if failed)
                self.completed_analyses[analysis_id] = analysis
                del self.running_analyses[analysis_id]

    async def get_analysis(self, analysis_id: str) -> Optional[AnalysisDetail]:
        """
        Get analysis details by ID

        Args:
            analysis_id: Analysis identifier

        Returns:
            AnalysisDetail or None if not found
        """
        # Check running analyses first
        if analysis_id in self.running_analyses:
            data = self.running_analyses[analysis_id]
            return self._build_analysis_detail(data)

        # Check completed in-memory analyses
        if analysis_id in self.completed_analyses:
            data = self.completed_analyses[analysis_id]
            return self._build_analysis_detail(data)

        # Query database for historical analyses
        try:
            from eiqora_v2.tools.db import get_connection
            import logging

            logger = logging.getLogger(__name__)
            logger.info(f"Querying database for analysis_id: {analysis_id}")

            # Convert string to UUID for database query
            try:
                analysis_uuid = UUID(analysis_id)
            except ValueError as e:
                logger.error(f"Invalid UUID format: {analysis_id}, error: {e}")
                return None

            async with get_connection() as conn:
                logger.info(f"Database connection established, querying for UUID: {analysis_uuid}")
                # Use text cast to avoid UUID type issues in asyncpg
                row = await conn.fetchrow(
                    """
                    SELECT
                        analysis_id,
                        symbol,
                        analysis_time,
                        trigger_type,
                        trigger_detail,
                        final_decision,
                        decision_reason,
                        topdown_output,
                        context_output,
                        chart_output,
                        fundamental_output,
                        idea_generator_output,
                        exit_policy_output,
                        decision_output,
                        position_manager_output,
                        red_team_output,
                        risk_model_output,
                        sanity_veto_output
                    FROM analysis_log
                    WHERE analysis_id::text = $1
                    """,
                    analysis_id,  # Pass original string, not UUID object
                )
                logger.info(f"Query result: {row is not None}")

                if row:
                    return AnalysisDetail(
                        analysis_id=str(row["analysis_id"]),
                        symbol=row["symbol"],
                        status="completed",
                        created_at=row["analysis_time"],
                        completed_at=row["analysis_time"],
                        result={"decision": row["final_decision"], "reasoning": row["decision_reason"]},
                        error=None,
                        trigger_type=row["trigger_type"],
                        agent_outputs={
                            "topdown": parse_json_field(row["topdown_output"]),
                            "context": parse_json_field(row["context_output"]),
                            "chart": parse_json_field(row["chart_output"]),
                            "fundamental": parse_json_field(row["fundamental_output"]),
                            "ideas": parse_json_field(row["idea_generator_output"]),
                            "exit_policy": parse_json_field(row["exit_policy_output"]),
                            "decision": parse_json_field(row["decision_output"]),
                            "position_manager": parse_json_field(row["position_manager_output"]),
                            "red_team": parse_json_field(row["red_team_output"]),
                            "risk_model": parse_json_field(row["risk_model_output"]),
                            "veto": parse_json_field(row["sanity_veto_output"]),
                        },
                        decision=row["final_decision"],
                        steps=["completed"],
                        # Add raw fields for frontend compatibility - parse JSON strings
                        final_decision=row["final_decision"],
                        decision_reason=row["decision_reason"],
                        trigger_detail=parse_json_field(row["trigger_detail"]),
                        topdown_output=parse_json_field(row["topdown_output"]),
                        context_output=parse_json_field(row["context_output"]),
                        fundamental_output=parse_json_field(row["fundamental_output"]),
                        chart_output=parse_json_field(row["chart_output"]),
                        idea_generator_output=parse_json_field(row["idea_generator_output"]),
                        exit_policy_output=parse_json_field(row["exit_policy_output"]),
                        red_team_output=parse_json_field(row["red_team_output"]),
                        decision_output=parse_json_field(row["decision_output"]),
                        position_manager_output=parse_json_field(row["position_manager_output"]),
                        risk_model_output=parse_json_field(row["risk_model_output"]),
                    )

        except Exception as e:
            import logging
            import traceback
            logger = logging.getLogger(__name__)
            logger.error(f"Error fetching analysis from database: {e}")
            logger.error(traceback.format_exc())
            # Re-raise to see the actual error
            raise

        return None

    async def list_analyses(
        self,
        limit: int = 20,
        status: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list:
        """
        List analyses from database

        Args:
            limit: Maximum number of analyses to return
            status: Filter by status (optional)
            start_date: Filter from date inclusive (optional)
            end_date: Filter to date inclusive (optional)

        Returns:
            List of AnalysisResponse
        """
        try:
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # Query analysis_log from database with optional date filters
                rows = await conn.fetch(
                    """
                    SELECT
                        analysis_id,
                        symbol,
                        analysis_time as created_at,
                        trigger_type,
                        final_decision,
                        decision_reason,
                        decision_output
                    FROM analysis_log
                    WHERE ($2::date IS NULL OR analysis_date >= $2)
                      AND ($3::date IS NULL OR analysis_date <= $3)
                    ORDER BY analysis_time DESC
                    LIMIT $1
                    """,
                    limit,
                    start_date,
                    end_date,
                )

                return [
                    AnalysisResponse(
                        analysis_id=str(row["analysis_id"]),
                        symbol=row["symbol"],
                        status="completed",
                        created_at=row["created_at"],
                        completed_at=row["created_at"],
                        result={"decision": row["final_decision"], "reasoning": row["decision_reason"]},
                        error=None,
                        trigger_type=row["trigger_type"],
                    )
                    for row in rows
                ]

        except Exception as e:
            print(f"Error fetching analyses from database: {e}")
            # Fall back to in-memory storage
            all_analyses = list(self.running_analyses.values()) + list(
                self.completed_analyses.values()
            )
            if status:
                all_analyses = [a for a in all_analyses if a["status"] == status]
            all_analyses.sort(key=lambda x: x["created_at"], reverse=True)
            all_analyses = all_analyses[:limit]
            return [
                AnalysisResponse(
                    analysis_id=a["analysis_id"],
                    symbol=a["symbol"],
                    status=a["status"],
                    created_at=a["created_at"],
                    completed_at=a["completed_at"],
                    result=a["result"],
                    error=a["error"],
                    trigger_type=a["trigger_type"],
                )
                for a in all_analyses
            ]

    def _build_analysis_detail(self, data: Dict[str, Any]) -> AnalysisDetail:
        """Build AnalysisDetail from raw data"""
        result = data.get("result", {})

        # Extract decision from result
        decision = result.get("decision") if result else None

        # Build agent_outputs dict
        agent_outputs = {}
        if result:
            for key in ["topdown", "context", "chart", "fundamental", "ideas", "decision", "veto", "narrative"]:
                if key in result:
                    agent_outputs[key] = result[key]

        return AnalysisDetail(
            analysis_id=data["analysis_id"],
            symbol=data["symbol"],
            status=data["status"],
            created_at=data["created_at"],
            completed_at=data["completed_at"],
            result=data["result"],
            error=data["error"],
            trigger_type=data["trigger_type"],
            agent_outputs=agent_outputs if agent_outputs else None,
            decision=decision,
            steps=data.get("steps", []),
        )


# Singleton instance
analysis_service = AnalysisService()
