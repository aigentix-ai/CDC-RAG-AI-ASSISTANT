"""
Tests for Anti-Bot Security Challenge Interstitial Verifier & Purger
Verifies that Cloudflare Turnstile, Managed Challenges, WAF interstitials,
and system errors are strictly detected, rejected, and never staged or indexed.
"""

import os
import sys
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "backend"))

from content_verifier import (
    is_security_challenge_or_blocked,
    is_valid_regulatory_content,
    purge_challenge_artifacts_from_db
)
from crawler_engine import extract_html_page, SiteCrawler
import review_manager


def test_detects_exact_user_cloudflare_interstitial():
    """Validates the exact Cloudflare challenge screen encountered by the user."""
    cf_title = "Just a moment..."
    cf_text = """
    cdcpakistan.com
    Performing security verification
    This website uses a security service to protect against malicious bots. This page is displayed while the website verifies you are not a bot.
    Verification successful. Waiting for cdcpakistan.com to respond
    Ray ID: a441efe87badfe4b
    Performance and Security by Cloudflare
    Privacy
    """
    assert is_security_challenge_or_blocked(title=cf_title, text=cf_text) is True

    is_valid, reason = is_valid_regulatory_content(title=cf_title, text=cf_text)
    assert is_valid is False
    assert "challenge" in reason.lower() or "bot" in reason.lower()


def test_detects_various_waf_and_challenge_signatures():
    # Attention required title
    assert is_security_challenge_or_blocked("Attention Required! | Cloudflare", "Please complete security check") is True

    # Checking your browser
    assert is_security_challenge_or_blocked("Checking your browser", "DDoS protection by Cloudflare") is True

    # Cloudflare Ray ID in text
    assert is_security_challenge_or_blocked("Notice", "Access Denied. Cloudflare Ray ID: 8934789abcf23") is True

    # Raw HTML with cf-turnstile widget
    assert is_security_challenge_or_blocked(
        "Welcome",
        "Loading...",
        html_text='<html><body><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script><div class="cf-turnstile"></div></body></html>'
    ) is True


def test_allows_genuine_regulatory_content():
    legit_title = "CDC Pakistan Central Depository System Regulations"
    legit_text = """
    Section 8.1: Eligible Securities and Deposit Procedures.
    The Central Depository Company of Pakistan Limited regulations govern the deposit,
    transfer, and settlement of book-entry securities. Depository participants must maintain
    mandatory sub-accounts in compliance with SECP anti-money laundering and know-your-customer directives.
    All capital market transactions must comply with statutory settlement schedules.
    """
    assert is_security_challenge_or_blocked(title=legit_title, text=legit_text) is False

    is_valid, reason = is_valid_regulatory_content(title=legit_title, text=legit_text, min_words=10)
    assert is_valid is True
    assert "valid" in reason.lower()


def test_rejects_error_status_pages():
    is_valid_404, r_404 = is_valid_regulatory_content("404 Not Found", "The requested regulatory circular was not found.")
    assert is_valid_404 is False
    assert "error" in r_404.lower()

    is_valid_403, r_403 = is_valid_regulatory_content("403 Forbidden", "Access Denied by web server.")
    assert is_valid_403 is False

    is_valid_502, r_502 = is_valid_regulatory_content("502 Bad Gateway", "The upstream server failed to respond.")
    assert is_valid_502 is False


def test_extract_html_page_filters_challenge():
    cf_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Just a moment...</title></head>
    <body>
        <h1>Performing security verification</h1>
        <p>This website uses a security service to protect against malicious bots.</p>
        <p>Ray ID: 99482abc110</p>
        <a href="https://cdcpakistan.com/privacy">Cloudflare Privacy Policy</a>
        <a href="https://cdcpakistan.com/regulations">Fake Link</a>
    </body>
    </html>
    """
    extracted, links = extract_html_page(cf_html, "https://cdcpakistan.com/rules")
    assert extracted.get("is_challenge") is True
    assert extracted.get("text") == ""
    assert len(links) == 0  # No challenge links should be traversed


def test_review_manager_rejects_challenge_insertion():
    with pytest.raises(ValueError) as excinfo:
        review_manager.add_pending_item(
            doc_id="cf_bad_doc_1234",
            source="https://cdcpakistan.com/blocked",
            source_type="html",
            title="Just a moment...",
            full_text="Performing security verification This website uses a security service to protect against malicious bots.",
            chunks=[{"id": "c1", "text": "bad"}]
        )
    assert "cannot stage invalid document" in str(excinfo.value).lower()


def test_purge_challenge_artifacts_from_db(tmp_path):
    db_path = tmp_path / "test_pending.sqlite3"
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute("""
            CREATE TABLE review_queue (
                id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                date_created TEXT NOT NULL,
                status TEXT NOT NULL,
                preview_text TEXT NOT NULL,
                full_text TEXT NOT NULL,
                chunks_json TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                category TEXT DEFAULT 'General Compliance'
            );
        """)
        # Insert 1 legitimate item and 2 challenge items
        conn.execute("""
            INSERT INTO review_queue VALUES (
                'rev_good', 'doc_good', 'https://cdcpakistan.com/good', 'html',
                'SECP Circular No. 12 of 2026', '2026-10-02T12:00:00Z', 'pending_review',
                'Official regulatory directives for all CDC depository participants.',
                'Full text of circular 12...', '[]', 1, 'Circulars & Directives'
            )
        """)
        conn.execute("""
            INSERT INTO review_queue VALUES (
                'rev_bad1', 'doc_bad1', 'https://cdcpakistan.com/bad1', 'html',
                'Just a moment...', '2026-10-02T12:00:00Z', 'pending_review',
                'Performing security verification Ray ID: a441efe87badfe4b',
                'Full challenge text...', '[]', 1, 'General Compliance'
            )
        """)
        conn.execute("""
            INSERT INTO review_queue VALUES (
                'rev_bad2', 'doc_bad2', 'https://cdcpakistan.com/bad2', 'html',
                'Attention Required! | Cloudflare', '2026-10-02T12:00:00Z', 'pending_review',
                'Please complete security check to access cdcpakistan.com',
                'Full challenge text...', '[]', 1, 'General Compliance'
            )
        """)
    conn.close()

    purged = purge_challenge_artifacts_from_db(db_path)
    assert purged == 2

    # Verify only legitimate item remains
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM review_queue")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "rev_good"
    assert rows[0][1] == "SECP Circular No. 12 of 2026"


def test_crawler_detects_challenge_and_flags_captcha_block():
    crawler = SiteCrawler(
        root_url="https://cdcpakistan.com/test-challenge",
        max_pages=1,
        delay_seconds=0.0,
        check_robots=False,
    )
    cf_html = """
    <html>
    <head><title>Just a moment...</title></head>
    <body>
        <h1>Performing security verification</h1>
        <p>This website uses a security service to protect against malicious bots.</p>
        <p>Ray ID: a441efe87badfe4b</p>
    </body>
    </html>
    """
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = cf_html
        mock_resp.content = cf_html.encode("utf-8")
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.apparent_encoding = "utf-8"
        mock_get.return_value = mock_resp

        with patch.object(crawler, "_stage_to_review_queue") as mock_stage:
            crawler.run()

            # The challenge page must NEVER be staged or indexed
            assert mock_stage.call_count == 0
            # Captcha / security challenge must be flagged
            assert crawler.has_captcha_block is True
            assert crawler.last_captcha_url == "https://cdcpakistan.com/test-challenge"
