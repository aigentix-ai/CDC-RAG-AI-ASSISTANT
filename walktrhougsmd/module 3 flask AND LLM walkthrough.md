# Walkthrough - Module 3: Flask Backend + LLM Integration

Module 3 of the **CDC Phase 1 RAG Regulatory Assistant** has been implemented and verified in strict accordance with the shared project contract.

---

## Architecture & File Structure

All implementation code is strictly encapsulated within `/backend` (plus the temporary seed data at `./vectordb/chroma_store`):

```
backend/
├── app.py              # Flask server exposing POST /ask with CORS & error handling
├── retriever.py        # Thin wrapper querying Chroma collection 'cdc_regulatory_docs'
├── llm.py              # Swappable LLM engine (Gemini API with Claude swap guide)
├── seed_fake_data.py   # Temporary seeder populating 3 sample regulatory chunks
├── requirements.txt    # Production dependency manifest
└── test_backend.py     # Automated unit and contract test suite
```

---

## Key Components Implemented

### 1. `backend/app.py`
- Exposes `POST /ask` with CORS enabled via `flask-cors`.
- Validates request body; returns HTTP 400 with `{"error": "..."}` if body is missing, not JSON, or `question` is blank.
- Orchestrates retrieval via `retriever.retrieve(question, top_k=5)` and answer generation via `llm.generate_answer(question, chunks)`.
- Handles unhandled exceptions cleanly with HTTP 500 and `{"error": "..."}`.
- Listens on `0.0.0.0` with port configurable via `FLASK_PORT` (default `5000`).

### 2. `backend/retriever.py`
- Connects to the persistent Chroma client located at `CHROMA_PERSIST_DIR` (default: `./vectordb/chroma_store`).
- Targets collection `"cdc_regulatory_docs"`.
- Implements `retrieve(query: str, top_k: int = 5) -> list[dict]`.
- Returns chunks formatted according to shared contract section 5:
  ```json
  {
    "text": "chunk text...",
    "title": "SECP Circular 12 of 2025",
    "source_url": "https://example-secp-circular.pdf",
    "doc_id": "9f86d081884c7d65"
  }
  ```

### 3. `backend/llm.py`
- Implements `generate_answer(question: str, retrieved_chunks: list[dict]) -> dict`.
- Top comment block details step-by-step instructions for swapping the Gemini engine to the Anthropic Claude API without modifying `app.py` or callers.
- Strict prompt grounding: instructs Gemini to answer **only** from the provided text chunks.
- If chunks do not contain a supporting answer (or if chunks are empty), returns the exact fallback string:
  `"answer": "I don't know based on the available sources."` and `"citations": []`.
- Output parsing extracts the answer and automatically deduplicates citations by `doc_id`.

### 4. `backend/seed_fake_data.py`
- Standalone seed utility creating 3 realistic chunks (SECP Circular 12 of 2025 and CDC Public Notice on Fund Updates) at `./vectordb/chroma_store`.
- Documented with clear comments noting it should be ignored/deleted once Module 2's ingestion pipeline is active.

### 5. `backend/requirements.txt`
Contains pinned dependencies:
- `flask>=3.0.0`
- `flask-cors>=4.0.0`
- `chromadb>=0.4.22`
- `google-genai>=0.1.0`
- `python-dotenv>=1.0.0`

---

## Verification & Test Results

An automated test suite (`backend/test_backend.py`) was executed to validate the contract:

| Test Case | Description | Result |
|---|---|:---:|
| `test_01_retriever_seeded_data` | Verified Chroma connection, retrieval, and exact schema (`text`, `title`, `source_url`, `doc_id`) | **PASS** |
| `test_02_retriever_empty_query` | Handled empty / whitespace queries safely returning `[]` | **PASS** |
| `test_03_llm_empty_chunks` | Returned `"I don't know based on the available sources."` with `citations: []` when chunks are empty | **PASS** |
| `test_04_llm_parse_dont_know` | Verified model normalization of "I don't know" responses | **PASS** |
| `test_05_llm_parse_and_deduplicate` | Verified citation deduplication by `doc_id` | **PASS** |
| `test_06_app_ask_validation_missing_body` | Verified `400 Bad Request` with `{"error": "..."}` for non-JSON requests | **PASS** |
| `test_07_app_ask_validation_missing_question` | Verified `400 Bad Request` when `question` is missing or empty string | **PASS** |
| `test_08_app_cors_headers` | Verified CORS preflight and access-control headers on `/ask` | **PASS** |
| `test_09_app_ask_500_on_missing_api_key` | Verified `500 Internal Server Error` with `{"error": "..."}` when API key is unset | **PASS** |
| `test_10_app_ask_200_success_contract` | Verified `200 OK` matching the exact JSON contract shape when answer is generated | **PASS** |
| `test_11_app_ask_dont_know_contract` | Verified `200 OK` with exact `I don't know` and empty citations array | **PASS** |

### Live HTTP Verification

The Flask server was started on port 5005 and queried with `curl.exe`:

```bash
# 1. Missing Question (HTTP 400)
curl -X POST http://127.0.0.1:5005/ask -H "Content-Type: application/json" -d '{}'
# -> {"error":"Field 'question' is required and must not be empty."} [HTTP 400]

# 2. Blank Question (HTTP 400)
curl -X POST http://127.0.0.1:5005/ask -H "Content-Type: application/json" -d '{"question": "   "}'
# -> {"error":"Field 'question' is required and must not be empty."} [HTTP 400]

# 3. Missing GEMINI_API_KEY (HTTP 500 Error Handling)
curl -X POST http://127.0.0.1:5005/ask -H "Content-Type: application/json" -d '{"question": "What is SECP circular 12?"}'
# -> {"error":"GEMINI_API_KEY environment variable is not set. Please set GEMINI_API_KEY to query the LLM."} [HTTP 500]
```

---

## How to Run Module 3

1. Activate virtual environment:
   ```powershell
   .\backend\.venv\Scripts\activate
   ```
2. Set environment variables (or create `.env` in `backend/` or root):
   ```powershell
   $env:GEMINI_API_KEY = "your-api-key-here"
   $env:FLASK_PORT = "5000"                      # default 5000
   $env:CHROMA_PERSIST_DIR = "./vectordb/chroma_store" # default
   ```
3. (Optional) Re-seed fake data if testing without Module 2:
   ```powershell
   python backend\seed_fake_data.py
   ```
4. Start the backend server:
   ```powershell
   python backend\app.py
   ```
