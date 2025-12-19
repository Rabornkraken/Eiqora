from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseScout(ABC):
    """
    Abstract base class for all Scout agents.
    Scouts are responsible for gathering specific types of data (Market, News, Sentiment).
    """
    
    def __init__(self):
        self.name = self.__class__.__name__

    @abstractmethod
    def gather(self, ticker: str) -> Any:
        """
        Main entry point for the scout.
        Args:
            ticker: The stock ticker symbol to research.
        Returns:
            The gathered data, ready to be merged into AgentState.
        """
        pass
