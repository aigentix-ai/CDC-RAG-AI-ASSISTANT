import os
import sys
import pytest

# Ensure backend directory is in path
backend_dir = os.path.abspath("backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

def test_llm_fallback_contract_on_empty_chunks():
    """
    Shared Contract Section 4 & 5:
    If retrieved chunks are empty, generate_answer must return EXACTLY:
    {"answer": "I don't know based on the available sources.", "citations": []}
    without making external API calls.
    """
    from llm import generate_answer, DONT_KNOW_ANSWER

    expected_fallback = "I don't know based on the available sources."
    assert DONT_KNOW_ANSWER == expected_fallback

    # Empty list
    res = generate_answer("What is SECP regulation 12?", [])
    assert res["answer"] == expected_fallback
    assert res["citations"] == []

    # Non-empty list but empty content
    res_empty_query = generate_answer("", [])
    assert res_empty_query["answer"] == expected_fallback
    assert res_empty_query["citations"] == []

def test_llm_citation_deduplication():
    """
    Shared Contract Section 4 & 5:
    Citations list must be deduplicated by doc_id.
    """
    from llm import _parse_llm_response

    chunks_by_id = {
        "doc1": {"title": "Doc 1", "source_url": "https://doc1.pdf", "doc_id": "doc1"},
        "doc2": {"title": "Doc 2", "source_url": "https://doc2.pdf", "doc_id": "doc2"}
    }

    # Simulate raw LLM output referencing duplicate doc_ids
    raw_response = '{"answer": "Compliance requires capital buffers.", "used_doc_ids": ["doc1", "doc1", "doc2"]}'
    parsed = _parse_llm_response(raw_response, chunks_by_id)

    assert parsed["answer"] == "Compliance requires capital buffers."
    assert len(parsed["citations"]) == 2
    ids = [c["doc_id"] for c in parsed["citations"]]
    assert ids == ["doc1", "doc2"]

def test_backend_ask_endpoint_validation():
    """
    Shared Contract Section 4:
    POST /ask
    Request: {"question": "..."}
    Validation:
    - Non-JSON -> HTTP 400 {"error": "..."}
    - Empty JSON -> HTTP 400 {"error": "..."}
    - Missing 'question' -> HTTP 400 {"error": "..."}
    - Blank 'question' -> HTTP 400 {"error": "..."}
    """
    from app import app

    client = app.test_client()

    # 1. Non-JSON body
    r = client.post("/ask", data="plain text body")
    assert r.status_code == 400
    assert "error" in r.get_json()

    # 2. Empty JSON dict
    r = client.post("/ask", json={})
    assert r.status_code == 400
    assert "error" in r.get_json()

    # 3. Missing 'question'
    r = client.post("/ask", json={"query": "test"})
    assert r.status_code == 400
    assert "error" in r.get_json()

    # 4. Blank string 'question'
    r = client.post("/ask", json={"question": "   "})
    assert r.status_code == 400
    assert "error" in r.get_json()

    # 5. Non-string 'question'
    r = client.post("/ask", json={"question": 12345})
    assert r.status_code == 400
    assert "error" in r.get_json()

def test_backend_ask_endpoint_fallback_response(monkeypatch):
    """
    Shared Contract Section 4:
    If no relevant context is found in retriever,
    POST /ask returns HTTP 200 with exact fallback and empty citations:
    {"answer": "I don't know based on the available sources.", "citations": []}
    """
    import app as app_module
    import retriever
    from app import app

    # Mock retrieve to return empty list (simulating unanswerable query)
    monkeypatch.setattr(app_module, "retrieve", lambda q, top_k=5: [])
    monkeypatch.setattr(retriever, "retrieve", lambda q, top_k=5: [])

    client = app.test_client()
    r = client.post("/ask", json={"question": "What is the capital of Mars?"})

    assert r.status_code == 200
    data = r.get_json()
    assert "answer" in data
    assert "citations" in data
    assert data["answer"] == "I don't know based on the available sources."
    assert data["citations"] == []
