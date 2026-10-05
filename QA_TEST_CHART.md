# QA & Test Matrix Chart — CDC Phase 1 RAG Regulatory Assistant

**Last Updated:** 2026-09-17 (Automated Continuous QA Audit)  
**Target Project:** `CDC Phase 1 RAG Regulatory Assistant — Demo Build`  
**Base Hub Session:** `89fbe77d-f888-4e7b-97ef-950557cc16a5`  
**QA Engine Session:** `19cf564a-2a1f-476b-a413-437c6996ad2a`  

---

## 🚦 Executive Summary & System Health Dashboard

| Module | Component | Owner | Contract Compliance | Functional Test | Status |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **Module 1** | Scraping & Extraction | Module 1 Agent | ✅ Pass | ⚠️ Partial (Live 403) | 🟡 **Working (Live Alert)** |
| **Module 2** | Ingest, Chunking & Chroma | Module 2 Agent | ✅ Pass | ✅ Pass (100%) | 🟢 **Ready / Verified** |
| **Module 3** | Flask Backend & LLM | Module 3 Agent | ✅ Pass | ✅ Pass (100%) | 🟢 **Ready / Verified** |
| **Module 4** | Frontend Web UI | Module 4 Agent | ✅ Pass | ✅ Pass (100%) | 🟢 **Ready / Verified** |
| **End-to-End**| Full RAG Integration | All Modules | ✅ Pass | ✅ Pass (14/14 Pytest) | 🟢 **All Tests Passing** |

---

## 🚨 Critical & High-Priority Alerts for the Main Base Hub

> [!WARNING]
> ### 1. Real-World Target `cdcpakistan.com` Returns HTTP 403 Forbidden
> - **Issue**: Live web requests to `https://cdcpakistan.com/` and `https://cdcpakistan.com/some-public-page` return `HTTP 403 Client Error: Forbidden`. The official CDC Pakistan portal uses WAF / Cloudflare bot-mitigation blocking standard Python `requests`.
> - **Remedy / Recommendation**: 
>   1. Provide local downloaded PDF circulars/notices under `data/samples/` or local `file://` URLs in `config/whitelist.json`.
>   2. Or configure a proxy / headless browser session (or use curated regulatory mock text).

> [!CAUTION]
> ### 2. Data Overwrite Risk Between Scraper and Vector Store
> - **Issue**: Running `python scrape.py` idempotently rebuilds `data/raw/documents.jsonl`. When live URLs fail (e.g. 403 on CDC and connection reset on dummy W3C PDF), only 1 document is retained in `documents.jsonl`, replacing the rich SECP Circular 12 mock data required for demo Q&A.
> - **Remedy / Recommendation**: Keep baseline curated regulatory sample documents in `config/default_documents.jsonl` so `scrape.py` appends or preserves verified regulatory text even if remote web endpoints fail.

> [!NOTE]
> ### 3. Schema Consistency between `retrieval.py` and `backend/retriever.py`
> - `retrieval.py` (Module 2) returns: `{"text", "title", "source_url", "doc_id", "chunk_index"}`
> - `backend/retriever.py` (Module 3) returns: `{"text", "title", "source_url", "doc_id"}`
> - **Resolution**: Compatible! `backend/llm.py` and `frontend/script.js` require `text`, `title`, `source_url`, and `doc_id`. `chunk_index` is optional metadata.
>
> ### 4. Environment & Dependency Status (Resolved)
> - `google-genai` (v2.24.0) and `chromadb` (v1.5.9) are successfully installed in `.venv`.
> - Live Flask server on port 5000 verified for CORS preflight, HTTP 400 validation, and fallback handling.

---

## 📋 Detailed Verification Matrix by Module

### 1. Module 1: Scraping & Extraction
* **Contract Specification**:
  * Input: `config/whitelist.json` (`url`, `type` in `["pdf", "html"]`, `label`)
  * Output: `data/raw/documents.jsonl` (one JSON object per line)
  * Schema: `doc_id` (16-char sha256), `source_url`, `source_type`, `title`, `date_scraped` (ISO8601 UTC), `text`
* **Audit Results**:
  * `config/whitelist.json`: ✅ Exists, valid JSON array, matches schema.
  * `data/raw/documents.jsonl`: ✅ Valid JSONL format, deterministic `doc_id` hashes verified.
  * `scrape.py`: ✅ Resilient error handling per source; strips HTML boilerplate (`nav`, `script`, `style`, `footer`); cleans PDF page-break artifacts via `pdfplumber`.

### 2. Module 2: Chunking, Embedding, and Chroma Vector Storage
* **Contract Specification**:
  * Reads: `data/raw/documents.jsonl`
  * Persistent storage path: `./vectordb/chroma_store` (or `CHROMA_PERSIST_DIR`)
  * Collection name: `cdc_regulatory_docs`
  * Chunk ID: `f"{doc_id}_{chunk_index}"`
  * Target chunk size: ~500-800 tokens with ~50-100 token overlap (word approximation)
  * Reusable module: `retrieval.py` exposing `retrieve(query: str, top_k: int = 5) -> list[dict]`
* **Audit Results**:
  * `ingest.py`: ✅ Verified chunking logic (`TARGET_CHUNK_WORDS=500`, `OVERLAP_WORDS=75`). Recreates collection cleanly before insertion.
  * `retrieval.py`: ✅ Verified query resolution, returns sorted list with required keys. Gracefully handles empty query (`[]`) and uninitialized collection.

### 3. Module 3: Flask Backend & LLM Integration
* **Contract Specification**:
  * Server: `backend/app.py` running on `FLASK_PORT` (default: 5000) with CORS enabled.
  * Endpoint: `POST /ask` with request body `{"question": "..."}`.
  * Success response (HTTP 200): `{"answer": "...", "citations": [{"title": ..., "source_url": ..., "doc_id": ...}]}`.
  * Validation error (HTTP 400): `{"error": "human-readable message"}`.
  * Fallback contract: exact string `"I don't know based on the available sources."` and empty citations `[]` if retrieved chunks lack info.
  * Swappable LLM: `backend/llm.py` `generate_answer(question, chunks)`.
* **Audit Results**:
  * `POST /ask` input validation: ✅ Correctly rejects non-JSON, missing `question`, empty strings with HTTP 400.
  * Fallback contract: ✅ Returns exact string `DONT_KNOW_ANSWER` without making external API calls when chunks are empty.
  * Deduplication: ✅ Deduplicates citation records by `doc_id`.
  * Claude swappability: ✅ Detailed comments and clean interface separation provided in `backend/llm.py`.

### 4. Module 4: Frontend UI
* **Contract Specification**:
  * Files: `frontend/index.html`, `frontend/style.css`, `frontend/script.js`.
  * Single configurable API endpoint constant: `const API_BASE_URL = "http://localhost:5000/ask"`.
  * Renders: user bubble, assistant bubble, source citations with clickable external links.
  * Error handling: graceful display of backend `{"error": "..."}` in chat UI without crashing.
  * Loading state: animated typing dots indicator.
* **Audit Results**:
  * `index.html`: ✅ Clean financial compliance styling, accessibility attributes (`aria-live`, `aria-label`).
  * `script.js`: ✅ Configurable `API_BASE_URL`, auto-expanding textarea, sanitized URL links, handles Enter key and prompt chips.

---

## 🧪 Automated Test Suite Commands

Run the full automated test suite anytime using:
```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```
Or execute the automated QA audit script:
```powershell
.\.venv\Scripts\python.exe run_qa_tests.py
```
