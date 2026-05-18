"""Core research logic — shared by local server and Vercel function."""

import re
import urllib.parse
from typing import Optional

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)

LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc\.?|incorporated|llc|l\.l\.c\.?|ltd\.?|limited|corp\.?|corporation|"
    r"co\.?|pvt\.?|private|plc|gmbh|ag|sa)\b\.?",
    re.I,
)

HR_LOCAL_RE = re.compile(
    r"^(hr|human[._-]?resources?|recruit\w*|hiring|talent|career|careers|"
    r"jobs?|people|staffing|employ\w*|personnel|workforce|hrd|hrbp|hrm|"
    r"ta|hq\.hr|joinus|apply|opportunities)",
    re.I,
)

JUNK_DOMAINS = {
    "example.com", "test.com", "yourdomain.com", "domain.com", "email.com",
    "sentry.io", "wixpress.com", "schema.org", "w3.org", "amazonaws.com",
    "cloudfront.net", "googleapis.com", "github.com", "npmjs.com",
    "gravatar.com", "wordpress.com", "jquery.com",
}

HR_TITLE_RE = re.compile(
    r"\b(hr|human resources?|recruiter|recruiting|recruitment|talent acquisition|"
    r"talent partner|people ops|people operations|people partner|"
    r"hiring manager|staffing|workforce|hrbp|hrd|hr manager|hr director|"
    r"hr business partner|hr generalist|hr specialist|head of hr|"
    r"vp of hr|chief people|cpo|director of talent|head of talent|"
    r"head of people|people lead|people director)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 8, **kw) -> Optional[requests.Response]:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True, **kw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DuckDuckGo search
# ---------------------------------------------------------------------------

def ddg_search(query: str, max_results: int = 6) -> tuple[list[str], str]:
    """Post to DDG HTML endpoint, return (links, raw_html)."""
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "us-en", "kp": "-1"},
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        raw = resp.text
    except Exception as e:
        return [], str(e)

    # Decode DDG redirect params
    links: list[str] = []
    for uddg in re.findall(r"uddg=([^&\"'\s]+)", raw):
        try:
            decoded = urllib.parse.unquote(uddg)
            if decoded.startswith("http") and "duckduckgo.com" not in decoded:
                links.append(decoded)
        except Exception:
            pass

    # Fallback: plain href
    if not links:
        for href in re.findall(r'href="(https?://[^"]+)"', raw):
            if "duckduckgo.com" not in href and "duck.com" not in href:
                links.append(href)

    # Deduplicate preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for lnk in links:
        base = lnk.split("?")[0]
        if base not in seen:
            seen.add(base)
            deduped.append(lnk)
        if len(deduped) >= max_results:
            break

    return deduped, raw


# ---------------------------------------------------------------------------
# DDG result block parser — extracts titles + snippets per result
# ---------------------------------------------------------------------------

def parse_ddg_results(raw_html: str) -> list[dict]:
    """Parse DDG HTML into list of {title, url, snippet} dicts."""
    results = []

    # Each result lives in a <div class="result ..."> block
    blocks = re.split(r'<div[^>]+class="[^"]*result[^"]*"', raw_html)[1:]
    for block in blocks:
        # Title
        title_m = re.search(r'class="result__a"[^>]*>([^<]+)<', block)
        title = title_m.group(1).strip() if title_m else ""

        # URL from uddg or href
        url_m = re.search(r'uddg=([^&"\']+)', block)
        url = ""
        if url_m:
            try:
                url = urllib.parse.unquote(url_m.group(1))
            except Exception:
                pass
        if not url:
            href_m = re.search(r'href="(https?://[^"]+)"', block)
            url = href_m.group(1) if href_m else ""

        # Snippet
        snip_m = re.search(r'class="result__snippet"[^>]*>([^<]{0,300})', block)
        snippet = snip_m.group(1).strip() if snip_m else ""
        snippet = re.sub(r"<[^>]+>", " ", snippet)

        if title and url and "duckduckgo.com" not in url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


# ---------------------------------------------------------------------------
# Email extraction
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
        if domain in JUNK_DOMAINS or len(domain) > 50:
            continue
        local = em.split("@", 1)[0]
        is_hr = bool(HR_LOCAL_RE.search(local))
        out.append({"email": em, "is_hr": is_hr})
    return out


# ---------------------------------------------------------------------------
# Page scraper
# ---------------------------------------------------------------------------

def scrape_page(url: str, timeout: int = 8) -> dict:
    resp = _get(url, timeout=timeout)
    if not resp:
        return {"url": url, "title": url, "emails": [], "error": True, "snippet": ""}

    text = resp.text
    title_m = re.search(r"<title[^>]*>([^<]{1,120})</title>", text, re.I)
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


# ---------------------------------------------------------------------------
# Company name normalisation
# ---------------------------------------------------------------------------

def normalize_company_name(raw: str) -> str:
    """Strip legal suffixes and tidy whitespace. 'IDX Inc.' → 'IDX'"""
    name = re.sub(r"\.", " ", raw)           # dots → spaces: "idx.inc" → "idx inc"
    name = LEGAL_SUFFIX_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name if name else raw.strip()


# ---------------------------------------------------------------------------
# Domain probe
# ---------------------------------------------------------------------------

def probe_domain(company_name: str) -> Optional[str]:
    clean = normalize_company_name(company_name)
    slug = re.sub(r"[^a-z0-9]", "", clean.lower())
    candidates = [
        f"https://www.{slug}.com",
        f"https://{slug}.com",
        f"https://www.{slug}.io",
        f"https://www.{slug}.co",
        f"https://www.{slug}.in",
        f"https://www.{slug}.ai",
    ]
    for url in candidates:
        resp = _get(url, timeout=6)
        if resp and resp.status_code < 400:
            return resp.url.rstrip("/")
    return None


# ---------------------------------------------------------------------------
# LinkedIn HR people (via DDG — no login needed)
# ---------------------------------------------------------------------------

def _parse_linkedin_title(title: str) -> Optional[dict]:
    """
    Parse titles like:
      "Jane Smith - Senior Recruiter - Stripe | LinkedIn"
      "Jane Smith - HR Manager at OpenAI | LinkedIn"
    Returns {name, role} or None.
    """
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title).strip()
    title = re.sub(r"\s*- LinkedIn$", "", title).strip()

    # Split on " - " or " at "
    parts = re.split(r"\s+[-–]\s+", title)
    if len(parts) < 2:
        return None

    name = parts[0].strip()
    rest = " - ".join(parts[1:])

    # strip " at COMPANY..." from role
    role = re.sub(r"\s+at\s+.+$", "", rest, flags=re.I).strip()

    # Basic sanity: name should have 2-4 words, no digits, not too long
    name_words = name.split()
    if not (2 <= len(name_words) <= 4):
        return None
    if re.search(r"\d", name):
        return None
    if len(name) > 60:
        return None

    return {"name": name, "role": role}


def find_linkedin_hr_people(company_name: str) -> list[dict]:
    """Search DDG for LinkedIn profiles of HR/recruiting staff."""
    people: list[dict] = []
    seen_names: set[str] = set()
    seen_urls: set[str] = set()

    queries = [
        f'site:linkedin.com/in "{company_name}" recruiter',
        f'site:linkedin.com/in "{company_name}" "talent acquisition"',
        f'site:linkedin.com/in "{company_name}" "HR manager" OR "HR director" OR "people ops"',
        f'site:linkedin.com/in "{company_name}" "human resources"',
    ]

    for query in queries[:3]:
        _, raw = ddg_search(query, max_results=8)
        blocks = parse_ddg_results(raw)

        for b in blocks:
            url = b["url"]
            if "linkedin.com/in/" not in url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            parsed = _parse_linkedin_title(b["title"])
            if not parsed:
                # Try from URL slug
                slug = url.split("/in/")[-1].rstrip("/").split("?")[0]
                slug_clean = re.sub(r"-[a-z0-9]{3,8}$", "", slug)
                name_parts = [p.capitalize() for p in slug_clean.split("-") if p and not p.isdigit()]
                if 2 <= len(name_parts) <= 4:
                    parsed = {"name": " ".join(name_parts), "role": ""}

            if not parsed:
                continue

            name = parsed["name"]
            role = parsed["role"]

            # Only keep if role looks HR-related (or we have no role info)
            if role and not HR_TITLE_RE.search(role):
                continue

            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)

            people.append({
                "name": name,
                "role": role or "HR / Recruiting",
                "linkedin": url,
                "source": "LinkedIn",
            })

        if len(people) >= 10:
            break

    return people


# ---------------------------------------------------------------------------
# Team page people (company website)
# ---------------------------------------------------------------------------

def find_team_page_people(domain: str) -> list[dict]:
    """Scrape /about, /team etc. for HR staff names."""
    people: list[dict] = []
    seen: set[str] = set()

    paths = ["/about", "/team", "/about-us", "/people", "/leadership", "/company"]
    for path in paths:
        page = scrape_page(domain.rstrip("/") + path, timeout=6)
        if page["error"]:
            continue

        text = page["snippet"]
        # Look for patterns like "Name, HR Title" or "Name\nRecruiter"
        # Rough heuristic: find capitalized word pairs near HR keywords
        sentences = re.split(r"[.\n,;]", text)
        for sent in sentences:
            if HR_TITLE_RE.search(sent):
                # Try to find a person name nearby
                name_m = re.search(r"\b([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b", sent)
                if name_m:
                    name = name_m.group(1)
                    role_m = HR_TITLE_RE.search(sent)
                    role = role_m.group(0).title() if role_m else "HR"
                    key = name.lower()
                    if key not in seen:
                        seen.add(key)
                        people.append({
                            "name": name,
                            "role": role,
                            "linkedin": "",
                            "source": "Company website",
                        })

    return people[:8]


# ---------------------------------------------------------------------------
# Main research entry point
# ---------------------------------------------------------------------------

def research(company_name: str, known_domain: Optional[str] = None) -> dict:
    clean_name = normalize_company_name(company_name)

    result: dict = {
        "company": clean_name,
        "domain": None,
        "hr_people": [],
        "hr_emails": [],
        "other_emails": [],
        "sources": [],
    }

    seen_emails: set[str] = set()
    all_emails: list[dict] = []

    def absorb(email_list: list[dict]):
        for e in email_list:
            if e["email"] not in seen_emails:
                seen_emails.add(e["email"])
                all_emails.append(e)

    # 1 — Find official domain; trust the caller's domain if provided
    if known_domain:
        domain = known_domain.rstrip("/")
    else:
        domain = probe_domain(clean_name)
    result["domain"] = domain

    # 2 — LinkedIn HR people
    linkedin_people = find_linkedin_hr_people(clean_name)
    result["hr_people"].extend(linkedin_people)

    # 3 — Team page HR people (if domain found)
    if domain:
        team_people = find_team_page_people(domain)
        seen_names = {p["name"].lower() for p in result["hr_people"]}
        for p in team_people:
            if p["name"].lower() not in seen_names:
                result["hr_people"].append(p)
                seen_names.add(p["name"].lower())

    # 4 — Email searches via DDG
    queries = [
        f'"{clean_name}" HR email contact',
        f'"{clean_name}" careers hiring email',
    ]
    if domain:
        host = re.sub(r"https?://", "", domain).split("/")[0]
        queries.append(f"site:{host} email")

    found_links: list[str] = []
    for q in queries:
        links, raw_html = ddg_search(q, max_results=5)
        absorb(extract_emails(re.sub(r"<[^>]+>", " ", raw_html)))
        for lnk in links:
            if lnk not in found_links:
                found_links.append(lnk)

    # 5 — Scrape found pages
    for url in found_links[:8]:
        page = scrape_page(url)
        absorb(page["emails"])
        if not page["error"]:
            result["sources"].append({
                "url": page["url"],
                "title": page["title"],
                "snippet": page["snippet"],
            })

    # 6 — Scrape company site contact/careers pages
    if domain:
        for path in ["/contact", "/contact-us", "/careers", "/jobs", "/hr", "/about"]:
            page = scrape_page(domain + path, timeout=6)
            if not page["error"]:
                absorb(page["emails"])

    # 7 — Sort and split emails
    hr_first = [e for e in all_emails if e["is_hr"]]
    others = [e for e in all_emails if not e["is_hr"]]
    result["hr_emails"] = [e["email"] for e in hr_first]
    result["other_emails"] = [e["email"] for e in others]

    return result
