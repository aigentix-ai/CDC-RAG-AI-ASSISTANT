import os
import chromadb
from typing import List, Dict, Any

COLLECTION_NAME = "cdc_regulatory_docs"

def get_chroma_persist_dir() -> str:
    """
    Resolves the Chroma persistent store directory.
    Uses CHROMA_PERSIST_DIR env var if set, otherwise defaults to ./vectordb/chroma_store.
    Handles relative paths correctly whether invoked from root or backend directory.
    """
    env_dir = os.getenv("CHROMA_PERSIST_DIR")
    if env_dir:
        return os.path.abspath(env_dir)

    default_path = os.path.abspath("./vectordb/chroma_store")
    if not os.path.exists(default_path):
        parent_candidate = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vectordb", "chroma_store"))
        if os.path.exists(parent_candidate):
            return parent_candidate
    return default_path

def get_client() -> chromadb.PersistentClient:
    persist_dir = get_chroma_persist_dir()
    os.makedirs(persist_dir, exist_ok=True)
    return chromadb.PersistentClient(path=persist_dir)

def get_collection(client: chromadb.PersistentClient = None):
    if client is None:
        client = get_client()
    return client.get_or_create_collection(name=COLLECTION_NAME)

def retrieve(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Retrieves the top_k most relevant chunks for a given query from Chroma store.

    Returns:
        list of {"text": str, "title": str, "source_url": str, "doc_id": str}
    """
    if not query or not query.strip():
        return []

    client = get_client()
    collection = get_collection(client)

    count = collection.count()
    if count == 0:
        return []

    actual_k = min(top_k, count)

    search_query = query.strip()
    try:
        from typo_corrector import correct_query_typos
        normalized_q, _ = correct_query_typos(search_query)
        if normalized_q:
            search_query = normalized_q
    except Exception:
        pass

    results = collection.query(
        query_texts=[search_query],
        n_results=actual_k
    )

    retrieved_chunks: List[Dict[str, Any]] = []
    if results and "documents" in results and results["documents"] and results["documents"][0]:
        documents = results["documents"][0]
        metadatas = results["metadatas"][0] if ("metadatas" in results and results["metadatas"]) else [{}] * len(documents)

        for doc_text, meta in zip(documents, metadatas):
            metadata = meta or {}
            retrieved_chunks.append({
                "text": doc_text,
                "title": metadata.get("title", ""),
                "source_url": metadata.get("source_url", ""),
                "doc_id": metadata.get("doc_id", ""),
                "source_type": metadata.get("source_type", "html"),
                "page_number": metadata.get("page_number", 1),
                "chunk_index": metadata.get("chunk_index", 0)
            })

    return retrieved_chunks
