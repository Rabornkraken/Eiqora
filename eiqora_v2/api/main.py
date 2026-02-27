"""
FastAPI Main Application for Eiqora v2
Exposes REST API and WebSocket endpoints for the frontend
"""
import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from .config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# Router imports
from .routers import analysis, calendar, dashboard, news, positions, symbol, websocket


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    logger.info(f"Starting {settings.API_TITLE} v{settings.API_VERSION}")
    logger.info(f"API running on http://{settings.API_HOST}:{settings.API_PORT}")
    yield
    # Shutdown
    logger.info("Shutting down API server")


# Create FastAPI application
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description="API for Eiqora v2 Multiagent Stock Analysis System",
    lifespan=lifespan,
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handler to log errors
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback
    logger.error(f"Unhandled exception: {exc}")
    logger.error(traceback.format_exc())
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
    }


# Debug endpoint to test database query
@app.get("/debug/analysis/{analysis_id}")
async def debug_analysis(analysis_id: str):
    """Debug endpoint to test analysis query"""
    from uuid import UUID
    try:
        from eiqora_v2.tools.db import get_connection

        # Test 1: Simple connection
        async with get_connection() as conn:
            # Test 2: Count analyses
            count = await conn.fetchval("SELECT COUNT(*) FROM analysis_log")

            # Test 3: Query with text cast
            row = await conn.fetchrow(
                "SELECT analysis_id, symbol FROM analysis_log WHERE analysis_id::text = $1",
                analysis_id
            )

            return {
                "analysis_id": analysis_id,
                "total_analyses": count,
                "found": row is not None,
                "row_data": dict(row) if row else None
            }
    except Exception as e:
        import traceback
        return {
            "error": str(e),
            "traceback": traceback.format_exc()
        }


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "name": settings.API_TITLE,
        "version": settings.API_VERSION,
        "docs": "/docs",
        "health": "/health",
    }


# Include routers
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["analysis"])
app.include_router(calendar.router, prefix="/api/v1/calendar", tags=["calendar"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
app.include_router(news.router, prefix="/api/v1/news", tags=["news"])
app.include_router(positions.router, prefix="/api/v1/positions", tags=["positions"])
app.include_router(symbol.router, prefix="/api/v1/symbol", tags=["symbol"])
app.include_router(websocket.router, prefix="/api/v1/ws", tags=["websocket"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "eiqora_v2.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
    )