"""
Unit & Integration Tests for Human Stealth Browser & Anti-Block Simulation
CDC Regulatory Phase 1 RAG Assistant
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend and root are in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "backend"))

from crawler_engine import HumanStealthBrowser, SiteCrawler, start_crawl, get_crawl_job
from app import app

AUTH_HEADERS = {"X-Admin-Password": "cdc-admin-2026"}


# ---------------------------------------------------------------------------
# Test 1: HumanStealthBrowser Initialization & Binary Detection
# ---------------------------------------------------------------------------
def test_stealth_browser_init():
    browser = HumanStealthBrowser()
    assert browser.user_agent is not None
    # On Windows machine with Chrome installed, binary path is discovered or None
    assert browser.chrome_path is None or os.path.isfile(browser.chrome_path)


# ---------------------------------------------------------------------------
# Test 2: Fallback when Headless Chrome is unavailable
# ---------------------------------------------------------------------------
def test_stealth_browser_fallback_when_no_chrome():
    browser = HumanStealthBrowser()
    browser.chrome_path = None  # Simulate Chrome missing

    logs = []
    def log_capture(msg, level="INFO"):
        logs.append(msg)

    with patch("crawler_engine.cffi_requests", None), patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Fallback content</body></html>"
        mock_get.return_value = mock_resp

        html, meta = browser.fetch_with_human_simulation("https://cdcpakistan.com/rules", logger_func=log_capture)

        assert "Fallback content" in html
        assert meta["success"] is True
        assert any("Falling back" in l for l in logs)


# ---------------------------------------------------------------------------
# Test 3: Simulated CDP Interaction Protocol (Scrolling, Clicks, DOM Dump)
# ---------------------------------------------------------------------------
def test_stealth_browser_cdp_flow():
    browser = HumanStealthBrowser()
    browser.chrome_path = r"C:\fake\chrome.exe"

    mock_ws = MagicMock()
    sent_messages = []

    def fake_send(msg):
        sent_messages.append(json.loads(msg))

    def fake_recv():
        if not sent_messages:
            return json.dumps({"result": {}})
        last = sent_messages[-1]
        msg_id = last.get("id")
        params = last.get("params", {})
        expr = params.get("expression", "")

        if "document.readyState" in expr:
            return json.dumps({"id": msg_id, "result": {"result": {"value": "complete"}}})
        elif "window.scrollBy" in expr:
            return json.dumps({"id": msg_id, "result": {"result": {"value": {"y": 500}}}})
        elif "clicked: actions" in expr or "document.querySelectorAll" in expr:
            return json.dumps({
                "id": msg_id,
                "result": {
                    "result": {
                        "value": {
                            "clicked": ["Dismissed: Accept Cookies", "Expanded accordion: Regulations 2026"],
                            "title": "CDC Regulations & Circulars",
                            "url": "https://cdcpakistan.com/regulations"
                        }
                    }
                }
            })
        elif "document.documentElement.outerHTML" in expr:
            return json.dumps({
                "id": msg_id,
                "result": {
                    "result": {
                        "value": "<html><head><title>CDC Regulations & Circulars</title></head><body><h1>Official CDC Regulatory Directives</h1><p>Compliance notice for participants.</p></body></html>"
                    }
                }
            })
        return json.dumps({"id": msg_id, "result": {}})

    mock_ws.send.side_effect = fake_send
    mock_ws.recv.side_effect = fake_recv

    logs = []
    def log_capture(msg, level="INFO"):
        logs.append(msg)

    with patch("subprocess.Popen") as mock_popen, \
         patch("urllib.request.urlopen") as mock_urlopen, \
         patch("websocket.create_connection", return_value=mock_ws):

        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        mock_http_resp = MagicMock()
        mock_http_resp.read.return_value = json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1:9255/devtools/page/1"}).encode("utf-8")
        mock_http_resp.__enter__.return_value = mock_http_resp
        mock_urlopen.return_value = mock_http_resp

        html, meta = browser.fetch_with_human_simulation("https://cdcpakistan.com/regulations", logger_func=log_capture)

        assert meta["success"] is True
        assert meta["engine"] == "chrome_cdp"
        assert len(meta["buttons_clicked"]) == 2
        assert "Dismissed: Accept Cookies" in meta["buttons_clicked"]
        assert "Official CDC Regulatory Directives" in html
        assert any("Auto-interacted" in l for l in logs)
        assert any("human scrolling" in l for l in logs)


# ---------------------------------------------------------------------------
# Test 4: SiteCrawler Stealth Mode State & Reporting
# ---------------------------------------------------------------------------
def test_site_crawler_stealth_configuration():
    crawler_stealth = SiteCrawler(
        root_url="https://cdcpakistan.com",
        max_pages=5,
        stealth_mode=True
    )
    assert crawler_stealth.stealth_mode is True
    assert crawler_stealth.stealth_browser is not None
    status = crawler_stealth.get_status()
    assert status["stealth_mode"] is True

    crawler_non_stealth = SiteCrawler(
        root_url="https://cdcpakistan.com",
        max_pages=5,
        stealth_mode=False
    )
    assert crawler_non_stealth.stealth_mode is False
    assert crawler_non_stealth.stealth_browser is None
    status_off = crawler_non_stealth.get_status()
    assert status_off["stealth_mode"] is False


# ---------------------------------------------------------------------------
# Test 5: SiteCrawler Ingesting via Stealth Human Browser
# ---------------------------------------------------------------------------
def test_site_crawler_execution_with_stealth():
    crawler = SiteCrawler(
        root_url="https://teststealth.com",
        max_pages=1,
        delay_seconds=0.0,
        check_robots=False,
        stealth_mode=True
    )
    crawler._force_stealth = True  # Enable stealth execution under pytest

    mock_html = """
    <html>
        <head><title>Stealth Crawled Directive</title></head>
        <body>
            <h1>CDC Sub-Account Operating Rules</h1>
            <p>Mandatory biometric verification requirements for all active CDS participants.</p>
        </body>
    </html>
    """
    mock_meta = {
        "engine": "chrome_cdp",
        "scroll_steps": 4,
        "buttons_clicked": ["Dismissed: Accept All"],
        "title": "Stealth Crawled Directive",
        "success": True
    }

    with patch.object(crawler.stealth_browser, "fetch_with_human_simulation", return_value=(mock_html, mock_meta)) as mock_sim, \
         patch("review_manager.add_pending_item") as mock_add_pending:

        crawler.run()

        assert mock_sim.called
        assert crawler.pages_crawled == 1
        assert crawler.status == "completed"
        assert mock_add_pending.called
        args, kwargs = mock_add_pending.call_args
        assert kwargs["title"] == "Stealth Crawled Directive"
        assert "biometric verification requirements" in kwargs["full_text"]


# ---------------------------------------------------------------------------
# Test 6: API Integration: POST /admin/api/crawl/start with stealth_mode
# ---------------------------------------------------------------------------
def test_admin_api_crawl_start_stealth():
    client = app.test_client()

    with patch("crawler_engine.start_crawl") as mock_start:
        mock_start.return_value = "crawl_test_stealth_123"

        # Explicit stealth_mode = True (with admin auth header)
        resp = client.post(
            "/admin/api/crawl/start",
            json={
                "root_url": "https://cdcpakistan.com",
                "max_pages": 10,
                "stealth_mode": True
            },
            headers=AUTH_HEADERS
        )
        assert resp.status_code == 202
        args, kwargs = mock_start.call_args
        assert kwargs["stealth_mode"] is True

        # Explicit stealth_mode = False
        resp = client.post(
            "/admin/api/crawl/start",
            json={
                "root_url": "https://cdcpakistan.com",
                "max_pages": 10,
                "stealth_mode": False
            },
            headers=AUTH_HEADERS
        )
        assert resp.status_code == 202
        args, kwargs = mock_start.call_args
        assert kwargs["stealth_mode"] is False
