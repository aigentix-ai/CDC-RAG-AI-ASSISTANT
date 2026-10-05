import os
import sys
import pytest

# Ensure root directory is in path
sys.path.insert(0, os.path.abspath("."))

def test_chunking_algorithm_contract():
    """
    Shared Contract Section 3:
    - Target chunk size: ~500-800 tokens with ~50-100 token overlap
    - Preserves word boundaries and handles short texts cleanly
    """
    import ingest

    # Test short text (smaller than chunk size)
    short_text = "This is a brief regulatory announcement from SECP."
    chunks = ingest.chunk_text(short_text, chunk_size=500, overlap=75)
    assert len(chunks) == 1
    assert chunks[0] == short_text

    # Test long text (requires multiple chunks with overlap)
    long_words = [f"word_{i}" for i in range(1200)]
    long_text = " ".join(long_words)
    chunks = ingest.chunk_text(long_text, chunk_size=500, overlap=75)
    assert len(chunks) > 1

    # Check overlap exists between consecutive chunks
    chunk1_words = chunks[0].split()
    chunk2_words = chunks[1].split()
    # End of chunk1 should match start of chunk2
    overlap_count = len(set(chunk1_words[-75:]).intersection(set(chunk2_words[:75])))
    assert overlap_count > 0, "Consecutive chunks must overlap"

def test_retrieval_contract_signature_and_schema(tmp_path):
    """
    Shared Contract Section 3 & Module 2 spec:
    - Signature: retrieve(query: str, top_k: int = 5) -> list[dict]
    - Return keys: 'text', 'title', 'source_url', 'doc_id', 'chunk_index'
    - Empty query returns empty list []
    """
    import retrieval

    # Empty query must return []
    assert retrieval.retrieve("") == []
    assert retrieval.retrieve("   ") == []
    assert retrieval.retrieve(None) == []

    # Non-existent collection or empty collection returns []
    dummy_persist_dir = str(tmp_path / "empty_chroma")
    results = retrieval.retrieve("test query", top_k=5, persist_dir=dummy_persist_dir)
    assert isinstance(results, list)
    assert len(results) == 0

def test_retrieval_output_schema_with_seeded_store(tmp_path):
    """
    Tests build_vector_store and retrieve with actual Chroma client.
    """
    import ingest
    import retrieval

    # Point to test chroma dir
    test_chroma_dir = str(tmp_path / "test_chroma_store")
    raw_docs_path = "data/raw/documents.jsonl"

    if not os.path.exists(raw_docs_path):
        pytest.skip(f"documents.jsonl not present at {raw_docs_path}")

    # Ingest into test store
    inserted_count = ingest.build_vector_store(
        docs_path=raw_docs_path,
        persist_dir=test_chroma_dir,
        chunk_size=100,
        overlap=20
    )
    assert inserted_count > 0

    # Retrieve from test store
    results = retrieval.retrieve("SECP Circular", top_k=3, persist_dir=test_chroma_dir)
    assert isinstance(results, list)
    assert len(results) > 0
    assert len(results) <= 3

    for item in results:
        assert "text" in item, "Item missing 'text'"
        assert "title" in item, "Item missing 'title'"
        assert "source_url" in item, "Item missing 'source_url'"
        assert "doc_id" in item, "Item missing 'doc_id'"
        assert "chunk_index" in item, "Item missing 'chunk_index'"
        assert isinstance(item["text"], str) and item["text"]
        assert isinstance(item["doc_id"], str) and len(item["doc_id"]) == 16
