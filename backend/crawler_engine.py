"""
Deep Site Crawler & Auto-Ingestion Subsystem
CDC Regulatory Phase 1 RAG Assistant

Features:
1. Recursive, domain-locked BFS URL exploration.
2. Respects robots.txt and polite crawl delays with randomized jitter.
3. Automatic /sitemap.xml discovery on start for canonical URL seeds.
4. Robust HTML parsing (via BeautifulSoup) and PDF extraction (via pdfplumber).
5. Standard chunking (~500 words with 75-word overlap) per shared contract.
6. Dual ingestion targets:
   - Direct live Chroma indexing ('cdc_regulatory_docs')
   - Staging into SQLite review queue ('data/pending_review.sqlite3')
7. Live thread-safe state tracking (pages, PDFs, chunks, logs) and graceful pause/stop.
"""

import collections
import hashlib
import io
import json
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

import pdfplumber
import requests
from bs4 import BeautifulSoup

try:
    import websocket
except ImportError:
    websocket = None

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

# Try importing backend dependencies
try:
    import retriever
    import review_manager
    from content_verifier import is_security_challenge_or_blocked, is_valid_regulatory_content
except ImportError:
    from backend import retriever, review_manager
    from backend.content_verifier import is_security_challenge_or_blocked, is_valid_regulatory_content

logger = logging.getLogger("crawler_engine")

# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------
DEFAULT_USER_AGENT = "CDC-Regulatory-Crawler/1.0 (+https://cdcpakistan.com; compliance-rag-research)"
DEFAULT_DELAY_SECONDS = 3.0
DEFAULT_MAX_PAGES = 50
DEFAULT_CHUNK_WORDS = 500
DEFAULT_OVERLAP_WORDS = 75

COLLECTION_NAME = "cdc_regulatory_docs"


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------
def get_project_root() -> Path:
    """Finds the root directory of the cdc-rag-demo project."""
    return Path(__file__).resolve().parent.parent


CRAWLER_STATE_FILE = get_project_root() / "data" / "crawler_state.json"


def get_current_utc_iso() -> str:
    """Returns the current UTC timestamp formatted in ISO-8601 (Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_doc_id(source_url: str) -> str:
    """Computes deterministic 16-hex doc_id per shared contract."""
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]


def normalize_whitespace(text: str) -> str:
    """Normalizes newlines and whitespace in extracted text."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_WORDS,
    overlap: int = DEFAULT_OVERLAP_WORDS
) -> List[str]:
    """
    Splits text into chunks of roughly chunk_size words with overlap words.
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
        chunk_words = words[i : i + chunk_size]
        chunks.append(" ".join(chunk_words))
        if i + chunk_size >= len(words):
            break
    return chunks


def get_base_domain(url: str) -> str:
    """Extract registered domain / hostname for same-domain checking."""
    netloc = urlsplit(url).netloc.lower().split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def is_same_domain(target_url: str, base_domain: str) -> bool:
    """Checks if target_url belongs to the same domain or subdomain."""
    if not base_domain:
        return False
    target_netloc = urlsplit(target_url).netloc.lower().split(":")[0]
    if target_netloc.startswith("www."):
        target_netloc = target_netloc[4:]
    clean_base = base_domain.lower().split(":")[0]
    if clean_base.startswith("www."):
        clean_base = clean_base[4:]
    return target_netloc == clean_base or target_netloc.endswith("." + clean_base)


def classify_url(url: str, content_type: str = "") -> str:
    """Classifies a resource into 'pdf' or 'html'."""
    path = urlsplit(url).path.lower()
    ct = (content_type or "").lower()

    if path.endswith(".pdf") or "application/pdf" in ct:
        return "pdf"
    return "html"


# ---------------------------------------------------------------------------
# Robots.txt Manager
# ---------------------------------------------------------------------------
class RobotsManager:
    """Manages and caches robots.txt rules per domain."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT):
        self.user_agent = user_agent
        self._parsers: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._lock = threading.Lock()

    def get_parser(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        split = urlsplit(url)
        domain = split.netloc.lower()
        if not domain:
            return None

        with self._lock:
            if domain in self._parsers:
                return self._parsers[domain]

        robots_url = f"{split.scheme}://{split.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)

        try:
            resp = requests.get(
                robots_url,
                headers={"User-Agent": self.user_agent},
                timeout=8
            )
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                logger.info(f"[ROBOTS] Loaded rules from {robots_url}")
            elif resp.status_code in (403, 404):
                rp = None
            else:
                rp = None
        except Exception as err:
            logger.debug(f"[ROBOTS] Could not fetch {robots_url}: {err}")
            rp = None

        with self._lock:
            self._parsers[domain] = rp
        return rp

    def can_fetch(self, url: str) -> bool:
        split = urlsplit(url)
        if split.scheme not in ("http", "https"):
            return True
        rp = self.get_parser(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True


# ---------------------------------------------------------------------------
# Human Stealth Browser Engine (Anti-Block & Natural Human Interaction)
# ---------------------------------------------------------------------------
class HumanStealthBrowser:
    """
    Simulates real human browser behavior (smooth scrolling, reading pauses,
    mouse micro-movements, disclaimer dismissal, and accordion section checking)
    using native headless Chrome/Edge via Chrome DevTools Protocol (CDP),
    with graceful fallback to TLS-impersonated curl_cffi and standard requests.
    """

    def __init__(self, user_agent: Optional[str] = None):
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.chrome_path = self._find_browser_binary()

    def _find_browser_binary(self) -> Optional[str]:
        """Locates installed Google Chrome or Microsoft Edge binaries."""
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        for name in ("chrome", "google-chrome", "google-chrome-stable", "msedge", "chromium"):
            which_p = shutil.which(name)
            if which_p and os.path.isfile(which_p):
                return which_p
        return None

    def fetch_with_human_simulation(
        self,
        url: str,
        logger_func=None,
        timeout: int = 25
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Executes human browsing simulation on url via Chrome DevTools Protocol:
        1. Smooth multi-step scrolling with human reading pauses.
        2. Occasional reverse scrolling to re-inspect sections.
        3. Mouse micro-movements and hover simulation.
        4. Disclaimer/cookie banner dismissal & accordion header auto-expansion.
        5. Extraction of dynamic post-interaction DOM.
        """
        meta: Dict[str, Any] = {
            "engine": "chrome_cdp",
            "scroll_steps": 0,
            "buttons_clicked": [],
            "title": "",
            "success": False
        }

        def log(msg, level="INFO"):
            if logger_func:
                logger_func(f"[STEALTH-BROWSER] {msg}", level=level)
            else:
                logger.info(f"[STEALTH-BROWSER] {msg}")

        if not self.chrome_path or not websocket:
            log("Headless Chrome or websocket library unavailable. Falling back to TLS-impersonated HTTP.", level="WARN")
            return self._fetch_via_tls_fallback(url, logger_func)

        port = random.randint(9250, 9990)
        temp_profile = tempfile.mkdtemp(prefix="cdc_stealth_chrome_")
        cmd = [
            self.chrome_path,
            "--headless=new",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={temp_profile}",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-background-networking",
            "--window-size=1440,900",
            f"--user-agent={self.user_agent}"
        ]

        proc = None
        ws = None
        try:
            log(f"Spawning native browser engine (Port {port})...")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.2)

            # Create new page tab
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{url}", method="PUT")
            with urllib.request.urlopen(req, timeout=8) as resp:
                tab = json.loads(resp.read().decode())
            ws_url = tab.get("webSocketDebuggerUrl")
            if not ws_url:
                raise ValueError("Could not obtain Chrome WebSocket URL.")

            ws = websocket.create_connection(ws_url, timeout=timeout)

            # Enable CDP Domains
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))

            # Wait for document.readyState complete
            for _ in range(15):
                time.sleep(0.3)
                ws.send(json.dumps({
                    "id": 10,
                    "method": "Runtime.evaluate",
                    "params": {"expression": "document.readyState", "returnByValue": True}
                }))
                msg = json.loads(ws.recv())
                if msg.get("result", {}).get("result", {}).get("value") == "complete":
                    break

            # 1. Human Scrolling Simulation
            scroll_count = random.randint(3, 5)
            log(f"Simulating human scrolling ({scroll_count} smooth steps with reading pauses)...")
            for i in range(scroll_count):
                delta = random.randint(320, 680)
                scroll_code = f"window.scrollBy({{top: {delta}, behavior: 'smooth'}}); ({{ y: window.scrollY }})"
                ws.send(json.dumps({
                    "id": 20 + i,
                    "method": "Runtime.evaluate",
                    "params": {"expression": scroll_code, "returnByValue": True}
                }))
                _ = ws.recv()
                time.sleep(random.uniform(0.35, 0.70))

            # Occasional human reverse scroll (simulating rereading)
            if random.random() < 0.45:
                rev_delta = -random.randint(180, 320)
                scroll_back = f"window.scrollBy({{top: {rev_delta}, behavior: 'smooth'}})"
                ws.send(json.dumps({
                    "id": 30,
                    "method": "Runtime.evaluate",
                    "params": {"expression": scroll_back, "returnByValue": True}
                }))
                _ = ws.recv()
                time.sleep(random.uniform(0.3, 0.5))
                log("Simulated human reverse scroll (checking previous section).")

            meta["scroll_steps"] = scroll_count

            # 2. Human Button Checking & Accordion Auto-Clicking
            log("Checking page for regulatory disclaimers, cookie banners, or collapsed accordions...")
            click_code = r"""
            (function() {
                let actions = [];
                // Check and dismiss disclaimers / cookie prompts
                const buttons = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"], [role="button"], summary'));
                for (const el of buttons) {
                    const text = (el.innerText || el.value || '').trim();
                    if (/^(accept|i agree|agree|close|dismiss|acknowledge|got it|understand|ok|continue|confirm)$/i.test(text) ||
                        /accept all|agree & continue|accept cookies|allow all|close dialog|close notice/i.test(text)) {
                        try {
                            el.click();
                            actions.push('Dismissed: ' + text);
                            break;
                        } catch(e) {}
                    }
                }
                // Check and expand collapsed regulatory accordions
                const accordions = Array.from(document.querySelectorAll('details:not([open]) summary, .accordion-toggle:not(.active), [data-toggle="collapse"], .collapsible:not(.active), .accordion-button.collapsed, [aria-expanded="false"]'));
                for (let idx = 0; idx < Math.min(accordions.length, 4); idx++) {
                    try {
                        const acc = accordions[idx];
                        acc.click();
                        const accText = (acc.innerText || acc.getAttribute('aria-label') || 'section').trim().slice(0, 30);
                        actions.push('Expanded accordion: ' + accText);
                    } catch(e) {}
                }
                const fullText = (document.title + ' ' + (document.body ? document.body.innerText.slice(0, 1200) : '')).toLowerCase();
                const isChallenge = /just a moment|checking your browser|attention required|cf-turnstile|cf-challenge|challenges\.cloudflare/i.test(fullText);
                return {
                    clicked: actions,
                    title: document.title,
                    url: window.location.href,
                    is_challenge: isChallenge
                };
            })()
            """
            ws.send(json.dumps({
                "id": 40,
                "method": "Runtime.evaluate",
                "params": {"expression": click_code, "returnByValue": True}
            }))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 40:
                    click_res = msg.get("result", {}).get("result", {}).get("value", {})
                    meta["buttons_clicked"] = click_res.get("clicked", [])
                    meta["title"] = click_res.get("title", "")
                    meta["captcha_detected"] = click_res.get("is_challenge", False)
                    if meta["captcha_detected"]:
                        log(f"Cloudflare Turnstile challenge detected on {url}. Polling up to 5s for auto-verification/redirection...", level="WARN")
                        for _ in range(5):
                            time.sleep(1.0)
                            poll_code = r"""
                            (function() {
                                const full = (document.title + ' ' + (document.body ? document.body.innerText.slice(0, 1500) : '')).toLowerCase();
                                const isStillChallenge = /just a moment|checking your browser|attention required|cf-turnstile|cf-challenge|challenges\.cloudflare|performing security verification|protect against malicious bots|ray id:/i.test(full);
                                return {
                                    title: document.title,
                                    is_challenge: isStillChallenge
                                };
                            })()
                            """
                            try:
                                ws.send(json.dumps({
                                    "id": 48,
                                    "method": "Runtime.evaluate",
                                    "params": {"expression": poll_code, "returnByValue": True}
                                }))
                                poll_msg = json.loads(ws.recv())
                                poll_val = poll_msg.get("result", {}).get("result", {}).get("value", {})
                                if not poll_val.get("is_challenge"):
                                    meta["captcha_detected"] = False
                                    meta["title"] = poll_val.get("title", "")
                                    log("Cloudflare security verification passed; transitioned to genuine regulatory page.", level="SUCCESS")
                                    break
                            except Exception:
                                break

                    if meta["captcha_detected"]:
                        meta["captcha_url"] = url
                        meta["success"] = False
                        log(f"Cloudflare challenge active on {url}. Refusing to extract challenge interstitial screen.", level="WARN")
                        return "", meta
                    elif meta["buttons_clicked"]:
                        log(f"Auto-interacted with {len(meta['buttons_clicked'])} page elements: {meta['buttons_clicked']}", level="SUCCESS")
                    else:
                        log("No blocking modals detected; document body is fully accessible.")
                    break

            # Settle delay for DOM transitions
            time.sleep(random.uniform(0.4, 0.75))

            # 3. Extract final rendered DOM
            log("Extracting dynamic post-interaction DOM...")
            ws.send(json.dumps({
                "id": 50,
                "method": "Runtime.evaluate",
                "params": {"expression": "document.documentElement.outerHTML", "returnByValue": True}
            }))
            html = ""
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 50:
                    html = msg.get("result", {}).get("result", {}).get("value", "")
                    break

            # Secondary verification: Confirm extracted DOM is not a challenge screen
            if is_security_challenge_or_blocked(meta.get("title", ""), "", html):
                meta["captcha_detected"] = True
                meta["captcha_url"] = url
                meta["success"] = False
                log(f"Challenge signatures detected in extracted DOM for {url}. Dropping response.", level="WARN")
                return "", meta

            meta["success"] = True
            log(f"Human browsing simulation succeeded ({len(html)} bytes captured).", level="SUCCESS")
            return html, meta

        except Exception as e:
            log(f"Browser interaction warning: {e}. Falling back to TLS engine.", level="WARN")
            return self._fetch_via_tls_fallback(url, logger_func)
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            try:
                shutil.rmtree(temp_profile, ignore_errors=True)
            except Exception:
                pass

    def _fetch_via_tls_fallback(self, url: str, logger_func=None) -> Tuple[str, Dict[str, Any]]:
        """Fallback engine using curl_cffi with Chrome 120 TLS fingerprint and challenge detection."""
        if cffi_requests:
            try:
                session = cffi_requests.Session(impersonate="chrome120")
                headers = {
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
                resp = session.get(url, headers=headers, timeout=15)
                is_challenge = (resp.status_code == 403) or is_security_challenge_or_blocked("", resp.text, resp.text)
                if is_challenge:
                    if logger_func:
                        logger_func(f"[STEALTH-BROWSER] Security challenge detected on {url}", level="WARN")
                    return "", {
                        "engine": "curl_cffi_chrome120",
                        "status_code": resp.status_code,
                        "success": False,
                        "captcha_detected": True,
                        "captcha_url": url
                    }
                return resp.text, {
                    "engine": "curl_cffi_chrome120",
                    "status_code": resp.status_code,
                    "success": (resp.status_code == 200)
                }
            except Exception as cffi_err:
                if logger_func:
                    logger_func(f"[STEALTH-BROWSER] curl_cffi attempt failed ({cffi_err}), using standard requests.", level="WARN")

        import requests
        r = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=15)
        is_challenge = (r.status_code == 403) or is_security_challenge_or_blocked("", r.text, r.text)
        if is_challenge:
            if logger_func:
                logger_func(f"[STEALTH-BROWSER] Security challenge detected on {url}", level="WARN")
            return "", {
                "engine": "requests_standard",
                "status_code": r.status_code,
                "success": False,
                "captcha_detected": True,
                "captcha_url": url
            }
        return r.text, {
            "engine": "requests_standard",
            "status_code": r.status_code,
            "success": (r.status_code == 200)
        }


# ---------------------------------------------------------------------------
# Document Extractors
# ---------------------------------------------------------------------------
def extract_html_page(html_text: str, base_url: str) -> Tuple[Dict[str, str], List[str]]:
    """
    Parses HTML, removes boilerplates, extracts clean title and text,
    and returns (extracted_dict, list_of_internal_and_pdf_links).
    """
    soup = BeautifulSoup(html_text, "html.parser")

    # Discover links before removing tags
    discovered_links: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
            resolved = urljoin(base_url, href)
            split = urlsplit(resolved)
            clean = urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))
            if split.scheme in ("http", "https"):
                discovered_links.append(clean)

    # Title extraction
    title = ""
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        title = title_tag.get_text(strip=True)
    else:
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(strip=True)

    if not title:
        path_stem = Path(urlsplit(base_url).path).stem.replace("-", " ").replace("_", " ").title()
        title = path_stem if path_stem else "Regulatory Web Notice"

    # Decompose boilerplate elements
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "svg", "form"]):
        tag.decompose()

    raw_text = soup.get_text(separator="\n")
    cleaned_text = normalize_whitespace(raw_text)

    # Security challenge & bot interstitial filter
    if is_security_challenge_or_blocked(title, cleaned_text, html_text):
        return {"title": title, "text": "", "is_challenge": True}, []

    return {"title": title, "text": cleaned_text}, discovered_links


def extract_text_via_gemini_ocr(pdf_bytes: bytes, base_url: str = "") -> str:
    """Uses Gemini 2.5 Flash Vision OCR to extract text from scanned or image-based PDFs."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        prompt = (
            "You are an expert Optical Character Recognition (OCR) system for official regulatory documents. "
            "Extract all text from this scanned document verbatim. Include all headings, circular numbers, "
            "dates, sections, tables, and paragraphs. Do not add conversational commentary."
        )
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt
            ]
        )
        return response.text or ""
    except Exception as e:
        logger.warning(f"Gemini OCR fallback failed for {base_url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Dynamic Category Extraction & Acronym Dictionary
# ---------------------------------------------------------------------------
ACRONYM_MAP = {
    "aml": "AML", "cft": "CFT", "kyc": "KYC", "cdd": "CDD", "cds": "CDS",
    "cdc": "CDC", "secp": "SECP", "reit": "REIT", "reits": "REITs",
    "vps": "VPS", "ipo": "IPO", "eipo": "eIPO", "edividend": "eDividend",
    "eservices": "eServices", "sro": "SRO", "fatf": "FATF", "fmu": "FMU",
    "nbfc": "NBFC", "nbfcs": "NBFCs", "nccpl": "NCCPL", "psx": "PSX",
    "faq": "FAQ", "faqs": "FAQs", "pdf": "PDF", "api": "API", "apis": "APIs",
    "csr": "CSR", "hr": "HR", "it": "IT", "cnic": "CNIC", "iban": "IBAN",
    "wht": "WHT", "gst": "GST", "sop": "SOP", "sops": "SOPs"
}

GENERIC_SEGMENTS = {
    "wp-content", "wp-includes", "uploads", "assets", "static", "files",
    "documents", "doc", "downloads", "index.php", "document-category",
    "category", "categories", "taxonomy", "tag", "tags", "archive", "archives",
    "en", "pk", "page", "pages", "item", "items", "view", "details"
}

GENERIC_SITEMAP_HINTS = {
    "post", "posts", "page", "pages", "attachment", "attachments",
    "default", "sitemap", "index", "item", "items", "urlset", "all", "general", ""
}


def clean_sitemap_hint(hint: str) -> str:
    """Extracts a human-readable category from a sitemap filename or URL."""
    if not hint:
        return ""
    path = urlsplit(hint).path if ("://" in hint or "/" in hint) else hint
    filename = Path(path).name
    h = re.sub(r"\.(xml|gz|html)$", "", filename, flags=re.I)
    h = re.sub(r"^(wp-)?sitemap[-_]?", "", h, flags=re.I)
    h = re.sub(r"[-_]?sitemap.*$", "", h, flags=re.I)
    h = re.sub(r"[-_]\d+$", "", h)
    if not h:
        return ""
    return clean_category_slug(h)


def clean_category_slug(text: str) -> str:
    """Formats a slug or path segment into title casing with proper acronyms."""
    text = re.sub(r"\.(html|htm|php|aspx|pdf|xml|json)$", "", text, flags=re.I)
    raw_words = re.split(r"[-_\s/]+", text)
    cleaned = []
    i = 0
    while i < len(raw_words):
        w = raw_words[i]
        if not w:
            i += 1
            continue
        wl = w.lower()

        # Special phrase check: trustee-custodial
        if wl == "trustee" and i + 1 < len(raw_words) and raw_words[i + 1].lower() in ("custodial", "custody"):
            cleaned.append("Trustee & Custodial Services" if i + 2 == len(raw_words) or raw_words[i + 2].lower() != "services" else "Trustee & Custodial")
            i += 2
            continue

        if wl in ACRONYM_MAP:
            acro = ACRONYM_MAP[wl]
            # If followed by another acronym like AML followed by CFT
            if i + 1 < len(raw_words) and raw_words[i + 1].lower() in ACRONYM_MAP:
                next_acro = ACRONYM_MAP[raw_words[i + 1].lower()]
                cleaned.append(f"{acro} / {next_acro}")
                i += 2
                continue
            cleaned.append(acro)
        elif wl in ("and", "et"):
            cleaned.append("&")
        elif wl in ("of", "in", "for", "the", "on", "at", "to"):
            cleaned.append(wl)
        else:
            cleaned.append(w.capitalize())
        i += 1
    return " ".join(cleaned).strip() or "General"


def extract_category_from_url_and_context(
    url: str,
    title: str = "",
    text: str = "",
    sitemap_hint: str = ""
) -> str:
    """
    Dynamically derives the category for a document or link from:
    1. Direct sitemap category hint or sub-sitemap origin (unless generic)
    2. URL path taxonomy & directory hierarchy (without hardcoded fixed categories)
    3. Document title or content context if URL has no path segments
    """
    # 1. Sitemap hint if provided and non-generic
    if sitemap_hint:
        hint_clean = clean_sitemap_hint(sitemap_hint)
        if hint_clean and hint_clean.lower() not in GENERIC_SITEMAP_HINTS:
            return hint_clean

    parsed = urlsplit(url)
    path = parsed.path.strip("/")

    # 2. Extract meaningful segments from URL path
    segments = [s for s in path.split("/") if s]
    meaningful = []
    for s in segments:
        sl = re.sub(r"\.(html|htm|php|aspx|pdf|xml)$", "", s, flags=re.I).lower()
        if sl in GENERIC_SEGMENTS and len(segments) > 1:
            continue
        meaningful.append(s)

    if not meaningful and segments:
        meaningful = segments

    if meaningful:
        seg1 = re.sub(r"\.(html|htm|php|aspx|pdf|xml)$", "", meaningful[0], flags=re.I)
        cat_top = clean_category_slug(seg1)

        if len(meaningful) > 1:
            seg2 = re.sub(r"\.(html|htm|php|aspx|pdf|xml)$", "", meaningful[1], flags=re.I)
            is_file_like = any(meaningful[1].lower().endswith(ext) for ext in [".pdf", ".html", ".htm", ".aspx"])
            if not is_file_like:
                cat_sub = clean_category_slug(seg2)
                if cat_top.lower() in cat_sub.lower():
                    return cat_sub
                elif cat_sub.lower() in cat_top.lower():
                    return cat_top
                else:
                    return f"{cat_top} / {cat_sub}"
        return cat_top

    # 3. Fallback from title or text
    if title:
        for sep in [" - ", " | ", " — ", " : "]:
            if sep in title:
                parts = [p.strip() for p in title.split(sep) if p.strip()]
                for p in parts:
                    if len(p) <= 40 and not any(k in p.lower() for k in ["cdc", "pakistan", "home", "official"]):
                        return clean_category_slug(p)

        tl = title.lower()
        if "circular" in tl or "directive" in tl or "sro" in tl or "notice" in tl:
            return "Circulars & Directives"
        if "regulation" in tl or "rule" in tl or "procedure" in tl:
            return "Regulations & Procedures"
        if "aml" in tl or "cft" in tl or "kyc" in tl or "cdd" in tl:
            return "AML / CFT & Compliance"
        if "trustee" in tl or "custodial" in tl or "reit" in tl:
            return "Trustee & Custodial Services"
        if "eservice" in tl or "eipo" in tl or "portal" in tl:
            return "eServices & Portals"
        if "governance" in tl or "policy" in tl or "board" in tl:
            return "Corporate Governance & Policies"
        return clean_category_slug(title[:40])

    return "Main Site / Overview"


def classify_regulatory_category(title: str, url: str, text: str = "") -> str:
    """Classifies a document dynamically from its URL taxonomy, title, and context."""
    return extract_category_from_url_and_context(url=url, title=title, text=text)


def extract_pdf_document(pdf_bytes: bytes, base_url: str) -> Dict[str, str]:
    """
    Extracts text from PDF bytes using pdfplumber, with automatic fallback
    to Gemini Vision OCR if pages are scanned images.
    """
    stream = io.BytesIO(pdf_bytes)
    title = None
    pages_text: List[str] = []

    with pdfplumber.open(stream) as pdf:
        metadata = pdf.metadata or {}
        meta_title = metadata.get("Title") or metadata.get("title")
        if meta_title and str(meta_title).strip():
            title = str(meta_title).strip()

        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)

    if not title:
        stem = Path(urlsplit(base_url).path).stem.replace("-", " ").replace("_", " ").title()
        title = stem if stem else "Regulatory Document (PDF)"

    # Clean repeated headers/footers across multi-page PDFs
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

    # Scanned PDF Detection: if extract_text returned < 25 words, run Gemini Vision OCR
    if not cleaned or len(cleaned.split()) < 25:
        logger.info(f"PDF at {base_url} appears scanned/image-based ({len(cleaned.split())} words). Triggering Gemini OCR...")
        ocr_result = extract_text_via_gemini_ocr(pdf_bytes, base_url)
        if ocr_result and len(ocr_result.split()) >= 15:
            cleaned = normalize_whitespace(ocr_result)

    if not cleaned:
        raise ValueError(f"No readable text extracted from PDF at {base_url}")

    return {"title": title, "text": cleaned}


def is_url_already_ingested(url: str) -> bool:
    """
    Checks if a URL has already been ingested into the SQLite review queue
    or live documents/Chroma store to prevent duplicate downloads and allow seamless resuming.
    """
    doc_id = compute_doc_id(url)
    try:
        conn = review_manager.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM review_queue WHERE doc_id = ? LIMIT 1", (doc_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            return True
    except Exception:
        pass

    raw_path = get_project_root() / "data" / "raw" / "documents.jsonl"
    if raw_path.exists():
        try:
            with open(raw_path, "r", encoding="utf-8") as f:
                for line in f:
                    if f'"{doc_id}"' in line:
                        return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Main SiteCrawler Class
# ---------------------------------------------------------------------------
class SiteCrawler:
    """
    Autonomous domain-locked website crawler with polite rate-limiting,
    sitemap auto-discovery, HTML/PDF extraction, and dual indexing targets.
    """

    def __init__(
        self,
        root_url: str,
        max_pages: int = DEFAULT_MAX_PAGES,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        auto_approve: bool = False,
        crawl_id: Optional[str] = None,
        check_robots: bool = True,
        jitter_min: float = 0.2,
        jitter_max: float = 0.8,
        user_agent: str = DEFAULT_USER_AGENT,
        allowed_urls: Optional[List[str]] = None,
        stealth_mode: bool = True,
        resume_existing: bool = True,
    ):
        self.root_url = root_url.strip()
        self.base_domain = get_base_domain(self.root_url)
        self.allowed_urls = [u.strip() for u in allowed_urls if u.strip()] if allowed_urls else None
        if self.allowed_urls:
            self.max_pages = max(1, len(self.allowed_urls))
        else:
            self.max_pages = max(1, int(max_pages))
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.auto_approve = bool(auto_approve)
        self.crawl_id = crawl_id or f"crawl_{int(time.time())}_{random.randint(1000, 9999)}"
        self.check_robots = check_robots
        self.jitter_min = jitter_min
        self.jitter_max = jitter_max
        self.user_agent = user_agent
        self.stealth_mode = bool(stealth_mode)
        self.stealth_browser = HumanStealthBrowser(user_agent=self.user_agent) if self.stealth_mode else None
        self.resume_existing = bool(resume_existing)
        self.last_captcha_url: Optional[str] = None
        self.has_captcha_block: bool = False

        # Human Pacing, Batching & Retries
        self.batch_size = 4
        self.batch_break_min = 8.0
        self.batch_break_max = 15.0
        self.batch_count = 0
        self.max_retries = 3
        self.remaining_queue: List[str] = []

        # Concurrency & Control Flags
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused by default
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None

        # State tracking
        self.status = "pending"  # pending, running, paused, completed, stopped, failed
        self.start_epoch: Optional[float] = None
        self.current_stage: str = "Ready"
        self.category_counts: Dict[str, int] = collections.defaultdict(int)
        self.pages_crawled = 0
        self.pdfs_extracted = 0
        self.chunks_indexed = 0
        self.errors_count = 0
        self.current_url: str = ""
        self.start_time: Optional[str] = None
        self.end_time: Optional[str] = None
        self.last_error: Optional[str] = None
        self._cf_warned: bool = False

        # Logging & Frontiers
        self.logs: List[Dict[str, Any]] = []
        self.visited_urls: Set[str] = set()
        self.robots_mgr = RobotsManager(user_agent=self.user_agent) if check_robots else None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes crawler configuration, metrics, and frontier state for disk persistence."""
        with self._lock:
            return {
                "crawl_id": self.crawl_id,
                "root_url": self.root_url,
                "base_domain": self.base_domain,
                "max_pages": self.max_pages,
                "delay_seconds": self.delay_seconds,
                "auto_approve": self.auto_approve,
                "check_robots": self.check_robots,
                "allowed_urls": self.allowed_urls,
                "stealth_mode": self.stealth_mode,
                "resume_existing": self.resume_existing,
                "status": self.status,
                "pages_crawled": self.pages_crawled,
                "pdfs_extracted": self.pdfs_extracted,
                "chunks_indexed": self.chunks_indexed,
                "errors_count": self.errors_count,
                "current_url": self.current_url,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "last_error": self.last_error,
                "last_captcha_url": self.last_captcha_url,
                "has_captcha_block": bool(self.has_captcha_block or self.last_captcha_url),
                "visited_urls": list(self.visited_urls),
                "remaining_queue": getattr(self, "remaining_queue", []),
                "logs": list(self.logs[-80:]),
                "category_counts": dict(self.category_counts),
                "batch_count": getattr(self, "batch_count", 0),
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SiteCrawler":
        """Reconstructs a SiteCrawler instance from saved disk dictionary."""
        crawler = cls(
            root_url=data.get("root_url", ""),
            max_pages=data.get("max_pages", DEFAULT_MAX_PAGES),
            delay_seconds=data.get("delay_seconds", DEFAULT_DELAY_SECONDS),
            auto_approve=data.get("auto_approve", False),
            crawl_id=data.get("crawl_id"),
            check_robots=data.get("check_robots", True),
            allowed_urls=data.get("allowed_urls"),
            stealth_mode=data.get("stealth_mode", True),
            resume_existing=data.get("resume_existing", True),
        )
        crawler.status = data.get("status", "paused")
        crawler.pages_crawled = data.get("pages_crawled", 0)
        crawler.pdfs_extracted = data.get("pdfs_extracted", 0)
        crawler.chunks_indexed = data.get("chunks_indexed", 0)
        crawler.errors_count = data.get("errors_count", 0)
        crawler.current_url = data.get("current_url", "")
        crawler.start_time = data.get("start_time")
        crawler.end_time = data.get("end_time")
        crawler.last_error = data.get("last_error")
        crawler.last_captcha_url = data.get("last_captcha_url")
        crawler.has_captcha_block = bool(data.get("has_captcha_block", False) or data.get("last_captcha_url"))
        crawler.visited_urls = set(data.get("visited_urls", []))
        crawler.remaining_queue = list(data.get("remaining_queue", []))
        crawler.logs = list(data.get("logs", []))
        crawler.category_counts = collections.defaultdict(int, data.get("category_counts", {}))
        crawler.batch_count = data.get("batch_count", 0)

        # Reconcile ghost running states on restore
        if crawler.status == "running" and not crawler.remaining_queue:
            crawler.status = "completed"
            if not crawler.end_time:
                crawler.end_time = get_current_utc_iso()
        return crawler

    def add_log(self, message: str, level: str = "INFO"):
        """Records a timestamped log entry in memory and emits via standard logging."""
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = {
            "timestamp": timestamp,
            "level": level,
            "message": message
        }
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 300:
                self.logs.pop(0)

        if level == "ERROR":
            logger.error(f"[{self.crawl_id}] {message}")
        elif level == "WARN":
            logger.warning(f"[{self.crawl_id}] {message}")
        else:
            logger.info(f"[{self.crawl_id}] {message}")

    def get_status(self) -> Dict[str, Any]:
        """Returns snapshot of current crawler metrics, ETA, and recent logs."""
        with self._lock:
            processed = self.pages_crawled + self.errors_count
            total_target = max(1, self.max_pages)
            progress = round((processed / total_target) * 100, 1)
            elapsed_sec = int(time.time() - self.start_epoch) if self.start_epoch else 0

            # Calculate ETA and processing velocity
            if processed > 0 and self.status == "running":
                sec_per_item = elapsed_sec / processed
                items_left = max(0, total_target - processed)
                eta_sec = int(items_left * sec_per_item)
                if eta_sec < 60:
                    eta_str = f"{eta_sec}s"
                else:
                    eta_str = f"{eta_sec // 60}m {eta_sec % 60}s"
                speed_str = f"{(processed / max(1, elapsed_sec)):.2f} p/s"
            elif self.status in ("completed", "stopped", "failed", "completed_with_errors"):
                if self.status == "completed":
                    eta_str = "Done"
                elif self.status == "failed":
                    eta_str = "Failed"
                elif self.status == "stopped":
                    eta_str = "Stopped"
                else:
                    eta_str = "Done"
                speed_str = f"{(processed / max(1, elapsed_sec)):.2f} p/s" if elapsed_sec > 0 else "0 p/s"
            else:
                eta_str = "Calc..." if self.status == "running" else "--"
                speed_str = "0 p/s"

            return {
                "crawl_id": self.crawl_id,
                "root_url": self.root_url,
                "base_domain": self.base_domain,
                "status": self.status,
                "stage": self.current_stage,
                "pages_crawled": self.pages_crawled,
                "pdfs_extracted": self.pdfs_extracted,
                "chunks_indexed": self.chunks_indexed,
                "errors_count": self.errors_count,
                "max_pages": self.max_pages,
                "current_url": self.current_url,
                "progress_percent": min(100.0, progress),
                "elapsed_seconds": elapsed_sec,
                "eta": eta_str,
                "speed": speed_str,
                "auto_approve": self.auto_approve,
                "delay_seconds": self.delay_seconds,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "error": self.last_error,
                "logs": list(self.logs[-80:]),
                "visited_count": len(self.visited_urls),
                "categories": dict(self.category_counts),
                "stealth_mode": self.stealth_mode,
                "captcha_blocked_url": self.last_captcha_url,
                "has_captcha_block": bool(self.has_captcha_block or self.last_captcha_url),
                "resume_existing": self.resume_existing
            }

    def start(self):
        """Spawns non-blocking crawl background thread."""
        should_start = False
        with self._lock:
            if self.status == "running":
                return
            self.status = "running"
            self.start_epoch = time.time()
            self.current_stage = "Discovering seed links and sitemap"
            self.start_time = get_current_utc_iso()
            self._thread = threading.Thread(target=self._run_crawl, daemon=True, name=f"crawler_{self.crawl_id}")
            should_start = True
        self.add_log(f"Crawl job started for {self.root_url} (Domain: {self.base_domain}, Max: {self.max_pages})")
        if should_start and self._thread:
            self._thread.start()
        save_crawler_state()

    def run(self):
        """Runs crawl synchronously (useful for test cases)."""
        with self._lock:
            self.status = "running"
            self.start_epoch = time.time()
            self.current_stage = "Discovering seed links and sitemap"
            self.start_time = get_current_utc_iso()
        self.add_log(f"Crawl job running synchronously for {self.root_url}")
        self._run_crawl()

    def pause(self):
        """Pauses crawl loop execution."""
        with self._lock:
            if self.status == "running":
                self.status = "paused"
                self._pause_event.clear()
                self.add_log("Crawl paused by operator.", level="WARN")
        save_crawler_state()

    def resume(self):
        """Resumes paused crawl loop and clears captcha challenge state."""
        should_start_thread = False
        with self._lock:
            if self.status in ("paused", "running", "stopped", "failed") or self.has_captcha_block:
                self.status = "running"
                self.has_captcha_block = False
                self.last_captcha_url = None
                self._stop_event.clear()
                self._pause_event.set()
                if not (self._thread and self._thread.is_alive()):
                    self._thread = threading.Thread(target=self._run_crawl, daemon=True, name=f"crawler_{self.crawl_id}")
                    should_start_thread = True
                self.add_log("Crawl resumed by operator. Proceeding to fetch next targets...")
        if should_start_thread and self._thread:
            self._thread.start()
        save_crawler_state()

    def stop(self):
        """Signals graceful termination of crawler thread."""
        self._stop_event.set()
        self._pause_event.set()  # Unblock if paused
        with self._lock:
            self.status = "stopped"
            self.end_time = get_current_utc_iso()
        self.add_log("Stop signal received; halting crawler gracefully.", level="WARN")
        save_crawler_state()

    def _discover_sitemap_urls(self) -> List[str]:
        """Checks standard sitemap endpoints for fast canonical link discovery."""
        sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"]
        found: List[str] = []

        for path in sitemap_paths:
            if self._stop_event.is_set():
                break

            sitemap_url = urljoin(self.root_url, path)
            if self.robots_mgr and not self.robots_mgr.can_fetch(sitemap_url):
                self.add_log(f"Robots.txt disallows sitemap: {sitemap_url}", level="WARN")
                continue

            try:
                self.add_log(f"Checking for sitemap at {sitemap_url}...")
                resp = requests.get(
                    sitemap_url,
                    headers={"User-Agent": self.user_agent},
                    timeout=8
                )
                if resp.status_code == 200 and ("<loc>" in resp.text or "<url>" in resp.text):
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for loc in soup.find_all("loc"):
                        raw_loc = loc.get_text(strip=True)
                        if is_same_domain(raw_loc, self.base_domain):
                            found.append(raw_loc)
                    if found:
                        self.add_log(f"Discovered {len(found)} canonical URLs from sitemap: {sitemap_url}", level="SUCCESS")
                        break
            except Exception as e:
                self.add_log(f"Notice: Could not parse sitemap at {sitemap_url}: {e}", level="INFO")

        return found

    def _wait_polite_delay(self):
        """Wait polite delay with random jitter, responsive to cancellation."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if self.delay_seconds <= 0:
            return
        jitter = random.uniform(self.jitter_min, self.jitter_max) if self.jitter_max > 0 else 0
        total_delay = self.delay_seconds + jitter
        self.add_log(f"Simulating natural human reading interval ({total_delay:.1f}s)...", level="INFO")
        self._stop_event.wait(timeout=total_delay)

    def _run_crawl(self):
        """Internal recursive domain-locked crawl loop."""
        try:
            queue: collections.deque = collections.deque()
            frontier_set: Set[str] = set()

            if getattr(self, "remaining_queue", None):
                self.add_log(f"Resuming crawl from saved state: {len(self.remaining_queue)} URLs remaining.")
                for sm_url in self.remaining_queue:
                    if sm_url not in frontier_set:
                        queue.append(sm_url)
                        frontier_set.add(sm_url)
            elif self.allowed_urls:
                self.add_log(f"Pre-approved mode: Queuing {len(self.allowed_urls)} approved URLs for crawl.")
                for sm_url in self.allowed_urls:
                    if sm_url not in frontier_set:
                        queue.append(sm_url)
                        frontier_set.add(sm_url)
            else:
                # 1. Sitemap Check
                sitemap_urls = self._discover_sitemap_urls()
                for sm_url in sitemap_urls[: self.max_pages]:
                    if sm_url not in frontier_set:
                        queue.append(sm_url)
                        frontier_set.add(sm_url)

                # 2. Add Start URL
                if self.root_url not in frontier_set:
                    queue.append(self.root_url)
                    frontier_set.add(self.root_url)

            self.add_log(f"Initial exploration frontier size: {len(queue)} URLs")

            while queue and self.pages_crawled < self.max_pages:
                # Check for stop request
                if self._stop_event.is_set():
                    self.add_log("Crawler loop stopped cleanly.", level="WARN")
                    break

                # Handle pause state
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                # Human pacing: After every batch_size pages, take a natural human resting break
                if self.batch_count >= self.batch_size and not os.environ.get("PYTEST_CURRENT_TEST"):
                    break_sec = round(random.uniform(self.batch_break_min, self.batch_break_max), 1)
                    self.add_log(f"[HUMAN-PACING] Batch completed ({self.batch_size} pages). Taking a natural reading break ({break_sec}s)...", level="INFO")
                    self.batch_count = 0
                    break_slices = int(break_sec / 0.5)
                    for _ in range(break_slices):
                        if self._stop_event.is_set():
                            break
                        time.sleep(0.5)
                    if self._stop_event.is_set():
                        break

                current_url = queue.popleft()
                self.remaining_queue = list(queue)
                save_crawler_state()

                with self._lock:
                    self.current_url = current_url
                    current_step = min(self.max_pages, self.pages_crawled + self.errors_count + 1)
                    self.current_stage = f"Fetching [{current_step}/{self.max_pages}]"

                # Domain safety check (Strict domain boundary locking)
                if not is_same_domain(current_url, self.base_domain):
                    self.add_log(f"Skipping external domain URL: {current_url}", level="INFO")
                    continue

                # Deduplication
                if current_url in self.visited_urls:
                    continue
                self.visited_urls.add(current_url)

                # Check robots.txt
                if self.robots_mgr and not self.robots_mgr.can_fetch(current_url):
                    self.add_log(f"Robots.txt disallows: {current_url}", level="WARN")
                    continue

                # Polite delay before request
                self._wait_polite_delay()
                if self._stop_event.is_set():
                    break

                # Deduplication & Resume: Skip documents already ingested in review queue or live Chroma
                if self.resume_existing and not (os.environ.get("PYTEST_CURRENT_TEST") and getattr(self, "_force_stealth", False) is False):
                    if is_url_already_ingested(current_url):
                        self.add_log(f"Resuming: '{current_url}' is already ingested. Skipping to avoid duplicate requests.", level="INFO")
                        with self._lock:
                            self.pages_crawled += 1
                        save_crawler_state()
                        continue

                doc_type = classify_url(current_url)
                is_binary = (doc_type == "pdf")

                headers = {
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }

                try:
                    current_step = min(self.max_pages, self.pages_crawled + self.errors_count + 1)

                    use_stealth = (
                        self.stealth_mode
                        and self.stealth_browser is not None
                        and not os.environ.get("DISABLE_STEALTH_BROWSER")
                        and not (os.environ.get("PYTEST_CURRENT_TEST") and getattr(self, "_force_stealth", False) is False)
                    )

                    extracted_data: Dict[str, str] = {}
                    discovered_links: List[str] = []
                    resp_content: bytes = b""
                    resp_text: str = ""

                    if use_stealth and doc_type == "html":
                        self.add_log(f"Fetching [HTML/STEALTH-HUMAN] ({current_step}/{self.max_pages}): {current_url}")
                        html_text, stealth_meta = self.stealth_browser.fetch_with_human_simulation(current_url, logger_func=self.add_log)
                        if not html_text or stealth_meta.get("captcha_detected"):
                            with self._lock:
                                self.last_captcha_url = current_url
                                self.has_captcha_block = True
                            self.add_log(f"[CAPTCHA-DETECTED] Cloudflare Turnstile challenge active on {current_url}. Click 'Open Page in Browser' to complete verification.", level="WARN")
                            raise requests.exceptions.HTTPError(f"Cloudflare Turnstile challenge active on {current_url}")

                        with self._lock:
                            self.current_stage = f"Parsing HTML Page: {Path(urlsplit(current_url).path).name[:25] or 'Index'}"
                        extracted_data, discovered_links = extract_html_page(html_text, current_url)
                        if extracted_data.get("is_challenge"):
                            with self._lock:
                                self.last_captcha_url = current_url
                                self.has_captcha_block = True
                            self.add_log(f"[CAPTCHA-DETECTED] Cloudflare challenge content detected on {current_url}.", level="WARN")
                            raise requests.exceptions.HTTPError(f"Cloudflare challenge detected on {current_url}")

                        self.add_log(f"Extracted HTML '{extracted_data.get('title')}' with {len(discovered_links)} new links")

                        if not self.allowed_urls:
                            for link in discovered_links:
                                if is_same_domain(link, self.base_domain):
                                    if link not in frontier_set and link not in self.visited_urls:
                                        frontier_set.add(link)
                                        queue.append(link)

                        resp_content = html_text.encode("utf-8", errors="replace")
                        resp_text = html_text

                    elif use_stealth and doc_type == "pdf" and cffi_requests:
                        self.add_log(f"Fetching [PDF/TLS-STEALTH] ({current_step}/{self.max_pages}): {current_url}")
                        cffi_session = cffi_requests.Session(impersonate="chrome120")
                        cffi_resp = cffi_session.get(current_url, headers=headers, timeout=20)
                        if cffi_resp.status_code == 403:
                            with self._lock:
                                self.last_captcha_url = current_url
                                self.has_captcha_block = True
                            raise requests.exceptions.HTTPError(f"403 Client Error: Forbidden for url: {current_url}")
                        resp_content = cffi_resp.content
                        with self._lock:
                            self.current_stage = f"Extracting PDF / OCR: {Path(urlsplit(current_url).path).name[:25]}"
                        extracted_data = extract_pdf_document(resp_content, current_url)
                        with self._lock:
                            self.pdfs_extracted += 1
                        self.add_log(f"Successfully extracted PDF '{extracted_data.get('title')}'", level="SUCCESS")

                    else:
                        self.add_log(f"Fetching [{doc_type.upper()}] ({current_step}/{self.max_pages}): {current_url}")
                        resp = requests.get(current_url, headers=headers, timeout=15)
                        is_cf = (
                            resp.status_code == 403
                            or "cloudflare" in resp.headers.get("server", "").lower()
                            or "cf-mitigated" in resp.headers
                            or "cf-ray" in resp.headers
                            or is_security_challenge_or_blocked("", resp.text, resp.text)
                        )
                        if is_cf and (resp.status_code == 403 or is_security_challenge_or_blocked("", resp.text, resp.text)):
                            with self._lock:
                                self.last_captcha_url = current_url
                                self.has_captcha_block = True
                            if not self._cf_warned:
                                self._cf_warned = True
                                self.add_log(f"[CAPTCHA-DETECTED] Cloudflare WAF challenge detected on {current_url}. Click 'Open Page in Browser' to complete verification.", level="WARN")
                            raise requests.exceptions.HTTPError(f"Blocked by Cloudflare WAF/Turnstile challenge for url: {current_url}", response=resp)
                        resp.raise_for_status()

                        content_type = resp.headers.get("Content-Type", "")
                        refined_type = classify_url(current_url, content_type)
                        if refined_type == "pdf" and doc_type != "pdf":
                            doc_type = "pdf"
                            is_binary = True

                        resp_content = resp.content
                        resp_text = resp.text

                        if doc_type == "pdf":
                            with self._lock:
                                self.current_stage = f"Extracting PDF / OCR: {Path(urlsplit(current_url).path).name[:25]}"
                            extracted_data = extract_pdf_document(resp.content, current_url)
                            with self._lock:
                                self.pdfs_extracted += 1
                            self.add_log(f"Successfully extracted PDF '{extracted_data.get('title')}'", level="SUCCESS")
                        else:
                            with self._lock:
                                self.current_stage = f"Parsing HTML Page: {Path(urlsplit(current_url).path).name[:25] or 'Index'}"
                            resp.encoding = resp.apparent_encoding or "utf-8"
                            extracted_data, discovered_links = extract_html_page(resp.text, current_url)
                            if extracted_data.get("is_challenge"):
                                with self._lock:
                                    self.last_captcha_url = current_url
                                    self.has_captcha_block = True
                                self.add_log(f"[CAPTCHA-DETECTED] Cloudflare challenge content detected on {current_url}.", level="WARN")
                                raise requests.exceptions.HTTPError(f"Cloudflare security challenge detected for url: {current_url}")

                            self.add_log(f"Extracted HTML '{extracted_data.get('title')}' with {len(discovered_links)} new links")

                            # Enqueue internal links within domain only if not in pre-approved targets mode
                            if not self.allowed_urls:
                                for link in discovered_links:
                                    if is_same_domain(link, self.base_domain):
                                        if link not in frontier_set and link not in self.visited_urls:
                                            frontier_set.add(link)
                                            queue.append(link)

                    full_text = extracted_data.get("text", "").strip()
                    title = extracted_data.get("title", "Regulatory Document").strip()

                    if not full_text:
                        self.add_log(f"No textual content in {current_url}; skipping indexing.", level="WARN")
                        with self._lock:
                            self.pages_crawled += 1
                        continue

                    # Substantive content verification guard
                    is_valid, validation_reason = is_valid_regulatory_content(
                        title=title,
                        text=full_text,
                        html_text=resp_text,
                        min_words=25
                    )
                    if not is_valid:
                        self.add_log(f"Content validation rejected for {current_url}: {validation_reason}. Skipping ingestion.", level="WARN")
                        with self._lock:
                            self.pages_crawled += 1
                        continue

                    # Chunking per shared contract (~500 words, 75 overlap)
                    chunks_text = chunk_text(full_text, chunk_size=DEFAULT_CHUNK_WORDS, overlap=DEFAULT_OVERLAP_WORDS)
                    doc_id = compute_doc_id(current_url)
                    now_iso = get_current_utc_iso()

                    # Save permanent local backup file in data/uploads/ for guaranteed offline availability
                    try:
                        uploads_dir = Path(__file__).resolve().parent.parent / "data" / "uploads"
                        uploads_dir.mkdir(parents=True, exist_ok=True)
                        if doc_type == "pdf":
                            backup_file = uploads_dir / f"{doc_id}.pdf"
                            if not backup_file.exists():
                                backup_file.write_bytes(resp_content)
                        else:
                            backup_file = uploads_dir / f"{doc_id}.html"
                            if not backup_file.exists():
                                backup_file.write_text(resp_text, encoding="utf-8", errors="replace")
                    except Exception as backup_err:
                        logger.warning(f"Could not save local document backup for {doc_id}: {backup_err}")

                    prepared_chunks = []
                    for idx, c_text in enumerate(chunks_text):
                        chunk_id = f"{doc_id}_{idx}"
                        prepared_chunks.append({
                            "id": chunk_id,
                            "document": c_text,
                            "metadata": {
                                "doc_id": doc_id,
                                "source_url": current_url,
                                "source_type": doc_type,
                                "title": title,
                                "chunk_index": idx,
                                "date_scraped": now_iso
                            }
                        })

                    # Ingestion Destination: Direct Live Chroma vs SQLite Review Queue
                    if self.auto_approve:
                        self._index_directly_to_chroma(
                            doc_id=doc_id,
                            source_url=current_url,
                            source_type=doc_type,
                            title=title,
                            full_text=full_text,
                            date_scraped=now_iso,
                            chunks=prepared_chunks
                        )
                        self.add_log(f"Indexed {len(prepared_chunks)} chunks directly into Live Chroma for '{title}'", level="SUCCESS")
                    else:
                        self._stage_to_review_queue(
                            doc_id=doc_id,
                            source_url=current_url,
                            source_type=doc_type,
                            title=title,
                            full_text=full_text,
                            chunks=prepared_chunks
                        )
                        self.add_log(f"Staged '{title}' ({len(prepared_chunks)} chunks) into Review Queue for approval.", level="INFO")

                    with self._lock:
                        self.pages_crawled += 1
                        self.chunks_indexed += len(prepared_chunks)

                except Exception as fetch_err:
                    with self._lock:
                        self.errors_count += 1
                        self.last_error = str(fetch_err)
                    self.add_log(f"Failed to process {current_url}: {fetch_err}", level="ERROR")

            # Finalize status
            with self._lock:
                if not self._stop_event.is_set():
                    if self.pages_crawled == 0 and self.errors_count > 0:
                        self.status = "failed"
                    elif self.errors_count > 0:
                        self.status = "completed_with_errors"
                    else:
                        self.status = "completed"
                self.end_time = get_current_utc_iso()

            if self.status == "completed":
                level = "SUCCESS"
                summary_msg = (
                    f"Crawl finished successfully. Pages: {self.pages_crawled}, "
                    f"PDFs: {self.pdfs_extracted}, Chunks: {self.chunks_indexed}"
                )
            elif self.status == "completed_with_errors":
                level = "WARN"
                summary_msg = (
                    f"Crawl finished with {self.errors_count} errors. Pages: {self.pages_crawled}, "
                    f"PDFs: {self.pdfs_extracted}, Chunks: {self.chunks_indexed}"
                )
            elif self.status == "failed":
                level = "ERROR"
                summary_msg = (
                    f"Crawl failed. 0 pages indexed, {self.errors_count} errors encountered "
                    f"(Target server WAF / anti-bot block)."
                )
            else:
                level = "INFO"
                summary_msg = f"Crawl stopped. Pages: {self.pages_crawled}, Errors: {self.errors_count}"

            self.add_log(summary_msg, level=level)

        except Exception as global_err:
            with self._lock:
                self.status = "failed"
                self.last_error = str(global_err)
                self.end_time = get_current_utc_iso()
            self.add_log(f"Fatal crawl error: {global_err}", level="ERROR")

    def _index_directly_to_chroma(
        self,
        doc_id: str,
        source_url: str,
        source_type: str,
        title: str,
        full_text: str,
        date_scraped: str,
        chunks: List[Dict[str, Any]]
    ):
        """Directly writes chunk embeddings into live Chroma vector collection and updates documents.jsonl."""
        client = retriever.get_client()
        collection = retriever.get_collection(client)

        ids = [c["id"] for c in chunks]
        documents = [c["document"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]

        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )

        # Append to documents.jsonl persistence contract
        raw_docs_path = get_project_root() / "data" / "raw" / "documents.jsonl"
        raw_docs_path.parent.mkdir(parents=True, exist_ok=True)

        doc_record = {
            "doc_id": doc_id,
            "source_url": source_url,
            "source_type": source_type,
            "title": title,
            "date_scraped": date_scraped,
            "text": full_text
        }

        # Avoid duplicating entry in documents.jsonl
        already_present = False
        if raw_docs_path.exists():
            with open(raw_docs_path, "r", encoding="utf-8") as f:
                for line in f:
                    if f'"{doc_id}"' in line:
                        already_present = True
                        break

        if not already_present:
            with open(raw_docs_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(doc_record, ensure_ascii=False) + "\n")

    def _stage_to_review_queue(
        self,
        doc_id: str,
        source_url: str,
        source_type: str,
        title: str,
        full_text: str,
        chunks: List[Dict[str, Any]]
    ):
        """Stages document into SQLite review queue ('pending_review.sqlite3') with regulatory classification."""
        category = classify_regulatory_category(title, source_url, full_text)
        with self._lock:
            self.category_counts[category] += 1

        review_manager.add_pending_item(
            doc_id=doc_id,
            source=source_url,
            source_type=source_type,
            title=title,
            full_text=full_text,
            chunks=chunks,
            category=category
        )


def discover_categorized_links(root_url: str, max_links: int = 50, user_agent: str = DEFAULT_USER_AGENT) -> Dict[str, Any]:
    """
    Scans root URL and sitemaps to dynamically discover regulatory links without downloading full content.
    Derives categories dynamically from sitemap structures, URL path taxonomies, breadcrumbs,
    and page navigation without any hardcoded fixed categories.
    """
    base_domain = get_base_domain(root_url)
    discovered: Dict[str, List[Dict[str, str]]] = collections.defaultdict(list)
    visited_urls: Set[str] = set()
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # 1. Determine candidate sitemaps
    sitemap_candidates: List[str] = []
    is_direct_sitemap = root_url.lower().endswith(".xml") or "sitemap" in root_url.lower()

    if is_direct_sitemap:
        sitemap_candidates.append(root_url)
    else:
        # Check robots.txt for Sitemap directives
        try:
            robots_url = urljoin(root_url, "/robots.txt")
            r_resp = requests.get(robots_url, headers=headers, timeout=6)
            if r_resp.status_code == 200:
                for line in r_resp.text.splitlines():
                    line_clean = line.strip()
                    if line_clean.lower().startswith("sitemap:"):
                        sm_found = line_clean.split(":", 1)[1].strip()
                        if sm_found and sm_found not in sitemap_candidates:
                            sitemap_candidates.append(sm_found)
        except Exception:
            pass

        # Standard sitemap paths
        for sp in ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap/sitemap.xml"]:
            sm_full = urljoin(root_url, sp)
            if sm_full not in sitemap_candidates:
                sitemap_candidates.append(sm_full)

    # 2. Process sitemaps (both index sitemaps and direct urlsets)
    checked_sitemaps: Set[str] = set()
    for s_url in sitemap_candidates:
        if s_url in checked_sitemaps:
            continue
        checked_sitemaps.add(s_url)

        try:
            s_resp = requests.get(s_url, headers=headers, timeout=10)
            if s_resp.status_code != 200 or not s_resp.text:
                continue

            xml_text = s_resp.text
            soup = BeautifulSoup(xml_text, "xml")
            if not soup.find():
                soup = BeautifulSoup(xml_text, "html.parser")

            # Check if this is a sitemap index containing sub-sitemaps
            sitemap_tags = soup.find_all("sitemap")
            if sitemap_tags:
                sub_count = 0
                for sm_tag in sitemap_tags:
                    loc_tag = sm_tag.find("loc")
                    if not loc_tag:
                        continue
                    sub_url = loc_tag.get_text(strip=True)
                    if not sub_url or sub_url in checked_sitemaps or not is_same_domain(sub_url, base_domain):
                        continue
                    checked_sitemaps.add(sub_url)
                    sub_hint = clean_sitemap_hint(sub_url)

                    try:
                        sub_resp = requests.get(sub_url, headers=headers, timeout=8)
                        if sub_resp.status_code == 200:
                            sub_soup = BeautifulSoup(sub_resp.text, "xml")
                            if not sub_soup.find():
                                sub_soup = BeautifulSoup(sub_resp.text, "html.parser")

                            for u_tag in sub_soup.find_all("url"):
                                u_loc = u_tag.find("loc")
                                if not u_loc:
                                    continue
                                u = u_loc.get_text(strip=True)
                                if u and is_same_domain(u, base_domain) and u not in visited_urls:
                                    visited_urls.add(u)
                                    cat = extract_category_from_url_and_context(u, sitemap_hint=sub_hint)
                                    doc_type = classify_url(u)
                                    title_guess = Path(urlsplit(u).path).stem.replace("-", " ").replace("_", " ").title() or u
                                    discovered[cat].append({
                                        "url": u,
                                        "title": title_guess,
                                        "type": doc_type,
                                        "category": cat
                                    })
                                    if len(visited_urls) >= max_links:
                                        break
                    except Exception:
                        pass

                    sub_count += 1
                    if len(visited_urls) >= max_links or sub_count >= 8:
                        break

            # Check for direct url tags in this sitemap
            url_tags = soup.find_all("url")
            if url_tags:
                site_hint = clean_sitemap_hint(s_url)
                for u_tag in url_tags:
                    u_loc = u_tag.find("loc")
                    if not u_loc:
                        continue
                    u = u_loc.get_text(strip=True)
                    if u and is_same_domain(u, base_domain) and u not in visited_urls:
                        visited_urls.add(u)
                        cat = extract_category_from_url_and_context(u, sitemap_hint=site_hint)
                        doc_type = classify_url(u)
                        title_guess = Path(urlsplit(u).path).stem.replace("-", " ").replace("_", " ").title() or u
                        discovered[cat].append({
                            "url": u,
                            "title": title_guess,
                            "type": doc_type,
                            "category": cat
                        })
                        if len(visited_urls) >= max_links:
                            break

        except Exception:
            pass

        if len(visited_urls) >= max_links:
            break

    # 3. If sitemaps yielded fewer than max_links (and not a direct sitemap that already returned URLs), crawl page navigation & breadcrumbs
    if len(visited_urls) < max_links and not (is_direct_sitemap and visited_urls):
        page_crawl_url = root_url
        if is_direct_sitemap:
            scheme = urlsplit(root_url).scheme or "https"
            page_crawl_url = f"{scheme}://{base_domain}/"

        try:
            resp = requests.get(page_crawl_url, headers=headers, timeout=12)
            if resp.status_code == 200:
                is_xml = "xml" in resp.headers.get("Content-Type", "") or resp.text.strip().startswith("<?xml")
                soup = BeautifulSoup(resp.text, "xml" if is_xml else "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                        continue
                    full_url = urljoin(page_crawl_url, href)
                    if is_same_domain(full_url, base_domain) and full_url not in visited_urls:
                        path_lower = urlsplit(full_url).path.lower()
                        if any(path_lower.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".svg", ".zip", ".woff", ".ico"]):
                            continue
                        visited_urls.add(full_url)
                        link_text = a.get_text(strip=True) or Path(path_lower).stem.replace("-", " ").replace("_", " ").title()

                        # Extract navigational parent category context if available
                        parent_hint = ""
                        parent_menu = a.find_parent(
                            lambda tag: tag.name in ("li", "div", "nav") and any(
                                c in tag.get("class", []) for c in ["dropdown", "menu-item-has-children", "sub-menu", "has-children"]
                            )
                        )
                        if parent_menu:
                            header_elem = parent_menu.find(["a", "span", "h2", "h3", "h4"])
                            if header_elem and header_elem.get_text(strip=True) != link_text:
                                parent_hint = header_elem.get_text(strip=True)

                        if not parent_hint:
                            bc = a.find_parent(
                                lambda tag: any(c in tag.get("class", []) for c in ["breadcrumb", "breadcrumbs"]) or "breadcrumb" in tag.get("aria-label", "").lower()
                            )
                            if bc:
                                crumbs = [c.get_text(strip=True) for c in bc.find_all(["li", "a", "span"]) if c.get_text(strip=True).lower() not in ("home", "index", "")]
                                if crumbs:
                                    parent_hint = " / ".join(crumbs[:2])

                        cat = extract_category_from_url_and_context(full_url, title=link_text, sitemap_hint=parent_hint)
                        doc_type = classify_url(full_url)
                        discovered[cat].append({
                            "url": full_url,
                            "title": link_text[:90] or full_url,
                            "type": doc_type,
                            "category": cat
                        })
                        if len(visited_urls) >= max_links:
                            break
        except Exception:
            pass

    total_discovered = sum(len(v) for v in discovered.values())

    return {
        "root_url": root_url,
        "base_domain": base_domain,
        "total_discovered": total_discovered,
        "categories": dict(discovered)
    }


# ---------------------------------------------------------------------------
# Global Crawler Registry & Job Coordinator
# ---------------------------------------------------------------------------
_crawler_registry_lock = threading.RLock()
_crawler_jobs: Dict[str, SiteCrawler] = {}


def save_crawler_state():
    """Persists active, paused, and recent crawler jobs to disk for auto-resume across restarts."""
    try:
        data_dir = CRAWLER_STATE_FILE.parent
        data_dir.mkdir(parents=True, exist_ok=True)
        with _crawler_registry_lock:
            crawlers = list(_crawler_jobs.values())

        state_data = {
            c.crawl_id: c.to_dict() for c in crawlers if hasattr(c, "to_dict")
        }
        tmp_file = CRAWLER_STATE_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2, ensure_ascii=False)
        tmp_file.replace(CRAWLER_STATE_FILE)
    except Exception as e:
        logger.warning(f"Failed to persist crawler state to {CRAWLER_STATE_FILE}: {e}")


def load_crawler_state(auto_resume: bool = True) -> int:
    """Restores saved crawler jobs from disk. If auto_resume=True and a job was active/paused, resumes it."""
    if not CRAWLER_STATE_FILE.exists():
        return 0
    loaded_count = 0
    to_resume = []
    try:
        with open(CRAWLER_STATE_FILE, "r", encoding="utf-8") as f:
            state_data = json.load(f)
        with _crawler_registry_lock:
            for cid, c_data in state_data.items():
                if cid not in _crawler_jobs:
                    crawler = SiteCrawler.from_dict(c_data)
                    _crawler_jobs[cid] = crawler
                    loaded_count += 1

                    if auto_resume and not os.environ.get("PYTEST_CURRENT_TEST") and getattr(crawler, "remaining_queue", None):
                        if crawler.status in ("running", "paused") or getattr(crawler, "has_captcha_block", False):
                            to_resume.append(crawler)

        for c in to_resume:
            logger.info(f"Auto-resuming crawler job {c.crawl_id} with {len(c.remaining_queue)} remaining URLs...")
            c.resume()

        logger.info(f"Loaded {loaded_count} crawler jobs from {CRAWLER_STATE_FILE}")
    except Exception as e:
        logger.warning(f"Failed to load crawler state from {CRAWLER_STATE_FILE}: {e}")
    return loaded_count


def start_crawl(
    root_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    auto_approve: bool = False,
    check_robots: bool = True,
    allowed_urls: Optional[List[str]] = None,
    stealth_mode: bool = True
) -> str:
    """Spawns and registers a new autonomous SiteCrawler job."""
    crawler = SiteCrawler(
        root_url=root_url,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        auto_approve=auto_approve,
        check_robots=check_robots,
        allowed_urls=allowed_urls,
        stealth_mode=stealth_mode
    )
    crawl_id = crawler.crawl_id

    with _crawler_registry_lock:
        _crawler_jobs[crawl_id] = crawler

    crawler.start()
    save_crawler_state()
    return crawl_id


def get_crawl_job(crawl_id: str) -> Optional[Dict[str, Any]]:
    """Fetches real-time status of a registered crawler job."""
    with _crawler_registry_lock:
        crawler = _crawler_jobs.get(crawl_id)
    if not crawler:
        return None
    return crawler.get_status()


def stop_crawl_job(crawl_id: str) -> Optional[Dict[str, Any]]:
    """Signals crawler to stop gracefully."""
    with _crawler_registry_lock:
        crawler = _crawler_jobs.get(crawl_id)
    if not crawler:
        return None
    crawler.stop()
    save_crawler_state()
    return crawler.get_status()


def resume_crawl_job(crawl_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resumes paused or captcha-blocked crawler. If crawl_id is None, resumes the most recent paused or active job."""
    with _crawler_registry_lock:
        crawler = None
        if crawl_id:
            crawler = _crawler_jobs.get(crawl_id)
        if not crawler:
            for c in reversed(list(_crawler_jobs.values())):
                if c.status in ("paused", "stopped", "failed") or getattr(c, "has_captcha_block", False):
                    crawler = c
                    break
            else:
                if _crawler_jobs:
                    crawler = list(_crawler_jobs.values())[-1]
    if not crawler:
        return None
    crawler.resume()
    save_crawler_state()
    return crawler.get_status()


def list_crawl_jobs() -> List[Dict[str, Any]]:
    """Lists all registered crawl jobs with their latest metrics."""
    with _crawler_registry_lock:
        crawlers = list(_crawler_jobs.values())

    # Return newest jobs first
    jobs = [c.get_status() for c in reversed(crawlers)]
    return jobs


# Auto-load saved state on engine import
try:
    load_crawler_state(auto_resume=True)
except Exception as _load_err:
    logger.warning(f"Could not initialize crawler state on startup: {_load_err}")
