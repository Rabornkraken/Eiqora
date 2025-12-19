from typing import Any, Dict
from langchain_core.prompts import ChatPromptTemplate
from eiqora.domain.scouts.base import BaseScout
from eiqora.tools.search import search_web
from eiqora.core.llm import get_llm

class SentimentScout(BaseScout):
    def __init__(self):
        super().__init__()
        self.llm = get_llm(temperature=0.0)

    def gather(self, ticker: str) -> Dict[str, Any]:
        """
        Gauges 'Retail Sentiment' by looking at Reddit/forum discussions via search.
        Returns both raw sources and analysis.
        """
        # 1. Search for discussions
        query = f"site:reddit.com {ticker} stock discussion analysis sentiment"
        results = search_web(query, max_results=5)
        
        # 2. Format raw results for transparency
        raw_sources = [
            {"url": r.get("url", ""), "snippet": r.get("content", "")[:300]}
            for r in results
        ]
        
        # 3. Analyze Sentiment
        analysis = self._analyze_sentiment(ticker, results)
        
        return {
            "raw_sources": raw_sources,
            "analysis": analysis
        }

    def _analyze_sentiment(self, ticker: str, results: list) -> Dict[str, Any]:
        context = "\n\n".join([f"Snippet: {r['content']}" for r in results])
        
        prompt = ChatPromptTemplate.from_template(
            """You are a Sentiment Analyst.
            Review the following social media/forum snippets for {ticker}.
            
            Snippets:
            {context}
            
            Output a JSON-like response (do not use code blocks, just keys/values) with:
            - bullish_score: (0.0 to 1.0)
            - main_concerns: (List of strings)
            - main_hype: (List of strings)
            """
        )
        chain = prompt | self.llm
        try:
            res = chain.invoke({"ticker": ticker, "context": context})
            return res.content
        except Exception as e:
            return {"error": str(e)}
