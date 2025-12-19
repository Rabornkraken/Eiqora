import chromadb
from chromadb.config import Settings
from typing import List, Dict
import uuid

class MemoryStore:
    def __init__(self, persist_dir: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(name="financial_memories")

    def add_memory(self, ticker: str, situation: str, outcome: str, embedding_text: str):
        """
        Saves a scenario to memory.
        args:
            situation: Description of market conditions (Market Scout summary)
            outcome: What happened / The decision made
            embedding_text: The text used for vector search (usually situation + ticker)
        """
        self.collection.add(
            documents=[embedding_text],
            metadatas=[{"ticker": ticker, "situation": situation, "outcome": outcome}],
            ids=[str(uuid.uuid4())]
        )

    def retrieve_similar(self, query: str, n_results: int = 3) -> List[Dict]:
        """
        Finds similar historical situations.
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        
        memories = []
        if results['documents']:
            for i in range(len(results['documents'][0])):
                memories.append({
                    "text": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i]
                })
        return memories

# Singleton instance
memory_store = MemoryStore()
