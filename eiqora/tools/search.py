from typing import List, Dict, Any
from tavily import TavilyClient
from eiqora.config.settings import settings

def search_web(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Performs a web search using Tavily.
    """
    if not settings.TAVILY_API_KEY:
        return [{"url": "error", "content": "TAVILY_API_KEY not set."}]
        
    try:
        client = TavilyClient(api_key=settings.TAVILY_API_KEY)
        response = client.search(query, search_depth="advanced", max_results=max_results)
        return response.get("results", [])
    except Exception as e:
        return [{"url": "error", "content": f"Search failed: {str(e)}"}]
