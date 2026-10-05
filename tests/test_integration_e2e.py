import os
import sys
import pytest

# Ensure root and backend are in path
sys.path.insert(0, os.path.abspath("."))
backend_dir = os.path.abspath("backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

def test_full_pipeline_ingest_and_retrieval(tmp_path):
    """
    Tests end-to-end data flow:
    data/raw/documents.jsonl -> ingest.py -> Chroma collection -> backend/retriever.py
    """
    import ingest
    import retriever

    test_chroma_dir = str(tmp_path / "integration_chroma")
    raw_docs_path = "data/raw/documents.jsonl"

    if not os.path.exists(raw_docs_path):
        pytest.skip("data/raw/documents.jsonl not found")

    # 1. Run ingestion
    count = ingest.build_vector_store(
        docs_path=raw_docs_path,
        persist_dir=test_chroma_dir
    )
    assert count > 0, "Ingestion must store chunks"

    # 2. Configure backend retriever to point to test_chroma_dir
    os.environ["CHROMA_PERSIST_DIR"] = test_chroma_dir

    # 3. Retrieve using backend retriever
    results = retriever.retrieve("SECP Circular 12", top_k=3)
    assert len(results) > 0, "Retriever must find SECP Circular chunks"

    top_result = results[0]
    assert "text" in top_result
    assert "title" in top_result
    assert "source_url" in top_result
    assert "doc_id" in top_result
    assert len(top_result["doc_id"]) == 16

def test_api_ask_endpoint_contract_with_retrieval(tmp_path, monkeypatch):
    """
    Tests end-to-end Flask endpoint POST /ask with simulated LLM answer
    to ensure the contract schema is 100% compliant.
    """
    import ingest
    import retriever
    import llm
    from app import app

    test_chroma_dir = str(tmp_path / "api_test_chroma")
    raw_docs_path = "data/raw/documents.jsonl"

    if not os.path.exists(raw_docs_path):
        pytest.skip("data/raw/documents.jsonl not found")

    ingest.build_vector_store(
        docs_path=raw_docs_path,
        persist_dir=test_chroma_dir
    )
    os.environ["CHROMA_PERSIST_DIR"] = test_chroma_dir

    # Mock call to Gemini model to return valid structured JSON without needing live network API key
    def mock_call_gemini(client, prompt):
        return (
            '{\n'
            '  "answer": "Under SECP Circular 12 of 2025, intermediaries must maintain minimum net capital reserves.",\n'
            '  "used_doc_ids": ["05a494cd2a9389cf"]\n'
            '}'
        )

    monkeypatch.setattr(llm, "_call_gemini_model", mock_call_gemini)
    # Also mock _get_gemini_client so it doesn't fail if GEMINI_API_KEY is unset in test runner
    monkeypatch.setattr(llm, "_get_gemini_client", lambda: object())

    client = app.test_client()

    response = client.post("/ask", json={"question": "What are the capital requirements?"})
    assert response.status_code == 200

    data = response.get_json()
    assert "answer" in data
    assert "citations" in data
    assert isinstance(data["citations"], list)
    assert len(data["citations"]) >= 1

    cit = data["citations"][0]
    assert "title" in cit
    assert "source_url" in cit
    assert "doc_id" in cit
    assert len(cit["doc_id"]) > 0

