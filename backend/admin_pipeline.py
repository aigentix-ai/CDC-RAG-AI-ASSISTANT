"""
Admin Ingestion Pipeline & Background Job Manager
Handles async single-item scraping/extraction/chunking and stages
documents into the review queue.
"""

import os
import re
import io
import time
import json
import uuid
import hashlib
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from security import validate_safe_url, safe_fetch_url

try:
    from content_verifier import is_security_challenge_or_blocked, is_valid_regulatory_content
except ImportError:
    from backend.content_verifier import is_security_challenge_or_blocked, is_valid_regulatory_content

logger = logging.getLogger(__name__)

# Constants per shared contract
TARGET_CHUNK_WORDS = 500
OVERLAP_WORDS = 75
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CDC-Regulatory-Assistant/1.0"

# In-memory background jobs registry (thread-safe)
_jobs_lock = threading.Lock()
_jobs_store: Dict[str, Dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ingest_worker")

def compute_doc_id(source_identifier: str) -> str:
    """Computes deterministic 16-hex doc_id per shared contract."""
    return hashlib.sha256(source_identifier.encode("utf-8")).hexdigest()[:16]

def get_current_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def normalize_whitespace(text: str) -> str:
    """Normalizes newlines and horizontal spaces."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def format_table_as_markdown(table: List[List[Any]]) -> str:
    """Converts a raw pdfplumber table matrix into clean GitHub Flavored Markdown."""
    if not table or not any(table):
        return ""
    clean_rows = []
    for row in table:
        if row and any(cell is not None and str(cell).strip() for cell in row):
            clean_rows.append([str(c or "").strip().replace("\n", " ") for c in row])
    if not clean_rows or len(clean_rows) < 2:
        return ""
    header = clean_rows[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in clean_rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        lines.append("| " + " | ".join(padded[:len(header)]) + " |")
    return "\n\n" + "\n".join(lines) + "\n\n"


def extract_document_date(text: str) -> Optional[str]:
    """Extracts formal publication or circular issuance date from regulatory text."""
    patterns = [
        r'\b(?:dated|date)[:\s]+([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s+[0-9]{4})\b',
        r'\b(?:dated|date)[:\s]+([A-Za-z]+\s+[0-9]{1,2},?\s+[0-9]{4})\b',
        r'\b([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})\b',
        r'\b(?:Circular\s+No\.?\s+[0-9]+\s+of\s+([0-9]{4}))\b'
    ]
    for pat in patterns:
        m = re.search(pat, text[:3000], re.I)
        if m:
            return m.group(1).strip()
    return None


def chunk_text(
    text: str,
    chunk_size: int = TARGET_CHUNK_WORDS,
    overlap: int = OVERLAP_WORDS,
    title: str = ""
) -> List[str]:
    """
    Splits text into chunks of roughly chunk_size words with overlap words between consecutive chunks.
    Prepends contextual document title and detected structural section headers.
    Matches Module 2 contract (~500 words, ~75 words overlap).
    """
    words = text.split()
    if not words:
        return []

    # Detect section headings in the text
    section_patterns = [
        r'\b((?:Regulation|Section|Rule|Clause|Part|Chapter)\s+[0-9]+(?:\([0-9a-zA-Z]+\))?(?::[^\n]{1,60})?)\b',
        r'(?:^|\n)(#{1,3}\s+[^\n]{3,60})'
    ]

    def find_chunk_section(chunk_str: str) -> str:
        for pat in section_patterns:
            m = re.search(pat, chunk_str, re.I)
            if m:
                return m.group(1).replace("#", "").strip()
        return ""

    if len(words) <= chunk_size:
        body = " ".join(words)
        if title:
            sec = find_chunk_section(body)
            header = f"[Context: {title}" + (f" | {sec}" if sec else "") + "]\n"
            return [header + body]
        return [body]

    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunk_words = words[i:i + chunk_size]
        body = " ".join(chunk_words)
        if title:
            sec = find_chunk_section(body)
            header = f"[Context: {title}" + (f" | {sec}" if sec else "") + "]\n"
            chunks.append(header + body)
        else:
            chunks.append(body)
        if i + chunk_size >= len(words):
            break
    return chunks

# ---------------------------------------------------------------------------
# Job Management
# ---------------------------------------------------------------------------
def create_job(job_type: str, target: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    now_iso = get_current_utc_iso()
    with _jobs_lock:
        _jobs_store[job_id] = {
            "job_id": job_id,
            "type": job_type,
            "target": target,
            "status": "pending",  # pending -> processing -> done | failed
            "progress": 0,
            "message": "Job queued for processing",
            "error": None,
            "created_at": now_iso,
            "completed_at": None,
            "item_id": None,
            "doc_id": None,
            "chunk_count": None,
            "title": None
        }
    return job_id

def update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs_store:
            _jobs_store[job_id].update(kwargs)

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        return dict(job) if job else None

def get_recent_jobs(limit: int = 25) -> List[Dict[str, Any]]:
    with _jobs_lock:
        jobs = list(_jobs_store.values())
    jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jobs[:limit]

# ---------------------------------------------------------------------------
# Extraction Logic
# ---------------------------------------------------------------------------
def extract_text_from_url(url: str, label: str = "", doc_type: str = "auto") -> Dict[str, str]:
    """Fetches and extracts clean text and title from URL (HTML or PDF) with SSRF defenses."""
    # Pre-flight SSRF check
    is_safe, reason = validate_safe_url(url)
    if not is_safe:
        raise ValueError(f"URL rejected by security policy ({reason}): {url}")

    headers = {"User-Agent": USER_AGENT}

    # Detect if PDF
    is_pdf = False
    if doc_type.lower() == "pdf" or url.lower().endswith(".pdf"):
        is_pdf = True
    else:
        # Check via HEAD request if auto
        try:
            head_resp = safe_fetch_url(url, method="HEAD", headers=headers, timeout=10)
            content_type = head_resp.headers.get("Content-Type", "").lower()
            if "application/pdf" in content_type:
                is_pdf = True
        except Exception:
            pass

    if is_pdf:
        # PDF Extraction via safe_fetch_url
        resp = safe_fetch_url(url, method="GET", headers=headers, timeout=35)
        resp.raise_for_status()

        import pdfplumber
        title = label.strip() if label else None
        pages_text = []

        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            meta = pdf.metadata or {}
            meta_title = meta.get("Title") or meta.get("title")
            if not title and meta_title and str(meta_title).strip():
                title = str(meta_title).strip()

            for page in pdf.pages:
                page_t = page.extract_text() or ""
                try:
                    tables = page.extract_tables()
                    if tables:
                        table_md_blocks = [format_table_as_markdown(tbl) for tbl in tables if tbl]
                        table_md_blocks = [tbl for tbl in table_md_blocks if tbl]
                        if table_md_blocks:
                            page_t += "\n" + "\n".join(table_md_blocks)
                except Exception:
                    pass
                if page_t.strip():
                    pages_text.append(page_t)

        joined = "\n".join(pages_text)
        cleaned = normalize_whitespace(joined)
        if not cleaned or len(cleaned.split()) < 25:
            from crawler_engine import extract_text_via_gemini_ocr
            ocr_text = extract_text_via_gemini_ocr(resp.content, url)
            if ocr_text and len(ocr_text.split()) >= 15:
                cleaned = normalize_whitespace(ocr_text)

        if not cleaned:
            raise ValueError(f"No extractable text found in PDF at {url}")

        if not title:
            parsed = urlparse(url)
            title = Path(parsed.path).name or "SECP Regulatory PDF Document"

        is_valid, reason = is_valid_regulatory_content(title=title, text=cleaned, min_words=20)
        if not is_valid:
            raise ValueError(f"Extracted PDF rejected: {reason}")

        return {
            "title": title,
            "text": cleaned,
            "source_type": "pdf"
        }
    else:
        # HTML Extraction via safe_fetch_url
        resp = safe_fetch_url(url, method="GET", headers=headers, timeout=20)
        resp.raise_for_status()

        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        title = label.strip() if label else None
        if not title:
            title_tag = soup.find("title")
            if title_tag and title_tag.get_text(strip=True):
                title = title_tag.get_text(strip=True)
            else:
                h1_tag = soup.find("h1")
                if h1_tag and h1_tag.get_text(strip=True):
                    title = h1_tag.get_text(strip=True)

        if not title:
            parsed = urlparse(url)
            title = parsed.netloc or "CDC Web Document"

        # Strip boilerplate
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "svg", "form"]):
            tag.decompose()

        cleaned = normalize_whitespace(soup.get_text(separator="\n"))
        if not cleaned:
            raise ValueError(f"No readable text extracted from HTML page at {url}")

        is_valid, reason = is_valid_regulatory_content(title=title, text=cleaned, html_text=resp.text, min_words=20)
        if not is_valid:
            raise ValueError(f"Extracted HTML rejected: {reason}")

        return {
            "title": title,
            "text": cleaned,
            "source_type": "html"
        }

def extract_text_from_file(file_path: Path, original_filename: str, label: str = "") -> Dict[str, str]:
    """Extracts text from an uploaded file (.pdf, .html, .txt, .md, .json)."""
    suffix = file_path.suffix.lower()
    title = label.strip() if label else original_filename

    if suffix == ".pdf":
        import pdfplumber
        pages_text = []
        with pdfplumber.open(file_path) as pdf:
            meta = pdf.metadata or {}
            meta_title = meta.get("Title") or meta.get("title")
            if not label and meta_title and str(meta_title).strip():
                title = str(meta_title).strip()

            for page in pdf.pages:
                t = page.extract_text() or ""
                try:
                    tables = page.extract_tables()
                    if tables:
                        table_md_blocks = [format_table_as_markdown(tbl) for tbl in tables if tbl]
                        table_md_blocks = [tbl for tbl in table_md_blocks if tbl]
                        if table_md_blocks:
                            t += "\n" + "\n".join(table_md_blocks)
                except Exception:
                    pass
                if t.strip():
                    pages_text.append(t)

        cleaned = normalize_whitespace("\n".join(pages_text))
        if not cleaned or len(cleaned.split()) < 25:
            # Fallback to Gemini OCR for scanned PDFs
            from crawler_engine import extract_text_via_gemini_ocr
            ocr_text = extract_text_via_gemini_ocr(file_path.read_bytes(), original_filename)
            if ocr_text and len(ocr_text.split()) >= 15:
                cleaned = normalize_whitespace(ocr_text)

        if not cleaned:
            raise ValueError(f"No extractable text found in uploaded PDF: {original_filename}")

        is_valid, reason = is_valid_regulatory_content(title=title, text=cleaned, min_words=20)
        if not is_valid:
            raise ValueError(f"Uploaded PDF rejected: {reason}")

        return {"title": title, "text": cleaned, "source_type": "pdf"}

    elif suffix in (".html", ".htm"):
        from bs4 import BeautifulSoup
        content = file_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(content, "html.parser")

        if not label:
            title_tag = soup.find("title")
            if title_tag and title_tag.get_text(strip=True):
                title = title_tag.get_text(strip=True)

        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
            tag.decompose()

        cleaned = normalize_whitespace(soup.get_text(separator="\n"))
        is_valid, reason = is_valid_regulatory_content(title=title, text=cleaned, html_text=content, min_words=20)
        if not is_valid:
            raise ValueError(f"Uploaded HTML rejected: {reason}")

        return {"title": title, "text": cleaned, "source_type": "html"}

    else:
        # Plain text, markdown, or json (represented as html for contract compliance)
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        cleaned = normalize_whitespace(raw)
        if not cleaned:
            raise ValueError(f"File {original_filename} is empty or has no readable text.")
        return {"title": title, "text": cleaned, "source_type": "pdf" if suffix == ".pdf" else "html"}

# ---------------------------------------------------------------------------
# Background Pipeline Execution
# ---------------------------------------------------------------------------
def _execute_pipeline_task(
    job_id: str,
    job_type: str,
    source: str,
    file_path: Optional[Path] = None,
    label: str = "",
    doc_type: str = "auto"
):
    from review_manager import add_pending_item

    try:
        update_job(job_id, status="processing", progress=15, message="Starting content extraction...")
        time.sleep(0.3)

        if job_type == "url":
            extracted = extract_text_from_url(url=source, label=label, doc_type=doc_type)
        else:
            extracted = extract_text_from_file(file_path=file_path, original_filename=source, label=label)

        title = extracted["title"]
        text = extracted["text"]
        source_type = extracted["source_type"]

        update_job(job_id, progress=50, message="Text extracted. Chunking content...", title=title)
        time.sleep(0.2)

        # Generate chunks with structural context header
        raw_chunks = chunk_text(text, title=title)
        if not raw_chunks:
            raise ValueError("Document yielded 0 chunks after text extraction.")

        doc_id = compute_doc_id(source)
        now_iso = get_current_utc_iso()
        doc_date = extract_document_date(text) or ""

        prepared_chunks = []
        for i, chunk_str in enumerate(raw_chunks):
            prepared_chunks.append({
                "id": f"{doc_id}_{i}",
                "document": chunk_str,
                "metadata": {
                    "doc_id": doc_id,
                    "source_url": source,
                    "title": title,
                    "chunk_index": i,
                    "date_scraped": now_iso,
                    "doc_date": doc_date,
                    "source_type": source_type
                }
            })

        update_job(job_id, progress=80, message="Staging in Review Queue (pending reviewer approval)...")
        time.sleep(0.2)

        # CRITICAL: Stage into SQLite review queue only — do NOT insert into Chroma
        from crawler_engine import classify_regulatory_category
        category = classify_regulatory_category(title, source, text)

        item_id = add_pending_item(
            doc_id=doc_id,
            source=source,
            source_type=source_type,
            title=title,
            full_text=text,
            chunks=prepared_chunks,
            category=category
        )

        update_job(
            job_id,
            status="done",
            progress=100,
            message=f"Successfully extracted {len(prepared_chunks)} chunks. Staged in Review Queue.",
            completed_at=get_current_utc_iso(),
            item_id=item_id,
            doc_id=doc_id,
            chunk_count=len(prepared_chunks),
            title=title
        )
        logger.info(f"Job {job_id} completed successfully for source: {source}")

    except Exception as exc:
        logger.error(f"Job {job_id} failed: {exc}", exc_info=True)
        update_job(
            job_id,
            status="failed",
            progress=100,
            message=f"Processing failed: {str(exc)}",
            error=str(exc),
            completed_at=get_current_utc_iso()
        )

def submit_url_job(url: str, label: str = "", doc_type: str = "auto") -> str:
    """Queues a URL ingestion background job."""
    job_id = create_job(job_type="url", target=url)
    _executor.submit(
        _execute_pipeline_task,
        job_id=job_id,
        job_type="url",
        source=url,
        label=label,
        doc_type=doc_type
    )
    return job_id

def submit_file_job(file_path: Path, filename: str, label: str = "") -> str:
    """Queues an uploaded file ingestion background job."""
    job_id = create_job(job_type="upload", target=filename)
    _executor.submit(
        _execute_pipeline_task,
        job_id=job_id,
        job_type="upload",
        source=filename,
        file_path=file_path,
        label=label
    )
    return job_id
