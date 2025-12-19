from langchain_core.prompts import ChatPromptTemplate
from eiqora.core.state import AgentState
from eiqora.core.llm import get_llm

class DecisionMaker:
    def __init__(self):
        self.llm = get_llm(temperature=0.0)

    def decide(self, state: AgentState) -> dict:
        """
        Makes the final binding decision.
        """
        prompt = ChatPromptTemplate.from_template(
            """You are the Portfolio Manager with final authority.
            
            Plan: {investment_plan}
            Risk Assessment: {risk_assessment}
            
            -- Key Data Checks --
            Technicals: {technical_indicators}
            Earnings: {earnings_reports}
            
            Make a final decision for {ticker}.
            Output format:
            ACTION: [BUY / SELL / HOLD]
            CONFIDENCE: [0-100%]
            REASON: [One sentence summary]
            """
        )
        
        chain = prompt | self.llm
        response = chain.invoke({
            "ticker": state["ticker"],
            "investment_plan": state["investment_plan"],
            "risk_assessment": state["risk_assessment"],
            "technical_indicators": str(state.get("technical_indicators", "N/A")),
            "earnings_reports": str(state.get("earnings_reports", "N/A")),
        })
        
        return {"final_decision": response.content}