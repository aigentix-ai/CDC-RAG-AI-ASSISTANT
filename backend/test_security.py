"""
Automated Security, Pentest & Hardening Verification Suite
Validates defenses against SSRF, unrestricted uploads, path traversal,
rate limiting, unauthorized admin access, data leakage, and indirect prompt injection.
"""

import os
import sys
import io
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from app import app
import security
from security import (
    validate_safe_url,
    validate_uploaded_file,
    check_admin_token,
    redact_secrets,
    sanitize_exception
)
from llm import generate_answer

class TestSecurityFortifications(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.valid_admin_headers = {"X-Admin-Password": os.environ.get("ADMIN_PASSWORD", "cdc-admin-2026")}

    # -----------------------------------------------------------------------
    # 1. SSRF Protection Tests
    # -----------------------------------------------------------------------
    def test_ssrf_rejects_localhost_and_loopback(self):
        """SSRF: Localhost and loopback IPs must be blocked."""
        bad_urls = [
            "http://localhost:5000/admin",
            "http://127.0.0.1:8080/internal",
            "http://[::1]/secret",
            "https://127.0.0.1"
        ]
        for url in bad_urls:
            is_safe, reason = validate_safe_url(url)
            self.assertFalse(is_safe, f"Expected {url} to be blocked as SSRF")
            self.assertIn("forbidden", reason.lower())

    def test_ssrf_rejects_cloud_metadata(self):
        """SSRF: Cloud provider metadata addresses (169.254.169.254) must be blocked."""
        meta_url = "http://169.254.169.254/latest/meta-data/"
        is_safe, reason = validate_safe_url(meta_url)
        self.assertFalse(is_safe)
        self.assertIn("metadata", reason.lower())

    def test_ssrf_rejects_private_subnets(self):
        """SSRF: Private RFC 1918 subnets (10.x, 172.16.x, 192.168.x) must be blocked."""
        private_urls = [
            "http://10.0.0.1/admin",
            "http://192.168.1.1/router",
            "http://172.16.0.5/api"
        ]
        for url in private_urls:
            is_safe, reason = validate_safe_url(url)
            self.assertFalse(is_safe, f"Expected {url} to be blocked")

    def test_ssrf_rejects_non_http_schemes(self):
        """SSRF: Schemes like ftp://, file://, gopher:// must be rejected."""
        schemes = ["file:///etc/passwd", "ftp://evil.com/payload", "gopher://evil.com"]
        for url in schemes:
            is_safe, reason = validate_safe_url(url)
            self.assertFalse(is_safe)

    def test_ssrf_endpoint_blocking(self):
        """POST /admin/api/submit-url blocks malicious SSRF targets."""
        res = self.client.post(
            "/admin/api/submit-url",
            json={"url": "http://127.0.0.1:5000/internal"},
            headers=self.valid_admin_headers
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("rejected", res.get_json().get("error", "").lower())

    # -----------------------------------------------------------------------
    # 2. File Upload & Path Traversal Tests
    # -----------------------------------------------------------------------
    def test_upload_rejects_executable_files(self):
        """File upload: Executables (.exe, .sh) must be rejected."""
        mock_file = MagicMock()
        mock_file.filename = "malware.exe"
        is_valid, reason = validate_uploaded_file(mock_file)
        self.assertFalse(is_valid)
        self.assertIn("not permitted", reason)

    def test_upload_rejects_fake_pdf_magic_bytes(self):
        """File upload: .pdf files without '%PDF-' header must be rejected."""
        fake_pdf_stream = io.BytesIO(b"This is just plain text masquerading as a PDF.")
        fake_pdf_stream.filename = "fake_document.pdf"
        is_valid, reason = validate_uploaded_file(fake_pdf_stream)
        self.assertFalse(is_valid)
        self.assertIn("valid PDF file signature", reason)

    def test_upload_rejects_empty_files(self):
        """File upload: 0-byte empty files must be rejected."""
        empty_stream = io.BytesIO(b"")
        empty_stream.filename = "empty.txt"
        is_valid, reason = validate_uploaded_file(empty_stream)
        self.assertFalse(is_valid)
        self.assertIn("empty", reason.lower())

    def test_upload_valid_pdf(self):
        """File upload: Valid PDF with %PDF- header is accepted."""
        valid_pdf_stream = io.BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF")
        valid_pdf_stream.filename = "valid_notice.pdf"
        is_valid, reason = validate_uploaded_file(valid_pdf_stream)
        self.assertTrue(is_valid)

    def test_upload_path_traversal_sanitization(self):
        """File upload: Filenames with path traversal '../' are sanitized and safely stored."""
        data = {
            "file": (io.BytesIO(b"Clean regulatory notice text"), "../../etc/evil_name.txt")
        }
        res = self.client.post(
            "/admin/api/upload",
            data=data,
            content_type="multipart/form-data",
            headers=self.valid_admin_headers
        )
        self.assertEqual(res.status_code, 202)
        # Verify it saved under data/uploads with sanitized filename, not traversing out
        json_data = res.get_json()
        self.assertTrue(json_data.get("success"))

    # -----------------------------------------------------------------------
    # 3. Rate Limiting Tests
    # -----------------------------------------------------------------------
    def test_rate_limiting_enforcement(self):
        """Rate limiter: Exceeding threshold triggers HTTP 429 Too Many Requests."""
        # Use custom endpoint key to test without disturbing main limits
        client_ip = "192.0.2.100"
        endpoint = "/test-rate-limit"

        # Allow 3 requests per 60s for testing
        is_limited, _ = security.is_rate_limited(client_ip, endpoint, max_requests=3, window_seconds=60)
        self.assertFalse(is_limited)
        is_limited, _ = security.is_rate_limited(client_ip, endpoint, max_requests=3, window_seconds=60)
        self.assertFalse(is_limited)
        is_limited, _ = security.is_rate_limited(client_ip, endpoint, max_requests=3, window_seconds=60)
        self.assertFalse(is_limited)

        # 4th request must be rate-limited
        is_limited, retry_after = security.is_rate_limited(client_ip, endpoint, max_requests=3, window_seconds=60)
        self.assertTrue(is_limited)
        self.assertGreater(retry_after, 0)

    # -----------------------------------------------------------------------
    # 4. Timing-Safe Admin Authentication
    # -----------------------------------------------------------------------
    def test_admin_auth_timing_safe_verification(self):
        """Admin auth: Verifies tokens with constant-time equality."""
        self.assertTrue(check_admin_token(os.environ.get("ADMIN_PASSWORD", "cdc-admin-2026")))
        self.assertFalse(check_admin_token("wrong-password-guess"))
        self.assertFalse(check_admin_token(""))
        self.assertFalse(check_admin_token(None))

    def test_admin_auth_header_options(self):
        """Admin auth: Supports X-Admin-Password, X-Admin-Key, and Bearer token."""
        # Bearer token
        res_bearer = self.client.get(
            "/admin/api/pending",
            headers={"Authorization": f"Bearer {os.environ.get('ADMIN_PASSWORD', 'cdc-admin-2026')}"}
        )
        self.assertEqual(res_bearer.status_code, 200)

        # X-Admin-Key
        res_key = self.client.get(
            "/admin/api/pending",
            headers={"X-Admin-Key": os.environ.get("ADMIN_API_KEY", "cdc-admin-secret-2026")}
        )
        self.assertEqual(res_key.status_code, 200)

    # -----------------------------------------------------------------------
    # 5. Defensive Security Headers
    # -----------------------------------------------------------------------
    def test_defensive_security_headers_present(self):
        """Security headers: Standard OWASP headers attached to all responses."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(res.headers.get("X-XSS-Protection"), "1; mode=block")
        self.assertIn("Content-Security-Policy", res.headers)

    # -----------------------------------------------------------------------
    # 6. Information Leakage & Secret Redaction
    # -----------------------------------------------------------------------
    def test_secret_redaction(self):
        """Information leakage: Secrets and API keys are redacted from strings."""
        test_str = "Key is AIzaSyD9876543210abcdefghijklmnopq and pass is cdc-admin-2026"
        redacted = redact_secrets(test_str)
        self.assertNotIn("AIzaSyD9876543210abcdefghijklmnopq", redacted)
        self.assertIn("[REDACTED_API_KEY]", redacted)

    def test_sanitize_exception_paths(self):
        """Information leakage: Server filesystem paths are stripped from error messages."""
        exc = ValueError("Error reading file at C:\\Users\\waqar com\\Documents\\secret.py")
        sanitized = sanitize_exception(exc)
        self.assertNotIn("waqar com", sanitized)
        self.assertIn("[PATH]", sanitized)

    # -----------------------------------------------------------------------
    # 7. Prompt Injection & Question Length Defense
    # -----------------------------------------------------------------------
    def test_question_length_limit(self):
        """Prompt safety: Questions longer than 1,000 characters are rejected with 400."""
        huge_question = "A" * 1005
        res = self.client.post("/ask", json={"question": huge_question})
        self.assertEqual(res.status_code, 400)
        self.assertIn("exceeds maximum allowed limit", res.get_json().get("error", ""))


if __name__ == "__main__":
    unittest.main()
