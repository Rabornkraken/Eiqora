"""
Analysis Service - Business logic for stock analysis
Integrates with eiqora_v2 orchestrators
"""
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
import asyncio

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
        # Check running analyses
        if analysis_id in self.running_analyses:
            data = self.running_analyses[analysis_id]
            return self._build_analysis_detail(data)

        # Check completed analyses
        if analysis_id in self.completed_analyses:
            data = self.completed_analyses[analysis_id]
            return self._build_analysis_detail(data)

        return None

    async def list_analyses(self, limit: int = 20, status: Optional[str] = None) -> list:
        """
        List analyses

        Args:
            limit: Maximum number of analyses to return
            status: Filter by status (optional)

        Returns:
            List of AnalysisResponse
        """
        all_analyses = list(self.running_analyses.values()) + list(
            self.completed_analyses.values()
        )

        # Filter by status if provided
        if status:
            all_analyses = [a for a in all_analyses if a["status"] == status]

        # Sort by created_at descending
        all_analyses.sort(key=lambda x: x["created_at"], reverse=True)

        # Apply limit
        all_analyses = all_analyses[:limit]

        # Convert to response models
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
