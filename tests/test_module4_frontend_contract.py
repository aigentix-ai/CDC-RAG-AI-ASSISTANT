import os
import re
import pytest

FRONTEND_DIR = "frontend"
INDEX_PATH = os.path.join(FRONTEND_DIR, "index.html")
SCRIPT_PATH = os.path.join(FRONTEND_DIR, "script.js")
STYLE_PATH = os.path.join(FRONTEND_DIR, "style.css")

def test_frontend_files_exist():
    assert os.path.isfile(INDEX_PATH), f"Missing {INDEX_PATH}"
    assert os.path.isfile(SCRIPT_PATH), f"Missing {SCRIPT_PATH}"
    assert os.path.isfile(STYLE_PATH), f"Missing {STYLE_PATH}"

def test_index_html_contract():
    """
    Shared Contract Section 7 & Module 4 spec:
    - HTML page with header, title 'CDC Regulatory Assistant — Phase 1 Demo'
    - Required elements: chatMessages container, chatForm, questionInput, sendBtn
    - Links to style.css and script.js
    """
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    assert "CDC Regulatory Assistant" in html
    assert "Phase 1 Demo" in html
    assert 'id="chatMain"' in html, "Missing #chatMain element"
    assert 'id="chatMessages"' in html, "Missing #chatMessages container"
    assert 'id="chatForm"' in html, "Missing #chatForm input form"
    assert 'id="questionInput"' in html, "Missing #questionInput textarea/input"
    assert 'id="sendBtn"' in html, "Missing #sendBtn submit button"
    assert 'href="style.css"' in html, "index.html must link to style.css"
    assert 'src="script.js"' in html, "index.html must link to script.js"

def test_script_js_contract():
    """
    Shared Contract Section 4 & 7:
    - Single configurable constant near the top: const API_BASE_URL = "http://localhost:5000/ask"
    - Body sent: {"question": ...}
    - Renders answer and citations
    - Handles {"error": "..."} gracefully
    - Includes loading / typing indicator
    """
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        js = f.read()

    # Verify configurable API_BASE_URL
    api_url_match = re.search(r'const\s+API_BASE_URL\s*=\s*["\']([^"\']+)["\']', js)
    assert api_url_match is not None, "script.js must define 'const API_BASE_URL = ...'"
    assert "http://localhost:5000/ask" in api_url_match.group(1), (
        f"API_BASE_URL must target http://localhost:5000/ask by default, found {api_url_match.group(1)}"
    )

    # Verify JSON body with 'question'
    assert 'question:' in js or '"question":' in js or "'question':" in js or "question" in js, (
        "script.js must send JSON body with key 'question'"
    )

    # Verify error handling
    assert "error" in js.lower(), "script.js must handle error response shape"

    # Verify typing indicator
    assert "typing" in js.lower() or "loading" in js.lower(), (
        "script.js must have a loading/typing indicator"
    )

    # Verify citations rendering
    assert "citation" in js.lower() or "sources" in js.lower(), (
        "script.js must render citations / sources section"
    )
