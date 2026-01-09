"""
Analysis Router - REST endpoints for stock analysis
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from ..models.analysis import AnalysisRequest, AnalysisResponse, AnalysisDetail, AnalysisListResponse
from ..services.analysis_service import analysis_service

router = APIRouter()


@router.post("/start", response_model=AnalysisResponse, status_code=202)
async def start_analysis(request: AnalysisRequest):
    """
    Start a new stock analysis

    This endpoint initiates an asynchronous analysis of the specified stock.
    The analysis runs in the background and can be tracked using the analysis_id.

    Args:
        request: Analysis request with symbol and trigger type

    Returns:
        AnalysisResponse with analysis_id for tracking
    """
    try:
        analysis_id = await analysis_service.start_analysis(
            symbol=request.symbol,
            trigger_type=request.trigger_type,
            asof_time=request.asof_time,
        )

        # Return initial response
        return AnalysisResponse(
            analysis_id=analysis_id,
            symbol=request.symbol.upper(),
            status="running",
            created_at=analysis_service.running_analyses[analysis_id]["created_at"],
            completed_at=None,
            result=None,
            error=None,
            trigger_type=request.trigger_type,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start analysis: {str(e)}")


@router.get("/{analysis_id}", response_model=AnalysisDetail)
async def get_analysis(analysis_id: str):
    """
    Get analysis details by ID

    Retrieve the current status and results of an analysis.
    If the analysis is still running, the status will be 'running'.

    Args:
        analysis_id: Unique analysis identifier

    Returns:
        AnalysisDetail with full analysis information
    """
    analysis = await analysis_service.get_analysis(analysis_id)

    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Analysis {analysis_id} not found")

    return analysis


@router.get("/", response_model=AnalysisListResponse)
async def list_analyses(
    limit: int = Query(20, ge=1, le=100, description="Maximum number of analyses to return"),
    status: Optional[str] = Query(None, description="Filter by status (running, completed, failed)"),
):
    """
    List recent analyses

    Retrieve a list of recent analyses, optionally filtered by status.

    Args:
        limit: Maximum number of analyses to return (1-100)
        status: Filter by status (optional)

    Returns:
        AnalysisListResponse with list of analyses
    """
    try:
        analyses = await analysis_service.list_analyses(limit=limit, status=status)

        return AnalysisListResponse(
            analyses=analyses,
            total=len(analyses),
            limit=limit,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list analyses: {str(e)}")