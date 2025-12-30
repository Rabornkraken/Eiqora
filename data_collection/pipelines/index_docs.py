"""Document indexing pipeline: chunking, embeddings, FTS."""

from __future__ import annotations

import argparse
from typing import Iterable

import tiktoken
from sentence_transformers import SentenceTransformer

from data_collection.common.settings import load_config
from data_collection.db.connection import get_connection


def _chunk_text(text: str, encoder, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    tokens = encoder.encode(text)
    if not tokens:
        return []
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + chunk_tokens)
        chunk_tokens_list = tokens[start:end]
        chunks.append(encoder.decode(chunk_tokens_list))
        if end == len(tokens):
            break
        start = end - overlap_tokens
        if start < 0:
            start = 0
    return chunks


def _upsert_fts(conn, doc_id: int, text: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO document_fts (doc_id, tsv)
            VALUES (%s, to_tsvector('english', %s))
            ON CONFLICT (doc_id)
            DO UPDATE SET tsv = EXCLUDED.tsv
            """,
            (doc_id, text),
        )


def run() -> None:
    config = load_config()
    embed_cfg = config["embeddings"]

    enabled = bool(embed_cfg.get("enabled", False))
    store_in_pgvector = bool(embed_cfg.get("store_in_pgvector", True))
    model_name = embed_cfg.get("model", "all-MiniLM-L6-v2")
    chunk_tokens = int(embed_cfg.get("chunk_tokens", 700))
    overlap_tokens = int(embed_cfg.get("overlap_tokens", 80))

    encoder = tiktoken.get_encoding("cl100k_base")
    model = SentenceTransformer(model_name) if enabled and store_in_pgvector else None

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT doc_id, text
                FROM document
                WHERE text IS NOT NULL AND length(text) >= 200
                """
            )
            docs = cursor.fetchall()

        for doc_id, text in docs:
            _upsert_fts(conn, doc_id, text)

            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM document_chunk WHERE doc_id = %s", (doc_id,))

            chunks = _chunk_text(text, encoder, chunk_tokens, overlap_tokens)
            if not chunks:
                continue

            embeddings = []
            if enabled and store_in_pgvector:
                embeddings = model.encode(chunks, normalize_embeddings=False).tolist()
            else:
                embeddings = [None] * len(chunks)

            with conn.cursor() as cursor:
                for idx, chunk in enumerate(chunks):
                    cursor.execute(
                        """
                        INSERT INTO document_chunk
                            (doc_id, chunk_index, text, token_count, embedding, active)
                        VALUES (%s, %s, %s, %s, %s, true)
                        """,
                        (doc_id, idx, chunk, len(encoder.encode(chunk)), embeddings[idx]),
                    )

        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Index documents into FTS + pgvector")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Index documents")
    args = parser.parse_args()
    if args.command == "run":
        run()


if __name__ == "__main__":
    main()
