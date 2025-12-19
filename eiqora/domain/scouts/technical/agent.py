from typing import Dict, Any
from eiqora.domain.scouts.base import BaseScout
from eiqora.domain.scouts.technical.tools import get_technical_indicators

class TechnicalScout(BaseScout):
    def __init__(self):
        super().__init__()
        # Technical analysis is deterministic/math-based, so we might not need an LLM 
        # for the 'gathering' phase, but we can use one to interpret if needed.
        # For now, we return the raw indicators directly.

    def gather(self, ticker: str) -> Dict[str, Any]:
        """
        Calculates technical indicators.
        """
        indicators = get_technical_indicators(ticker)
        return indicators
