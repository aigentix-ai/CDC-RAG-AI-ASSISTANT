import json
from pathlib import Path

docs_file = Path("data/raw/documents.jsonl")
uploads_dir = Path("data/uploads")
uploads_dir.mkdir(parents=True, exist_ok=True)

created = 0
with open(docs_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        doc_id = doc.get("doc_id")
        title = doc.get("title", "CDC Regulatory Document")
        text = doc.get("text", "")
        source_url = doc.get("source_url", "")
        date = doc.get("date_scraped", "2026-10-02")

        if not doc_id or not text or len(text.split()) < 20:
            continue

        target_html = uploads_dir / f"{doc_id}.html"
        if not target_html.exists():
            html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
        header {{ border-bottom: 2px solid #0284c7; padding-bottom: 15px; margin-bottom: 30px; }}
        h1 {{ color: #0f172a; font-size: 24px; margin: 0 0 10px 0; }}
        .meta {{ color: #64748b; font-size: 14px; margin-top: 8px; }}
        .content {{ white-space: pre-wrap; font-size: 15px; line-height: 1.8; }}
        .badge {{ display: inline-block; background: #e0f2fe; color: #0369a1; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 12px; }}
    </style>
</head>
<body>
    <header>
        <span class="badge">Official Regulatory Backup</span>
        <h1>{title}</h1>
        <div class="meta">Source: <a href="{source_url}">{source_url}</a> | Archived: {date}</div>
    </header>
    <div class="content">{text}</div>
</body>
</html>"""
            target_html.write_text(html_content, encoding="utf-8")
            created += 1

print(f"Created {created} official regulatory backup HTML documents in data/uploads/")
