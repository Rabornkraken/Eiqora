"""
FastAPI Main Application for Eiqora v2
Exposes REST API and WebSocket endpoints for the frontend
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from .config import settings


# Router imports
from .routers import analysis, dashboard, positions, websocket


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    print(f"🚀 Starting {settings.API_TITLE} v{settings.API_VERSION}")
    print(f"📍 API running on http://{settings.API_HOST}:{settings.API_PORT}")
    yield
    # Shutdown
    print("🛑 Shutting down API server")


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

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
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
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
app.include_router(positions.router, prefix="/api/v1/positions", tags=["positions"])
app.include_router(websocket.router, prefix="/api/v1/ws", tags=["websocket"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "eiqora_v2.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
    )