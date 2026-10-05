"""
Unit and integration tests for the Deep Site Crawler & Auto-Ingestion Subsystem
Tests domain locking, HTML/PDF extraction, chunking, max pages cap,
politeness/robots, live Chroma vs review queue staging, and Flask API endpoints.
"""

import io
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend and root are in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "backend"))

from crawler_engine import (
    SiteCrawler,
    RobotsManager,
    get_base_domain,
    is_same_domain,
    classify_url,
    chunk_text,
    extract_html_page,
    compute_doc_id,
    start_crawl,
    get_crawl_job,
    stop_crawl_job,
    list_crawl_jobs,
)
from app import app
import review_manager


# ---------------------------------------------------------------------------
# Test 1: Domain Boundaries and Normalization
# ---------------------------------------------------------------------------
def test_domain_locking_and_normalization():
    assert get_base_domain("https://cdcpakistan.com") == "cdcpakistan.com"
    assert get_base_domain("https://www.cdcpakistan.com/about-us") == "cdcpakistan.com"
    assert get_base_domain("http://secp.gov.pk:8080/path?query=1") == "secp.gov.pk"

    # Same domain check
    assert is_same_domain("https://cdcpakistan.com/page1", "cdcpakistan.com") is True
    assert is_same_domain("https://www.cdcpakistan.com/page2", "cdcpakistan.com") is True
    assert is_same_domain("https://sub.cdcpakistan.com/notice", "cdcpakistan.com") is True

    # External domain rejection
    assert is_same_domain("https://twitter.com/cdcpakistan", "cdcpakistan.com") is False
    assert is_same_domain("https://linkedin.com/company/cdc", "cdcpakistan.com") is False
    assert is_same_domain("https://fake-cdcpakistan.com", "cdcpakistan.com") is False
    assert is_same_domain("https://cdcpakistan.com.attacker.com", "cdcpakistan.com") is False


# ---------------------------------------------------------------------------
# Test 2: URL Resource Classification
# ---------------------------------------------------------------------------
def test_classify_url():
    assert classify_url("https://cdcpakistan.com/files/circular-2025.pdf") == "pdf"
    assert classify_url("https://cdcpakistan.com/files/download", "application/pdf") == "pdf"
    assert classify_url("https://cdcpakistan.com/notices/update.html") == "html"
    assert classify_url("https://cdcpakistan.com/regulations/") == "html"


# ---------------------------------------------------------------------------
# Test 3: Shared Contract Chunking (~500 words with 75-word overlap)
# ---------------------------------------------------------------------------
def test_chunking_contract():
    # Empty text
    assert chunk_text("") == []

    # Short text
    short_text = "This is a short regulatory text from CDC Pakistan."
    short_chunks = chunk_text(short_text, chunk_size=500, overlap=75)
    assert len(short_chunks) == 1
    assert short_chunks[0] == short_text

    # Long text (600 words)
    words = [f"word{i}" for i in range(600)]
    long_text = " ".join(words)
    chunks = chunk_text(long_text, chunk_size=500, overlap=75)
    assert len(chunks) == 2
    # Check overlap
    first_chunk_words = chunks[0].split()
    second_chunk_words = chunks[1].split()
    assert len(first_chunk_words) == 500
    # Overlap starts at 500 - 75 = 425
    assert first_chunk_words[-75:] == second_chunk_words[:75]


# ---------------------------------------------------------------------------
# Test 4: HTML Extraction and Clean Boilerplate Removal
# ---------------------------------------------------------------------------
def test_html_extraction_and_cleaning():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>CDC Regulations & Circulars 2026</title>
        <script>console.log("tracker");</script>
        <style>.nav { color: red; }</style>
    </head>
    <body>
        <header><nav><a href="/home">Home</a></nav></header>
        <main>
            <h1>Official Regulatory Directive</h1>
            <p>All mutual fund trustees must comply with CDC regulatory schedules.</p>
            <a href="/circular-12.pdf">Download Circular 12 (PDF)</a>
            <a href="https://external-bank.com/portal">External Portal</a>
            <a href="javascript:void(0)">Click Here</a>
        </main>
        <footer><p>Copyright CDC Pakistan 2026</p></footer>
    </body>
    </html>
    """

    extracted, links = extract_html_page(html_content, "https://cdcpakistan.com/rules")
    assert extracted["title"] == "CDC Regulations & Circulars 2026"
    assert "All mutual fund trustees must comply with CDC regulatory schedules." in extracted["text"]
    assert "tracker" not in extracted["text"]
    assert "Copyright CDC Pakistan" not in extracted["text"]

    # Check discovered links
    assert "https://cdcpakistan.com/circular-12.pdf" in links
    assert "https://external-bank.com/portal" in links
    assert not any("javascript:" in link for link in links)


# ---------------------------------------------------------------------------
# Test 5: Robots.txt Politeness Check
# ---------------------------------------------------------------------------
def test_robots_manager():
    manager = RobotsManager()
    robots_txt = """User-agent: *
Disallow: /admin/
Disallow: /private/
Allow: /
"""
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = robots_txt
        mock_get.return_value = mock_resp

        assert manager.can_fetch("https://cdcpakistan.com/public-notice") is True
        assert manager.can_fetch("https://cdcpakistan.com/admin/login") is False
        assert manager.can_fetch("https://cdcpakistan.com/private/doc.pdf") is False


# ---------------------------------------------------------------------------
# Test 6: Respect Max Pages Cap
# ---------------------------------------------------------------------------
def test_crawler_respects_max_pages():
    crawler = SiteCrawler(
        root_url="https://testdomain.com",
        max_pages=2,
        delay_seconds=0.0,
        check_robots=False,
        jitter_min=0.0,
        jitter_max=0.0,
    )

    page_html = """
    <html>
    <head><title>Test Page</title></head>
    <body>
        <p>Regulatory content for testing.</p>
        <a href="/page1">Page 1</a>
        <a href="/page2">Page 2</a>
        <a href="/page3">Page 3</a>
    </body>
    </html>
    """

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = page_html
        mock_resp.content = page_html.encode("utf-8")
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.apparent_encoding = "utf-8"
        mock_get.return_value = mock_resp

        with patch.object(crawler, "_stage_to_review_queue") as mock_stage:
            crawler.run()

            assert crawler.pages_crawled <= 2
            assert crawler.status == "completed"
            assert mock_stage.call_count == 2


# ---------------------------------------------------------------------------
# Test 7: Graceful Cancellation / Stop
# ---------------------------------------------------------------------------
def test_crawler_graceful_stop():
    crawler = SiteCrawler(
        root_url="https://testdomain.com",
        max_pages=100,
        delay_seconds=0.0,
        check_robots=False,
    )

    # Immediately signal stop
    crawler.stop()
    assert crawler.status == "stopped"

    # Running crawl when already stopped exits immediately
    crawler.run()
    assert crawler.pages_crawled == 0


# ---------------------------------------------------------------------------
# Test 8: Staging into Review Queue vs Direct Live Chroma
# ---------------------------------------------------------------------------
def test_crawler_staging_vs_direct_indexing():
    # 1. Staging Mode (auto_approve=False)
    crawler_stage = SiteCrawler(
        root_url="https://testdomain.com",
        max_pages=1,
        delay_seconds=0.0,
        auto_approve=False,
        check_robots=False,
    )

    page_content = "<html><head><title>Stage Notice</title></head><body><p>Staged text.</p></body></html>"

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = page_content
        mock_resp.content = page_content.encode("utf-8")
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.apparent_encoding = "utf-8"
        mock_get.return_value = mock_resp

        with patch("review_manager.add_pending_item") as mock_add_pending:
            crawler_stage.run()
            assert mock_add_pending.called
            args, kwargs = mock_add_pending.call_args
            assert kwargs["title"] == "Stage Notice"
            assert kwargs["source"] == "https://testdomain.com"
            assert len(kwargs["chunks"]) >= 1

    # 2. Live Chroma Mode (auto_approve=True)
    crawler_live = SiteCrawler(
        root_url="https://testdomain.com/live",
        max_pages=1,
        delay_seconds=0.0,
        auto_approve=True,
        check_robots=False,
    )

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = page_content
        mock_resp.content = page_content.encode("utf-8")
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.apparent_encoding = "utf-8"
        mock_get.return_value = mock_resp

        with patch.object(crawler_live, "_index_directly_to_chroma") as mock_direct:
            crawler_live.run()
            assert mock_direct.called
            args, kwargs = mock_direct.call_args
            assert kwargs["title"] == "Stage Notice"
            assert kwargs["source_url"] == "https://testdomain.com/live"
            assert len(kwargs["chunks"]) >= 1

    # 3. Verify _index_directly_to_chroma calls Chroma upsert
    with patch("retriever.get_client"), patch("retriever.get_collection") as mock_get_col, patch("pathlib.Path.exists", return_value=False), patch("builtins.open", MagicMock()):
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col
        crawler_live._index_directly_to_chroma(
            doc_id="1234567812345678",
            source_url="https://testdomain.com/live",
            source_type="html",
            title="Live Title",
            full_text="Live text",
            date_scraped="2026-09-18T10:00:00Z",
            chunks=[{"id": "c1", "document": "doc1", "metadata": {"doc_id": "1234567812345678"}}]
        )
        assert mock_col.upsert.called


# ---------------------------------------------------------------------------
# Test 9: Flask Admin Crawler Endpoints
# ---------------------------------------------------------------------------
def test_admin_crawler_endpoints():
    client = app.test_client()

    # 0. Unauthorized access without password returns 401
    res_unauth = client.post("/admin/api/crawl/start", json={})
    assert res_unauth.status_code == 401

    auth_headers = {"X-Admin-Password": "cdc-admin-2026"}

    # 1. Validation error on missing root_url (with auth)
    res_bad = client.post("/admin/api/crawl/start", json={}, headers=auth_headers)
    assert res_bad.status_code == 400
    assert "error" in res_bad.get_json()

    # 2. Validation error on non-http scheme
    res_scheme = client.post("/admin/api/crawl/start", json={"root_url": "ftp://bad.com"}, headers=auth_headers)
    assert res_scheme.status_code == 400

    # 3. Successful crawl start
    with patch("crawler_engine.SiteCrawler.start"):
        res_ok = client.post(
            "/admin/api/crawl/start",
            json={
                "root_url": "https://cdcpakistan.com",
                "max_pages": 10,
                "delay_seconds": 1.0,
                "auto_approve": False
            },
            headers=auth_headers
        )
        assert res_ok.status_code == 202
        data = res_ok.get_json()
        assert data["success"] is True
        crawl_id = data["crawl_id"]
        assert crawl_id.startswith("crawl_")

        # 4. Status endpoint
        res_status = client.get(f"/admin/api/crawl/status/{crawl_id}", headers=auth_headers)
        assert res_status.status_code == 200
        status_data = res_status.get_json()
        assert status_data["success"] is True
        assert status_data["job"]["crawl_id"] == crawl_id

        # 5. Stop endpoint
        res_stop = client.post(f"/admin/api/crawl/stop/{crawl_id}", headers=auth_headers)
        assert res_stop.status_code == 200
        assert res_stop.get_json()["success"] is True

        # 6. List jobs endpoint (with auth)
        res_jobs = client.get("/admin/api/crawl/jobs", headers=auth_headers)
        assert res_jobs.status_code == 200
        jobs_data = res_jobs.get_json()
        assert "jobs" in jobs_data
        assert any(j["crawl_id"] == crawl_id for j in jobs_data["jobs"])
