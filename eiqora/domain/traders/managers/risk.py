from langchain_core.prompts import ChatPromptTemplate
from eiqora.core.state import AgentState
from eiqora.core.llm import get_llm

class RiskManager:
    def __init__(self):
        self.llm = get_llm(temperature=0.0)

    def assess(self, state: AgentState) -> dict:
        """
        Critiques the proposed plan from a risk perspective.
        """
        prompt = ChatPromptTemplate.from_template(
            """You are the Chief Risk Officer. Review the proposed trading plan for {ticker}.
            
            -- Proposed Plan --
            {investment_plan}
            
            -- Risk Factors --
            Technicals: {technical_indicators}
            Earnings Calendar: {earnings_reports}
            Fundamentals: {market_data}
            
            Identify potential pitfalls.
            1. EVENT RISK: Assess the timing of upcoming earnings/events and their potential for volatility shocks.
            2. TECHNICAL RISK: Evaluate if the price action is extended or showing signs of exhaustion based on the indicators.
            3. LOGIC GAP: Does the plan ignore key data points?
            
            Output a rigorous risk assessment.
            """
        )
        
        chain = prompt | self.llm
        response = chain.invoke({
            "ticker": state["ticker"],
            "investment_plan": state["investment_plan"],
            "market_data": str(state.get("market_data", "N/A")),
            "technical_indicators": str(state.get("technical_indicators", "N/A")),
            "earnings_reports": str(state.get("earnings_reports", "N/A")),
        })
        
        return {"risk_assessment": response.content}