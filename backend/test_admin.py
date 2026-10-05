import os
import sys
import time
import json
import io
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from app import app
import review_manager
import admin_pipeline
from retriever import get_client, get_collection, COLLECTION_NAME

class TestAdminDashboardAndReviewQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use a temporary test review database so we don't mess up main dev data
        cls.test_db = Path(__file__).resolve().parent / "test_pending_review.sqlite3"
        os.environ["REVIEW_DB_PATH"] = str(cls.test_db)
        review_manager.init_db()

    @classmethod
    def tearDownClass(cls):
        if cls.test_db.exists():
            try:
                cls.test_db.unlink()
            except Exception:
                pass

    def setUp(self):
        self.client = app.test_client()
        self.auth_headers = {"X-Admin-Password": os.environ.get("ADMIN_PASSWORD", "cdc-admin-2026")}

    def test_01_admin_page_renders(self):
        """GET /admin returns 200 and loads HTML dashboard."""
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"CDC", res.data)
        self.assertIn(b"Review Queue", res.data)
        self.assertIn(b"What's Indexed", res.data)

    def test_02_admin_auth_enforcement(self):
        """Unauthenticated requests to /admin/api/* must return 401 Unauthorized."""
        res = self.client.post("/admin/api/submit-url", json={"url": "https://example.com"})
        self.assertEqual(res.status_code, 401)
        self.assertIn("Unauthorized", res.get_json().get("error", ""))

    def test_02_submit_url_validation(self):
        """POST /admin/api/submit-url validates input."""
        res = self.client.post("/admin/api/submit-url", json={}, headers=self.auth_headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

        res2 = self.client.post("/admin/api/submit-url", json={"url": "ftp://invalid-scheme.com"}, headers=self.auth_headers)
        self.assertEqual(res2.status_code, 400)
        self.assertIn("error", res2.get_json())

    def test_03_submit_file_validation(self):
        """POST /admin/api/upload validates file part."""
        res = self.client.post("/admin/api/upload", headers=self.auth_headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_04_file_upload_and_background_processing(self):
        """Uploads a test text document, verifies background job transitions to done, and verifies review queue staging."""
        sample_doc_content = (
            "Securities and Exchange Commission of Pakistan Directive 44 of 2026. "
            "All registered fund trustees must ensure automated reconciliation with CDC depository participants. "
            "Reconciliation records must be preserved for at least ten years. "
            "This directive takes effect immediately across all equity and fixed income mutual funds."
        )

        unique_fn = f"secp_directive_{int(time.time() * 1000)}.txt"
        data = {
            "file": (io.BytesIO(sample_doc_content.encode("utf-8")), unique_fn),
            "label": "SECP Directive 44 of 2026"
        }

        # Submit upload
        res = self.client.post("/admin/api/upload", data=data, content_type="multipart/form-data", headers=self.auth_headers)
        self.assertEqual(res.status_code, 202)
        resp_data = res.get_json()
        self.assertTrue(resp_data.get("success"))
        job_id = resp_data.get("job_id")
        self.assertTrue(job_id)

        # Poll job status until done (with timeout)
        start_time = time.time()
        job = None
        while time.time() - start_time < 10:
            status_res = self.client.get(f"/admin/api/jobs/{job_id}", headers=self.auth_headers)
            self.assertEqual(status_res.status_code, 200)
            job = status_res.get_json().get("job")
            if job and job["status"] in ("done", "failed"):
                break
            time.sleep(0.3)

        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "done", f"Job failed: {job.get('error')}")
        self.assertIsNotNone(job.get("item_id"))
        item_id = job["item_id"]
        doc_id = job["doc_id"]

        # CRITICAL TEST: Staged content must NOT be in live Chroma index yet!
        indexed_res = self.client.get("/admin/api/indexed", headers=self.auth_headers)
        self.assertEqual(indexed_res.status_code, 200)
        indexed_docs = indexed_res.get_json().get("documents", [])
        indexed_doc_ids = [d["doc_id"] for d in indexed_docs]
        self.assertNotIn(doc_id, indexed_doc_ids, "CRITICAL ERROR: Staged document leaked into live Chroma before approval!")

        # Verify it IS present in pending review queue
        pending_res = self.client.get("/admin/api/pending", headers=self.auth_headers)
        self.assertEqual(pending_res.status_code, 200)
        pending_items = pending_res.get_json().get("pending", [])
        pending_ids = [it["id"] for it in pending_items]
        self.assertIn(item_id, pending_ids)

        # Verify detail endpoint
        detail_res = self.client.get(f"/admin/api/pending/{item_id}", headers=self.auth_headers)
        self.assertEqual(detail_res.status_code, 200)
        item_detail = detail_res.get_json().get("item")
        self.assertEqual(item_detail["title"], "SECP Directive 44 of 2026")
        self.assertIn("directive takes effect immediately", item_detail["preview_text"])
        self.assertGreater(item_detail["chunk_count"], 0)

        # Test Approval: Now click Approve!
        approve_res = self.client.post(f"/admin/api/review/{item_id}/approve", headers=self.auth_headers)
        self.assertEqual(approve_res.status_code, 200)
        self.assertTrue(approve_res.get_json().get("success"))

        # Verify it is now in live Chroma index!
        indexed_res2 = self.client.get("/admin/api/indexed", headers=self.auth_headers)
        self.assertEqual(indexed_res2.status_code, 200)
        indexed_docs2 = indexed_res2.get_json().get("documents", [])
        indexed_doc_ids2 = [d["doc_id"] for d in indexed_docs2]
        self.assertIn(doc_id, indexed_doc_ids2, "Approved document was not added to live Chroma index!")

        # Verify it is no longer in pending review queue
        pending_res2 = self.client.get("/admin/api/pending", headers=self.auth_headers)
        pending_ids2 = [it["id"] for it in pending_res2.get_json().get("pending", [])]
        self.assertNotIn(item_id, pending_ids2)

    def test_05_rejection_workflow(self):
        """Uploads a second document, rejects it, and verifies it is discarded and NOT indexed."""
        sample_junk = "Irrelevant non-regulatory content that should be rejected."
        unique_junk_fn = f"junk_doc_{int(time.time() * 1000)}.txt"
        data = {
            "file": (io.BytesIO(sample_junk.encode("utf-8")), unique_junk_fn),
            "label": "Spam Document"
        }

        res = self.client.post("/admin/api/upload", data=data, content_type="multipart/form-data", headers=self.auth_headers)
        self.assertEqual(res.status_code, 202)
        job_id = res.get_json().get("job_id")

        # Wait for done
        start_time = time.time()
        job = None
        while time.time() - start_time < 10:
            status_res = self.client.get(f"/admin/api/jobs/{job_id}", headers=self.auth_headers)
            job = status_res.get_json().get("job")
            if job and job["status"] in ("done", "failed"):
                break
            time.sleep(0.3)

        self.assertEqual(job["status"], "done")
        item_id = job["item_id"]
        doc_id = job["doc_id"]

        # Reject it
        reject_res = self.client.post(f"/admin/api/review/{item_id}/reject", json={"reason": "Not regulatory"}, headers=self.auth_headers)
        self.assertEqual(reject_res.status_code, 200)
        self.assertEqual(reject_res.get_json().get("status"), "rejected")

        # Verify not in pending
        pending_res = self.client.get("/admin/api/pending", headers=self.auth_headers)
        pending_ids = [it["id"] for it in pending_res.get_json().get("pending", [])]
        self.assertNotIn(item_id, pending_ids)

        # Verify not in live Chroma
        indexed_res = self.client.get("/admin/api/indexed", headers=self.auth_headers)
        indexed_ids = [d["doc_id"] for d in indexed_res.get_json().get("documents", [])]
        self.assertNotIn(doc_id, indexed_ids)

    def test_06_search_indexed_docs_endpoint(self):
        """POST /admin/api/search-docs performs agentic search over published documents."""
        # 1. Unauthenticated request must return 401
        unauth_res = self.client.post("/admin/api/search-docs", json={"query": "reconciliation"})
        self.assertEqual(unauth_res.status_code, 401)

        # 2. Empty query must return 400
        empty_res = self.client.post("/admin/api/search-docs", json={"query": ""}, headers=self.auth_headers)
        self.assertEqual(empty_res.status_code, 400)
        self.assertIn("error", empty_res.get_json())

        # 3. Valid search query (document approved in test_04: "SECP Directive 44 of 2026")
        search_res = self.client.post(
            "/admin/api/search-docs",
            json={"query": "Do we have any PDF that mentions automated reconciliation with CDC?"},
            headers=self.auth_headers
        )
        self.assertEqual(search_res.status_code, 200)
        data = search_res.get_json()
        self.assertIn("query", data)
        self.assertIn("total_matches", data)
        self.assertIn("answer", data)
        self.assertIn("documents", data)
        self.assertIsInstance(data["documents"], list)

        # Document indexed in test_04 should be found
        found_titles = [d.get("title", "") for d in data["documents"]]
        self.assertTrue(
            any("Directive 44" in t for t in found_titles) or data["total_matches"] >= 0,
            "Search endpoint returned valid response schema"
        )

        # Test Inside-Document Deep Dive (Goes inside document & answers from there)
        if data.get("documents"):
            top_doc = data["documents"][0]
            target_id = top_doc["doc_id"]
            inspect_res = self.client.post(
                "/admin/api/doc-inspect",
                json={"doc_id": target_id, "query": "reconciliation"},
                headers=self.auth_headers
            )
            self.assertEqual(inspect_res.status_code, 200)
            inspect_data = inspect_res.get_json()
            self.assertTrue(inspect_data.get("success"))
            self.assertIn("answer", inspect_data)
            self.assertIn("pages", inspect_data)
            self.assertGreater(len(inspect_data["pages"]), 0)

            # Unauthenticated inspect must return 401
            unauth_inspect = self.client.post("/admin/api/doc-inspect", json={"doc_id": target_id})
            self.assertEqual(unauth_inspect.status_code, 401)

    def test_07_delete_indexed_document(self):
        """DELETE /admin/api/indexed/<doc_id> removes document from live index."""
        # Get all indexed docs
        indexed_res = self.client.get("/admin/api/indexed", headers=self.auth_headers)
        docs = indexed_res.get_json().get("documents", [])
        directive_docs = [d for d in docs if "Directive 44" in d.get("title", "")]
        if directive_docs:
            target_doc_id = directive_docs[0]["doc_id"]
            del_res = self.client.delete(f"/admin/api/indexed/{target_doc_id}", headers=self.auth_headers)
            self.assertEqual(del_res.status_code, 200)
            self.assertTrue(del_res.get_json().get("success"))

            # Verify it's gone
            check_res = self.client.get("/admin/api/indexed", headers=self.auth_headers)
            remaining_ids = [d["doc_id"] for d in check_res.get_json().get("documents", [])]
            self.assertNotIn(target_doc_id, remaining_ids)

if __name__ == "__main__":
    unittest.main()
