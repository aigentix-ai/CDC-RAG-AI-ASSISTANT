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
    Retrieves the top_k most relevant chunks for a given query from Chroma store
    using Hybrid Search (Chroma Dense Vector + BM25 Sparse Lexical + Reciprocal Rank Fusion).

    Returns:
        list of {"text": str, "title": str, "source_url": str, "doc_id": str, ...}
    """
    if not query or not query.strip():
        return []

    client = get_client()
    collection = get_collection(client)

    count = collection.count()
    if count == 0:
        return []

    search_query = query.strip()
    try:
        from typo_corrector import correct_query_typos
        normalized_q, _ = correct_query_typos(search_query)
        if normalized_q:
            search_query = normalized_q
    except Exception:
        pass

    # Step 1: Candidate Vector Search (retrieve top 15-20 candidates for re-ranking)
    candidate_k = min(max(top_k * 3, 15), count)
    vector_chunks: List[Dict[str, Any]] = []

    try:
        results = collection.query(
            query_texts=[search_query],
            n_results=candidate_k
        )
        if results and "documents" in results and results["documents"] and results["documents"][0]:
            documents = results["documents"][0]
            metadatas = results["metadatas"][0] if ("metadatas" in results and results["metadatas"]) else [{}] * len(documents)

            for doc_text, meta in zip(documents, metadatas):
                metadata = meta or {}
                vector_chunks.append({
                    "text": doc_text,
                    "title": metadata.get("title", ""),
                    "source_url": metadata.get("source_url", ""),
                    "doc_id": metadata.get("doc_id", ""),
                    "source_type": metadata.get("source_type", "html"),
                    "page_number": metadata.get("page_number", 1),
                    "chunk_index": metadata.get("chunk_index", 0)
                })
    except Exception:
        vector_chunks = []

    # Step 2: BM25 Lexical Keyword Search
    bm25_chunks: List[Dict[str, Any]] = []
    try:
        from hybrid_search import build_or_get_bm25_index, tokenize
        bm25_index, cached_items = build_or_get_bm25_index(collection)
        if bm25_index and cached_items:
            q_tokens = tokenize(search_query)
            if q_tokens:
                scores = bm25_index.get_scores(q_tokens)
                # Pair with items and sort
                scored_items = [(score, item) for score, item in zip(scores, cached_items) if score > 0.0]
                scored_items.sort(key=lambda x: x[0], reverse=True)
                bm25_chunks = [item for _, item in scored_items[:candidate_k]]
    except Exception:
        bm25_chunks = []

    # Step 3: Reciprocal Rank Fusion & Re-ranking
    if bm25_chunks and vector_chunks:
        from hybrid_search import reciprocal_rank_fusion
        return reciprocal_rank_fusion(vector_chunks, bm25_chunks, search_query, top_k=top_k)
    elif vector_chunks:
        return vector_chunks[:top_k]
    elif bm25_chunks:
        return bm25_chunks[:top_k]

    return []
