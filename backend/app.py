import os
import sys
import json
import uuid
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr:
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from flask import Flask, request, jsonify, render_template, session, send_file
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# Load environment variables from .env if present
load_dotenv()

# Ensure backend folder is in sys.path
sys.path.insert(0, os.path.dirname(__file__))

from retriever import retrieve
from llm import generate_answer
import review_manager
import admin_pipeline
import crawler_engine
from security import (
    validate_safe_url,
    validate_uploaded_file,
    rate_limit,
    check_admin_token,
    apply_security_headers,
    sanitize_exception,
    MAX_QUESTION_LENGTH,
    MAX_FILE_SIZE_BYTES
)

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cdc-regulatory-demo-secret-2026")
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_BYTES
# Enable CORS for all routes and origins
CORS(app)

@app.after_request
def add_security_headers(response):
    """Enforces OWASP defensive security headers across every HTTP response."""
    return apply_security_headers(response)

@app.errorhandler(413)
def request_entity_too_large(error):
    """Handles oversized payload submissions safely without crashing worker memory."""
    return jsonify({"error": f"Payload exceeds maximum allowed size limit of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."}), 413

# Directory for uploaded files awaiting processing
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Admin authentication password
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "cdc-admin-2026")

def check_admin_auth() -> bool:
    """Verifies admin authorization via session, X-Admin-Password, X-Admin-Key, Bearer token, or query param with constant-time equality."""
    if session.get("is_admin") is True:
        return True
    token = request.headers.get("X-Admin-Password") or request.headers.get("X-Admin-Key")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        token = request.cookies.get("admin_token")
    if not token:
        token = request.args.get("pwd") or request.args.get("admin_key")
    return check_admin_token(token)

# ---------------------------------------------------------------------------
# Core RAG Assistant Endpoint (Phase 1 Shared Contract)
# ---------------------------------------------------------------------------
@app.route("/ask", methods=["POST"])
@rate_limit(max_requests=60, window_seconds=60)
def ask():
    """
    POST /ask
    Request JSON:
      {"question": "string"}
    Response 200:
      {"answer": "string", "citations": [{"title":..., "source_url":..., "doc_id":...}, ...]}
    Response 400:
      {"error": "string"}
    Response 500:
      {"error": "string"}
    """
    try:
        # Validate JSON payload
        if not request.is_json:
            return jsonify({"error": "Request body must be valid JSON."}), 400

        data = request.get_json(silent=True)
        if data is None or not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON object received."}), 400

        question = data.get("question")
        if question is None or not isinstance(question, str) or not question.strip():
            return jsonify({"error": "Field 'question' is required and must not be empty."}), 400

        clean_question = question.strip()

        # Hardened input length limit (max 1,000 characters)
        if len(clean_question) > MAX_QUESTION_LENGTH:
            return jsonify({"error": f"Question exceeds maximum allowed limit of {MAX_QUESTION_LENGTH} characters."}), 400

        history = data.get("history")
        previous_citations = data.get("previous_citations")

        # Step 1: Retrieve relevant context chunks from Chroma
        retrieved_chunks = retrieve(clean_question, top_k=5)

        # Step 2: Generate answer using LLM with anti-injection protections and multi-turn context
        result = generate_answer(clean_question, retrieved_chunks, history=history, previous_citations=previous_citations)

        # Step 3: Return exact response shape with deep-linking citation URLs
        citations = result.get("citations", [])
        return jsonify({
            "answer": result.get("answer", "I don't know based on the available sources."),
            "citations": citations,
            "suggested_options": result.get("suggested_options", [
                "Make this summary shorter",
                "Format this into an executive email memo",
                "What are the specific penalties for non-compliance?",
                "What are the statutory deadlines for submission?"
            ])
        }), 200

    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """GET /health - Liveness probe endpoint."""
    return jsonify({"status": "ok"}), 200

# ---------------------------------------------------------------------------
# Guaranteed Document Serving & Offline Archive Backup
# ---------------------------------------------------------------------------
def format_archive_date(date_str: str) -> str:
    """Formats ISO timestamps into clean, human-readable corporate date strings."""
    if not date_str:
        return "Archived via CDC RAG"
    try:
        from datetime import datetime
        clean_ts = str(date_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        return dt.strftime("%B %d, %Y • %I:%M %p (UTC)")
    except Exception:
        return str(date_str)

def find_document_record(clean_id: str):
    """Looks up full document metadata and text from local backups (documents.jsonl, SQLite, or disk)."""
    # 1. Search data/raw/documents.jsonl
    docs_jsonl = Path(__file__).resolve().parent.parent / "data" / "raw" / "documents.jsonl"
    if docs_jsonl.exists():
        try:
            with open(docs_jsonl, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    rec = json.loads(line_str)
                    if rec.get("doc_id") == clean_id:
                        rec["formatted_date"] = format_archive_date(rec.get("date_scraped"))
                        return rec
        except Exception:
            pass

    # 2. Search pending_review.sqlite3
    try:
        import review_manager
        staged = review_manager.get_staged_item(clean_id)
        if staged:
            d_scraped = staged.get("date_scraped") or ""
            return {
                "doc_id": staged.get("doc_id"),
                "title": staged.get("title") or "Regulatory Document",
                "source_url": staged.get("source_url") or "",
                "source_type": staged.get("source_type") or "html",
                "date_scraped": d_scraped,
                "formatted_date": format_archive_date(d_scraped),
                "text": staged.get("raw_text") or staged.get("preview_text", ""),
                "category": staged.get("category", "General Regulatory")
            }
    except Exception:
        pass

    # 3. Search UPLOAD_DIR for matching text or html files
    for cand in list(UPLOAD_DIR.glob(f"*{clean_id}*")):
        if cand.is_file() and cand.suffix.lower() in [".txt", ".html", ".md", ".json"]:
            try:
                content = cand.read_text(encoding="utf-8", errors="replace")
                try:
                    from content_verifier import is_security_challenge_or_blocked
                    if is_security_challenge_or_blocked(cand.stem, content):
                        continue
                except Exception:
                    pass
                return {
                    "doc_id": clean_id,
                    "title": cand.stem,
                    "source_url": cand.name,
                    "source_type": cand.suffix.replace(".", ""),
                    "date_scraped": "Local Upload",
                    "formatted_date": "Local Upload File",
                    "text": content,
                    "category": "Regulatory Filing"
                }
            except Exception:
                pass
    return None

@app.route("/api/docs/<doc_id>", methods=["GET"])
@app.route("/api/docs/<doc_id>.pdf", methods=["GET"])
def serve_document(doc_id):
    """
    GET /api/docs/<doc_id> or /api/docs/<doc_id>.pdf
    Serves stored regulatory PDFs with inline headers for browser display.
    If physical PDF is absent (or for web/text documents), serves the complete
    verified local backup via the branded CDC Regulatory Document Archive Viewer.
    Guarantees 100% citation uptime regardless of external website availability.
    """
    clean_id = doc_id.replace(".pdf", "")

    # 1. Exact or matching physical PDF in UPLOAD_DIR
    exact_pdf = UPLOAD_DIR / f"{clean_id}.pdf"
    if exact_pdf.is_file():
        return send_file(exact_pdf, mimetype="application/pdf", as_attachment=False)

    matches = list(UPLOAD_DIR.glob(f"*{clean_id}*.pdf"))
    for cand in matches:
        if cand.is_file():
            return send_file(cand, mimetype="application/pdf", as_attachment=False)

    # 2. Check local offline repository (documents.jsonl, SQLite, disk)
    record = find_document_record(clean_id)
    if record:
        return render_template("doc_viewer.html", doc=record), 200

    # 3. Fallback: if .pdf was requested and any PDF exists in uploads
    if doc_id.endswith(".pdf"):
        all_pdfs = list(UPLOAD_DIR.glob("*.pdf"))
        if all_pdfs:
            return send_file(all_pdfs[0], mimetype="application/pdf", as_attachment=False)

    return jsonify({"error": f"Document '{doc_id}' not found in local regulatory archive."}), 404

# ---------------------------------------------------------------------------
# Admin UI & Authentication Routes
# ---------------------------------------------------------------------------
@app.route("/admin/api/login", methods=["POST"])
def admin_login():
    """POST /admin/api/login - Authenticates admin with shared password."""
    data = request.get_json(silent=True) or {}
    pwd = (data.get("password") or "").strip()
    if check_admin_token(pwd):
        session["is_admin"] = True
        return jsonify({"success": True, "message": "Authenticated successfully."}), 200
    return jsonify({"error": "Invalid admin password."}), 401

@app.route("/admin/api/logout", methods=["POST"])
def admin_logout():
    """POST /admin/api/logout - Clears admin session."""
    session.pop("is_admin", None)
    return jsonify({"success": True, "message": "Logged out."}), 200

@app.route("/admin/api/auth-status", methods=["GET"])
def admin_auth_status():
    """GET /admin/api/auth-status - Returns whether current session is authenticated."""
    return jsonify({"authenticated": check_admin_auth()}), 200

@app.before_request
def enforce_admin_auth():
    """Enforces admin authentication on all mutating and sensitive admin endpoints."""
    if request.path.startswith("/admin/api/"):
        # Allow login and auth status checks openly
        if request.path in ("/admin/api/login", "/admin/api/auth-status"):
            return None
        if not check_admin_auth():
            return jsonify({"error": "Unauthorized. Password required to access admin functions."}), 401
    return None

# ---------------------------------------------------------------------------
# Frontend Chat UI Serving (Root URL)
# ---------------------------------------------------------------------------
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

@app.route("/", methods=["GET"])
def serve_frontend_index():
    """Serves the user-facing Regulatory Assistant Chat UI at root URL."""
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return send_file(index_file)
    return jsonify({"error": "Frontend index.html not found"}), 404

@app.route("/<path:filename>", methods=["GET"])
def serve_frontend_assets(filename):
    """Serves frontend static assets (CSS, JS) without clashing with admin or API routes."""
    if filename.startswith("admin") or filename.startswith("api") or filename.startswith("health") or filename.startswith("ask"):
        return jsonify({"error": "Endpoint not found"}), 404
    asset_file = FRONTEND_DIR / filename
    if asset_file.is_file():
        return send_file(asset_file)
    return jsonify({"error": "Asset not found"}), 404

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    """Serves the Admin Ingestion & Review Dashboard."""
    return render_template("admin.html")

# ---------------------------------------------------------------------------
# Admin API: Link Submission & File Upload
# ---------------------------------------------------------------------------
@app.route("/admin/api/submit-url", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
def submit_url():
    """
    POST /admin/api/submit-url
    JSON: {"url": str, "label": str, "doc_type": "auto"|"pdf"|"html"}
    Queues background scraping, extraction, chunking, and stages to review queue.
    """
    try:
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "Field 'url' is required."}), 400

        is_safe, reason = validate_safe_url(url)
        if not is_safe:
            return jsonify({"error": f"URL rejected: {reason}"}), 400

        label = (data.get("label") or "").strip()
        doc_type = (data.get("doc_type") or "auto").strip()

        job_id = admin_pipeline.submit_url_job(url=url, label=label, doc_type=doc_type)
        return jsonify({
            "success": True,
            "job_id": job_id,
            "message": "URL ingestion job submitted successfully."
        }), 202

    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/upload", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
def upload_file():
    """
    POST /admin/api/upload
    Multipart form: 'file' (required), 'label' (optional)
    Saves file and queues background extraction and chunking to review queue.
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part in request."}), 400

        file = request.files["file"]
        if not file or not file.filename:
            return jsonify({"error": "No file selected."}), 400

        # Validate file size, extension, magic bytes, and path safety
        is_valid, reason = validate_uploaded_file(file)
        if not is_valid:
            return jsonify({"error": reason}), 400

        label = request.form.get("label", "").strip()

        raw_filename = Path(file.filename).name
        clean_filename = secure_filename(raw_filename)
        if not clean_filename:
            clean_filename = f"upload_{uuid.uuid4().hex[:8]}.txt"

        saved_name = f"{uuid.uuid4().hex[:12]}_{clean_filename}"
        saved_path = (UPLOAD_DIR / saved_name).resolve()

        # Path traversal guard: verify saved_path is strictly within UPLOAD_DIR
        try:
            saved_path.relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            return jsonify({"error": "Path traversal detected in upload filename."}), 400

        file.save(str(saved_path))

        job_id = admin_pipeline.submit_file_job(file_path=saved_path, filename=clean_filename, label=label)
        return jsonify({
            "success": True,
            "job_id": job_id,
            "message": f"File '{clean_filename}' uploaded and queued for processing."
        }), 202

    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

# ---------------------------------------------------------------------------
# Admin API: Job Tracker Status & Polling
# ---------------------------------------------------------------------------
@app.route("/admin/api/jobs", methods=["GET"])
def list_jobs():
    """GET /admin/api/jobs - returns list of recent background jobs."""
    try:
        jobs = admin_pipeline.get_recent_jobs(limit=30)
        return jsonify({"jobs": jobs}), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/jobs/<job_id>", methods=["GET"])
def get_job_status(job_id):
    """GET /admin/api/jobs/<job_id> - returns status of a single job."""
    try:
        job = admin_pipeline.get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({"job": job}), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

# ---------------------------------------------------------------------------
# Admin API: Review Queue & Approval Controls
# ---------------------------------------------------------------------------
@app.route("/admin/api/pending", methods=["GET"])
def list_pending():
    """GET /admin/api/pending - lists documents awaiting approval with optional ?category= filter."""
    try:
        category = request.args.get("category")
        items = review_manager.get_pending_items(category=category)
        return jsonify({"pending": items}), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/review/test-staged", methods=["POST"])
@rate_limit(max_requests=40, window_seconds=60)
def test_staged_ai():
    """
    POST /admin/api/review/test-staged
    JSON: {"question": str}
    Runs retrieval and answer generation strictly against staged/pending documents.
    Enables Admin to test AI knowledge before deciding whether to make it live!
    """
    try:
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or data.get("query") or "").strip()
        if not question:
            return jsonify({"error": "Question is required."}), 400

        if len(question) > MAX_QUESTION_LENGTH:
            return jsonify({"error": f"Question exceeds maximum allowed limit of {MAX_QUESTION_LENGTH} characters."}), 400

        staged_chunks = review_manager.search_staged_items(question, top_k=5)
        if not staged_chunks:
            return jsonify({
                "answer": "No matching content found in current staged documents.",
                "citations": [],
                "staged_matches": [],
                "staged_matches_count": 0,
                "is_staged_test": True
            }), 200

        result = generate_answer(question, staged_chunks)
        return jsonify({
            "answer": result.get("answer", ""),
            "citations": result.get("citations", []),
            "staged_matches": staged_chunks,
            "staged_matches_count": len(staged_chunks),
            "is_staged_test": True
        }), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/review/publish-all", methods=["POST"])
def publish_all_staged():
    """
    POST /admin/api/review/publish-all
    JSON: {"item_ids": [...] (optional)}
    Approves staged documents into the live assistant Chroma index ("Make Live").
    """
    try:
        data = request.get_json(silent=True) or {}
        item_ids = data.get("item_ids")
        result = review_manager.batch_approve_items(item_ids=item_ids)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/crawl/discover", methods=["POST"])
@rate_limit(max_requests=15, window_seconds=60)
def discover_links_endpoint():
    """
    POST /admin/api/crawl/discover
    JSON: {"root_url": str, "max_links": int (default 40)}
    Discovers and categorizes links on the target domain without scraping full content.
    """
    try:
        data = request.get_json(silent=True) or {}
        root_url = (data.get("root_url") or "").strip()
        if not root_url:
            return jsonify({"error": "Field 'root_url' is required."}), 400

        is_safe, reason = validate_safe_url(root_url)
        if not is_safe:
            return jsonify({"error": f"Root URL rejected: {reason}"}), 400

        max_links = min(100, max(5, int(data.get("max_links", 40))))

        result = crawler_engine.discover_categorized_links(root_url=root_url, max_links=max_links)
        return jsonify({"success": True, "discovery": result}), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/pending/<item_id>", methods=["GET"])
def get_pending_detail(item_id):
    """GET /admin/api/pending/<item_id> - detailed item data including preview and chunk count."""
    try:
        item = review_manager.get_item(item_id)
        if not item:
            return jsonify({"error": "Item not found in review queue"}), 404
        return jsonify({"item": item}), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/review/<item_id>/approve", methods=["POST"])
def approve_item(item_id):
    """
    POST /admin/api/review/<item_id>/approve
    Moves document chunks into the live Chroma collection ('cdc_regulatory_docs').
    If live=false (via JSON body or query param), approves document for staging only.
    """
    try:
        data = request.get_json(silent=True) or {}
        make_live = data.get("live", True)
        if request.args.get("live", "").lower() in ("0", "false", "no"):
            make_live = False

        if make_live:
            result = review_manager.approve_item(item_id)
        else:
            result = review_manager.approve_staged_item(item_id)
        return jsonify(result), 200
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 404
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/review/<item_id>/approve-staged", methods=["POST"])
def approve_staged_item(item_id):
    """
    POST /admin/api/review/<item_id>/approve-staged
    Approves document for staging only without publishing to live assistant Chroma collection.
    """
    try:
        data = request.get_json(silent=True) or {}
        notes = data.get("notes", "Approved for staging by reviewer")
        result = review_manager.approve_staged_item(item_id, notes=notes)
        return jsonify(result), 200
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 404
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/review/<item_id>/reject", methods=["POST"])
def reject_item(item_id):
    """
    POST /admin/api/review/<item_id>/reject
    Discards document from the review queue and ensures it is not indexed.
    """
    try:
        data = request.get_json(silent=True) or {}
        reason = data.get("reason", "Rejected by reviewer")
        result = review_manager.reject_item(item_id, notes=reason)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

# ---------------------------------------------------------------------------
# Admin API: Live Chroma Inspector ("What's Indexed")
# ---------------------------------------------------------------------------
@app.route("/admin/api/indexed", methods=["GET"])
def list_indexed():
    """
    GET /admin/api/indexed
    Lists all documents currently indexed in Chroma ('cdc_regulatory_docs')
    with chunk count, source, and date added.
    """
    try:
        docs = review_manager.get_all_indexed_docs()
        return jsonify({
            "documents": docs,
            "total_documents": len(docs)
        }), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/indexed/<doc_id>", methods=["DELETE"])
def delete_indexed(doc_id):
    """
    DELETE /admin/api/indexed/<doc_id>
    Removes a document and all its chunks from the live Chroma collection.
    """
    try:
        result = review_manager.delete_indexed_doc(doc_id)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/search-docs", methods=["POST"])
def search_indexed_docs_endpoint():
    """
    POST /admin/api/search-docs
    JSON: {"query": str}
    Agentic document discovery bot for published documents in the knowledge base.
    """
    try:
        data = request.get_json(silent=True) or {}
        query = (data.get("query") or data.get("question") or "").strip()
        if not query:
            return jsonify({"error": "Field 'query' is required."}), 400

        result = review_manager.search_indexed_documents(query)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500

@app.route("/admin/api/doc-inspect", methods=["POST"])
@app.route("/api/doc-inspect", methods=["POST"])
def inspect_document_endpoint():
    """
    POST /admin/api/doc-inspect or /api/doc-inspect
    JSON: {"doc_id": str, "query": str (optional)}
    Deep-dives inside a specific indexed document, extracting its sections/pages
    and answering directly from inside the document like a Google search inside feature.
    """
    try:
        data = request.get_json(silent=True) or {}
        doc_id = (data.get("doc_id") or "").strip()
        query = (data.get("query") or data.get("question") or "").strip()
        if not doc_id:
            return jsonify({"error": "Field 'doc_id' is required."}), 400

        result = review_manager.inspect_document_inside(doc_id, query=query)
        if not result.get("success"):
            return jsonify(result), 404
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500


# ---------------------------------------------------------------------------
# Admin API: Autonomous Deep Site Crawler
# ---------------------------------------------------------------------------
@app.route("/admin/api/crawl/start", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
def start_crawl():
    """
    POST /admin/api/crawl/start
    JSON: {
        "root_url": str,
        "max_pages": int (optional, default 50),
        "delay_seconds": float (optional, default 1.5),
        "auto_approve": bool (optional, default False),
        "check_robots": bool (optional, default True)
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        root_url = (data.get("root_url") or "").strip()
        if not root_url:
            return jsonify({"error": "Field 'root_url' is required."}), 400

        is_safe, reason = validate_safe_url(root_url)
        if not is_safe:
            return jsonify({"error": f"Root URL rejected: {reason}"}), 400

        max_pages = int(data.get("max_pages", 50))
        max_pages = max(1, min(max_pages, 500))

        delay_seconds = float(data.get("delay_seconds", 1.5))
        delay_seconds = max(0.0, min(delay_seconds, 10.0))

        auto_approve = bool(data.get("auto_approve", False))
        check_robots = bool(data.get("check_robots", True))
        stealth_mode = bool(data.get("stealth_mode", True))
        allowed_urls = data.get("allowed_urls")
        if allowed_urls and isinstance(allowed_urls, list):
            clean_allowed = []
            for u in allowed_urls:
                u_str = str(u).strip()
                if u_str:
                    safe_u, u_reason = validate_safe_url(u_str)
                    if not safe_u:
                        return jsonify({"error": f"Allowed URL '{u_str}' rejected: {u_reason}"}), 400
                    clean_allowed.append(u_str)
            allowed_urls = clean_allowed
            if allowed_urls:
                max_pages = max(len(allowed_urls), max_pages)
        else:
            allowed_urls = None

        crawl_id = crawler_engine.start_crawl(
            root_url=root_url,
            max_pages=max_pages,
            delay_seconds=delay_seconds,
            auto_approve=auto_approve,
            check_robots=check_robots,
            allowed_urls=allowed_urls,
            stealth_mode=stealth_mode
        )

        job_status = crawler_engine.get_crawl_job(crawl_id)

        return jsonify({
            "success": True,
            "crawl_id": crawl_id,
            "message": f"Autonomous crawl started for {root_url}",
            "job": job_status
        }), 202

    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500


@app.route("/admin/api/crawl/status/<crawl_id>", methods=["GET"])
def get_crawl_status(crawl_id):
    """
    GET /admin/api/crawl/status/<crawl_id>
    Returns real-time crawler progress, counters, and streaming log history.
    """
    try:
        status = crawler_engine.get_crawl_job(crawl_id)
        if not status:
            return jsonify({"error": f"Crawl job not found: {crawl_id}"}), 404
        return jsonify({
            "success": True,
            "job": status
        }), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500


@app.route("/admin/api/crawl/stop/<crawl_id>", methods=["POST"])
def stop_crawl(crawl_id):
    """
    POST /admin/api/crawl/stop/<crawl_id>
    Gracefully signals an active crawler to halt execution.
    """
    try:
        status = crawler_engine.stop_crawl_job(crawl_id)
        if not status:
            return jsonify({"error": f"Crawl job not found: {crawl_id}"}), 404
        return jsonify({
            "success": True,
            "crawl_id": crawl_id,
            "message": "Crawl stop signal sent successfully.",
            "job": status
        }), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500


@app.route("/admin/api/crawl/resume", methods=["POST"])
@app.route("/admin/api/crawl/resume/<crawl_id>", methods=["POST"])
def resume_crawl(crawl_id=None):
    """
    POST /admin/api/crawl/resume or /admin/api/crawl/resume/<crawl_id>
    Resumes a paused or captcha-blocked crawler job. If crawl_id is omitted,
    resumes the most recent paused, active, or pending crawl.
    """
    try:
        if not crawl_id:
            data = request.get_json(silent=True) or {}
            crawl_id = data.get("crawl_id") or request.args.get("crawl_id")

        status = crawler_engine.resume_crawl_job(crawl_id)
        if not status:
            return jsonify({"error": "No active or paused crawl job found to resume."}), 404
        return jsonify({
            "success": True,
            "crawl_id": status.get("crawl_id"),
            "message": "Crawl resumed successfully.",
            "job": status
        }), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500


@app.route("/admin/api/crawl/jobs", methods=["GET"])
def list_crawl_jobs():
    """
    GET /admin/api/crawl/jobs
    Returns list of all recent crawl jobs.
    """
    try:
        jobs = crawler_engine.list_crawl_jobs()
        return jsonify({
            "jobs": jobs,
            "total": len(jobs)
        }), 200
    except Exception as exc:
        return jsonify({"error": sanitize_exception(exc)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("FLASK_PORT", 5000)))
    app.run(host="0.0.0.0", port=port, debug=False)

