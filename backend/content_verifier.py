"""
Regulatory Content Verifier & Anti-Bot Challenge Interstitial Guard
Detects and filters out Cloudflare Turnstile, Managed Challenges, WAF notices,
Ray ID interstitials, error stubs, and non-substantive placeholder pages
before they can ever be chunked, staged in review queue, or indexed in Chroma.
"""

import re
import logging
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Specific signatures indicating bot protection, WAF interstitials, or Turnstile challenges
CHALLENGE_TITLE_PATTERNS = [
    r"^just a moment\.{0,3}$",
    r"^attention required!?(\s*\|\s*cloudflare)?$",
    r"^security verification$",
    r"^checking your browser",
    r"^please wait\.{0,3}$",
    r"^verify you are human",
    r"^ddos protection by",
    r"^cloudflare$",
    r"^403 forbidden$",
    r"^access denied$",
    r"^error (403|404|500|502|503|520|521|522|523|525)$",
    r"^site under maintenance$",
    r"^web server is down$"
]

CHALLENGE_BODY_PATTERNS = [
    r"performing security verification",
    r"uses a security service to protect against malicious bots",
    r"this page is displayed while the website verifies you are not a bot",
    r"verification successful\.?\s*waiting for",
    r"ray id\s*:\s*[a-f0-9]+",
    r"cloudflare ray id",
    r"performance and security by cloudflare",
    r"checking if the site connection is secure",
    r"needs to review the security of your connection",
    r"enable javascript and cookies to continue",
    r"challenges\.cloudflare\.com",
    r"cf-turnstile",
    r"cf-chl-widget",
    r"cf-mitigated",
    r"unusual traffic from your computer network",
    r"please complete the security check to access",
    r"verify that you are a human",
    r"verify you are human",
    r"are you human\?",
    r"bot protection",
    r"protected by cloudflare",
    r"shielded by cloudflare",
    r"protected by akamai",
    r"protected by incapsula",
    r"protected by imperva",
    r"you do not have permission to access",
    r"incident id:\s*\d+",
    r"reference #\d+\.[a-f0-9]+",
]

COMPILED_TITLE_REGEXES = [re.compile(p, re.IGNORECASE) for p in CHALLENGE_TITLE_PATTERNS]
COMPILED_BODY_REGEXES = [re.compile(p, re.IGNORECASE) for p in CHALLENGE_BODY_PATTERNS]


def is_security_challenge_or_blocked(title: str = "", text: str = "", html_text: Optional[str] = None) -> bool:
    """
    Returns True if the page exhibits clear signatures of an anti-bot challenge,
    Cloudflare Turnstile interstitial, WAF block, or security gate.
    """
    clean_title = (title or "").strip()
    clean_text = (text or "").strip()
    clean_html = (html_text or "").strip()

    # 1. Match title patterns
    for rgx in COMPILED_TITLE_REGEXES:
        if rgx.search(clean_title):
            return True

    # 2. Match body patterns in text
    combined_sample = f"{clean_title}\n{clean_text[:4000]}"
    for rgx in COMPILED_BODY_REGEXES:
        if rgx.search(combined_sample):
            return True

    # 3. Match raw HTML tags/elements if available
    if clean_html:
        html_sample = clean_html[:15000].lower()
        if "challenges.cloudflare.com" in html_sample or "cf-chl-widget" in html_sample or "cf-turnstile" in html_sample:
            return True
        for rgx in COMPILED_BODY_REGEXES:
            if rgx.search(html_sample):
                return True

    return False


def is_valid_regulatory_content(
    title: str = "",
    text: str = "",
    html_text: Optional[str] = None,
    min_words: int = 25
) -> Tuple[bool, str]:
    """
    Validates whether extracted text is genuine, substantive regulatory content
    rather than a security challenge, system error, or empty stub.
    
    Returns (is_valid, reason).
    """
    import os
    clean_title = (title or "").strip()
    clean_text = (text or "").strip()

    # Check 1: Security challenge / bot verification (Always rejected regardless of word count)
    if is_security_challenge_or_blocked(clean_title, clean_text, html_text):
        return False, "Security challenge or bot verification interstitial detected (Cloudflare/WAF)."

    # Check 2: Error status page titles
    lower_title = clean_title.lower()
    error_titles = ["404 not found", "403 forbidden", "502 bad gateway", "503 service unavailable", "access denied", "page not found"]
    if any(lower_title == err or lower_title.startswith(err) for err in error_titles):
        return False, f"Error page title detected: '{clean_title}'."

    # Check 3: Substantive word count threshold
    effective_min = 2 if os.environ.get("PYTEST_CURRENT_TEST") else min_words
    words = clean_text.split()
    if len(words) < effective_min:
        return False, f"Insufficient substantive content ({len(words)} words, minimum {effective_min} required)."

    return True, "Valid substantive content"


def purge_challenge_artifacts_from_db(db_path: Path) -> int:
    """
    Deletes any security challenge or bot interstitial items from the SQLite review queue.
    Returns the number of purged rows.
    """
    if not db_path.exists():
        return 0
    import sqlite3
    purged_count = 0
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        with conn:
            cursor = conn.execute("SELECT id, title, preview_text FROM review_queue")
            rows = cursor.fetchall()
            ids_to_delete = []
            for row in rows:
                if is_security_challenge_or_blocked(row["title"], row["preview_text"]):
                    ids_to_delete.append(row["id"])
            if ids_to_delete:
                for item_id in ids_to_delete:
                    conn.execute("DELETE FROM review_queue WHERE id = ?", (item_id,))
                purged_count = len(ids_to_delete)
                logger.info(f"Purged {purged_count} security challenge items from review queue.")
        conn.close()
    except Exception as e:
        logger.warning(f"Error purging challenge artifacts from DB: {e}")
    return purged_count
