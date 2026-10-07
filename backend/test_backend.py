import os
import sys
import unittest
import json
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from retriever import retrieve, get_chroma_persist_dir, get_client, get_collection
from llm import generate_answer, _parse_llm_response, DONT_KNOW_ANSWER
from app import app

class TestBackendModule(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_01_retriever_seeded_data(self):
        """Verify retriever connects to Chroma and returns exact contract schema."""
        chunks = retrieve("SECP Circular 12 of 2025", top_k=3)
        self.assertIsInstance(chunks, list)
        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            self.assertIn("text", chunk)
            self.assertIn("title", chunk)
            self.assertIn("source_url", chunk)
            self.assertIn("doc_id", chunk)
            self.assertIsInstance(chunk["text"], str)
            self.assertIsInstance(chunk["title"], str)
            self.assertIsInstance(chunk["source_url"], str)
            self.assertIsInstance(chunk["doc_id"], str)

    def test_02_retriever_empty_query(self):
        """Verify retriever handles blank queries safely."""
        self.assertEqual(retrieve(""), [])
        self.assertEqual(retrieve("   "), [])

    def test_03_llm_empty_chunks(self):
        """Contract: When chunks are empty, return exact 'I don't know' string and empty citations."""
        result = generate_answer("What is the regulation?", [])
        self.assertEqual(result, {
            "answer": DONT_KNOW_ANSWER,
            "citations": []
        })

    def test_04_llm_parse_dont_know(self):
        """Contract: Model response containing 'I don't know' normalizes to exact answer and empty citations."""
        chunks_by_id = {
            "doc1": {"title": "SECP Circular", "source_url": "https://secp.gov.pk", "doc_id": "doc1"}
        }
        res = _parse_llm_response("I don't know based on the available sources.", chunks_by_id)
        self.assertEqual(res["answer"], DONT_KNOW_ANSWER)
        self.assertEqual(res["citations"], [])

    def test_05_llm_parse_and_deduplicate(self):
        """Contract: Citations must be deduplicated by doc_id."""
        chunks_by_id = {
            "doc1": {"title": "SECP Circular", "source_url": "https://secp.gov.pk", "doc_id": "doc1"},
            "doc2": {"title": "CDC Notice", "source_url": "https://cdcpakistan.com", "doc_id": "doc2"}
        }
        json_output = json.dumps({
            "answer": "All AMCs must submit monthly compliance reports.",
            "used_doc_ids": ["doc1", "doc1", "doc2"]
        })
        res = _parse_llm_response(json_output, chunks_by_id)
        self.assertEqual(res["answer"], "All AMCs must submit monthly compliance reports.")
        self.assertEqual(len(res["citations"]), 2)
        doc_ids = [c["doc_id"] for c in res["citations"]]
        self.assertEqual(doc_ids, ["doc1", "doc2"])

    def test_06_app_ask_validation_missing_body(self):
        """Verify POST /ask returns 400 if body is missing or non-JSON."""
        res = self.client.post("/ask", data="plain text", content_type="text/plain")
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

    def test_07_app_ask_validation_missing_question(self):
        """Verify POST /ask returns 400 if 'question' field is missing or empty."""
        res = self.client.post("/ask", json={})
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

        res2 = self.client.post("/ask", json={"question": "   "})
        self.assertEqual(res2.status_code, 400)
        self.assertIn("error", res2.get_json())

        res3 = self.client.post("/ask", json={"question": 123})
        self.assertEqual(res3.status_code, 400)
        self.assertIn("error", res3.get_json())

    def test_08_app_cors_headers(self):
        """Verify CORS headers are present on /ask."""
        res = self.client.options("/ask", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST"
        })
        self.assertIn(res.status_code, [200, 204])
        self.assertIn(res.headers.get("Access-Control-Allow-Origin"), ["*", "http://localhost:3000"])

    def test_09_app_ask_500_on_unhandled_exception(self):
        """Verify POST /ask returns 500 with {"error": "..."} on unhandled exception."""
        with patch("app.retrieve", side_effect=RuntimeError("Database connection lost")):
            res = self.client.post("/ask", json={"question": "What is SECP Circular 12?"})
            self.assertEqual(res.status_code, 500)
            data = res.get_json()
            self.assertIn("error", data)
            self.assertEqual(data["error"], "Database connection lost")

    def test_09b_app_ask_demo_fallback_without_api_key(self):
        """Verify POST /ask gracefully falls back to grounded demo context when GEMINI_API_KEY is not set."""
        with patch.dict(os.environ, {}, clear=True):
            res = self.client.post("/ask", json={"question": "What is SECP Circular 12?"})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("answer", data)
            self.assertIn("citations", data)

    def test_10_app_ask_200_success_contract(self):
        """Verify POST /ask returns 200 with exact contract shape when answered."""
        mock_result = {
            "answer": "AMCs must submit monthly compliance reports.",
            "citations": [
                {
                    "title": "SECP Circular 12 of 2025",
                    "source_url": "https://example-secp-circular.pdf",
                    "doc_id": "9f86d081884c7d65"
                }
            ]
        }
        with patch("app.generate_answer", return_value=mock_result):
            res = self.client.post("/ask", json={"question": "What must AMCs submit?"})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["answer"], "AMCs must submit monthly compliance reports.")
            self.assertEqual(len(data["citations"]), 1)
            self.assertEqual(data["citations"][0]["doc_id"], "9f86d081884c7d65")

    def test_11_app_ask_dont_know_contract(self):
        """Verify POST /ask returns 200 with exact 'I don't know' and empty citations."""
        mock_result = {
            "answer": DONT_KNOW_ANSWER,
            "citations": []
        }
        with patch("app.generate_answer", return_value=mock_result):
            res = self.client.post("/ask", json={"question": "What is the capital of Mars?"})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["answer"], DONT_KNOW_ANSWER)
            self.assertEqual(data["citations"], [])

    def test_12_crawler_progress_and_eta_calculation(self):
        """Verify CrawlerJob calculates progress percentage and ETA dynamically across successes and skips."""
        from crawler_engine import SiteCrawler
        import time

        job = SiteCrawler(root_url="https://cdcpakistan.com", max_pages=10)
        job.status = "running"
        job.start_epoch = time.time() - 10.0  # 10s elapsed
        job.pages_crawled = 2
        job.errors_count = 3  # Total processed = 5/10

        status = job.get_status()
        self.assertEqual(status["progress_percent"], 50.0)
        self.assertIn("s", status["eta"])
        self.assertNotEqual(status["eta"], "Calculating...")
        self.assertGreater(float(status["speed"].split()[0]), 0)

    def test_13_crawler_failure_status_on_all_errors(self):
        """Verify CrawlerJob marks status as 'failed' when all URLs error out."""
        from crawler_engine import SiteCrawler

        job = SiteCrawler(root_url="https://cdcpakistan.com", max_pages=5)
        job.status = "failed"
        job.pages_crawled = 0
        job.errors_count = 5

        status = job.get_status()
        self.assertEqual(status["progress_percent"], 100.0)
        self.assertEqual(status["eta"], "Failed")
        self.assertEqual(status["status"], "failed")

    def test_14_typo_correction_and_fuzzy_mapping(self):
        """Verify typo corrector normalizes misspellings and shorthand into canonical regulatory terms."""
        from typo_corrector import correct_query_typos, is_chat_history_inquiry

        corrected, changed = correct_query_typos("wht is pennalty for unauthroized trnsfer in cdss?")
        self.assertTrue(changed)
        self.assertIn("penalty", corrected)
        self.assertIn("unauthorized", corrected)
        self.assertIn("transfer", corrected)
        self.assertIn("cds", corrected)

        corrected2, changed2 = correct_query_typos("cpaital adeqcy requirments for broker")
        self.assertTrue(changed2)
        self.assertIn("capital", corrected2)
        self.assertIn("adequacy", corrected2)
        self.assertIn("requirements", corrected2)

        self.assertTrue(is_chat_history_inquiry("what did i ask in last message?"))
        self.assertTrue(is_chat_history_inquiry("what was my last question?"))
        self.assertFalse(is_chat_history_inquiry("What is SECP regulation 12?"))

    def test_15_chat_history_recall_bot_memory(self):
        """Verify chatbot acts with conversational memory for past questions rather than treating it like empty doc search."""
        history = [
            {"role": "user", "text": "What is the net capital balance requirement for brokers?"},
            {"role": "model", "text": "Brokers must maintain a minimum Net Capital Balance of PKR 2.5 million."}
        ]

        # Recalls previous question accurately
        res = generate_answer("what did i ask in last message?", [], history=history)
        self.assertIn("net capital balance requirement for brokers", res["answer"].lower())
        self.assertNotEqual(res["answer"], DONT_KNOW_ANSWER)

        # Session start with no history explains politely
        res_empty = generate_answer("what did i ask in last message?", [], history=[])
        self.assertIn("start of our conversation", res_empty["answer"].lower())
        self.assertNotEqual(res_empty["answer"], DONT_KNOW_ANSWER)

if __name__ == "__main__":
    unittest.main()

