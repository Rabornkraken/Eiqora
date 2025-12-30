"""
Base agent class for all LLM agents.
Provides common functionality for prompting, validation, and error handling.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from eiqora_v2.llm.client import call_llm
from eiqora_v2.schemas.state import SwingTradeState

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class BaseAgent(ABC, Generic[T]):
    """
    Abstract base class for LLM agents.
    
    Each agent:
    1. Takes SwingTradeState as input
    2. Calls tools to gather data
    3. Constructs a prompt
    4. Calls LLM with structured output
    5. Returns validated output + state updates
    """
    
    name: str = "base_agent"
    output_schema: type[T]
    
    def __init__(self):
        self.logger = logging.getLogger(f"eiqora_v2.agents.{self.name}")
    
    @abstractmethod
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """
        Gather data from tools needed for this agent.
        Returns a dict that will be available in _build_prompt.
        """
        pass
    
    @abstractmethod
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """
        Build the user prompt for the LLM.
        Should include all context needed for the agent to do its job.
        """
        pass
    
    def _get_system_prompt(self) -> str:
        """
        Get the system prompt for this agent.
        Override for agent-specific system prompts.
        """
        return (
            "You are a financial analysis agent in a multi-agent trading system.\n"
            "CRITICAL RULES:\n"
            "1. Return ONLY valid JSON matching the provided schema. No markdown, no explanation.\n"
            "2. Never invent performance metrics (win rate, expected return).\n"
            "3. If uncertain, set quality flags; do not guess.\n"
            "4. All times are UTC. Never use data after asof_time."
        )
    
    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """
        Execute the agent and return state updates.
        
        Returns:
            Dict with state updates to merge into SwingTradeState
        """
        try:
            self.logger.info(f"Running {self.name} for {state.get('symbol')}")
            
            # Gather data from tools
            data = await self._gather_data(state)
            
            # Check for data errors
            if data.get("error"):
                self.logger.warning(f"Data error: {data['error']}")
                return {
                    self.name: {"error": data["error"]},
                    "errors": state.get("errors", []) + [f"{self.name}: {data['error']}"],
                }
            
            # Build prompt
            prompt = self._build_prompt(state, data)
            
            # Call LLM
            result = await call_llm(
                prompt=prompt,
                schema=self.output_schema,
                system_prompt=self._get_system_prompt(),
            )
            
            self.logger.info(f"{self.name} completed successfully")
            
            # Return state update
            return self._build_state_update(state, result)
            
        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                self.name: {"error": str(e)},
                "errors": state.get("errors", []) + [f"{self.name}: {str(e)}"],
            }
    
    def _build_state_update(self, state: SwingTradeState, result: T) -> dict[str, Any]:
        """
        Build the state update dict from the agent result.
        Override for agents that need custom state updates.
        """
        return {self.name: result.model_dump()}
