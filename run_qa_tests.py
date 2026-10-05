#!/usr/bin/env python3
"""
CDC Phase 1 RAG Regulatory Assistant — Central QA Test Runner
Executes comprehensive contract audits and test suites across all 4 modules.
"""

import os
import sys
import json
import subprocess
import time
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

def run_cmd(cmd, cwd=ROOT_DIR):
    python_exe = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable

    full_cmd = [python_exe] + cmd
    start = time.time()
    proc = subprocess.run(
        full_cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    elapsed = time.time() - start
    return proc.returncode, proc.stdout, proc.stderr, elapsed

def audit_file_presence():
    """Checks presence of owned files according to contract."""
    files = {
        "Module 1 (Scraper)": {
            "config/whitelist.json": os.path.exists(os.path.join(ROOT_DIR, "config", "whitelist.json")),
            "data/raw/documents.jsonl": os.path.exists(os.path.join(ROOT_DIR, "data", "raw", "documents.jsonl")),
            "scrape.py": os.path.exists(os.path.join(ROOT_DIR, "scrape.py")),
        },
        "Module 2 (Vector Store)": {
            "ingest.py": os.path.exists(os.path.join(ROOT_DIR, "ingest.py")),
            "retrieval.py": os.path.exists(os.path.join(ROOT_DIR, "retrieval.py")),
        },
        "Module 3 (Backend & LLM)": {
            "backend/app.py": os.path.exists(os.path.join(ROOT_DIR, "backend", "app.py")),
            "backend/llm.py": os.path.exists(os.path.join(ROOT_DIR, "backend", "llm.py")),
            "backend/retriever.py": os.path.exists(os.path.join(ROOT_DIR, "backend", "retriever.py")),
            "backend/requirements.txt": os.path.exists(os.path.join(ROOT_DIR, "backend", "requirements.txt")),
        },
        "Module 4 (Frontend UI)": {
            "frontend/index.html": os.path.exists(os.path.join(ROOT_DIR, "frontend", "index.html")),
            "frontend/style.css": os.path.exists(os.path.join(ROOT_DIR, "frontend", "style.css")),
            "frontend/script.js": os.path.exists(os.path.join(ROOT_DIR, "frontend", "script.js")),
        }
    }
    return files

def run_all_tests():
    print("=" * 70)
    print("CDC Regulatory Assistant - Comprehensive QA Test Runner")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 70)

    # 1. File presence check
    presence = audit_file_presence()
    print("\n[1] File Presence Matrix:")
    for module, file_map in presence.items():
        print(f"  {module}:")
        for filepath, exists in file_map.items():
            status = "PASS [EXISTS]" if exists else "WARN [PENDING/MISSING]"
            print(f"    - {filepath:<32}: {status}")

    # 2. Run pytest suite
    print("\n[2] Executing Automated Pytest Suite:")
    code, out, err, duration = run_cmd(["-m", "pytest", "tests", "-v", "--tb=short"])
    print(out)
    if err:
        print("STDERR:")
        print(err)

    print(f"\nCompleted in {duration:.2f}s with exit code {code}")
    return code == 0

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
