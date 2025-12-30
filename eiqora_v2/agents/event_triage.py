"""
Event Triage Agent implementation.
Maps triggers and documents to canonical event types and priorities.
"""

from datetime import timedelta
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.triage import TriageOutput
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.documents import get_documents, count_recent_documents
from eiqora_v2.tools.events import get_sec_filings


class EventTriageAgent(BaseAgent[TriageOutput]):
    """
    Event Triage Agent: classifies trigger + docs into event type and priority.
    
    Responsibilities:
    - Map trigger type to canonical event type
    - Assess document freshness and relevance
    - Determine if Event Extractor should run
    - Set processing priority
    """
    
    name = "event_triage"
    output_schema = TriageOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Fetch recent documents and filings."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        trigger_type = state.get("trigger_type", "CHART_SETUP")
        
        # Get document counts by type
        doc_counts = await count_recent_documents(symbol, window_hours=24, asof_time=asof_time)
        
        # Get recent documents (summaries only)
        recent_docs = await get_documents(
            symbol=symbol,
            window_hours=24,
            asof_time=asof_time,
            limit=10,
        )
        
        # Get recent SEC filings
        sec_filings = await get_sec_filings(
            symbol=symbol,
            window_days=7,
            asof_time=asof_time,
        )
        
        return {
            "trigger_type": trigger_type,
            "doc_counts": doc_counts,
            "recent_docs": recent_docs,
            "sec_filings": sec_filings,
            "total_docs": sum(doc_counts.values()) if doc_counts else 0,
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt with document and filing information."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        trigger_type = data.get("trigger_type", "CHART_SETUP")
        
        # Format recent documents
        docs_text = self._format_documents(data.get("recent_docs", []))
        
        # Format SEC filings
        filings_text = self._format_filings(data.get("sec_filings", []))
        
        return f"""
Triage event for {symbol} as of {asof_time}.

TRIGGER TYPE: {trigger_type}

DOCUMENT COUNTS (last 24h):
{self._format_doc_counts(data.get('doc_counts', {}))}
Total documents: {data.get('total_docs', 0)}

RECENT DOCUMENTS:
{docs_text if docs_text else "No recent documents found."}

RECENT SEC FILINGS (last 7 days):
{filings_text if filings_text else "No recent SEC filings."}

Based on the trigger and available documents:
1. Classify the event type
2. Set priority based on materiality and freshness
3. Determine if Event Extractor should run (needs_extraction)
4. Calculate freshness_hours from most recent relevant document
"""
    
    def _format_doc_counts(self, counts: dict) -> str:
        """Format document counts by type."""
        if not counts:
            return "  No documents"
        return "\n".join(f"  - {doc_type}: {count}" for doc_type, count in counts.items())
    
    def _format_documents(self, docs: list[dict]) -> str:
        """Format document list for prompt."""
        if not docs:
            return ""
        
        lines = []
        for doc in docs[:5]:  # Limit to 5 in prompt
            title = doc.get("title", "Untitled")[:100]
            doc_type = doc.get("doc_type", "UNKNOWN")
            published = doc.get("published_at", "N/A")
            lines.append(f"  [{doc_type}] {title}")
            lines.append(f"    Published: {published}")
            lines.append(f"    ID: doc:{doc.get('doc_id', 'N/A')}")
        
        return "\n".join(lines)
    
    def _format_filings(self, filings: list[dict]) -> str:
        """Format SEC filings for prompt."""
        if not filings:
            return ""
        
        lines = []
        for filing in filings[:3]:  # Limit to 3 in prompt
            form_type = filing.get("form_type", "UNKNOWN")
            filed_at = filing.get("filed_at", "N/A")
            accession = filing.get("accession", "N/A")
            lines.append(f"  [{form_type}] Filed: {filed_at} (Accession: {accession})")
        
        return "\n".join(lines)
    
    def _get_system_prompt(self) -> str:
        return """You are an Event Triage Agent that classifies events and sets processing priorities.

EVENT TYPES (choose one):
- EARNINGS: Earnings release or earnings-related news
- GUIDANCE: Forward guidance update (raised, lowered, reaffirmed)
- LEGAL: Legal, regulatory, or compliance issues
- MNA: Mergers, acquisitions, or strategic transactions
- MGMT: Management changes (CEO, CFO departures/appointments)
- INSIDER: Significant insider transactions
- MACRO: Macroeconomic events affecting the stock
- TECHNICAL_ONLY: No fundamental catalyst, chart-driven only
- UNKNOWN: Cannot classify

PRIORITY RULES:
- HIGH: Material events (8-K filings, guidance changes, M&A, management changes)
- MED: Relevant but not urgent (analyst coverage, industry news)
- LOW: Background noise or stale information

NEEDS_EXTRACTION:
- Set to true if documents contain substantive text that should be parsed
- Set to false for TECHNICAL_ONLY or when no meaningful documents exist

OUTPUT SCHEMA:
{
  "event_type": "<from list above>",
  "priority": "HIGH|MED|LOW",
  "freshness_hours": <float>,
  "doc_ids": ["doc:123", "doc:456"],
  "needs_extraction": <bool>,
  "reason": "<brief explanation, max 200 chars>",
  "confidence": <0.0-1.0>
}

Return ONLY valid JSON. No explanation."""
    
    def _build_state_update(self, state: SwingTradeState, result: TriageOutput) -> dict[str, Any]:
        """Build state update with triage output."""
        return {
            "triage": result.model_dump(),
            "needs_extraction": result.needs_extraction,
        }
