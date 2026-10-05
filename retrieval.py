#!/usr/bin/env python3
"""
Module 2: Reusable Retrieval Module.

Shared Contract Compliance:
- Function signature: def retrieve(query: str, top_k: int = 5) -> list[dict]
- Returns list of: {"text": str, "title": str, "source_url": str, "doc_id": str, "chunk_index": int}
  sorted by relevance.
- Connects to persistent Chroma client at ./vectordb/chroma_store (or CHROMA_PERSIST_DIR)
- Collection name: 'cdc_regulatory_docs'
- Self-contained and dependency-light: can be imported directly or copied into /backend.
- CLI sanity-check test block under `if __name__ == "__main__":`.
"""

import os
import sys
import chromadb

COLLECTION_NAME = "cdc_regulatory_docs"
DEFAULT_PERSIST_DIR = "./vectordb/chroma_store"


def resolve_chroma_dir(persist_dir: str | None = None) -> str:
    """
    Locates the Chroma persistence directory reliably whether called from
    the project root or from inside the backend/ folder.
    """
    if persist_dir:
        return persist_dir

    env_dir = os.environ.get("CHROMA_PERSIST_DIR")
    if env_dir:
        return env_dir

    # Check root-relative path (when run from project root)
    if os.path.exists("./vectordb/chroma_store"):
        return "./vectordb/chroma_store"

    # Check parent-relative path (when imported or run from backend/)
    if os.path.exists("../vectordb/chroma_store"):
        return "../vectordb/chroma_store"

    return DEFAULT_PERSIST_DIR


def retrieve(query: str, top_k: int = 5, persist_dir: str | None = None) -> list[dict]:
    """
    Retrieves the top_k most relevant chunks from the Chroma collection for a given query.

    Args:
        query: Plain-language search question or query string.
        top_k: Maximum number of relevant chunks to retrieve (default: 5).
        persist_dir: Optional override for the Chroma persistent store directory.

    Returns:
        List of dicts sorted by relevance:
        [
            {
                "text": str,
                "title": str,
                "source_url": str,
                "doc_id": str,
                "chunk_index": int,
            },
            ...
        ]
    """
    if not query or not query.strip():
        return []

    target_dir = resolve_chroma_dir(persist_dir)
    client = chromadb.PersistentClient(path=target_dir)

    try:
        collection = client.get_collection(name=COLLECTION_NAME)
    except Exception:
        # Collection does not exist yet (e.g., ingest hasn't been run)
        return []

    total_chunks = collection.count()
    if total_chunks == 0:
        return []

    k = min(top_k, total_chunks)

    # Chroma uses DefaultEmbeddingFunction (all-MiniLM-L6-v2) by default
    # query_texts are automatically embedded and matched by cosine/L2 distance
    results = collection.query(
        query_texts=[query],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    retrieved_chunks = []
    if results and results.get("documents") and results["documents"][0]:
        docs = results["documents"][0]
        metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)

        for doc_text, meta in zip(docs, metas):
            retrieved_chunks.append({
                "text": doc_text,
                "title": meta.get("title", ""),
                "source_url": meta.get("source_url", ""),
                "doc_id": meta.get("doc_id", ""),
                "chunk_index": meta.get("chunk_index", 0),
            })

    return retrieved_chunks


if __name__ == "__main__":
    sample_query = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "What are the capital reserve and net capital requirements under SECP Circular 12?"
    )

    print("=" * 70)
    print("CDC Regulatory Assistant - Module 2 Retrieval Sanity Check")
    print("=" * 70)
    print(f"Query: '{sample_query}'")
    print(f"Store Path: '{resolve_chroma_dir()}'")
    print("-" * 70)

    try:
        results = retrieve(sample_query, top_k=3)
        print(f"Found {len(results)} relevant chunk(s):\n")

        for idx, item in enumerate(results, start=1):
            print(f"[{idx}] Title: {item['title']}")
            print(f"    Source URL: {item['source_url']}")
            print(f"    Doc ID: {item['doc_id']} (Chunk #{item['chunk_index']})")
            snippet = item["text"][:200] + "..." if len(item["text"]) > 200 else item["text"]
            print(f"    Text Preview: {snippet}\n")
    except Exception as e:
        print(f"Error during retrieval: {e}")
