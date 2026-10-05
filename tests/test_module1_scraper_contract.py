import os
import json
import hashlib
import re
import pytest

WHITELIST_PATH = "config/whitelist.json"
DOCUMENTS_PATH = "data/raw/documents.jsonl"
SCRAPE_SCRIPT_PATH = "scrape.py"

def test_whitelist_contract():
    """
    Shared Contract Section 1:
    config/whitelist.json
    - Must exist or be readable when configured
    - List of JSON objects
    - 'url' is string
    - 'type' in ['pdf', 'html']
    - 'label' is string
    """
    if not os.path.exists(WHITELIST_PATH):
        pytest.skip(f"Whitelist not created yet at {WHITELIST_PATH}")

    with open(WHITELIST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert isinstance(data, list), "Whitelist must be a JSON array"
    assert len(data) > 0, "Whitelist must have at least one entry"

    for idx, item in enumerate(data):
        assert isinstance(item, dict), f"Whitelist entry #{idx} must be an object"
        assert "url" in item, f"Entry #{idx} missing 'url'"
        assert "type" in item, f"Entry #{idx} missing 'type'"
        assert "label" in item, f"Entry #{idx} missing 'label'"
        assert item["type"] in ["pdf", "html"], f"Entry #{idx} type must be 'pdf' or 'html', got {item['type']}"
        assert isinstance(item["url"], str) and item["url"].strip(), f"Entry #{idx} url must be non-empty string"
        assert isinstance(item["label"], str) and item["label"].strip(), f"Entry #{idx} label must be non-empty string"

def test_documents_jsonl_contract():
    """
    Shared Contract Section 2:
    data/raw/documents.jsonl
    - One JSON object per line (JSONL, not JSON array)
    - doc_id: deterministic sha256 hex hash of source_url (first 16 chars)
    - source_url: string
    - source_type: 'pdf' | 'html'
    - title: non-empty string
    - date_scraped: ISO8601 UTC timestamp (e.g. 2026-09-17T10:00:00Z)
    - text: full cleaned extracted text, whitespace-normalized
    """
    if not os.path.exists(DOCUMENTS_PATH):
        pytest.skip(f"Documents file not found at {DOCUMENTS_PATH}")

    with open(DOCUMENTS_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) > 0, "documents.jsonl must contain at least one document"

    iso8601_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?$")

    for line_num, line in enumerate(lines, start=1):
        line_clean = line.strip()
        if not line_clean:
            continue

        try:
            doc = json.loads(line_clean)
        except json.JSONDecodeError as err:
            pytest.fail(f"Line {line_num} in {DOCUMENTS_PATH} is not valid JSON: {err}")

        # Required fields
        for field in ["doc_id", "source_url", "source_type", "title", "date_scraped", "text"]:
            assert field in doc, f"Line {line_num} missing required field '{field}'"

        # Check doc_id determinism: first 16 chars of sha256(source_url)
        expected_hash = hashlib.sha256(doc["source_url"].encode("utf-8")).hexdigest()[:16]
        assert doc["doc_id"] == expected_hash, (
            f"Line {line_num} doc_id '{doc['doc_id']}' does not match expected sha256 hash '{expected_hash}'"
        )

        assert doc["source_type"] in ["pdf", "html"], (
            f"Line {line_num} source_type must be 'pdf' or 'html', got '{doc['source_type']}'"
        )

        assert isinstance(doc["title"], str) and doc["title"].strip(), f"Line {line_num} title must be non-empty"
        assert isinstance(doc["text"], str) and len(doc["text"].strip()) > 20, f"Line {line_num} text too short or empty"
        assert iso8601_pattern.match(doc["date_scraped"]), (
            f"Line {line_num} date_scraped '{doc['date_scraped']}' is not valid ISO8601 UTC timestamp"
        )
