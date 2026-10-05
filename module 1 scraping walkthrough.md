# Walkthrough: Module 1 — Scraping & Extraction

Module 1 has been built, tested, and verified for the **CDC Phase 1 RAG Regulatory Assistant**. It implements standalone scraping and text extraction conforming to the shared contract.

---

## 1. Deliverables Created & Modified

| File | Purpose |
|---|---|
| [scrape.py](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/scrape.py) | Main scraper script runnable from project root via `python scrape.py` |
| [config/whitelist.json](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/config/whitelist.json) | Whitelist configuration containing sample regulatory sources (PDF and HTML) |
| [data/raw/documents.jsonl](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/data/raw/documents.jsonl) | Destination JSON Lines file storing extracted documents |
| [requirements.txt](file:///c:/Users/waqar%20com/Documents/Saad%20Work/CDC%20AI%20FUND/cdc-rag-demo/requirements.txt) | Root dependency list with `requests`, `beautifulsoup4`, `pdfplumber`, and `chromadb` |

---

## 2. Features Implemented

1. **Whitelist Management**:
   - Auto-creates `config/whitelist.json` with 3 placeholder entries (PDF & HTML) if missing.
   - Strictly restricts scraping only to URLs defined in `whitelist.json`.

2. **Extraction Engine**:
   - **HTML**: Fetches page with standard headers, decomposes boilerplate tags (`<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<noscript>`, `<aside>`, `<svg>`, `<form>`), extracts title from `<title>` or first `<h1>` (falling back to whitelist `label`), and cleans whitespace.
   - **PDF**: Downloads remote PDF or reads local file paths, extracts page text via `pdfplumber`, joins pages with `\n`, deduplicates headers/footers across pages, strips pagination artifacts, and resolves title from PDF metadata or whitelist `label`.

3. **Deterministic Identification & Timestamping**:
   - Generates deterministic 16-character SHA-256 hex hash from the URL: `hashlib.sha256(url.encode()).hexdigest()[:16]`.
   - Formats UTC timestamps in ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`).

4. **Fault-Tolerant Execution & Logging**:
   - Catches per-source exceptions so bad URLs/network errors do not crash the run.
   - Logs progress and outputs a clear end-of-run summary detailing processed, succeeded, and failed items with root causes.

5. **JSONL Idempotent Output**:
   - Re-creates `data/raw/documents.jsonl` on each run with one valid JSON object per line.

---

## 3. Verification & Test Results

### Test 1: Package Import Verification
All required libraries (`requests`, `bs4`, `pdfplumber`) were verified to import cleanly inside the environment:
```powershell
.\.venv\Scripts\python.exe -c "import requests, bs4, pdfplumber; print('ALL REQUIRED PACKAGES IMPORTED SUCCESSFULLY!')"
# Output: ALL REQUIRED PACKAGES IMPORTED SUCCESSFULLY!
```

### Test 2: Scraper Execution
Executed `python scrape.py`:
```
======================================================================
CDC Phase 1 RAG - Module 1: Scraper & Extractor
======================================================================
Loaded 3 source(s) from whitelist: config\whitelist.json

[1/3] Processing: SECP Circular 12 of 2025
       Type: pdf | URL: https://pdfobject.com/pdf/sample.pdf
       [SUCCESS] doc_id: 5bd4b0eba9da263e | Title: sample | Text length: 2848 chars

[2/3] Processing: CDC Public Notice — Fund Update
       Type: html | URL: https://en.wikipedia.org/wiki/Central_Depository_Company
       [SUCCESS] doc_id: 9de5a4df77d10ef1 | Title: Central Depository Company of Pakistan - Wikipedia | Text length: 5544 chars

[3/3] Processing: CDC Regulatory Update
       Type: html | URL: https://cdcpakistan.com/some-public-page
       [FAILED] Error: 403 Client Error: Forbidden for url: https://cdcpakistan.com/some-public-page

[OUTPUT] Written 2 document(s) to: data\raw\documents.jsonl

======================================================================
SCRAPING SUMMARY
  Total processed: 3
  Succeeded:       2
  Failed:          1

Failures Detail:
  - [HTML] https://cdcpakistan.com/some-public-page
    Reason: 403 Client Error: Forbidden for url: https://cdcpakistan.com/some-public-page
======================================================================
```

### Test 3: Contract Conformance Check
Verified that each record in `data/raw/documents.jsonl` contains the exact fields and schema specified by the shared contract:
- `doc_id`
- `source_url`
- `source_type` ("pdf" | "html")
- `title`
- `date_scraped` (ISO8601 UTC)
- `text` (whitespace-normalized)

Determinism test confirmed `doc_id` produces the exact 16-character SHA-256 hex string every run.
