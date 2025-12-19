from typing import Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from eiqora.domain.scouts.base import BaseScout
from eiqora.domain.scouts.market.tools import get_stock_info, get_stock_history
from eiqora.core.llm import get_llm

class MarketScout(BaseScout):
    def __init__(self):
        super().__init__()
        self.llm = get_llm(temperature=0.0)

    def gather(self, ticker: str) -> Dict[str, Any]:
        """
        Fetches market data and generates a brief technical/fundamental summary.
        """
        # 1. Gather raw data
        info = get_stock_info(ticker)
        history = get_stock_history(ticker, period="1mo")
        
        # 2. Synthesize using LLM
        summary = self._summarize_financials(ticker, info, history)
        
        return {
            "raw_info": info,
            "raw_history_summary": history[:500] + "...", # Truncate for state size
            "summary": summary
        }

    def _summarize_financials(self, ticker: str, info: Dict, history: str) -> str:
        prompt = ChatPromptTemplate.from_template(
            """You are a senior financial analyst. 
            Analyze the following data for {ticker}.
            
            Fundamental Data: {info}
            
            Recent Price History (CSV):
            {history}
            
            Provide a concise, bullet-point summary of the financial health and recent price trend. 
            Focus on valuation (P/E), growth, and momentum.
            """
        )
        chain = prompt | self.llm
        try:
            res = chain.invoke({"ticker": ticker, "info": str(info), "history": history})
            return res.content
        except Exception as e:
            return f"Analysis failed: {str(e)}"
