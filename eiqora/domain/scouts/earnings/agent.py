from typing import Dict, Any
from eiqora.domain.scouts.base import BaseScout
from eiqora.domain.scouts.earnings.tools import get_earnings_data

class EarningsScout(BaseScout):
    def __init__(self):
        super().__init__()

    def gather(self, ticker: str) -> Dict[str, Any]:
        """
        Fetches earnings calendar and surprise history.
        """
        return get_earnings_data(ticker)
