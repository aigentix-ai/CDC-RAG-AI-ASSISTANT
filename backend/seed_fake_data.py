"""
=============================================================================
TEMPORARY FAKE DATA SEEDER FOR MODULE 3 STANDALONE TESTING
=============================================================================
NOTE: This script builds a minimal fake Chroma collection at CHROMA_PERSIST_DIR
(default: ./vectordb/chroma_store) with 2-3 realistic sample chunks matching
Module 2's schema contract.

THIS SCRIPT SHOULD BE DELETED OR IGNORED ONCE MODULE 2'S REAL STORE IS IN PLACE.
=============================================================================
"""

import os
import sys

# Ensure backend folder is in sys.path
sys.path.insert(0, os.path.dirname(__file__))

from retriever import get_chroma_persist_dir, get_client, get_collection

FAKE_CHUNKS = [
    {
        "doc_id": "9f86d081884c7d65",
        "chunk_index": 0,
        "source_url": "https://example-secp-circular.pdf",
        "title": "SECP Circular 12 of 2025",
        "date_scraped": "2026-09-17T10:00:00Z",
        "document": (
            "Securities and Exchange Commission of Pakistan (SECP) Circular 12 of 2025. "
            "Subject: Regulatory Framework and Enhanced Reporting for Asset Management Companies and Mutual Funds. "
            "All Asset Management Companies (AMCs) managing Collective Investment Schemes (CIS) are hereby directed "
            "to maintain strict compliance with updated trustee reporting standards. AMCs must submit monthly portfolio "
            "compliance declarations directly to the Central Depository Company (CDC) acting as trustee. "
            "Failure to adhere to these depository reconciliation deadlines may result in penalties under the "
            "Non-Banking Finance Companies (NBFC) Regulations."
        )
    },
    {
        "doc_id": "9f86d081884c7d65",
        "chunk_index": 1,
        "source_url": "https://example-secp-circular.pdf",
        "title": "SECP Circular 12 of 2025",
        "date_scraped": "2026-09-17T10:00:00Z",
        "document": (
            "SECP Circular 12 of 2025 (Section 4: Risk Mitigation and Valuation Disclosures). "
            "All valuation models utilized for unquoted debt securities must conform to standard MUFAP pricing guidelines. "
            "Trustees, including CDC Pakistan, must be notified within 24 hours of any material deviation or pricing "
            "override executed by fund managers. Independent third-party audit reports on digital risk infrastructure "
            "must be submitted annually by December 31."
        )
    },
    {
        "doc_id": "5e884898da280471",
        "chunk_index": 0,
        "source_url": "https://cdcpakistan.com/some-public-page",
        "title": "CDC Public Notice — Fund Update",
        "date_scraped": "2026-09-17T10:00:00Z",
        "document": (
            "Central Depository Company of Pakistan Limited (CDC) Public Notice — Fund Update and Trustee Oversight Guidelines. "
            "The CDC Trustee & Custodial Services division announces updated digital account opening procedures for open-end mutual funds. "
            "Effective Q3 2025, investor verification will be streamlined via standardized API integrations between Asset Management Companies "
            "and CDC. The revised trustee fee schedule remains benchmarked at 0.075% of Net Asset Value (NAV) per annum for equity funds, "
            "payable on a quarterly basis."
        )
    }
]

def seed_fake_data():
    persist_dir = get_chroma_persist_dir()
    print(f"[seed_fake_data] Target Chroma persist dir: {persist_dir}")

    client = get_client()
    collection = get_collection(client)

    ids = [f"{chunk['doc_id']}_{chunk['chunk_index']}" for chunk in FAKE_CHUNKS]
    documents = [chunk["document"] for chunk in FAKE_CHUNKS]
    metadatas = [
        {
            "doc_id": chunk["doc_id"],
            "source_url": chunk["source_url"],
            "title": chunk["title"],
            "chunk_index": chunk["chunk_index"],
            "date_scraped": chunk["date_scraped"]
        }
        for chunk in FAKE_CHUNKS
    ]

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )

    count = collection.count()
    print(f"[seed_fake_data] Successfully seeded {len(ids)} fake chunks. Total collection count: {count}")

if __name__ == "__main__":
    seed_fake_data()
