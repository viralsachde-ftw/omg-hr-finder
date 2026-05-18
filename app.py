#!/usr/bin/env python3
"""OMG HR Finder — pure stdlib HTTP server + requests"""

import http.server
import json
import re
import sys
import threading
import urllib.parse
from typing import Optional

import requests

PORT = 5000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

HR_LOCAL_RE = re.compile(
    r"^(hr|humanresource|human\.resource|recruit|recruiter|recruiting|"
    r"hiring|talent|career|careers|job|jobs|people|staffing|employ|"
    r"personnel|workforce|hrd|hrbp|hrm|ta\b|hq\.hr)",
    re.IGNORECASE,
)

JUNK_DOMAINS = {
    "example.com", "test.com", "yourdomain.com", "domain.com",
    "email.com", "sentry.io", "wixpress.com", "schema.org",
    "w3.org", "amazonaws.com", "cloudfront.net", "googleapis.com",
}


# ---------------------------------------------------------------------------
# Email utilities
# ---------------------------------------------------------------------------

def extract_emails(text: str) -> list[dict]:
    raw = EMAIL_RE.findall(text)
    seen: set[str] = set()
    out = []
    for em in raw:
        em = em.lower().rstrip(".")
        if em in seen:
            continue
        seen.add(em)
        domain = em.split("@", 1)[1]
        if domain in JUNK_DOMAINS:
            continue
        local = em.split("@", 1)[0]
        is_hr = bool(HR_LOCAL_RE.search(local))
        out.append({"email": em, "is_hr": is_hr})
    return out


def score_emails(emails: list[dict]) -> list[dict]:
    hr = [e for e in emails if e["is_hr"]]
    rest = [e for e in emails if not e["is_hr"]]
    return hr + rest


# ---------------------------------------------------------------------------
# Web helpers
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 8, **kw) -> Optional[requests.Response]:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True, **kw)
    except Exception:
        return None


def ddg_search(query: str, max_results: int = 6) -> tuple[list[str], str]:
    """DuckDuckGo HTML scrape — no API key."""
    url = "https://html.duckduckgo.com/html/"
    try:
        resp = requests.post(
            url,
            data={"q": query, "kl": "us-en", "kp": "-1"},
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=12,
        )
        raw = resp.text
    except Exception as e:
        return [], str(e)

    # pull /l/?uddg= redirect links that DDG uses
    redirects = re.findall(r'uddg=([^&"]+)', raw)
    links = []
    for r in redirects:
        try:
            decoded = urllib.parse.unquote(r)
            if decoded.startswith("http") and "duckduckgo.com" not in decoded:
                links.append(decoded)
        except Exception:
            pass

    # fallback: plain href extraction
    if not links:
        for href in re.findall(r'href="(https?://[^"]+)"', raw):
            if "duckduckgo.com" not in href and "duck.com" not in href:
                links.append(href)

    seen: set[str] = set()
    deduped = []
    for lnk in links:
        base = lnk.split("?")[0]
        if base not in seen:
            seen.add(base)
            deduped.append(lnk)
        if len(deduped) >= max_results:
            break

    return deduped, raw


def scrape_page(url: str, timeout: int = 8) -> dict:
    resp = _get(url, timeout=timeout)
    if not resp:
        return {"url": url, "title": url, "emails": [], "error": True, "snippet": ""}

    text = resp.text
    title_m = re.search(r"<title[^>]*>([^<]{1,120})</title>", text, re.IGNORECASE)
    title = title_m.group(1).strip() if title_m else url

    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"&[a-z#0-9]+;", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    return {
        "url": url,
        "title": title,
        "emails": extract_emails(clean),
        "snippet": clean[:400],
        "error": False,
    }


def probe_domain(company_name: str) -> Optional[str]:
    slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    candidates = [
        f"https://www.{slug}.com",
        f"https://{slug}.com",
        f"https://www.{slug}.io",
        f"https://www.{slug}.co",
        f"https://www.{slug}.in",
    ]
    for url in candidates:
        resp = _get(url, timeout=6)
        if resp and resp.status_code < 400:
            return resp.url.rstrip("/")
    return None


# ---------------------------------------------------------------------------
# Core research
# ---------------------------------------------------------------------------

def research(company_name: str) -> dict:
    result: dict = {
        "company": company_name,
        "domain": None,
        "sources": [],
        "hr_emails": [],
        "other_emails": [],
        "status": "done",
    }

    seen_emails: set[str] = set()
    all_emails: list[dict] = []

    def absorb(email_list: list[dict]):
        for e in email_list:
            if e["email"] not in seen_emails:
                seen_emails.add(e["email"])
                all_emails.append(e)

    # 1. Find domain
    domain = probe_domain(company_name)
    result["domain"] = domain

    # 2. DuckDuckGo searches
    queries = [
        f'"{company_name}" HR email',
        f'"{company_name}" careers hiring email contact',
    ]
    if domain:
        host = re.sub(r"https?://", "", domain).split("/")[0]
        queries.append(f"site:{host} email hr recruit")

    found_links: list[str] = []
    for q in queries:
        links, raw_html = ddg_search(q, max_results=5)
        absorb(extract_emails(re.sub(r"<[^>]+>", " ", raw_html)))
        for lnk in links:
            if lnk not in found_links:
                found_links.append(lnk)

    # 3. Scrape found pages
    for url in found_links[:8]:
        page = scrape_page(url)
        absorb(page["emails"])
        if not page["error"]:
            result["sources"].append({
                "url": page["url"],
                "title": page["title"],
                "snippet": page["snippet"],
            })

    # 4. Scrape company site contact/careers pages
    if domain:
        for path in ["/contact", "/contact-us", "/careers", "/jobs", "/about", "/hr"]:
            page = scrape_page(domain + path, timeout=6)
            if not page["error"]:
                absorb(page["emails"])

    # 5. Split and sort
    scored = score_emails(all_emails)
    result["hr_emails"] = [e["email"] for e in scored if e["is_hr"]]
    result["other_emails"] = [e["email"] for e in scored if not e["is_hr"]]

    return result


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OMG HR Finder</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0d0f12;
    --card: #13161b;
    --border: #22272e;
    --accent: #00e5ff;
    --accent2: #7c3aed;
    --green: #00e676;
    --red: #ff1744;
    --text: #e2e8f0;
    --muted: #64748b;
    --hr-badge: #00e676;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, sans-serif;
    min-height: 100vh;
    padding: 2rem 1rem;
  }

  header {
    text-align: center;
    margin-bottom: 2.5rem;
  }

  header h1 {
    font-size: 2.2rem;
    font-weight: 800;
    letter-spacing: -1px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  header p {
    color: var(--muted);
    margin-top: 0.4rem;
    font-size: 0.9rem;
  }

  .search-box {
    max-width: 640px;
    margin: 0 auto 2rem;
    display: flex;
    gap: 0.6rem;
  }

  .search-box input {
    flex: 1;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.85rem 1.1rem;
    color: var(--text);
    font-size: 1rem;
    outline: none;
    transition: border-color 0.2s;
  }

  .search-box input:focus { border-color: var(--accent); }

  .search-box button {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border: none;
    border-radius: 8px;
    padding: 0.85rem 1.6rem;
    color: #000;
    font-weight: 700;
    font-size: 0.95rem;
    cursor: pointer;
    white-space: nowrap;
    transition: opacity 0.2s;
  }

  .search-box button:hover { opacity: 0.85; }
  .search-box button:disabled { opacity: 0.4; cursor: not-allowed; }

  #status-bar {
    max-width: 900px;
    margin: 0 auto 1.2rem;
    font-size: 0.85rem;
    color: var(--muted);
    min-height: 1.2em;
  }

  #results { max-width: 900px; margin: 0 auto; display: none; }

  .section { margin-bottom: 1.6rem; }

  .section-title {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 0.8rem;
  }

  .company-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .company-name { font-size: 1.3rem; font-weight: 700; }

  .domain-link {
    color: var(--accent);
    text-decoration: none;
    font-size: 0.9rem;
  }

  .domain-link:hover { text-decoration: underline; }

  .domain-none { color: var(--muted); font-size: 0.85rem; font-style: italic; }

  /* Email grid */
  .email-grid {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .email-row {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem 1.1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.8rem;
  }

  .email-row.hr-email { border-left: 3px solid var(--hr-badge); }

  .email-addr {
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 0.92rem;
    word-break: break-all;
  }

  .badge {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    padding: 0.2rem 0.55rem;
    border-radius: 4px;
    white-space: nowrap;
  }

  .badge-hr { background: rgba(0,230,118,0.15); color: var(--hr-badge); }
  .badge-other { background: rgba(100,116,139,0.15); color: var(--muted); }

  .copy-btn {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 0.25rem 0.6rem;
    color: var(--muted);
    font-size: 0.75rem;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }

  .copy-btn:hover { border-color: var(--accent); color: var(--accent); }

  .empty-state {
    color: var(--muted);
    font-size: 0.85rem;
    font-style: italic;
    padding: 0.5rem 0;
  }

  /* Sources */
  .sources-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .source-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.8rem 1.1rem;
  }

  .source-title {
    font-size: 0.88rem;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .source-url {
    font-size: 0.75rem;
    color: var(--accent);
    word-break: break-all;
    margin: 0.2rem 0;
  }

  .source-snippet {
    font-size: 0.78rem;
    color: var(--muted);
    line-height: 1.5;
    margin-top: 0.35rem;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  /* Spinner */
  @keyframes spin { to { transform: rotate(360deg); } }

  .spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    vertical-align: middle;
    margin-right: 0.4rem;
  }

  /* Copy toast */
  #toast {
    position: fixed;
    bottom: 1.5rem; right: 1.5rem;
    background: var(--green);
    color: #000;
    font-weight: 700;
    font-size: 0.85rem;
    padding: 0.6rem 1.1rem;
    border-radius: 6px;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s;
  }

  #toast.show { opacity: 1; }

  /* Stats strip */
  .stats {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    margin-bottom: 1.2rem;
  }

  .stat {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.7rem 1.2rem;
    font-size: 0.85rem;
  }

  .stat-num {
    font-size: 1.6rem;
    font-weight: 800;
    display: block;
    line-height: 1.1;
  }

  .stat-num.green { color: var(--green); }
  .stat-num.cyan  { color: var(--accent); }
  .stat-num.muted { color: var(--muted); }

  .stat-label { color: var(--muted); font-size: 0.75rem; }
</style>
</head>
<body>

<header>
  <h1>OMG HR Finder</h1>
  <p>Drop a company name. Get HR emails. No fluff.</p>
</header>

<div class="search-box">
  <input id="company-input" type="text" placeholder="e.g. Stripe, Infosys, OpenAI…"
         autocomplete="off" spellcheck="false" />
  <button id="search-btn" onclick="doSearch()">Find HRs</button>
</div>

<div id="status-bar"></div>
<div id="results"></div>

<div id="toast">Copied!</div>

<script>
  const input = document.getElementById('company-input');
  input.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

  function setStatus(html) {
    document.getElementById('status-bar').innerHTML = html;
  }

  function showToast() {
    const t = document.getElementById('toast');
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 1500);
  }

  function copyText(text, btn) {
    navigator.clipboard.writeText(text).then(() => {
      const old = btn.textContent;
      btn.textContent = 'Copied!';
      showToast();
      setTimeout(() => btn.textContent = old, 1200);
    });
  }

  function emailRow(email, isHr) {
    const cls = isHr ? 'email-row hr-email' : 'email-row';
    const badge = isHr
      ? '<span class="badge badge-hr">HR</span>'
      : '<span class="badge badge-other">other</span>';
    return `
      <div class="${cls}">
        <span class="email-addr">${email}</span>
        <div style="display:flex;align-items:center;gap:0.5rem;flex-shrink:0">
          ${badge}
          <button class="copy-btn" onclick="copyText('${email}', this)">Copy</button>
        </div>
      </div>`;
  }

  function sourceCard(s) {
    const safeTitle = s.title.replace(/</g, '&lt;');
    const safeSnip  = s.snippet.replace(/</g, '&lt;');
    return `
      <div class="source-card">
        <div class="source-title">${safeTitle}</div>
        <div class="source-url"><a href="${s.url}" target="_blank" rel="noopener"
             style="color:inherit">${s.url.slice(0, 80)}${s.url.length > 80 ? '…' : ''}</a></div>
        <div class="source-snippet">${safeSnip}</div>
      </div>`;
  }

  function renderResults(data) {
    const hrEmails   = data.hr_emails   || [];
    const otherEmails = data.other_emails || [];
    const sources     = data.sources     || [];

    const domainHtml = data.domain
      ? `<a class="domain-link" href="${data.domain}" target="_blank" rel="noopener">${data.domain}</a>`
      : `<span class="domain-none">Domain not detected</span>`;

    const hrHtml = hrEmails.length
      ? hrEmails.map(e => emailRow(e, true)).join('')
      : '<p class="empty-state">No dedicated HR emails found.</p>';

    const otherHtml = otherEmails.length
      ? otherEmails.slice(0, 20).map(e => emailRow(e, false)).join('')
      : '<p class="empty-state">No other emails found.</p>';

    const sourcesHtml = sources.length
      ? sources.map(sourceCard).join('')
      : '<p class="empty-state">No web sources scraped.</p>';

    document.getElementById('results').style.display = 'block';
    document.getElementById('results').innerHTML = `

      <div class="section">
        <div class="company-card">
          <span class="company-name">${data.company}</span>
          ${domainHtml}
        </div>
      </div>

      <div class="stats">
        <div class="stat">
          <span class="stat-num green">${hrEmails.length}</span>
          <span class="stat-label">HR Emails</span>
        </div>
        <div class="stat">
          <span class="stat-num cyan">${otherEmails.length}</span>
          <span class="stat-label">Other Emails</span>
        </div>
        <div class="stat">
          <span class="stat-num muted">${sources.length}</span>
          <span class="stat-label">Sources Scraped</span>
        </div>
      </div>

      <div class="section">
        <div class="section-title">HR / Recruiting Emails</div>
        <div class="email-grid">${hrHtml}</div>
      </div>

      <div class="section">
        <div class="section-title">All Other Emails Found</div>
        <div class="email-grid">${otherHtml}</div>
      </div>

      <div class="section">
        <div class="section-title">Web Sources</div>
        <div class="sources-list">${sourcesHtml}</div>
      </div>
    `;
  }

  async function doSearch() {
    const company = input.value.trim();
    if (!company) { input.focus(); return; }

    const btn = document.getElementById('search-btn');
    btn.disabled = true;
    document.getElementById('results').style.display = 'none';
    setStatus('<span class="spinner"></span> Scanning the web for <strong>' + company + '</strong>…');

    try {
      const resp = await fetch('/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company }),
      });

      if (!resp.ok) throw new Error('Server error ' + resp.status);
      const data = await resp.json();

      if (data.error) {
        setStatus('<span style="color:var(--red)">Error: ' + data.error + '</span>');
      } else {
        const total = (data.hr_emails || []).length + (data.other_emails || []).length;
        setStatus(`Found <strong>${total}</strong> email(s) across <strong>${(data.sources || []).length}</strong> source(s).`);
        renderResults(data);
      }
    } catch (err) {
      setStatus('<span style="color:var(--red)">Request failed: ' + err.message + '</span>');
    } finally {
      btn.disabled = false;
    }
  }
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} — {fmt % args}")

    def _send_json(self, data: dict, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_html(HTML)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/search":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            company = payload.get("company", "").strip()
        except Exception:
            self._send_json({"error": "Bad JSON"}, 400)
            return

        if not company:
            self._send_json({"error": "company name is required"}, 400)
            return

        try:
            data = research(company)
            self._send_json(data)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"\n  OMG HR Finder running → http://localhost:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Bye.")


if __name__ == "__main__":
    main()
