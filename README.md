# 🏛️ CDC Regulatory Compliance AI Assistant

[![Python 3.11](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0+-000000?style=flat&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![ChromaDB](https://img.shields.io/badge/VectorDB-ChromaDB-FF6F00?style=flat)](https://www.trychroma.com/)
[![Gemini](https://img.shields.io/badge/LLM-Gemini_2.5_Flash-4285F4?style=flat&logo=google&logoColor=white)](https://ai.google.dev/)
[![Tests](https://img.shields.io/badge/Tests-89%20Passing-success?style=flat)](./tests)
[![Cloudflare Pages](https://img.shields.io/badge/Cloudflare-Pages_Ready-F38020?style=flat&logo=cloudflare&logoColor=white)](https://pages.cloudflare.com/)

An audit-grade, retrieval-augmented regulatory intelligence assistant and compliance ingestion pipeline tailored for the **Central Depository Company of Pakistan (CDC)** and the **Securities & Exchange Commission of Pakistan (SECP)**.

---

## 🌟 Key Features

1. **Zero-Hallucination Regulatory Grounding**
   - Retrieves authentic snippets from SECP circulars, CDC regulations, and financial directives using ChromaDB vector search.
   - Powered by Google Gemini 2.5 Flash with strict fiduciary system prompts that forbid guessing.

2. **Verifiable Deep-Link Citations & Document Archive**
   - Every claim is tied to an authentic citation badge (`SECP Circular No. X • Page Y`).
   - Integrated offline Document Archive viewer ensures 100% citation uptime even if upstream government servers are down.

3. **Anti-Prompt-Injection Defense**
   - Hardened sanitization and input length constraints defend against adversarial system prompt overrides.

4. **Autonomous Crawler & Pre-Approval Gate**
   - Discovers and categorizes links across 6 regulatory domains (*Circulars, Operating Regulations, AML/CFT, Trustee, eServices, Governance*).
   - Compliance officers review and approve categories before crawling begins.

5. **Multimodal Vision OCR**
   - Automatically falls back to Gemini Vision OCR when encountering scanned or stamped non-searchable government circulars.

6. **Staging Review Sandbox ("Make Live" Gate)**
   - Extracted documents are isolated in SQLite staging.
   - Compliance officers can test query responses in a secure staging sandbox before publishing to the live production index.

---

## 🏗️ Architecture

```
cdc-rag-demo/
├── backend/
│   ├── app.py                   # Production Flask application & REST API
│   ├── retriever.py             # ChromaDB vector retrieval engine
│   ├── llm.py                   # Google Gemini API integration & prompt guardrails
│   ├── crawler_engine.py        # Stealth crawler & link discovery engine
│   ├── review_manager.py        # SQLite staging and document review lifecycle
│   ├── content_verifier.py      # Cloudflare & bot challenge detection
│   ├── security.py              # Rate limiting, OWASP headers, URL validators
│   └── templates/
│       ├── admin.html           # Dark-mode administrative control panel
│       └── doc_viewer.html      # Regulatory document archive viewer
├── frontend/
│   ├── index.html               # Public Assistant chat interface
│   ├── style.css                # FinTech corporate design system
│   ├── script.js                # Dynamic chat client & API handler
│   └── _redirects               # Cloudflare Pages reverse-proxy configuration
├── data/
│   ├── raw/documents.jsonl      # Baseline verified regulatory documents
│   └── uploads/                 # Local document archive and PDFs
├── vectordb/
│   └── chroma_store/            # Persistent pre-indexed vector embeddings
├── tests/                       # Complete 89-test verification suite
├── Dockerfile                   # Production container definition
├── requirements.txt             # Universal Python dependencies
└── .env.example                 # Configuration template
```

---

## 🚀 Quick Start (Local Setup)

### 1. Prerequisites
- Python 3.11+
- Git

### 2. Clone & Install
```bash
git clone https://github.com/MaazDurrani45/cdc-rag-demo.git
cd cdc-rag-demo

# Create virtual environment
python -m venv .venv

# Activate on Windows:
.venv\Scripts\activate
# Activate on Linux / macOS:
# source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Open `.env` and configure your API key:
```ini
GEMINI_API_KEY=your_google_gemini_api_key_here
ADMIN_PASSWORD=cdc-admin-2026
FLASK_PORT=5000
```
*(Get a free Gemini API key from [Google AI Studio](https://aistudio.google.com/))*

### 4. Run the Application
**On Windows:**
Double-click `start_demo.bat` or run:
```bash
python backend/app.py
```
Open your browser:
- **Assistant UI**: [http://127.0.0.1:5000](http://127.0.0.1:5000)
- **Admin Dashboard**: [http://127.0.0.1:5000/admin](http://127.0.0.1:5000/admin) *(Password: `cdc-admin-2026`)*

---

## 🧪 Running Automated Tests

Run the full 89-case automated verification test suite:
```bash
pytest
```
All tests verify contract compliance, RAG retrieval schemas, rate limiting, and administrative security.

---

## 🌐 Deploying Online

### Option A: Cloudflare Pages + Hosted Backend (Recommended)

1. **Deploy Frontend on Cloudflare Pages**:
   - Go to [Cloudflare Dashboard](https://dash.cloudflare.com/) > **Workers & Pages** > **Create application** > **Pages** > **Connect to Git**.
   - Select your GitHub repository (`cdc-rag-demo`).
   - Set **Build output directory** to `frontend`.
   - Set **Build command** to *(empty / leave blank)*.
   - Click **Save and Deploy**. Your frontend is instantly live at `https://cdc-rag-demo.pages.dev`!

2. **Deploy Backend to Cloud Platform** (Render / Railway / Cloud Run / Hugging Face Spaces):
   - **Render.com (Free Web Service)**:
     - Connect your repo, select **Docker** environment or Python 3.11.
     - Add environment variable `GEMINI_API_KEY`.
     - Your backend will receive a live URL (e.g. `https://cdc-backend.onrender.com`).
   - **Update Cloudflare Pages Proxy**:
     - In `frontend/_redirects`, set:
       ```
       /ask https://cdc-backend.onrender.com/ask 200
       /api/* https://cdc-backend.onrender.com/api/:splat 200
       /admin https://cdc-backend.onrender.com/admin 200
       /admin/* https://cdc-backend.onrender.com/admin/:splat 200
       ```
     - Commit and push. Cloudflare Pages will now seamlessly proxy all API and admin calls without CORS restrictions!

---

### Option B: Cloudflare Tunnel (100% Free Instant Online Hosting)

If you run the app on your computer or a VPS and want it online with free SSL and a public URL in 60 seconds:

1. Download [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/):
   ```bash
   winget install Cloudflare.cloudflared
   ```
2. Start the local server:
   ```bash
   python backend/app.py
   ```
3. Run the Cloudflare Tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:5000
   ```
4. Cloudflare will instantly output a public HTTPS URL (e.g. `https://cdc-compliance-demo.trycloudflare.com`) accessible from anywhere in the world!

---

## 🔒 Security & Compliance

- **Constant-Time Admin Authentication**: Resistant to timing attacks.
- **OWASP HTTP Security Headers**: HSTS, CSP, X-Frame-Options, X-Content-Type-Options.
- **Payload & Rate Limiting**: Max 60 requests/min per IP, 1,000-character max query size.
- **Fiduciary Isolation**: Staged crawler documents are blocked from production RAG answers until explicitly approved by an administrator.

---

## 📄 License
Internal demo developed for CDC Pakistan compliance evaluation.
