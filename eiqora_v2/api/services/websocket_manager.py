"""
WebSocket Connection Manager
Manages WebSocket connections and broadcasts events
"""
from fastapi import WebSocket
from typing import Dict, Set
import json
import asyncio
import logging

from ..models.websocket import WebSocketMessage

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts"""

    def __init__(self):
        # Active connections organized by channel
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str):
        """
        Accept a WebSocket connection and add it to a channel

        Args:
            websocket: WebSocket connection
            channel: Channel name (e.g., "analysis:{analysis_id}")
        """
        await websocket.accept()

        if channel not in self.active_connections:
            self.active_connections[channel] = set()

        self.active_connections[channel].add(websocket)
        logger.info(f"WebSocket connected to channel: {channel}")

    async def disconnect(self, websocket: WebSocket, channel: str):
        """
        Remove a WebSocket connection from a channel

        Args:
            websocket: WebSocket connection
            channel: Channel name
        """
        if channel in self.active_connections:
            self.active_connections[channel].discard(websocket)
            logger.info(f"WebSocket disconnected from channel: {channel}")

            # Clean up empty channels
            if not self.active_connections[channel]:
                del self.active_connections[channel]

    async def broadcast(self, channel: str, message: WebSocketMessage):
        """
        Broadcast a message to all connections in a channel

        Args:
            channel: Channel name
            message: WebSocket message to broadcast
        """
        if channel not in self.active_connections:
            return

        disconnected = set()

        for connection in self.active_connections[channel]:
            try:
                await connection.send_json(message.model_dump())
            except Exception as e:
                logger.error(f"Error sending to WebSocket: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.active_connections[channel].discard(conn)

        # Clean up empty channels
        if channel in self.active_connections and not self.active_connections[channel]:
            del self.active_connections[channel]

    async def send_to_connection(self, websocket: WebSocket, message: WebSocketMessage):
        """
        Send a message to a specific connection

        Args:
            websocket: WebSocket connection
            message: WebSocket message to send
        """
        try:
            await websocket.send_json(message.model_dump())
        except Exception as e:
            logger.error(f"Error sending to WebSocket: {e}")

    def get_connection_count(self, channel: str) -> int:
        """
        Get the number of active connections in a channel

        Args:
            channel: Channel name

        Returns:
            Number of active connections
        """
        return len(self.active_connections.get(channel, set()))

    def get_all_channels(self) -> Set[str]:
        """
        Get all active channels

        Returns:
            Set of channel names
        """
        return set(self.active_connections.keys())


# Singleton instance
websocket_manager = WebSocketManager()