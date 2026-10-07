"""
=============================================================================
HYBRID SEARCH & BM25 RETRIEVAL MODULE
=============================================================================
Provides production-grade BM25 keyword retrieval, Reciprocal Rank Fusion (RRF),
and exact regulatory clause/number re-ranking for CDC Pakistan & SECP RAG.
=============================================================================
"""

import math
import re
import time
from typing import List, Dict, Any, Tuple, Optional

# Standard BM25 hyperparameters
BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60

# Cache for BM25 index to avoid re-tokenizing on every single request
_BM25_CACHE: Dict[str, Any] = {
    "doc_count": -1,
    "last_updated": 0,
    "bm25_index": None,
    "cached_items": []
}

def tokenize(text: str) -> List[str]:
    """Tokenizes text preserving numbers, circular numbers, and legal keywords."""
    if not text:
        return []
    # Normalize and split into alphanumerics + hyphens
    tokens = re.findall(r"[a-zA-Z0-9]+(?:[-_][a-zA-Z0-9]+)*", text.lower())
    return [t for t in tokens if len(t) > 1 or t.isdigit()]


class BM25Okapi:
    """
    Zero-dependency, pure Python BM25Okapi implementation.
    Optimized for regulatory text, circular numbers, and statutory terms.
    """
    def __init__(self, corpus: List[List[str]], k1: float = BM25_K1, b: float = BM25_B):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.doc_lens = [len(doc) for doc in corpus]
        self.avg_doc_len = (sum(self.doc_lens) / self.corpus_size) if self.corpus_size > 0 else 1.0

        # Term frequencies per document and document frequencies
        self.doc_freqs: Dict[str, int] = {}
        self.term_freqs: List[Dict[str, int]] = []

        for doc in corpus:
            tf: Dict[str, int] = {}
            for token in doc:
                tf[token] = tf.get(token, 0) + 1
            self.term_freqs.append(tf)

            for token in tf:
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

        # Precompute IDFs
        self.idf: Dict[str, float] = {}
        for token, freq in self.doc_freqs.items():
            # Standard Lucene/BM25Okapi IDF formula with +1 smoothing
            self.idf[token] = math.log(1.0 + (self.corpus_size - freq + 0.5) / (freq + 0.5))

    def get_scores(self, query_tokens: List[str]) -> List[float]:
        """Calculates BM25 score for all documents against given query tokens."""
        scores = [0.0] * self.corpus_size
        if not query_tokens or self.corpus_size == 0:
            return scores

        for token in query_tokens:
            if token not in self.idf:
                continue
            idf_val = self.idf[token]
            for doc_idx, tf_dict in enumerate(self.term_freqs):
                if token not in tf_dict:
                    continue
                tf = tf_dict[token]
                doc_len = self.doc_lens[doc_idx]
                denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))
                scores[doc_idx] += idf_val * ((tf * (self.k1 + 1.0)) / denominator)

        return scores


def build_or_get_bm25_index(collection) -> Tuple[Optional[BM25Okapi], List[Dict[str, Any]]]:
    """
    Maintains an in-memory cached BM25 index over the Chroma collection.
    Automatically refreshes if the collection count changes or cache expires.
    """
    global _BM25_CACHE

    try:
        current_count = collection.count()
    except Exception:
        return None, []

    if current_count == 0:
        return None, []

    now = time.time()
    # If cache is valid (same document count and updated within last 60 seconds)
    if (_BM25_CACHE["doc_count"] == current_count and 
        _BM25_CACHE["bm25_index"] is not None and 
        (now - _BM25_CACHE["last_updated"]) < 60):
        return _BM25_CACHE["bm25_index"], _BM25_CACHE["cached_items"]

    # Fetch all documents and metadatas from Chroma
    try:
        all_data = collection.get(include=["documents", "metadatas"])
    except Exception:
        return None, []

    docs = all_data.get("documents", [])
    metas = all_data.get("metadatas", [])
    ids = all_data.get("ids", [])

    if not docs:
        return None, []

    cached_items = []
    tokenized_corpus = []

    for idx, (doc_text, doc_id) in enumerate(zip(docs, ids)):
        meta = metas[idx] if (metas and idx < len(metas) and metas[idx]) else {}
        item = {
            "id": doc_id,
            "text": doc_text or "",
            "title": meta.get("title", ""),
            "source_url": meta.get("source_url", ""),
            "doc_id": meta.get("doc_id", ""),
            "source_type": meta.get("source_type", "html"),
            "page_number": meta.get("page_number", 1),
            "chunk_index": meta.get("chunk_index", 0),
            "date_scraped": meta.get("date_scraped", "")
        }
        cached_items.append(item)
        # Tokenize text combined with title for rich lexical indexing
        combined_text = f"{item['title']} {item['text']}"
        tokenized_corpus.append(tokenize(combined_text))

    bm25 = BM25Okapi(tokenized_corpus)

    _BM25_CACHE["doc_count"] = current_count
    _BM25_CACHE["last_updated"] = now
    _BM25_CACHE["bm25_index"] = bm25
    _BM25_CACHE["cached_items"] = cached_items

    return bm25, cached_items


def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    query: str,
    top_k: int = 5,
    rrf_k: int = RRF_K
) -> List[Dict[str, Any]]:
    """
    Fuses dense vector results and sparse BM25 results using Reciprocal Rank Fusion (RRF).
    RRF(d) = sum(1 / (k + rank(d)))
    Also applies an exact regulatory clause/number re-ranking boost.
    """
    scores: Dict[str, float] = {}
    doc_map: Dict[str, Dict[str, Any]] = {}

    # Extract exact numeric or clause markers from the query (e.g. "12", "2025", "2.5")
    query_tokens = tokenize(query)
    exact_markers = [t for t in query_tokens if t.isdigit() or (len(t) > 2 and any(ch.isdigit() for ch in t))]

    # 1. Score Vector Results
    for rank, item in enumerate(vector_results, start=1):
        uid = f"{item.get('doc_id')}_{item.get('chunk_index', 0)}"
        scores[uid] = scores.get(uid, 0.0) + (1.0 / (rrf_k + rank))
        if uid not in doc_map:
            doc_map[uid] = item

    # 2. Score BM25 Results
    for rank, item in enumerate(bm25_results, start=1):
        uid = f"{item.get('doc_id')}_{item.get('chunk_index', 0)}"
        scores[uid] = scores.get(uid, 0.0) + (1.0 / (rrf_k + rank))
        if uid not in doc_map:
            doc_map[uid] = item

    # 3. Apply Re-ranking Boost for Exact Regulatory Matches
    for uid, item in doc_map.items():
        text_lower = (item.get("text", "") + " " + item.get("title", "")).lower()
        # Bonus for exact query numbers/circulars
        for marker in exact_markers:
            if marker in text_lower:
                scores[uid] += 0.015  # Decisive boost for exact circular/regulation number
        # Bonus if query phrase appears directly
        if len(query.strip()) > 5 and query.strip().lower() in text_lower:
            scores[uid] += 0.03

    # Sort candidates by combined RRF score descending
    sorted_uids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    # Return top_k deduplicated items
    results = [doc_map[uid] for uid in sorted_uids[:top_k]]
    return results
