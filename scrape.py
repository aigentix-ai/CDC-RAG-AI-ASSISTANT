#!/usr/bin/env python3
"""
Module 1: Full-Site Crawler & Multi-Document Extractor with Anti-Blocking & Rate Limiting
CDC Phase 1 RAG Regulatory Assistant

Robustness Features:
1. robots.txt compliance via urllib.robotparser.RobotFileParser
2. Configurable rate limiting delay between every single request (pages & files)
3. Browser-assisted fetcher (DrissionPage CDP) to pass Cloudflare Turnstile without bot bans
4. Exponential backoff on HTTP 429, 503, and network timeouts (2s, 4s, 8s)
5. Cross-run persistent caching of visited URLs in data/raw/visited_urls.json
6. Strictly sequential single-threaded crawling to avoid burst patterns
7. Domain-bounded link exploration with max depth and page caps
8. Detailed per-request logging and comprehensive end-of-run summary
9. Preserves exact Section 2 shared contract format in data/raw/documents.jsonl
"""

import collections
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
import urllib.robotparser

from bs4 import BeautifulSoup
import pdfplumber
import requests

# Optional document libraries
try:
    import docx
except ImportError:
    docx = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from curl_cffi import requests as c_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    c_requests = None
    CURL_CFFI_AVAILABLE = False

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    DRISSION_AVAILABLE = True
except ImportError:
    ChromiumPage = None
    ChromiumOptions = None
    DRISSION_AVAILABLE = False


# ==============================================================================
# CRAWLER CONFIGURATION (Tunable Settings)
# Adjust these parameters to customize crawler behavior
# ==============================================================================
CONFIG = {
    # Entry point for crawling
    "START_URL": os.environ.get("CRAWL_START_URL", "https://www.cdcpakistan.com/"),

    # Clear, honest User-Agent string
    "USER_AGENT": os.environ.get(
        "CRAWLER_USER_AGENT",
        "CDC-Demo-Assistant-Bot/1.0 (compliance research demo)"
    ),

    # Politeness delay between EVERY request (seconds) - page fetches & file downloads
    "REQUEST_DELAY_SECONDS": float(os.environ.get("REQUEST_DELAY", "1.5")),

    # Maximum depth of internal links to crawl from start URL
    "MAX_DEPTH": int(os.environ.get("MAX_DEPTH", "3")),

    # Maximum total pages/documents to fetch in a single crawl run
    "MAX_PAGES": int(os.environ.get("MAX_PAGES", "35")),

    # Max retries on HTTP 429, 503, or timeout/connection errors
    "MAX_RETRIES": int(os.environ.get("MAX_RETRIES", "3")),

    # Initial backoff delay (seconds) doubled on each retry (e.g. 2s -> 4s -> 8s)
    "INITIAL_BACKOFF_SECONDS": float(os.environ.get("INITIAL_BACKOFF", "2.0")),

    # Check robots.txt compliance before crawling any domain
    "CHECK_ROBOTS_TXT": True,

    # Persist and check already-processed URLs across runs to avoid re-hitting the site
    "PERSIST_VISITED_URLS": True,

    # Use CDP browser driver to transparently clear Cloudflare Turnstile challenges
    "USE_BROWSER_DRIVER": True,
}

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "whitelist.json"
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
OUTPUT_PATH = DATA_RAW_DIR / "documents.jsonl"
CACHE_PATH = DATA_RAW_DIR / "visited_urls.json"

# Default whitelist fallback seeds
DEFAULT_WHITELIST = [
    {
        "url": "https://www.cdcpakistan.com/",
        "type": "html",
        "label": "CDC Pakistan Official Homepage"
    },
    {
        "url": "https://www.cdcpakistan.com/about-us/overview/",
        "type": "html",
        "label": "CDC Corporate Overview"
    },
    {
        "url": "https://www.cdcpakistan.com/circular-notices/",
        "type": "html",
        "label": "CDC Circulars & Notices"
    },
    {
        "url": "https://www.cdcpakistan.com/assets/uploads/2026/04/Procedures-for-Direct-Transactions.pdf",
        "type": "pdf",
        "label": "CDC Procedures for Direct Transactions"
    },
    {
        "url": "https://pdfobject.com/pdf/sample.pdf",
        "type": "pdf",
        "label": "Sample Regulatory PDF"
    }
]


# ==============================================================================
# BROWSER FETCHER (CLOUDFLARE TURNSTILE BYPASS)
# ==============================================================================
class BrowserFetcher:
    """
    High-resilience browser driver using DrissionPage (CDP).
    Transparently bypasses Cloudflare Turnstile / Managed Challenges without bot bans.
    """

    def __init__(self):
        self.page = None
        self.is_active = False

    def _init_browser(self) -> bool:
        if not DRISSION_AVAILABLE:
            return False
        if self.page is not None:
            return True

        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        browser_path = None
        for candidate in chrome_candidates:
            if os.path.exists(candidate):
                browser_path = candidate
                break

        try:
            co = ChromiumOptions()
            if browser_path:
                co.set_browser_path(browser_path)
            co.headless(False)
            co.set_argument("--window-size=1200,800")
            co.set_argument("--disable-notifications")
            co.set_argument("--mute-audio")
            self.page = ChromiumPage(co)
            self.is_active = True
            print("       [BROWSER] CDP Browser driver initialized.")
            return True
        except Exception as e:
            print(f"       [WARN] Could not initialize browser driver: {e}")
            self.page = None
            self.is_active = False
            return False

    def fetch_page(self, url: str) -> tuple[str, str]:
        """Fetches rendered HTML through browser, ensuring Cloudflare challenge is resolved."""
        if not self._init_browser():
            raise RuntimeError("Browser driver unavailable")

        time.sleep(CONFIG["REQUEST_DELAY_SECONDS"])
        self.page.get(url, timeout=25)

        for _ in range(15):
            if "Just a moment" not in self.page.title and len(self.page.html) > 30000:
                break
            time.sleep(1)

        if "Just a moment" in self.page.title:
            raise RuntimeError(f"Cloudflare Turnstile challenge could not be resolved for {url}")

        return self.page.html, "text/html"

    def fetch_binary(self, url: str, timeout: int = 60) -> tuple[bytes, str]:
        """Fetches binary document bytes in-session via JavaScript fetch arrayBuffer."""
        import base64

        if not self._init_browser():
            raise RuntimeError("Browser driver unavailable")

        time.sleep(CONFIG["REQUEST_DELAY_SECONDS"])

        split = urlsplit(url)
        origin = f"{split.scheme}://{split.netloc}/"
        if not self.page.url or not self.page.url.startswith(origin) or "Just a moment" in self.page.title:
            self.page.get(origin, timeout=15)
            for _ in range(12):
                if "Just a moment" not in self.page.title:
                    break
                time.sleep(1)

        js = """
        const url = arguments[0];
        return fetch(url)
          .then(r => {
              if (!r.ok) throw new Error("HTTP " + r.status);
              return r.arrayBuffer();
          })
          .then(buf => {
              let binary = '';
              const bytes = new Uint8Array(buf);
              for (let i = 0; i < bytes.byteLength; i++) {
                  binary += String.fromCharCode(bytes[i]);
              }
              return btoa(binary);
          });
        """
        b64_res = self.page.run_js(js, url, timeout=timeout)
        if not b64_res:
            raise ValueError(f"Empty binary payload received for {url}")

        content_bytes = base64.b64decode(b64_res)
        return content_bytes, "application/octet-stream"

    def close(self):
        """Cleanly releases browser process."""
        if self.page is not None:
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None
            self.is_active = False


# ==============================================================================
# ROBOTS.TXT COMPLIANCE MANAGER
# ==============================================================================
class RobotsManager:
    """Manages and caches robots.txt rules per domain."""

    def __init__(self, user_agent: str, timeout: int = 10):
        self.user_agent = user_agent
        self.timeout = timeout
        self._parsers = {}

    def get_parser(self, url: str):
        split = urlsplit(url)
        domain = split.netloc.lower()
        if not domain:
            return None
        if domain in self._parsers:
            return self._parsers[domain]

        robots_url = f"{split.scheme}://{split.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)

        try:
            time.sleep(CONFIG["REQUEST_DELAY_SECONDS"])
            headers = {"User-Agent": self.user_agent}
            resp = requests.get(robots_url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                print(f"[ROBOTS.TXT] Successfully loaded rules from {robots_url}")
            elif resp.status_code in (403, 404):
                print(f"[ROBOTS.TXT] Notice: robots.txt not found or inaccessible at {robots_url} (HTTP {resp.status_code}); proceeding with standard crawl.")
                rp = None
            else:
                rp = None
        except Exception as e:
            print(f"[ROBOTS.TXT] Notice: Could not fetch robots.txt from {robots_url} ({e}); proceeding with standard crawl.")
            rp = None

        self._parsers[domain] = rp
        return rp

    def can_fetch(self, url: str) -> bool:
        if not CONFIG["CHECK_ROBOTS_TXT"]:
            return True
        split = urlsplit(url)
        if split.scheme not in ("http", "https"):
            return True
        rp = self.get_parser(url)
        if rp is None:
            return True
        allowed = rp.can_fetch(self.user_agent, url)
        return allowed


# ==============================================================================
# STATE & CACHE MANAGEMENT
# ==============================================================================
def load_visited_cache() -> dict:
    """Loads persistent record of already processed URLs across runs."""
    if not CONFIG["PERSIST_VISITED_URLS"] or not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[WARN] Could not load visited cache: {e}")
        return {}


def save_visited_cache(cache: dict) -> None:
    """Saves persistent record of visited URLs."""
    if not CONFIG["PERSIST_VISITED_URLS"]:
        return
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] Could not save visited cache: {e}")


def load_existing_documents() -> dict:
    """Loads previously extracted documents from documents.jsonl keyed by doc_id."""
    docs_by_id = {}
    if not OUTPUT_PATH.exists():
        return docs_by_id
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    doc = json.loads(line)
                    if "doc_id" in doc:
                        # Reject challenge or placeholder pages
                        title_lower = doc.get("title", "").lower()
                        text_start = doc.get("text", "")[:100].lower()
                        if "just a moment" not in title_lower and "just a moment" not in text_start:
                            docs_by_id[doc["doc_id"]] = doc
    except Exception as e:
        print(f"[WARN] Could not load existing documents.jsonl: {e}")
    return docs_by_id


def ensure_whitelist_exists() -> list:
    """Ensures config/whitelist.json exists, creating with default entries if not."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_WHITELIST, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Created missing whitelist configuration at: {CONFIG_PATH}")

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            whitelist = json.load(f)
        if isinstance(whitelist, list):
            return whitelist
    except Exception as e:
        print(f"[WARN] Error reading whitelist.json: {e}")
    return DEFAULT_WHITELIST


def compute_doc_id(source_url: str) -> str:
    """Compute deterministic doc_id as the first 16 hex characters of sha256(source_url.encode())."""
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]


def get_current_utc_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format (e.g. 2026-09-17T10:00:00Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_whitespace(text: str) -> str:
    """Normalizes whitespace in extracted text."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_base_domain(url: str) -> str:
    """Extract registered domain / hostname for same-domain checking."""
    netloc = urlsplit(url).netloc.lower().split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def is_same_domain(target_url: str, base_domain: str) -> bool:
    """Checks if target_url belongs to the same domain or subdomain."""
    target_netloc = urlsplit(target_url).netloc.lower().split(":")[0]
    if target_netloc.startswith("www."):
        target_netloc = target_netloc[4:]
    return target_netloc == base_domain or target_netloc.endswith("." + base_domain)


def classify_url(url: str, content_type: str = "") -> str:
    """Classifies a resource into: 'pdf', 'docx', 'xlsx', or 'html'."""
    path = urlsplit(url).path.lower()
    ct = content_type.lower()

    if path.endswith(".pdf") or "application/pdf" in ct:
        return "pdf"
    if path.endswith(".docx") or "wordprocessingml" in ct or "msword" in ct:
        return "docx"
    if path.endswith(".xlsx") or path.endswith(".xls") or "spreadsheetml" in ct or "ms-excel" in ct:
        return "xlsx"
    return "html"


# ==============================================================================
# NETWORK FETCHER WITH CLOUDFLARE BYPASS & RATE LIMITING
# ==============================================================================
def fetch_resource(url: str, is_binary: bool = False, browser_fetcher: BrowserFetcher = None) -> tuple:
    """
    Fetches URL content with:
    1. Mandatory politeness delay before every request
    2. CDP browser driver for Cloudflare-protected sites (cdcpakistan.com)
    3. Exponential backoff on HTTP 429, 503, and network timeouts
    4. Fallback to browser driver when challenges detected
    """
    # Local file support
    if url.startswith("file://") or not urlsplit(url).scheme:
        file_path = Path(url[7:] if url.startswith("file://") else url)
        if not file_path.is_absolute():
            file_path = BASE_DIR / file_path
        if not file_path.exists():
            raise FileNotFoundError(f"Local file not found: {file_path}")
        mode = "rb" if is_binary else "r"
        encoding = None if is_binary else "utf-8"
        with open(file_path, mode, encoding=encoding) as f:
            return f.read(), ""

    # Route Cloudflare-protected domains directly to the browser driver if available
    domain = get_base_domain(url)
    if browser_fetcher and (domain == "cdcpakistan.com" or CONFIG.get("USE_BROWSER_DRIVER")):
        try:
            if is_binary:
                return browser_fetcher.fetch_binary(url)
            else:
                return browser_fetcher.fetch_page(url)
        except Exception as b_err:
            print(f"       [BROWSER ERROR] {b_err}. Falling back to standard HTTP...")

    headers = {
        "User-Agent": CONFIG["USER_AGENT"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    last_exception = None
    for attempt in range(CONFIG["MAX_RETRIES"] + 1):
        time.sleep(CONFIG["REQUEST_DELAY_SECONDS"])

        try:
            resp = requests.get(url, headers=headers, timeout=15)

            # Check for rate-limiting
            if resp.status_code in (429, 503):
                if attempt < CONFIG["MAX_RETRIES"]:
                    backoff = CONFIG["INITIAL_BACKOFF_SECONDS"] * (2 ** attempt)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        backoff = max(backoff, float(retry_after))
                    print(f"       [RATE LIMIT {resp.status_code}] Backing off for {backoff:.1f}s (Attempt {attempt+1}/{CONFIG['MAX_RETRIES']})...")
                    time.sleep(backoff)
                    continue
                else:
                    resp.raise_for_status()

            # Check if Cloudflare 403 challenge encountered
            if resp.status_code == 403:
                if browser_fetcher:
                    print("       [CLOUDFLARE DETECTED] Routing to browser driver...")
                    if is_binary:
                        return browser_fetcher.fetch_binary(url)
                    else:
                        return browser_fetcher.fetch_page(url)

                if CURL_CFFI_AVAILABLE:
                    c_resp = c_requests.get(url, impersonate="chrome124", timeout=15)
                    c_resp.raise_for_status()
                    ct = c_resp.headers.get("Content-Type", "")
                    return (c_resp.content if is_binary else c_resp.text), ct

            resp.raise_for_status()
            if not is_binary and (resp.encoding is None or resp.encoding.lower() == "iso-8859-1"):
                resp.encoding = resp.apparent_encoding or "utf-8"
            ct = resp.headers.get("Content-Type", "")
            return (resp.content if is_binary else resp.text), ct

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
            last_exception = net_err
            if attempt < CONFIG["MAX_RETRIES"]:
                backoff = CONFIG["INITIAL_BACKOFF_SECONDS"] * (2 ** attempt)
                print(f"       [NETWORK ERROR] {net_err}. Retrying in {backoff:.1f}s (Attempt {attempt+1}/{CONFIG['MAX_RETRIES']})...")
                time.sleep(backoff)
                continue
            else:
                raise last_exception

        except Exception as err:
            if browser_fetcher:
                try:
                    if is_binary:
                        return browser_fetcher.fetch_binary(url)
                    else:
                        return browser_fetcher.fetch_page(url)
                except Exception:
                    pass
            raise err


# ==============================================================================
# SPECIALIZED DOCUMENT EXTRACTORS
# ==============================================================================
def extract_pdf(content_stream_or_bytes, url: str, fallback_label: str = "") -> dict:
    """Extracts text from PDF stream using pdfplumber, with header/footer cleanup."""
    if isinstance(content_stream_or_bytes, bytes):
        stream = io.BytesIO(content_stream_or_bytes)
    else:
        stream = content_stream_or_bytes

    title = None
    pages_text = []

    with pdfplumber.open(stream) as pdf:
        metadata = pdf.metadata or {}
        meta_title = metadata.get("Title") or metadata.get("title")
        if meta_title and str(meta_title).strip():
            title = str(meta_title).strip()

        # Limit parsing to first 50 pages for large reports
        for page in pdf.pages[:50]:
            t = page.extract_text()
            if t:
                pages_text.append(t)

    if not title:
        title = fallback_label or Path(urlsplit(url).path).stem.replace("-", " ").replace("_", " ").title() or "Untitled PDF"

    # Clean headers / footers across multi-page PDFs
    if len(pages_text) >= 3:
        first_lines = [p.strip().split("\n")[0] for p in pages_text if p.strip()]
        last_lines = [p.strip().split("\n")[-1] for p in pages_text if p.strip()]
        common_header = first_lines[0] if (first_lines and first_lines.count(first_lines[0]) >= len(pages_text) * 0.7) else None
        common_footer = last_lines[0] if (last_lines and last_lines.count(last_lines[0]) >= len(pages_text) * 0.7) else None

        cleaned_pages = []
        for p in pages_text:
            lines = p.split("\n")
            if common_header and lines and lines[0].strip() == common_header:
                lines = lines[1:]
            if common_footer and lines and lines[-1].strip() == common_footer:
                lines = lines[:-1]
            cleaned_pages.append("\n".join(lines))
        joined = "\n".join(cleaned_pages)
    else:
        joined = "\n".join(pages_text)

    joined = re.sub(r"(?i)^\s*page\s+\d+(\s+of\s+\d+)?\s*$", "", joined, flags=re.MULTILINE)
    cleaned = normalize_whitespace(joined)
    if not cleaned:
        raise ValueError(f"No readable text extracted from PDF at {url}")

    return {"title": title, "text": cleaned}


def extract_docx(content_bytes: bytes, url: str, fallback_label: str = "") -> dict:
    """Extracts paragraphs and tables from Word (.docx) documents using python-docx."""
    if docx is None:
        raise ImportError("python-docx is not installed.")

    stream = io.BytesIO(content_bytes)
    doc = docx.Document(stream)
    title = None

    try:
        if doc.core_properties and doc.core_properties.title:
            title = doc.core_properties.title.strip()
    except Exception:
        pass

    text_parts = []
    for p in doc.paragraphs:
        p_text = p.text.strip()
        if p_text:
            if not title and p.style and "heading" in p.style.name.lower():
                title = p_text
            text_parts.append(p_text)

    for tbl in doc.tables:
        table_rows = []
        for row in tbl.rows:
            row_vals = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if any(row_vals):
                table_rows.append(" | ".join(row_vals))
        if table_rows:
            text_parts.append("\n".join(table_rows))

    if not title:
        title = fallback_label or Path(urlsplit(url).path).stem.replace("-", " ").replace("_", " ").title() or "Untitled Document"

    cleaned = normalize_whitespace("\n\n".join(text_parts))
    if not cleaned:
        raise ValueError(f"No readable text extracted from DOCX at {url}")

    return {"title": title, "text": cleaned}


def extract_excel(content_bytes: bytes, url: str, fallback_label: str = "") -> dict:
    """Extracts tabular text from Excel (.xlsx/.xls) spreadsheets using openpyxl/pandas."""
    stream = io.BytesIO(content_bytes)
    text_parts = []
    title = None

    if openpyxl is not None:
        try:
            wb = openpyxl.load_workbook(stream, data_only=True, read_only=True)
            for sheetname in wb.sheetnames:
                sheet = wb[sheetname]
                sheet_lines = [f"=== Sheet: {sheetname} ==="]
                for row in sheet.iter_rows(values_only=True):
                    row_vals = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if row_vals:
                        sheet_lines.append(" | ".join(row_vals))
                if len(sheet_lines) > 1:
                    text_parts.append("\n".join(sheet_lines))
            wb.close()
        except Exception:
            text_parts = []

    if not text_parts and pd is not None:
        try:
            stream.seek(0)
            xls_dict = pd.read_excel(stream, sheet_name=None)
            for sheetname, df in xls_dict.items():
                if not df.empty:
                    sheet_lines = [f"=== Sheet: {sheetname} ==="]
                    cols = [str(c).strip() for c in df.columns]
                    sheet_lines.append(" | ".join(cols))
                    for _, row in df.iterrows():
                        row_vals = [str(val).strip() for val in row if pd.notna(val) and str(val).strip()]
                        if row_vals:
                            sheet_lines.append(" | ".join(row_vals))
                    text_parts.append("\n".join(sheet_lines))
        except Exception as err:
            if not text_parts:
                raise ValueError(f"Failed to extract Excel data: {err}")

    if not title:
        title = fallback_label or Path(urlsplit(url).path).stem.replace("-", " ").replace("_", " ").title() or "Untitled Spreadsheet"

    cleaned = normalize_whitespace("\n\n".join(text_parts))
    if not cleaned:
        raise ValueError(f"No readable data extracted from Excel spreadsheet at {url}")

    return {"title": title, "text": cleaned}


def extract_html(html_text: str, url: str, fallback_label: str = "") -> tuple:
    """
    Parses HTML, strips boilerplate, extracts title and text,
    and returns (extracted_dict, list_of_internal_links).
    """
    soup = BeautifulSoup(html_text, "html.parser")

    title = None
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        title = title_tag.get_text(strip=True)
    else:
        h1_tag = soup.find("h1")
        if h1_tag and h1_tag.get_text(strip=True):
            title = h1_tag.get_text(strip=True)

    if not title:
        title = fallback_label or "Untitled Page"

    # Reject Cloudflare challenge pages
    if "just a moment" in title.lower():
        raise ValueError(f"Cloudflare Turnstile challenge intercepted at {url}")

    discovered_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
            resolved = urljoin(url, href)
            split = urlsplit(resolved)
            clean = urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))
            if split.scheme in ("http", "https"):
                discovered_links.append(clean)

    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "svg", "form"]):
        tag.decompose()

    raw_text = soup.get_text(separator="\n")
    cleaned = normalize_whitespace(raw_text)

    if not cleaned:
        raise ValueError(f"No readable text extracted from HTML at {url}")

    return {"title": title, "text": cleaned}, discovered_links


# ==============================================================================
# SITEMAP DISCOVERY
# ==============================================================================
def discover_sitemap_urls(start_url: str, base_domain: str, robots_mgr: RobotsManager, browser_fetcher: BrowserFetcher = None) -> list:
    """Attempts to discover and parse sitemap.xml for the starting domain."""
    sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"]
    found_urls = []

    for path in sitemap_paths:
        candidate_url = urljoin(start_url, path)
        if not robots_mgr.can_fetch(candidate_url):
            continue

        try:
            content, _ = fetch_resource(candidate_url, is_binary=False, browser_fetcher=browser_fetcher)
            if "<loc>" in content:
                soup = BeautifulSoup(content, "html.parser")
                for loc in soup.find_all("loc"):
                    u = loc.get_text(strip=True)
                    if is_same_domain(u, base_domain):
                        found_urls.append(u)
                if found_urls:
                    print(f"[SITEMAP] Discovered {len(found_urls)} URLs from {candidate_url}")
                    break
        except Exception:
            continue

    return found_urls


# ==============================================================================
# SEQUENTIAL DOMAIN CRAWLER
# ==============================================================================
def crawl_site(start_url: str) -> tuple:
    """
    Sequentially crawls domain links within max_depth and max_pages.
    Respects robots.txt, politeness delays, transparently clears Cloudflare challenges,
    and skips already processed URLs.
    """
    base_domain = get_base_domain(start_url)
    max_depth = CONFIG["MAX_DEPTH"]
    max_pages = CONFIG["MAX_PAGES"]

    print(f"[CONFIG] Start URL:      {start_url}")
    print(f"[CONFIG] Target Domain:  {base_domain}")
    print(f"[CONFIG] User-Agent:     {CONFIG['USER_AGENT']}")
    print(f"[CONFIG] Request Delay:  {CONFIG['REQUEST_DELAY_SECONDS']}s")
    print(f"[CONFIG] Max Depth:      {max_depth}")
    print(f"[CONFIG] Max Pages:      {max_pages}")
    print(f"[CONFIG] Max Retries:    {CONFIG['MAX_RETRIES']} (initial backoff: {CONFIG['INITIAL_BACKOFF_SECONDS']}s)")
    print(f"[CONFIG] Robots.txt:     {'Enabled' if CONFIG['CHECK_ROBOTS_TXT'] else 'Disabled'}")
    print(f"[CONFIG] Cache Persist:  {'Enabled' if CONFIG['PERSIST_VISITED_URLS'] else 'Disabled'}")
    print(f"[CONFIG] Browser Driver: {'Enabled (Anti-Ban)' if CONFIG['USE_BROWSER_DRIVER'] else 'Disabled'}\n")

    robots_mgr = RobotsManager(CONFIG["USER_AGENT"])
    visited_cache = load_visited_cache()
    existing_docs = load_existing_documents()

    print(f"[CACHE] Loaded {len(visited_cache)} visited URL(s) and {len(existing_docs)} document(s) from previous runs.\n")

    # Sequential FIFO queue of (url, depth, label)
    queue = collections.deque()
    in_frontier = set()

    # Initialize browser fetcher if enabled
    browser_fetcher = BrowserFetcher() if (CONFIG.get("USE_BROWSER_DRIVER") and DRISSION_AVAILABLE) else None

    try:
        # 1. Sitemap check
        sitemap_urls = discover_sitemap_urls(start_url, base_domain, robots_mgr, browser_fetcher=browser_fetcher)
        for sm_u in sitemap_urls[:max_pages]:
            if sm_u not in in_frontier:
                queue.append((sm_u, 1, ""))
                in_frontier.add(sm_u)

        # 2. Add start URL
        if start_url not in in_frontier:
            queue.append((start_url, 0, "CDC Pakistan Home"))
            in_frontier.add(start_url)

        # 3. Add seeds from whitelist.json
        whitelist = ensure_whitelist_exists()
        for entry in whitelist:
            u = entry.get("url", "").strip()
            if u and u not in in_frontier:
                queue.append((u, 0, entry.get("label", "")))
                in_frontier.add(u)

        print(f"[CRAWLER] Initial sequential queue size: {len(queue)}\n")

        all_docs_by_id = dict(existing_docs)
        pages_crawled = 0
        skipped_cached = 0
        skipped_robots = 0
        newly_extracted = 0
        failures = []

        while queue and pages_crawled < max_pages:
            current_url, depth, label = queue.popleft()

            # Check domain constraint for remote URLs
            if urlsplit(current_url).scheme in ("http", "https"):
                is_internal = is_same_domain(current_url, base_domain)
            else:
                is_internal = False

            doc_type = classify_url(current_url)

            # 1. Check if already successfully processed in cache
            if current_url in visited_cache and visited_cache[current_url].get("status") == "success":
                skipped_cached += 1
                print(f"[{pages_crawled + skipped_cached}/{max_pages}] [SKIPPED - CACHED] {current_url}")
                continue

            # 2. Check robots.txt compliance
            if not robots_mgr.can_fetch(current_url):
                skipped_robots += 1
                print(f"[{pages_crawled + 1}/{max_pages}] [SKIPPED - ROBOTS.TXT] Disallowed: {current_url}")
                visited_cache[current_url] = {"status": "disallowed_by_robots", "timestamp": get_current_utc_iso()}
                continue

            pages_crawled += 1
            print(f"[{pages_crawled}/{max_pages}] [Depth {depth}] [FETCH] [{doc_type.upper()}] {label or current_url}")

            try:
                is_binary = doc_type in ("pdf", "docx", "xlsx")
                content, content_type = fetch_resource(current_url, is_binary=is_binary, browser_fetcher=browser_fetcher)

                if content_type:
                    refined_type = classify_url(current_url, content_type)
                    if refined_type != "html":
                        doc_type = refined_type

                extracted = None
                new_links = []

                if doc_type == "pdf":
                    extracted = extract_pdf(content, current_url, fallback_label=label)
                elif doc_type == "docx":
                    extracted = extract_docx(content, current_url, fallback_label=label)
                elif doc_type == "xlsx":
                    extracted = extract_excel(content, current_url, fallback_label=label)
                else:
                    extracted, new_links = extract_html(content, current_url, fallback_label=label)

                doc_id = compute_doc_id(current_url)
                doc_record = {
                    "doc_id": doc_id,
                    "source_url": current_url,
                    "source_type": doc_type,
                    "title": extracted["title"],
                    "date_scraped": get_current_utc_iso(),
                    "text": extracted["text"]
                }

                all_docs_by_id[doc_id] = doc_record
                newly_extracted += 1
                visited_cache[current_url] = {"status": "success", "doc_id": doc_id, "timestamp": doc_record["date_scraped"]}
                print(f"       -> [SUCCESS] Extracted '{extracted['title'][:40]}' ({len(extracted['text'])} chars)")

                # Add internal links to queue if within depth limit
                if is_internal and depth < max_depth:
                    for link in new_links:
                        if link not in in_frontier and is_same_domain(link, base_domain):
                            if link not in visited_cache or visited_cache[link].get("status") != "success":
                                queue.append((link, depth + 1, ""))
                                in_frontier.add(link)

            except Exception as e:
                error_msg = str(e)
                print(f"       -> [FAILED] {error_msg}")
                failures.append({"url": current_url, "type": doc_type, "error": error_msg})
                visited_cache[current_url] = {"status": "failed", "error": error_msg, "timestamp": get_current_utc_iso()}

        # Persist updated cache
        save_visited_cache(visited_cache)

        stats = {
            "crawled_count": pages_crawled,
            "skipped_cached": skipped_cached,
            "skipped_robots": skipped_robots,
            "newly_extracted": newly_extracted,
            "total_documents": len(all_docs_by_id),
            "failures": failures
        }
        return list(all_docs_by_id.values()), stats

    finally:
        if browser_fetcher:
            browser_fetcher.close()


# ==============================================================================
# MAIN ORCHESTRATOR
# ==============================================================================
def run_scraper() -> None:
    """Main execution entry point."""
    print("=" * 70)
    print("CDC Phase 1 RAG - Module 1: Robust Crawler & Multi-Extractor")
    print("=" * 70)

    start_url = CONFIG["START_URL"]
    docs, stats = crawl_site(start_url)

    # Rebuild data/raw/documents.jsonl (idempotent write)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            for doc in docs:
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
        print(f"\n[OUTPUT] Written {len(docs)} document(s) to: {OUTPUT_PATH}")
    except Exception as e:
        print(f"\n[ERROR] Failed to write documents.jsonl: {e}")

    # Summary report
    print("\n" + "=" * 70)
    print("CRAWL & EXTRACTION SUMMARY")
    print(f"  Requests made in this run:  {stats['crawled_count']}")
    print(f"  Skipped (already cached):   {stats['skipped_cached']}")
    print(f"  Skipped (robots.txt):       {stats['skipped_robots']}")
    print(f"  Newly extracted this run:   {stats['newly_extracted']}")
    print(f"  Total indexable documents:  {stats['total_documents']}")
    print(f"  Failed / Unreachable:       {len(stats['failures'])}")

    type_counts = collections.Counter(d.get("source_type", "unknown") for d in docs)
    print("  Index by Document Type:")
    for dt, cnt in sorted(type_counts.items()):
        print(f"    - {dt.upper()}: {cnt}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run_scraper()
