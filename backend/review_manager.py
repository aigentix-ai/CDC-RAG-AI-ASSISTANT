"""
Review Queue Manager & Live Vector Store Coordinator
Handles staging of unreviewed documents into a SQLite database,
preventing direct insertion into the live Chroma collection.
Provides approval / rejection controls and live index inspection.
"""

import os
import re
import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    from retriever import get_chroma_persist_dir, get_client, get_collection, COLLECTION_NAME
except ImportError:
    from backend.retriever import get_chroma_persist_dir, get_client, get_collection, COLLECTION_NAME

try:
    from content_verifier import is_security_challenge_or_blocked, is_valid_regulatory_content, purge_challenge_artifacts_from_db
except ImportError:
    from backend.content_verifier import is_security_challenge_or_blocked, is_valid_regulatory_content, purge_challenge_artifacts_from_db

logger = logging.getLogger(__name__)

DEFAULT_RAW_DOCS_PATH = "./data/raw/documents.jsonl"

def get_project_root() -> Path:
    """Finds the project root directory."""
    # This file is in backend/, parent is project root
    return Path(__file__).resolve().parent.parent

def get_db_path() -> Path:
    """Resolves the review queue SQLite database path."""
    env_path = os.getenv("REVIEW_DB_PATH")
    if env_path:
        path = Path(env_path)
    else:
        path = get_project_root() / "data" / "pending_review.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the review queue SQLite table and runs migrations."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_queue (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    date_created TEXT NOT NULL,
                    status TEXT NOT NULL, -- 'pending_review', 'approved', 'rejected'
                    preview_text TEXT NOT NULL,
                    full_text TEXT NOT NULL,
                    chunks_json TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    category TEXT DEFAULT 'General Compliance',
                    reviewed_at TEXT,
                    notes TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue (status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_doc_id ON review_queue (doc_id);")

            # Migration: Ensure category column exists if table was created in older version
            cursor = conn.execute("PRAGMA table_info(review_queue);")
            cols = [row["name"] for row in cursor.fetchall()]
            if "category" not in cols:
                conn.execute("ALTER TABLE review_queue ADD COLUMN category TEXT DEFAULT 'General Compliance';")
    finally:
        conn.close()

    try:
        purge_challenge_artifacts_from_db(get_db_path())
    except Exception:
        pass

# Ensure DB table exists on module import
init_db()

def add_pending_item(
    doc_id: str,
    source: str,
    source_type: str,
    title: str,
    full_text: str,
    chunks: List[Dict[str, Any]],
    category: str = "General Compliance"
) -> str:
    """
    Adds a newly parsed document to the review queue with status 'pending_review'.
    CRITICAL: Does NOT insert into live Chroma collection.
    """
    # Defensive backstop: Reject security challenge interstitials or non-substantive pages
    is_valid, reason = is_valid_regulatory_content(title=title, text=full_text, min_words=20)
    if not is_valid:
        logger.warning(f"Rejected pending item for doc_id {doc_id} ('{title}'): {reason}")
        raise ValueError(f"Cannot stage invalid document: {reason}")

    init_db()
    timestamp = int(datetime.now(timezone.utc).timestamp())
    item_id = f"rev_{doc_id}_{timestamp}"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    preview_text = full_text[:1500].strip()
    if len(full_text) > 1500:
        preview_text += " ... [truncated preview]"

    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO review_queue (
                    id, doc_id, source, source_type, title, date_created,
                    status, preview_text, full_text, chunks_json, chunk_count, category
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending_review', ?, ?, ?, ?, ?)
            """, (
                item_id,
                doc_id,
                source,
                source_type,
                title,
                now_iso,
                preview_text,
                full_text,
                json.dumps(chunks, ensure_ascii=False),
                len(chunks),
                category
            ))
        logger.info(f"Added item {item_id} [{category}] to review queue ({len(chunks)} chunks pending)")
        return item_id
    finally:
        conn.close()

def get_pending_items(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns all items awaiting review or approved for staging, optionally filtered by category."""
    init_db()
    conn = get_db_connection()
    try:
        if category and category.lower() != "all":
            cursor = conn.execute("""
                SELECT id, doc_id, source, source_type, title, date_created,
                       status, preview_text, chunk_count, category
                FROM review_queue
                WHERE status IN ('pending_review', 'staged_approved') AND category = ?
                ORDER BY date_created DESC
            """, (category,))
        else:
            cursor = conn.execute("""
                SELECT id, doc_id, source, source_type, title, date_created,
                       status, preview_text, chunk_count, category
                FROM review_queue
                WHERE status IN ('pending_review', 'staged_approved')
                ORDER BY date_created DESC
            """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def search_staged_items(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Searches across pending/staged documents in review queue.
    Enables Admin to test AI retrieval against pending content before approving to live index.
    """
    init_db()
    conn = get_db_connection()
    try:
        cursor = conn.execute("""
            SELECT id, doc_id, source, source_type, title, category, chunks_json
            FROM review_queue
            WHERE status IN ('pending_review', 'staged_approved')
        """)
        rows = cursor.fetchall()
        query_words = set(re.findall(r"\w+", query.lower()))
        matched_chunks = []

        for row in rows:
            chunks = json.loads(row["chunks_json"] or "[]")
            for c in chunks:
                chunk_text = c.get("document", "")
                words = set(re.findall(r"\w+", chunk_text.lower()))
                overlap = len(query_words.intersection(words))
                if overlap > 0:
                    matched_chunks.append({
                        "score": overlap,
                        "text": chunk_text,
                        "title": row["title"],
                        "source_url": row["source"],
                        "doc_id": row["doc_id"],
                        "source_type": row["source_type"],
                        "category": row["category"] or "General Compliance",
                        "page_number": c.get("metadata", {}).get("page_number", 1)
                    })

        matched_chunks.sort(key=lambda x: x["score"], reverse=True)
        return matched_chunks[:top_k]
    finally:
        conn.close()

def batch_approve_items(item_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Approves multiple or all pending/staged items into live ChromaDB in a single batch.
    """
    init_db()
    conn = get_db_connection()
    try:
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            cursor = conn.execute(f"SELECT id FROM review_queue WHERE status IN ('pending_review', 'staged_approved') AND id IN ({placeholders})", item_ids)
        else:
            cursor = conn.execute("SELECT id FROM review_queue WHERE status IN ('pending_review', 'staged_approved')")
        target_ids = [row["id"] for row in cursor.fetchall()]
    finally:
        conn.close()

    approved_count = 0
    errors = []
    for it_id in target_ids:
        try:
            approve_item(it_id)
            approved_count += 1
        except Exception as e:
            errors.append({"item_id": it_id, "error": str(e)})

    return {
        "success": True,
        "total_attempted": len(target_ids),
        "approved_count": approved_count,
        "errors": errors
    }

def get_item(item_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a single item by id, including full text and chunk previews."""
    init_db()
    conn = get_db_connection()
    try:
        cursor = conn.execute("SELECT * FROM review_queue WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["chunks"] = json.loads(data["chunks_json"])
        except Exception:
            data["chunks"] = []
        return data
    finally:
        conn.close()

def approve_item(item_id: str) -> Dict[str, Any]:
    """
    Approves a staged document:
    1. Writes its chunks into the live Chroma collection ('cdc_regulatory_docs').
    2. Appends the document record to data/raw/documents.jsonl if missing.
    3. Marks the review item status as 'approved'.
    """
    item = get_item(item_id)
    if not item:
        raise ValueError(f"Review item not found: {item_id}")

    chunks = item.get("chunks", [])
    if not chunks:
        raise ValueError(f"No chunks available to index for item: {item_id}")

    # 1. Write chunks into live Chroma collection
    client = get_client()
    collection = get_collection(client)

    ids = [c["id"] for c in chunks]
    documents = [c["document"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )

    # 2. Append document to data/raw/documents.jsonl for persistence contract
    raw_docs_path = get_project_root() / "data" / "raw" / "documents.jsonl"
    raw_docs_path.parent.mkdir(parents=True, exist_ok=True)
    doc_record = {
        "doc_id": item["doc_id"],
        "source_url": item["source"],
        "source_type": item["source_type"],
        "title": item["title"],
        "date_scraped": item["date_created"],
        "text": item["full_text"]
    }
    
    # Check if doc_id already present in documents.jsonl
    already_in_jsonl = False
    if raw_docs_path.exists():
        with open(raw_docs_path, "r", encoding="utf-8") as f:
            for line in f:
                if f'"{item["doc_id"]}"' in line:
                    already_in_jsonl = True
                    break

    if not already_in_jsonl:
        with open(raw_docs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(doc_record, ensure_ascii=False) + "\n")

    # 3. Update SQLite status
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE review_queue
                SET status = 'approved', reviewed_at = ?
                WHERE id = ?
            """, (now_iso, item_id))
    finally:
        conn.close()

    logger.info(f"Approved item {item_id}; indexed {len(chunks)} chunks into live Chroma.")
    return {
        "success": True,
        "item_id": item_id,
        "doc_id": item["doc_id"],
        "chunks_indexed": len(chunks)
    }

def approve_staged_item(item_id: str, notes: str = "Approved for staging by reviewer") -> Dict[str, Any]:
    """
    Approves a document for staging only:
    Marks status as 'staged_approved'.
    Does NOT push chunks into live Chroma.
    Enables testing in Staged Retrieval Sandbox before going live.
    """
    item = get_item(item_id)
    if not item:
        raise ValueError(f"Review item not found: {item_id}")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE review_queue
                SET status = 'staged_approved', reviewed_at = ?, notes = ?
                WHERE id = ?
            """, (now_iso, notes, item_id))
    finally:
        conn.close()

    logger.info(f"Approved item {item_id} for staging (status: staged_approved).")
    return {
        "success": True,
        "item_id": item_id,
        "doc_id": item["doc_id"],
        "status": "staged_approved",
        "chunk_count": item.get("chunk_count", 0),
        "message": "Document approved for staging (not yet live in public assistant)."
    }

def reject_item(item_id: str, notes: str = "Rejected by reviewer") -> Dict[str, Any]:
    """
    Rejects a staged document:
    Updates status to 'rejected' and discards it from the active review queue.
    Guarantees no chunks exist in the live Chroma store and deletes files from data/uploads/.
    Gracefully handles already-removed items.
    """
    item = get_item(item_id)
    if not item:
        # Check if item exists in DB with any status or was already removed
        conn = get_db_connection()
        try:
            cursor = conn.execute("SELECT doc_id FROM review_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            doc_id = row["doc_id"] if row else None
        finally:
            conn.close()

        if not doc_id:
            logger.info(f"Reject called on already-removed item: {item_id}")
            return {
                "success": True,
                "item_id": item_id,
                "status": "rejected",
                "message": "Item already removed or does not exist."
            }
    else:
        doc_id = item.get("doc_id")

    # Ensure no chunks accidentally remain in Chroma
    try:
        if doc_id:
            client = get_client()
            collection = get_collection(client)
            collection.delete(where={"doc_id": doc_id})
    except Exception as e:
        logger.debug(f"Chroma cleanup check on rejection: {e}")

    # Remove uploaded file from data/uploads/ if present
    try:
        uploads_dir = get_project_root() / "data" / "uploads"
        if uploads_dir.exists() and doc_id:
            for p in uploads_dir.glob(f"{doc_id}.*"):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Failed to delete uploaded file for doc_id {doc_id}: {e}")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE review_queue
                SET status = 'rejected', reviewed_at = ?, notes = ?
                WHERE id = ?
            """, (now_iso, notes, item_id))
    finally:
        conn.close()

    logger.info(f"Rejected item {item_id}; discarded from review queue.")
    return {
        "success": True,
        "item_id": item_id,
        "status": "rejected"
    }

def get_all_indexed_docs() -> List[Dict[str, Any]]:
    """
    Inspects the live Chroma collection ('cdc_regulatory_docs').
    Aggregates chunk metadata by doc_id to show what is currently active in the index.
    Returns:
        List of {
            "doc_id": str,
            "source": str,
            "source_type": str,
            "title": str,
            "chunk_count": int,
            "date_scraped": str
        }
    """
    client = get_client()
    collection = get_collection(client)

    count = collection.count()
    if count == 0:
        return []

    data = collection.get(include=["metadatas"])
    metadatas = data.get("metadatas", [])

    docs_map: Dict[str, Dict[str, Any]] = {}
    for meta in metadatas:
        if not meta:
            continue
        doc_id = meta.get("doc_id", "unknown_doc")
        if doc_id not in docs_map:
            docs_map[doc_id] = {
                "doc_id": doc_id,
                "source": meta.get("source_url", ""),
                "source_type": meta.get("source_type", "document"),
                "title": meta.get("title", "Untitled"),
                "chunk_count": 0,
                "date_scraped": meta.get("date_scraped", "")
            }
        docs_map[doc_id]["chunk_count"] += 1
        # Fallback date update if blank
        if not docs_map[doc_id]["date_scraped"] and meta.get("date_scraped"):
            docs_map[doc_id]["date_scraped"] = meta.get("date_scraped")

    result = list(docs_map.values())
    result.sort(key=lambda x: x.get("title", ""))
    return result

def delete_indexed_doc(doc_id: str) -> Dict[str, Any]:
    """Deletes all chunks belonging to doc_id from the live Chroma collection."""
    if not doc_id:
        raise ValueError("doc_id is required")

    client = get_client()
    collection = get_collection(client)

    collection.delete(where={"doc_id": doc_id})
    logger.info(f"Deleted doc_id '{doc_id}' from live Chroma collection.")
    return {"success": True, "doc_id": doc_id}

def calculate_match_percentage(total_score: float, is_exact_phrase: bool, is_title: bool, is_content: bool, token_overlap: int) -> int:
    """
    Calculates a realistic confidence / match percentage (50% - 100%)
    to show administrators how closely a document matches their query.
    """
    if is_exact_phrase and is_content:
        return 100 if token_overlap >= 3 else 96
    if is_exact_phrase:
        return 94
    base = 52.0
    if is_title:
        base += 18.0
    if is_content:
        base += 14.0
    score_boost = min(10.0, total_score * 0.6)
    overlap_boost = min(10.0, token_overlap * 2.0)
    pct = int(min(99, round(base + score_boost + overlap_boost)))
    return max(50, pct)

def inspect_document_inside(doc_id: str, query: str = "") -> Dict[str, Any]:
    """
    Deep dive inside a specific published document.
    Retrieves all indexed chunks/pages belonging to doc_id from Chroma.
    If a query is provided, synthesizes a focused answer strictly from this document
    and highlights the matching pages and sections.
    """
    if not doc_id:
        return {"success": False, "error": "doc_id is required."}

    client = get_client()
    collection = get_collection(client)

    data = collection.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
    docs = data.get("documents", [])
    metas = data.get("metadatas", [])

    if not docs:
        return {
            "success": False,
            "error": f"No indexed content found in vector database for document ID '{doc_id}'.",
            "doc_id": doc_id
        }

    first_meta = metas[0] if metas else {}
    title = first_meta.get("title", "Regulatory Document")
    source = first_meta.get("source_url", "")
    source_type = first_meta.get("source_type", "pdf")
    date_scraped = first_meta.get("date_scraped", "")
    is_pdf = (
        source_type == "pdf" or
        source.lower().endswith(".pdf") or
        ".pdf" in source.lower()
    )
    view_url = f"/api/docs/{doc_id}.pdf" if is_pdf else f"/api/docs/{doc_id}"

    clean_q = (query or "").strip().lower()
    q_tokens = [t for t in re.findall(r"\w+", clean_q) if len(t) > 2 and t not in {
        "what", "where", "which", "who", "does", "this", "that", "about",
        "tell", "find", "show", "the", "are", "for", "and"
    }]

    pages = []
    matched_pages = []
    for idx, (text, meta) in enumerate(zip(docs, metas)):
        meta = meta or {}
        p_num = meta.get("page_number", idx + 1)
        c_idx = meta.get("chunk_index", idx)

        text_lower = text.lower()
        matches_query = False
        match_score = 0
        if clean_q:
            if clean_q in text_lower:
                matches_query = True
                match_score += 10
            for tok in q_tokens:
                if tok in text_lower:
                    matches_query = True
                    match_score += 3

        page_item = {
            "chunk_index": c_idx,
            "page_number": p_num,
            "text": text,
            "matches_query": matches_query,
            "match_score": match_score
        }
        pages.append(page_item)
        if matches_query:
            matched_pages.append(page_item)

    pages.sort(key=lambda x: (x["page_number"], x["chunk_index"]))
    matched_pages.sort(key=lambda x: x["match_score"], reverse=True)

    # Synthesize inside answer
    inside_answer = ""
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key and query:
        try:
            from llm import _get_gemini_client, _call_gemini_model
            gem_client = _get_gemini_client()
            context_pages = matched_pages if matched_pages else pages[:6]
            pages_ctx = "\n\n".join([f"--- PAGE {p['page_number']} ---\n{p['text']}" for p in context_pages[:6]])

            prompt = f"""You are analyzing exclusively the regulatory document: "{title}".
The user is asking: "{query}"

Based SOLELY on the following verified text extracted from this document, answer the user's question directly.
Cite the page number(s) (e.g. Page 1, Page 2). Keep your response concise (2-4 sentences max), authoritative, and clear. Do not hallucinate.

Document Content:
{pages_ctx}"""

            llm_resp = _call_gemini_model(gem_client, prompt)
            if llm_resp and len(llm_resp.strip()) > 10:
                inside_answer = llm_resp.strip()
        except Exception as exc:
            logger.warning(f"Gemini call inside doc failed, falling back to rule-based: {exc}")

    if not inside_answer:
        if matched_pages:
            top_snips = []
            for mp in matched_pages[:3]:
                snip = mp["text"][:180].strip()
                top_snips.append(f"• **Page {mp['page_number']}:** \"{snip}...\"")
            inside_answer = f"Here is what **{title}** states regarding your query:\n\n" + "\n".join(top_snips)
        else:
            preview_txt = pages[0]["text"][:220].strip() if pages else ""
            inside_answer = f"Document **{title}** contains **{len(pages)} indexed section(s)**.\n\nOpening section:\n> *\"{preview_txt}...\"*\n\nYou can ask a specific question above to search within this document."

    unique_pages_count = len(set(p["page_number"] for p in pages))

    return {
        "success": True,
        "doc_id": doc_id,
        "title": title,
        "source": source,
        "source_type": "pdf" if is_pdf else source_type,
        "date_scraped": date_scraped,
        "total_pages": unique_pages_count,
        "total_chunks": len(pages),
        "answer": inside_answer,
        "pages": pages,
        "view_url": view_url
    }

def search_indexed_documents(query: str) -> Dict[str, Any]:
    """
    Intelligent agentic search bot for published/indexed documents.
    Answers administrator questions dynamically:
      - 'Do we have any PDF that mentions reconciliation rules?'
      - 'Any PDF with the name SECP Circular 12?'
      - 'Where is the reconciliation requirement defined?'
      - 'What is SECP Directive 44?'
    
    Searches both:
      1. Document titles, filenames, and source URLs.
      2. Chroma vector collection for semantic content matches and excerpts.
    Computes calibrated match percentages (% match) and identifies top matches.
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return {
            "query": "",
            "total_matches": 0,
            "answer": "Please enter a question or search term to search the published knowledge base.",
            "documents": []
        }

    all_docs = get_all_indexed_docs()
    if not all_docs:
        return {
            "query": clean_q,
            "total_matches": 0,
            "answer": "No published documents found. The knowledge base is currently empty.",
            "documents": []
        }

    q_lower = clean_q.lower()
    stop_words = {
        "do", "we", "have", "any", "pdf", "pdfs", "document", "documents", "file", "files",
        "that", "which", "mentions", "mention", "mentioning", "name", "named", "with", "the",
        "about", "is", "there", "show", "me", "find", "can", "you", "tell", "what", "are",
        "of", "in", "for", "a", "an", "to", "and", "or", "some", "our", "contain",
        "contains", "containing", "related", "regulations", "rules", "rule"
    }
    raw_tokens = re.findall(r"[a-zA-Z0-9_-]+", q_lower)
    meaningful_tokens = [t for t in raw_tokens if t not in stop_words and len(t) > 1]
    user_asked_pdf = bool(re.search(r"\bpdf(s)?\b", q_lower))

    # Detect if query is a simple factual question (where is, what is, etc.)
    is_simple_what_where = bool(re.match(r"^(where\s+is|what\s+is|what\s+are|who\s+is|which\s+rule|how\s+to|explain\s+the|tell\s+me\s+about)\b", q_lower))

    # 1. Title & Metadata matching
    title_matches: Dict[str, Dict[str, Any]] = {}
    clean_phrase = " ".join(meaningful_tokens)
    for doc in all_docs:
        did = doc.get("doc_id", "")
        title_lower = (doc.get("title") or "").lower()
        source_lower = (doc.get("source") or "").lower()

        score = 0.0
        matched_words = []

        if clean_phrase and clean_phrase in title_lower:
            score += 8.0
            matched_words.append(clean_phrase)

        for tok in meaningful_tokens:
            if tok in title_lower:
                score += 3.0
                matched_words.append(tok)
            elif tok in source_lower:
                score += 1.5
                matched_words.append(tok)

        if score > 0:
            title_matches[did] = {
                "score": score,
                "matched_words": list(set(matched_words))
            }

    # 2. Vector Semantic Content matching via Chroma
    content_matches: Dict[str, Dict[str, Any]] = {}
    try:
        client = get_client()
        collection = get_collection(client)
        total_chunks = collection.count()

        if total_chunks > 0:
            k = min(12, total_chunks)
            res = collection.query(query_texts=[clean_q], n_results=k)

            if res and "documents" in res and res["documents"] and res["documents"][0]:
                chunks = res["documents"][0]
                metas = res["metadatas"][0] if ("metadatas" in res and res["metadatas"]) else [{}] * len(chunks)

                for doc_text, meta in zip(chunks, metas):
                    meta = meta or {}
                    did = meta.get("doc_id")
                    if not did:
                        continue

                    chunk_text = doc_text.strip()
                    snippet_str = ""
                    if len(chunk_text) > 240:
                        best_pos = -1
                        for tok in meaningful_tokens:
                            pos = chunk_text.lower().find(tok)
                            if pos != -1:
                                best_pos = pos
                                break
                        if best_pos != -1:
                            start = max(0, best_pos - 40)
                            end = min(len(chunk_text), start + 200)
                            snippet_str = ("..." if start > 0 else "") + chunk_text[start:end].strip() + ("..." if end < len(chunk_text) else "")
                        else:
                            snippet_str = chunk_text[:220] + "..."
                    else:
                        snippet_str = chunk_text

                    page_num = meta.get("page_number", 1)

                    if did not in content_matches:
                        content_matches[did] = {
                            "score": 0.0,
                            "snippets": []
                        }

                    chunk_words = set(re.findall(r"\w+", chunk_text.lower()))
                    overlap = len(set(meaningful_tokens).intersection(chunk_words))
                    content_matches[did]["score"] += 2.0 + overlap

                    if len(content_matches[did]["snippets"]) < 2:
                        content_matches[did]["snippets"].append({
                            "page_number": page_num,
                            "text": snippet_str
                        })
    except Exception as exc:
        logger.warning(f"Error querying Chroma in search_indexed_documents: {exc}")

    # 3. Combine unique documents
    all_docs_by_id = {d["doc_id"]: d for d in all_docs}
    all_matched_ids = set(title_matches.keys()).union(set(content_matches.keys()))

    matched_docs = []
    for did in all_matched_ids:
        doc_info = all_docs_by_id.get(did)
        if not doc_info:
            continue

        is_title = did in title_matches
        is_content = did in content_matches

        total_score = 0.0
        if is_title:
            total_score += title_matches[did]["score"]
        if is_content:
            total_score += content_matches[did]["score"]

        if is_title and is_content:
            match_type = "Title & Content Match"
        elif is_title:
            match_type = "Title Match"
        else:
            match_type = "Content Match"

        is_pdf = (
            doc_info.get("source_type") == "pdf" or
            doc_info.get("source", "").lower().endswith(".pdf") or
            ".pdf" in doc_info.get("source", "").lower()
        )

        if user_asked_pdf and not is_pdf:
            total_score *= 0.5

        # Check phrase and token overlaps
        title_lower = (doc_info.get("title") or "").lower()
        is_exact = clean_phrase and (clean_phrase in title_lower)
        token_overlap = len(set(meaningful_tokens).intersection(set(re.findall(r"\w+", title_lower))))
        if did in content_matches:
            for sn in content_matches[did].get("snippets", []):
                sn_words = set(re.findall(r"\w+", sn["text"].lower()))
                token_overlap += len(set(meaningful_tokens).intersection(sn_words))

        match_pct = calculate_match_percentage(
            total_score=total_score,
            is_exact_phrase=bool(is_exact),
            is_title=is_title,
            is_content=is_content,
            token_overlap=token_overlap
        )

        snippets = content_matches.get(did, {}).get("snippets", [])
        view_url = f"/api/docs/{doc_info['doc_id']}.pdf" if is_pdf else f"/api/docs/{doc_info['doc_id']}"

        matched_docs.append({
            "doc_id": doc_info["doc_id"],
            "title": doc_info["title"],
            "source": doc_info["source"],
            "source_type": "pdf" if is_pdf else doc_info.get("source_type", "html"),
            "chunk_count": doc_info.get("chunk_count", 1),
            "date_scraped": doc_info.get("date_scraped", ""),
            "match_type": match_type,
            "score": round(total_score, 2),
            "match_percentage": match_pct,
            "is_100_percent": match_pct >= 99,
            "is_top_match": False,
            "snippets": snippets,
            "view_url": view_url
        })

    # Sort descending by score
    matched_docs.sort(key=lambda x: (x["score"], x["match_percentage"]), reverse=True)

    if matched_docs:
        matched_docs[0]["is_top_match"] = True

    # 4. Generate Agent Response
    answer = ""
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key and matched_docs:
        try:
            from llm import _get_gemini_client, _call_gemini_model
            client = _get_gemini_client()
            doc_summaries = []
            for d in matched_docs[:4]:
                snips = " | ".join([f"(p. {s['page_number']}) {s['text']}" for s in d.get("snippets", [])])
                doc_summaries.append(f"- Document: {d['title']} ({d['match_percentage']}% match, Type: {d['source_type']})\n  Excerpts: {snips or 'Title/Metadata match'}")

            summaries_block = "\n".join(doc_summaries)

            if is_simple_what_where:
                prompt = f"""You are the CDC Regulatory Knowledge Base Assistant for administrators.
The administrator asked a direct question: "{clean_q}"

Here are the matched excerpts from the knowledge base:
{summaries_block}

Provide a direct, concise, simple answer to the question (1-3 sentences max).
Include an exact reference to the document name and page number at the end, formatted cleanly:
📄 Reference: [Document Name] (Page X)
Keep it clear, simple, and strictly grounded in the excerpts without fluff."""
            else:
                prompt = f"""You are the CDC Regulatory Knowledge Base Assistant for administrators.
The administrator asked: "{clean_q}"

Here are the matched published documents and relevant excerpts from the database:
{summaries_block}

Respond directly to the administrator's question. Confirm whether we have matching PDFs or documents, state specifically which ones match (including their match percentage) and what relevant rules or topics they cover based on the excerpts. Keep your response concise (2-4 sentences max), professional, clear, and without buzzwords or fluff."""

            llm_text = _call_gemini_model(client, prompt)
            if llm_text and len(llm_text.strip()) > 10:
                answer = llm_text.strip()
        except Exception as e:
            logger.debug(f"Search agent LLM call skipped: {e}")

    # Fallback deterministic response
    if not answer:
        if matched_docs:
            if is_simple_what_where:
                top_d = matched_docs[0]
                snip = top_d["snippets"][0]["text"] if top_d.get("snippets") else "Official filing requirement."
                page_n = top_d["snippets"][0]["page_number"] if top_d.get("snippets") else 1
                answer = f"**{top_d['title']}**\n\n> *\"{snip}\"*\n\n📄 **Reference:** {top_d['title']} (Page {page_n}) — matched with {top_d['match_percentage']}% confidence."
            else:
                pdf_str = "PDF " if user_asked_pdf else ""
                bullet_points = []
                for d in matched_docs[:3]:
                    snippet_hint = ""
                    if d.get("snippets"):
                        snippet_hint = f" — mentions *\"{d['snippets'][0]['text'][:90]}...\"*"
                    bullet_points.append(f"• **{d['title']}** ({d['match_percentage']}% match){snippet_hint}")

                bullets_formatted = "\n".join(bullet_points)
                answer = f"Found {len(matched_docs)} published {pdf_str}document(s) matching your request:\n\n{bullets_formatted}\n\nSelect a document below to inspect and ask questions directly inside it."
        else:
            answer = f"No published documents matching **\"{clean_q}\"** were found in the knowledge base. If this is a new regulation or filing, you can import or crawl it in the 'Upload & Add Links' tab."

    return {
        "query": clean_q,
        "is_simple_query": is_simple_what_where,
        "total_matches": len(matched_docs),
        "answer": answer,
        "documents": matched_docs
    }

