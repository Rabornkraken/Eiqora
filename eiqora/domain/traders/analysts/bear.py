from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from eiqora.core.state import AgentState
from eiqora.core.llm import get_llm

class BearAnalyst:
    def __init__(self):
        self.llm = get_llm(temperature=0.3)

    def argue(self, state: AgentState, mode: str = "opening") -> dict:
        """
        Generates a bearish argument based on the gathered data.
        """
        if mode == "opening":
            prompt = ChatPromptTemplate.from_template(
                """You are a Bearish Financial Analyst. Your job is to make the STRONGEST case to SELL/SHORT {ticker}.
                
                -- Fundamental Data --
                {market_data}
                
                -- Technical Indicators (RSI, MACD, Trend) --
                {technical_indicators}
                
                -- Earnings & Events --
                {earnings_reports}
                
                -- News & Sentiment --
                News: {news_summary}
                Sentiment: {sentiment_analysis}
                
                -- Historical Context --
                {relevant_memories}
                
                Construct a strong, data-backed argument for a short position.
                1. EXPOSE technical weakness or trend deterioration found in the indicators.
                2. WARN about earnings or event-driven risks.
                3. COMBINE with fundamentals and news.
                
                Keep it professional but persuasive. Be concise but thorough.
                """
            )
            history_context = []
        else:
            prompt = ChatPromptTemplate.from_template(
                """You are a Bearish Financial Analyst providing a REBUTTAL. Your job is to counter the bullish argument and reinforce the SELL/AVOID case for {ticker}.
                
                -- Data Context --
                Fundamentals: {market_data}
                Technicals: {technical_indicators}
                Earnings: {earnings_reports}
                News/Sentiment: {news_summary} | {sentiment_analysis}
                
                -- THE BULL ANALYST ARGUED --
                {opponent_argument}
                
                -- Historical Context --
                {relevant_memories}
                
                Counter each of the bull's points with data and logic.
                Use the Technicals and Earnings data to refute their claims.
                Reinforce why {ticker} should be SOLD or AVOIDED. Be persuasive and specific.
                """
            )
            debate_history = state.get("debate_history", [])
            bull_opening = ""
            for msg in debate_history:
                content = msg.content if hasattr(msg, 'content') else str(msg)
                if "**BULL" in content and "OPENING" in content:
                    bull_opening = content.replace("**BULL OPENING:**", "").strip()
                    break
            history_context = bull_opening
        
        chain = prompt | self.llm
        
        invoke_params = {
            "ticker": state["ticker"],
            "market_data": str(state.get("market_data", "N/A")),
            "technical_indicators": str(state.get("technical_indicators", "N/A")),
            "earnings_reports": str(state.get("earnings_reports", "N/A")),
            "news_summary": state.get("news_summary", "N/A"),
            "sentiment_analysis": str(state.get("sentiment_analysis", "N/A")),
            "relevant_memories": "\n".join(state.get("relevant_memories", []) or ["No prior history."])
        }
        
        if mode == "rebuttal":
            invoke_params["opponent_argument"] = history_context
        
        response = chain.invoke(invoke_params)
        
        label = "**BEAR OPENING:**" if mode == "opening" else "**BEAR REBUTTAL:**"
        return {"debate_history": [AIMessage(content=f"{label} {response.content}")]}