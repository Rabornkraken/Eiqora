from langchain_core.prompts import ChatPromptTemplate
from eiqora.core.state import AgentState
from eiqora.core.llm import get_llm

class Trader:
    def __init__(self):
        self.llm = get_llm(temperature=0.1)

    def plan(self, state: AgentState) -> dict:
        """
        Synthesizes the debate into a concrete trading plan.
        """
        prompt = ChatPromptTemplate.from_template(
            """You are the Head Trader. You have listened to the debate between Bull and Bear for {ticker}.
            
            -- Debate Transcript --
            {debate_history}
            
            -- Hard Data --
            Fundamentals: {market_data}
            Technicals: {technical_indicators}
            Earnings: {earnings_reports}
            
            Formulate a preliminary Investment Plan.
            1. Direction: (Long/Short/Stay Away)
            2. Rationale: (Synthesize the best arguments from the debate)
            3. Timing/Entry: Utilize the provided technical indicators to suggest optimal entry/exit points.
            4. Events: Note any upcoming earnings risks.
            """
        )
        
        chain = prompt | self.llm
        response = chain.invoke({
            "ticker": state["ticker"],
            "debate_history": state["debate_history"],
            "market_data": str(state.get("market_data", "N/A")),
            "technical_indicators": str(state.get("technical_indicators", "N/A")),
            "earnings_reports": str(state.get("earnings_reports", "N/A")),
        })
        
        return {"investment_plan": response.content}