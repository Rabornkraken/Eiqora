"""
Supply Chain Agent - analyzes cascading effects through stock relationships.
Identifies how movements in related stocks (suppliers, customers, competitors)
might affect the target symbol.
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.prices import get_indicators


class SupplyChainOutput(BaseModel):
    """Output schema for supply chain analysis."""
    summary: str = Field(..., max_length=500, description="Brief summary of supply chain signals")
    sentiment: str = Field(..., description="Overall sentiment: BULLISH, BEARISH, or NEUTRAL")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in analysis")


class SupplyChainAgent(BaseAgent[SupplyChainOutput]):
    """
    Analyzes supply chain and competitive relationships.
    
    Identifies:
    - Supplier movements (up/down affects input costs)
    - Customer movements (affects demand)
    - Competitor movements (sector momentum or relative weakness)
    """
    
    name = "supply_chain"
    output_schema = SupplyChainOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Fetch relationships and recent price movements."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Get relationships
        relationships = await self._get_relationships(symbol)
        
        # Get recent price movements for related stocks
        related_moves = await self._get_related_moves(relationships, asof_time)
        
        # Get correlations
        correlations = await self._get_correlations(symbol)
        
        return {
            "relationships": relationships,
            "related_moves": related_moves,
            "correlations": correlations,
        }
    
    async def _get_relationships(self, symbol: str) -> list[dict]:
        """Get known stock relationships."""
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT 
                    symbol_to as related_symbol,
                    relationship_type,
                    strength,
                    description
                FROM stock_relationships
                WHERE symbol_from = $1
                ORDER BY strength DESC
            """, symbol)
            
            return [dict(r) for r in rows]
    
    async def _get_related_moves(self, relationships: list[dict], asof_time) -> list[dict]:
        """Get recent price moves for related stocks."""
        moves = []
        for rel in relationships[:10]:  # Top 10 relationships
            related = rel['related_symbol']
            try:
                indicators = await get_indicators(related, 30, asof_time)
                
                if not indicators.get('error'):
                    moves.append({
                        "symbol": related,
                        "relationship": rel['relationship_type'],
                        "strength": float(rel['strength']) if rel['strength'] else None,
                        "ret_20d": indicators.get('ret_20d'),
                        "ret_60d": indicators.get('ret_60d'),
                        "description": rel['description'],
                    })
            except Exception as e:
                self.logger.debug(f"Failed to fetch indicators for {related}: {e}")
                continue
        
        return moves
    
    async def _get_correlations(self, symbol: str) -> list[dict]:
        """Get statistically correlated stocks."""
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT symbol_b as related_symbol, correlation_coef
                FROM stock_correlations
                WHERE symbol_a = $1 
                  AND ABS(correlation_coef) >= 0.6
                  AND calculated_date >= CURRENT_DATE - 7
                ORDER BY ABS(correlation_coef) DESC
                LIMIT 5
            """, symbol)
            
            return [
                {
                    "symbol": r['related_symbol'],
                    "correlation": float(r['correlation_coef']),
                }
                for r in rows
            ]
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build supply chain analysis prompt."""
        symbol = state["symbol"]
        related_moves = data.get("related_moves", [])
        correlations = data.get("correlations", [])
        
        if not related_moves and not correlations:
            return f"No supply chain relationships or correlations found for {symbol}."
        
        prompt = f"# Supply Chain & Relationship Analysis for {symbol}\n\n"
        
        # Add relationship analysis
        if related_moves:
            prompt += "## Known Relationships\n\n"
            
            for move in related_moves:
                rel_type = move['relationship']
                rel_symbol = move['symbol']
                ret_20d = move.get('ret_20d')
                strength = move.get('strength')
                
                prompt += f"### {rel_type}: {rel_symbol}"
                if strength:
                    prompt += f" (strength: {strength:.2f})"
                prompt += "\n"
                
                if ret_20d is not None:
                    direction = "UP" if ret_20d > 0 else "DOWN"
                    prompt += f"- Recent move: {ret_20d*100:+.1f}% (20d)\n"
                    prompt += f"- Description: {move['description']}\n"
                    
                    # Interpretation
                    if rel_type == "SUPPLIER":
                        if ret_20d > 0.05:  # Supplier up 5%+
                            prompt += f"  → **BULLISH** for {symbol}: supplier strength suggests strong demand\n"
                        elif ret_20d < -0.05:
                            prompt += f"  → **BEARISH** for {symbol}: supplier weakness may signal supply issues or weak demand\n"
                    
                    elif rel_type == "CUSTOMER":
                        if ret_20d > 0.05:
                            prompt += f"  → **BULLISH** for {symbol}: customer strength indicates good end-market demand\n"
                        elif ret_20d < -0.05:
                            prompt += f"  → **BEARISH** for {symbol}: customer weakness may reduce orders\n"
                    
                    elif rel_type == "COMPETITOR":
                        if ret_20d > 0.05:
                            prompt += f"  → **SECTOR MOMENTUM**: competitors rising suggests broad sector strength\n"
                        elif ret_20d < -0.05:
                            prompt += f"  → **RELATIVE OPPORTUNITY**: {symbol} may outperform if fundamentals stronger\n"
                
                else:
                    prompt += "- (No recent price data available)\n"
                
                prompt += "\n"
        
        # Add correlation analysis
        if correlations:
            prompt += "## Statistical Correlations (90-day)\n\n"
            for corr_data in correlations:
                corr = corr_data['correlation']
                prompt += f"- **{corr_data['symbol']}**: {corr:+.2f} correlation\n"
            
            prompt += "\n*High correlations suggest these stocks move together. "
            prompt += "If correlated stocks are showing strength, it confirms sector momentum.*\n\n"
        
        prompt += "---\n\n"
        prompt += "**Task**: Synthesize these supply chain and correlation signals. "
        prompt += "Do the related stock movements support or contradict your thesis?\n"
        
        return prompt
    
    def _get_system_prompt(self) -> str:
        """System prompt for supply chain analysis."""
        return """You are analyzing supply chain and competitive relationships.

Your output should be valid JSON matching this schema:
{
  "summary": "<2-3 sentence summary of supply chain signals, max 500 chars>",
  "sentiment": "BULLISH|BEARISH|NEUTRAL",
  "confidence": <0.0-1.0>
}

Identify the most important relationship signals and state whether they are BULLISH, BEARISH, or NEUTRAL for the trade.
Note any red flags or confirmation signals. Be specific about which relationships matter most and why.

Return ONLY valid JSON. No markdown, no explanation."""
