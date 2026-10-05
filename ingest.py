#!/usr/bin/env python3
"""
Module 2: Ingestion, Chunking, Embedding, and Chroma Vector Storage.

Shared Contract Compliance:
- Reads: data/raw/documents.jsonl (Section 2)
- Target chunk size: ~500-800 tokens with ~50-100 token overlap (word-count approximation)
- Embedding: Chroma default embedding function (all-MiniLM-L6-v2 via ONNX)
- Stores in persistent Chroma client at ./vectordb/chroma_store (or CHROMA_PERSIST_DIR)
- Collection name: 'cdc_regulatory_docs'
- Chunk id format: f"{doc_id}_{chunk_index}"
- Chunk metadata format: {"doc_id": ..., "source_url": ..., "title": ..., "chunk_index": ..., "date_scraped": ...}
- Clears/recreates collection before insertion to avoid duplicates.
"""

import os
import sys
import json
import logging
from pathlib import Path
import chromadb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Constants per shared contract
DEFAULT_PERSIST_DIR = "./vectordb/chroma_store"
COLLECTION_NAME = "cdc_regulatory_docs"
RAW_DOCS_PATH = "./data/raw/documents.jsonl"

# Approximate 500-800 tokens (~400-600 words) with ~50-100 token overlap (~40-75 words)
TARGET_CHUNK_WORDS = 500
OVERLAP_WORDS = 75


def resolve_chroma_dir(custom_path: str | None = None) -> str:
    """Resolve the Chroma persistence directory with env var fallback."""
    if custom_path:
        return custom_path
    return os.environ.get("CHROMA_PERSIST_DIR", DEFAULT_PERSIST_DIR)


def chunk_text(text: str, chunk_size: int = TARGET_CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    """
    Splits text into chunks of roughly chunk_size words with overlap words between consecutive chunks.
    Preserves word boundaries and handles short texts cleanly.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_size:
        return [" ".join(words)]

    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunk_words = words[i:i + chunk_size]
        chunks.append(" ".join(chunk_words))
        if i + chunk_size >= len(words):
            break
    return chunks


def load_documents(filepath: str | Path) -> list[dict]:
    """
    Reads data/raw/documents.jsonl (one JSON object per line).
    Does NOT modify the file.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Documents file not found at: {path.resolve()}")

    documents = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
                documents.append(doc)
            except json.JSONDecodeError as err:
                logger.warning(f"Skipping malformed JSON at line {line_num}: {err}")

    logger.info(f"Loaded {len(documents)} document(s) from {path}")
    return documents


def build_vector_store(
    docs_path: str = RAW_DOCS_PATH,
    persist_dir: str | None = None,
    chunk_size: int = TARGET_CHUNK_WORDS,
    overlap: int = OVERLAP_WORDS,
) -> int:
    """
    Processes all raw documents, chunks them, embeds them, and inserts them
    into the persistent Chroma vector collection.
    """
    chroma_path = resolve_chroma_dir(persist_dir)
    logger.info(f"Target Chroma persistent directory: {os.path.abspath(chroma_path)}")
    os.makedirs(chroma_path, exist_ok=True)

    documents = load_documents(docs_path)
    if not documents:
        logger.warning("No documents found to index.")
        return 0

    client = chromadb.PersistentClient(path=chroma_path)

    # Section 5: Clear/recreate collection so re-running doesn't duplicate chunks
    try:
        client.delete_collection(name=COLLECTION_NAME)
        logger.info(f"Reset existing collection '{COLLECTION_NAME}' for clean rebuild.")
    except Exception:
        logger.info(f"Collection '{COLLECTION_NAME}' did not previously exist; creating fresh.")

    # Section 3: Uses Chroma's default embedding function (all-MiniLM-L6-v2 via ONNX)
    # DefaultEmbeddingFunction produces 384-dimensional dense embeddings locally without external API dependencies.
    collection = client.get_or_create_collection(name=COLLECTION_NAME)

    ids = []
    texts = []
    metadatas = []

    for doc in documents:
        doc_id = doc.get("doc_id", "")
        source_url = doc.get("source_url", "")
        title = doc.get("title", "")
        date_scraped = doc.get("date_scraped", "")
        full_text = doc.get("text", "")

        chunks = chunk_text(full_text, chunk_size=chunk_size, overlap=overlap)
        logger.info(f"Document '{title}' ({doc_id}) split into {len(chunks)} chunk(s).")

        for chunk_idx, chunk in enumerate(chunks):
            # Section 3: id = f"{doc_id}_{chunk_index}"
            chunk_id = f"{doc_id}_{chunk_idx}"
            ids.append(chunk_id)
            texts.append(chunk)
            # Section 3: metadata contract
            metadatas.append({
                "doc_id": doc_id,
                "source_url": source_url,
                "title": title,
                "chunk_index": chunk_idx,
                "date_scraped": date_scraped,
            })

    if ids:
        logger.info(f"Adding {len(ids)} total chunk(s) to collection '{COLLECTION_NAME}'...")
        # Chroma batches embeddings automatically
        collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info(f"Successfully indexed {len(ids)} chunks into '{COLLECTION_NAME}'.")

    return len(ids)


if __name__ == "__main__":
    count = build_vector_store()
    print(f"\n[DONE] Ingestion completed. Total chunks in Chroma store: {count}")
