import os
import sys
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scrape import (
    CONFIG,
    RobotsManager,
    fetch_resource,
    classify_url,
    save_visited_cache,
    load_visited_cache,
    compute_doc_id
)

def test_classify_url_types():
    assert classify_url("https://example.com/report.pdf") == "pdf"
    assert classify_url("https://example.com/docs/file.docx") == "docx"
    assert classify_url("https://example.com/data/sheet.xlsx") == "xlsx"
    assert classify_url("https://example.com/data/sheet.xls") == "xlsx"
    assert classify_url("https://example.com/page.html") == "html"
    assert classify_url("https://example.com/regulatory/updates") == "html"

def test_robots_manager_disallow_and_allow():
    robots_txt = """User-agent: *
Allow: /private/public-report.pdf
Disallow: /private/
Disallow: /admin
"""
    manager = RobotsManager(user_agent="CDC-Demo-Assistant-Bot/1.0 (compliance research demo)")
    
    with patch("requests.get") as mock_get, patch("time.sleep"):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = robots_txt
        mock_get.return_value = mock_resp
        
        # Test disallowed
        assert manager.can_fetch("https://testdomain.com/private/secret.html") is False
        assert manager.can_fetch("https://testdomain.com/admin") is False
        
        # Test allowed
        assert manager.can_fetch("https://testdomain.com/about-us") is True
        assert manager.can_fetch("https://testdomain.com/private/public-report.pdf") is True

def test_exponential_backoff_on_429():
    with patch("requests.get") as mock_get, patch("time.sleep") as mock_sleep:
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "1"}
        
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.content = b"success content"
        resp_200.text = "success content"
        resp_200.headers = {"Content-Type": "text/html"}
        
        mock_get.side_effect = [resp_429, resp_200]
        
        content, ct = fetch_resource("https://testdomain.com/test-backoff", is_binary=False)
        
        assert content == "success content"
        assert mock_get.call_count == 2
        mock_sleep.assert_called()

def test_deterministic_doc_id():
    url = "https://example.com/sample"
    doc_id = compute_doc_id(url)
    assert len(doc_id) == 16
    assert doc_id == compute_doc_id(url)
