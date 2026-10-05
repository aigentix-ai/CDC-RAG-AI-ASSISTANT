# Module 2 Walkthrough: Chunking, Embedding, and Chroma Vector Storage

Module 2 for the **CDC Phase 1 RAG Regulatory Assistant** has been implemented and validated against the shared project contract.

## Overview of Implemented Components

### 1. Ingestion Engine ([`ingest.py`](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/ingest.py))
- **File Reading**: Safely reads [`data/raw/documents.jsonl`](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/data/raw/documents.jsonl) without modifying it.
- **Chunking Logic**: Splits document text into chunks of ~500 words (~650-800 tokens) with ~75 words (~100 tokens) overlap, preserving word boundaries.
- **Embedding Pipeline**: Uses Chroma's default embedding function (`all-MiniLM-L6-v2` via ONNX), generating 384-dimensional dense embeddings locally.
- **Collection Management**:
  - Connects to Chroma persistent client at `CHROMA_PERSIST_DIR` (defaults to `./vectordb/chroma_store`).
  - Resets and rebuilds the collection `cdc_regulatory_docs` on each run to guarantee idempotency and avoid duplicates.
  - Chunk ID format: `f"{doc_id}_{chunk_index}"` (e.g. `9de5a4df77d10ef1_0`).
  - Chunk metadata: `{"doc_id": ..., "source_url": ..., "title": ..., "chunk_index": ..., "date_scraped": ...}`.

### 2. Reusable Retrieval Interface ([`retrieval.py`](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/retrieval.py))
- **Interface Contract**: Implements `retrieve(query: str, top_k: int = 5) -> list[dict]`.
- **Return Shape**: List of dictionaries sorted by relevance:
  ```python
  {
      "text": "...",
      "title": "...",
      "source_url": "...",
      "doc_id": "...",
      "chunk_index": 0
  }
  ```
- **Self-Contained**: Automatically resolves `./vectordb/chroma_store` whether executed from the project root or when copied directly into `/backend`.
- **CLI Test Block**: Includes interactive `if __name__ == "__main__":` block to verify search results from the terminal.

### 3. Dependencies ([`requirements.txt`](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/requirements.txt))
- Specifies `chromadb>=0.4.24`.

---

## Verification & Test Results

### 1. Ingestion Execution
Command:
```powershell
python ingest.py
```
Output:
```
2026-09-17 13:02:02,635 [INFO] Target Chroma persistent directory: C:\Users\waqar com\Documents\Saad Work\CDC AI FUND\cdc-rag-demo\vectordb\chroma_store
2026-09-17 13:02:02,637 [INFO] Loaded 2 document(s) from data\raw\documents.jsonl
2026-09-17 13:02:03,679 [INFO] Reset existing collection 'cdc_regulatory_docs' for clean rebuild.
2026-09-17 13:02:03,720 [INFO] Document 'sample' (5bd4b0eba9da263e) split into 1 chunk(s).
2026-09-17 13:02:03,723 [INFO] Document 'Central Depository Company of Pakistan - Wikipedia' (9de5a4df77d10ef1) split into 2 chunk(s).
2026-09-17 13:02:03,723 [INFO] Adding 3 total chunk(s) to collection 'cdc_regulatory_docs'...
2026-09-17 13:02:05,376 [INFO] Successfully indexed 3 chunks into 'cdc_regulatory_docs'.

[DONE] Ingestion completed. Total chunks in Chroma store: 3
```

### 2. CLI Retrieval Sanity Check
Command:
```powershell
python retrieval.py "Central Depository Company of Pakistan CEO"
```
Output:
```
======================================================================
CDC Regulatory Assistant - Module 2 Retrieval Sanity Check
======================================================================
Query: 'Central Depository Company of Pakistan CEO'
Store Path: './vectordb/chroma_store'
----------------------------------------------------------------------
Found 3 relevant chunk(s):

[1] Title: Central Depository Company of Pakistan - Wikipedia
    Source URL: https://en.wikipedia.org/wiki/Central_Depository_Company
    Doc ID: 9de5a4df77d10ef1 (Chunk #0)
    Text Preview: Central Depository Company of Pakistan - Wikipedia Jump to content From Wikipedia, the free encyclopedia (Redirected from Central Depository Company ) Pakistani securities depository company This arti...

[2] Title: Central Depository Company of Pakistan - Wikipedia
    Source URL: https://en.wikipedia.org/wiki/Central_Depository_Company
    Doc ID: 9de5a4df77d10ef1 (Chunk #1)
    Text Preview: by the CDC, CISSII is an online platform designed to facilitate information sharing within the insurance industry , covering aspects such as claims processing, risk management , agent activities, and ...

[3] Title: sample
    Source URL: https://pdfobject.com/pdf/sample.pdf
    Doc ID: 5bd4b0eba9da263e (Chunk #0)
    Text Preview: Sample PDF This is a simple PDF file. Fun fun fun. Lorem ipsum dolor sit amet, consectetuer adipiscing elit. Phasellus facilisis odio sed mi. Curabitur suscipit. Nullam vel nisi. Etiam semper ipsum ut...
```

### 3. Automated Contract Assertions
A test script verified:
- `retrieve("")` and whitespace queries return `[]`.
- Each returned object strictly matches keys: `{"text", "title", "source_url", "doc_id", "chunk_index"}`.
- Re-running `ingest.py` resets and rebuilds without duplicating chunks.
- Result: **All verification assertions PASSED**.
