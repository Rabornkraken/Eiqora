"""
WebSocket Router - WebSocket endpoints for real-time updates
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional

from ..services.websocket_manager import websocket_manager
from ..models.websocket import AnalysisProgressMessage, ErrorMessage

router = APIRouter()


@router.websocket("/analysis/{analysis_id}")
async def analysis_websocket(
    websocket: WebSocket,
    analysis_id: str,
):
    """
    WebSocket endpoint for analysis progress updates

    Connect to this endpoint to receive real-time updates during analysis execution.
    The channel name is "analysis:{analysis_id}".

    Args:
        websocket: WebSocket connection
        analysis_id: Analysis identifier to track
    """
    channel = f"analysis:{analysis_id}"

    await websocket_manager.connect(websocket, channel)

    try:
        # Send initial connection message
        await websocket_manager.send_to_connection(
            websocket,
            AnalysisProgressMessage(
                event_type="analysis_progress",
                data={
                    "analysis_id": analysis_id,
                    "status": "connected",
                    "message": "Connected to analysis updates",
                },
            ),
        )

        # Keep connection alive and handle incoming messages
        while True:
            # Receive message from client (keepalive or control messages)
            data = await websocket.receive_text()

            # Echo back or handle control messages
            # For now, we just keep the connection alive

    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket, channel)
    except Exception as e:
        # Send error message before disconnecting
        try:
            await websocket_manager.send_to_connection(
                websocket,
                ErrorMessage(
                    event_type="error",
                    data={"message": str(e), "analysis_id": analysis_id},
                ),
            )
        except:
            pass
        await websocket_manager.disconnect(websocket, channel)


@router.websocket("/triggers")
async def triggers_websocket(
    websocket: WebSocket,
):
    """
    WebSocket endpoint for trigger detection updates

    Connect to this endpoint to receive real-time updates when new triggers are detected.
    The channel name is "triggers".

    Args:
        websocket: WebSocket connection
    """
    channel = "triggers"

    await websocket_manager.connect(websocket, channel)

    try:
        # Send initial connection message
        await websocket_manager.send_to_connection(
            websocket,
            AnalysisProgressMessage(
                event_type="trigger_detected",
                data={
                    "status": "connected",
                    "message": "Connected to trigger updates",
                },
            ),
        )

        # Keep connection alive
        while True:
            data = await websocket.receive_text()

    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket, channel)
    except Exception as e:
        try:
            await websocket_manager.send_to_connection(
                websocket,
                ErrorMessage(
                    event_type="error",
                    data={"message": str(e)},
                ),
            )
        except:
            pass
        await websocket_manager.disconnect(websocket, channel)


@router.websocket("/positions")
async def positions_websocket(
    websocket: WebSocket,
):
    """
    WebSocket endpoint for position updates

    Connect to this endpoint to receive real-time updates for positions.
    The channel name is "positions".

    Args:
        websocket: WebSocket connection
    """
    channel = "positions"

    await websocket_manager.connect(websocket, channel)

    try:
        # Send initial connection message
        await websocket_manager.send_to_connection(
            websocket,
            AnalysisProgressMessage(
                event_type="position_update",
                data={
                    "status": "connected",
                    "message": "Connected to position updates",
                },
            ),
        )

        # Keep connection alive
        while True:
            data = await websocket.receive_text()

    except WebSocketDisconnect:
        await websocket_manager.disconnect(websocket, channel)
    except Exception as e:
        try:
            await websocket_manager.send_to_connection(
                websocket,
                ErrorMessage(
                    event_type="error",
                    data={"message": str(e)},
                ),
            )
        except:
            pass
        await websocket_manager.disconnect(websocket, channel)