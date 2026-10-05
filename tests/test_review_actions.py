"""
Unit and integration tests for Review Queue Actions:
- Approve Only (Staged Approval)
- Approve & Live (Publish to Chroma)
- Reject / Discard (Resilient cleanup of DB, files, and Chroma)
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "backend"))

import review_manager
from app import app


@pytest.fixture
def sample_staged_item():
    """Inserts a clean sample document into pending_review SQLite."""
    import hashlib
    test_url = "https://cdcpakistan.com/regulations/sample-test-doc/"
    doc_id = hashlib.sha256(test_url.encode("utf-8")).hexdigest()[:16]

    review_manager.init_db()
    item_id = review_manager.add_pending_item(
        doc_id=doc_id,
        source=test_url,
        source_type="html",
        title="Sample Regulatory Test Document",
        full_text="Official CDC Operating Procedures detailing depository participant obligations and compliance standards.",
        chunks=[
            {
                "id": f"{doc_id}_chunk_0",
                "document": "Official CDC Operating Procedures detailing depository participant obligations.",
                "metadata": {"doc_id": doc_id, "page_number": 1}
            }
        ],
        category="Regulations / Procedures"
    )
    yield item_id, doc_id

    # Cleanup SQLite
    conn = review_manager.get_db_connection()
    try:
        with conn:
            conn.execute("DELETE FROM review_queue WHERE doc_id = ?", (doc_id,))
    finally:
        conn.close()

    # Cleanup documents.jsonl
    raw_docs_path = review_manager.get_project_root() / "data" / "raw" / "documents.jsonl"
    if raw_docs_path.exists():
        lines = raw_docs_path.read_text(encoding="utf-8").splitlines(keepends=True)
        filtered = [l for l in lines if doc_id not in l]
        raw_docs_path.write_text("".join(filtered), encoding="utf-8")


def test_approve_staged_only(sample_staged_item):
    """Verify Approve Only marks item as staged_approved without pushing to live Chroma."""
    item_id, doc_id = sample_staged_item

    # Verify initial status
    item = review_manager.get_item(item_id)
    assert item["status"] == "pending_review"

    # Call approve_staged_item
    res = review_manager.approve_staged_item(item_id)
    assert res["success"] is True
    assert res["status"] == "staged_approved"

    # Verify item status is now staged_approved
    updated_item = review_manager.get_item(item_id)
    assert updated_item["status"] == "staged_approved"

    # Verify item still appears in get_pending_items so admin can inspect/make live later
    pending_items = review_manager.get_pending_items()
    assert any(it["id"] == item_id for it in pending_items)

    # Verify it can be tested in staged retrieval sandbox
    staged_matches = review_manager.search_staged_items("depository participant")
    assert any(m["doc_id"] == doc_id for m in staged_matches)


def test_approve_and_make_live(sample_staged_item):
    """Verify Approve & Live publishes to Chroma and updates status to approved."""
    item_id, doc_id = sample_staged_item

    with patch("review_manager.get_client"), patch("review_manager.get_collection") as mock_get_col:
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col

        res = review_manager.approve_item(item_id)
        assert res["success"] is True
        assert res["chunks_indexed"] == 1
        assert mock_col.upsert.called

        # Status becomes approved (no longer pending)
        item = review_manager.get_item(item_id)
        assert item["status"] == "approved"

        # Item no longer in pending review list
        pending_items = review_manager.get_pending_items()
        assert not any(it["id"] == item_id for it in pending_items)


def test_reject_item_resilience_and_cleanup(sample_staged_item):
    """Verify Reject discards item, removes files, and handles missing IDs gracefully."""
    item_id, doc_id = sample_staged_item

    # Create dummy upload file
    uploads_dir = review_manager.get_project_root() / "data" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dummy_file = uploads_dir / f"{doc_id}.html"
    dummy_file.write_text("dummy content", encoding="utf-8")
    assert dummy_file.exists()

    with patch("review_manager.get_client"), patch("review_manager.get_collection") as mock_get_col:
        mock_col = MagicMock()
        mock_get_col.return_value = mock_col

        res = review_manager.reject_item(item_id)
        assert res["success"] is True
        assert res["status"] == "rejected"
        assert mock_col.delete.called
        assert not dummy_file.exists()

        # Rejecting again on already rejected or non-existent ID should succeed cleanly without crashing
        res_repeat = review_manager.reject_item(item_id)
        assert res_repeat["success"] is True

        res_nonexistent = review_manager.reject_item("rev_nonexistent_999999")
        assert res_nonexistent["success"] is True


def test_api_endpoints_approve_staged_and_reject(sample_staged_item):
    """Test Flask routes for approve-staged, approve live, and reject."""
    item_id, _ = sample_staged_item
    client = app.test_client()
    auth_headers = {"X-Admin-Password": "cdc-admin-2026"}

    # 1. POST /admin/api/review/<item_id>/approve-staged
    res = client.post(f"/admin/api/review/{item_id}/approve-staged", json={}, headers=auth_headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["status"] == "staged_approved"

    # 2. POST /admin/api/review/<item_id>/reject
    res_reject = client.post(f"/admin/api/review/{item_id}/reject", json={"reason": "Testing reject"}, headers=auth_headers)
    assert res_reject.status_code == 200
    reject_data = res_reject.get_json()
    assert reject_data["success"] is True
    assert reject_data["status"] == "rejected"

    # 3. Reject on non-existent item should return 200 rather than 404
    res_ghost = client.post("/admin/api/review/rev_ghost_12345/reject", json={}, headers=auth_headers)
    assert res_ghost.status_code == 200
    assert res_ghost.get_json()["success"] is True
