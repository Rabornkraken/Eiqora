"""
Event Extractor Agent implementation.
Extracts structured facts from documents identified by Event Triage.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.extractor import EventExtractorOutput
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.documents import get_document_by_id, get_documents


class EventExtractorAgent(BaseAgent[EventExtractorOutput]):
    """
    Event Extractor Agent: extracts structured facts from documents.
    
    Only runs if needs_extraction=True from Event Triage.
    Processes documents identified in triage.doc_ids.
    """
    
    name = "event_extractor"
    output_schema = EventExtractorOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Fetch full document text for extraction."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        triage = state.get("triage", {})
        
        doc_ids = triage.get("doc_ids", [])
        documents = []
        
        # Fetch documents by ID if available
        for doc_ref in doc_ids[:5]:  # Limit to 5 docs
            if doc_ref.startswith("doc:"):
                doc_id = int(doc_ref.split(":")[1])
                doc = await get_document_by_id(doc_id)
                if doc:
                    documents.append(doc)
        
        # Fallback: get recent documents if no specific IDs
        if not documents:
            documents = await get_documents(
                symbol=symbol,
                window_hours=24,
                asof_time=asof_time,
                limit=5,
            )
        
        return {
            "documents": documents,
            "event_type": triage.get("event_type", "UNKNOWN"),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt with document text for extraction."""
        symbol = state["symbol"]
        event_type = data.get("event_type", "UNKNOWN")
        documents = data.get("documents", [])
        
        if not documents:
            return f"""
No documents available for extraction for {symbol}.
Return an empty extraction with event_summary indicating no documents.
Set sentiment to NEUTRAL and materiality to LOW.
"""
        
        docs_text = self._format_documents(documents)
        
        return f"""
Extract structured facts from the following documents for {symbol}.
Trigger event type: {event_type}

DOCUMENTS:
{docs_text}

Extract:
1. A concise event summary (one line)
2. Key facts with confidence scores
3. Guidance changes if any (for EARNINGS/GUIDANCE events)
4. Transaction details if any (for MNA/INSIDER events)
5. Overall sentiment and materiality assessment
6. Catalyst date if identifiable
"""
    
    def _format_documents(self, documents: list[dict]) -> str:
        """Format documents for the prompt."""
        lines = []
        for i, doc in enumerate(documents, 1):
            lines.append(f"--- Document {i} ---")
            lines.append(f"ID: doc:{doc.get('doc_id', 'N/A')}")
            lines.append(f"Type: {doc.get('doc_type', 'UNKNOWN')}")
            lines.append(f"Title: {doc.get('title', 'Untitled')}")
            lines.append(f"Published: {doc.get('published_at', 'N/A')}")
            lines.append("")
            # Truncate text to avoid token limits
            text = doc.get("text", doc.get("text_preview", ""))
            if text:
                lines.append(text[:3000])  # First 3000 chars
            lines.append("")
        return "\n".join(lines)
    
    def _get_system_prompt(self) -> str:
        return """You are an Event Extractor Agent that extracts structured facts from financial documents.

EXTRACTION RULES:
1. Extract only verifiable facts from the text, not speculation
2. For guidance changes, capture direction (RAISE/LOWER/MAINTAIN) and metric
3. For transactions, capture counterparty and value if disclosed
4. Set confidence based on how explicit the information is in the text
5. Materiality: HIGH for material 8-K items, M&A, management changes; MEDIUM for guidance; LOW for routine

OUTPUT SCHEMA:
{
  "event_summary": "<one-line summary, max 300 chars>",
  "facts": [
    {"fact_type": "<type>", "description": "<text>", "source_doc_id": "doc:123", "confidence": 0.9}
  ],
  "guidance_changes": [
    {"metric": "revenue", "direction": "RAISE|LOWER|MAINTAIN", "magnitude": "5%" }
  ],
  "transactions": [
    {"counterparty": "<name>", "value": "$1B", "transaction_type": "acquisition"}
  ],
  "sentiment": "POSITIVE|NEGATIVE|NEUTRAL|MIXED",
  "materiality": "HIGH|MEDIUM|LOW",
  "catalyst_date": "YYYY-MM-DD" or null
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: EventExtractorOutput) -> dict[str, Any]:
        """Build state update with extracted facts."""
        return {"facts": result.model_dump()}
