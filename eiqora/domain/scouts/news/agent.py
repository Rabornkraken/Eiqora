from typing import Any, Dict
from langchain_core.prompts import ChatPromptTemplate
from eiqora.domain.scouts.base import BaseScout
from eiqora.tools.search import search_web
from eiqora.core.llm import get_llm

class NewsScout(BaseScout):
    def __init__(self):
        super().__init__()
        self.llm = get_llm(temperature=0.0)

    def gather(self, ticker: str) -> Dict[str, Any]:
        """
        Searches for recent news and summarizes key events.
        Returns both raw search results and LLM summary.
        """
        # 1. Search
        query = f"latest financial news and major events for {ticker} stock last 2 weeks"
        results = search_web(query, max_results=5)
        
        # 2. Format raw results for transparency
        raw_sources = [
            {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("content", "")[:200]}
            for r in results
        ]
        
        # 3. Synthesize
        summary = self._summarize_news(ticker, results)
        
        return {
            "raw_sources": raw_sources,
            "summary": summary
        }

    def _summarize_news(self, ticker: str, results: list) -> str:
        context = "\n\n".join([f"Source: {r['url']}\nContent: {r['content']}" for r in results])
        
        prompt = ChatPromptTemplate.from_template(
            """You are a news researcher. 
            Summarize the following search results for {ticker} into a concise briefing.
            Identify:
            1. Major recent events (earnings, product launches, scandals).
            2. General media sentiment (positive/negative).
            
            Results:
            {context}
            """
        )
        chain = prompt | self.llm
        try:
            res = chain.invoke({"ticker": ticker, "context": context})
            return res.content
        except Exception as e:
            return f"News summarization failed: {str(e)}"
