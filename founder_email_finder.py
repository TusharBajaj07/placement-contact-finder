"""
Founder/CEO Email Finder v2
-----------------------------
Input:  Comp.csv with columns: Company Name, Founder, Role, Website Domain
Output: founder_emails.csv with discovered/guessed emails

Targets ONLY: Founder, Co-founder, CEO (no HR)

Key features:
- Auto-discovers founder name via web search + Gemini when missing
- Catch-all domain detection (if domain accepts any email, marks accordingly)
- Multiple verification passes for unknown/unverified emails
- Gemini AI for intelligent founder discovery and email verification

Usage:
    python founder_email_finder.py --gemini-key YOUR_KEY
    python founder_email_finder.py --skip-gemini
"""

import csv
import re
import smtplib
import json
import time
import os
import sys
import argparse
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import OrderedDict

try:
    import dns.resolver
    HAS_DNS = True
except ImportError:
    HAS_DNS = False
    print("[WARN] dnspython not installed. SMTP verification will be limited.")
    print("       Install with: pip install dnspython")

try:
    from google import genai as genai_new
    HAS_GEMINI = True
except ImportError:
    try:
        import google.generativeai as genai_old
        HAS_GEMINI = True
        genai_new = None
    except ImportError:
        HAS_GEMINI = False
        genai_new = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
INPUT_CSV = "Comp.csv"
OUTPUT_CSV = "founder_emails.csv"
STATE_FILE = "founder_emails_state.json"

REQUEST_TIMEOUT = 10
CRAWL_DELAY = 1.0
SEARCH_DELAY = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

TARGET_ROLES = {"founder", "co-founder", "ceo", "co-founder & ceo", "ceo & co-founder", "ceo & founder"}

EMAIL_PATTERNS = [
    "{first}@{domain}",
    "{last}@{domain}",
    "{first}.{last}@{domain}",
    "{firstlast}@{domain}",
    "{f}{last}@{domain}",
    "{first}.{l}@{domain}",
    "{f}.{last}@{domain}",
    "{first}_{last}@{domain}",
    "{last}.{first}@{domain}",
    "{first}{l}@{domain}",
]

PRIORITY_PATHS = [
    "/contact", "/contact-us", "/about", "/about-us",
    "/team", "/our-team", "/people", "/leadership",
]

HONORIFICS = {"dr.", "dr", "mr.", "mr", "mrs.", "mrs", "ms.", "ms", "prof.", "prof", "shri", "smt.", "smt"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def clean_name(name):
    """Strip honorifics and return (first, last, middle_names) tuple."""
    parts = name.strip().split()
    parts = [p for p in parts if p.lower().strip(".") not in {h.strip(".") for h in HONORIFICS}]
    if not parts:
        parts = name.strip().split()
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) >= 2 else ""
    middles = parts[1:-1] if len(parts) > 2 else []
    return first, last, middles


def generate_email_patterns(first_name, last_name, domain):
    if not first_name or not domain:
        return []
    first = first_name.lower().strip()
    last = last_name.lower().strip() if last_name else ""
    f = first[0] if first else ""
    l = last[0] if last else ""

    candidates = []
    for pattern in EMAIL_PATTERNS:
        try:
            email = pattern.format(
                first=first, last=last, f=f, l=l,
                firstlast=first + last, firstl=first + l,
                domain=domain,
            )
            if not last and ("last" in pattern or "{l}" in pattern):
                continue
            candidates.append(email)
        except (KeyError, IndexError):
            continue
    return list(OrderedDict.fromkeys(candidates))


# ---------------------------------------------------------------------------
# SMTP Verifier + Catch-All Detection
# ---------------------------------------------------------------------------
_mx_cache = {}
_catchall_cache = {}


def get_mx_host(domain):
    if domain in _mx_cache:
        return _mx_cache[domain]
    if not HAS_DNS:
        return None
    try:
        records = dns.resolver.resolve(domain, "MX")
        mx = sorted(records, key=lambda r: r.preference)
        host = str(mx[0].exchange).rstrip(".")
        _mx_cache[domain] = host
        return host
    except Exception:
        _mx_cache[domain] = None
        return None


def is_catchall_domain(domain):
    """Check if domain accepts ALL emails (catch-all). If so, SMTP verify is useless."""
    if domain in _catchall_cache:
        return _catchall_cache[domain]
    if not HAS_DNS:
        _catchall_cache[domain] = None
        return None
    mx_host = get_mx_host(domain)
    if not mx_host:
        _catchall_cache[domain] = None
        return None
    try:
        # Try a random nonsense email — if accepted, it's catch-all
        fake_email = f"xyznonexistent99q@{domain}"
        smtp = smtplib.SMTP(timeout=10)
        smtp.connect(mx_host, 25)
        smtp.helo("verify.local")
        smtp.mail("check@verify.local")
        code, _ = smtp.rcpt(fake_email)
        smtp.quit()
        result = (code == 250)
        _catchall_cache[domain] = result
        if result:
            print(f"    [!] {domain} is a catch-all domain (accepts all emails)")
        return result
    except Exception:
        _catchall_cache[domain] = None
        return None


def smtp_verify(email):
    if not HAS_DNS:
        return "unknown"
    domain = email.split("@")[1]
    mx_host = get_mx_host(domain)
    if not mx_host:
        return "unknown"
    try:
        smtp = smtplib.SMTP(timeout=10)
        smtp.connect(mx_host, 25)
        smtp.helo("verify.local")
        smtp.mail("check@verify.local")
        code, _ = smtp.rcpt(email)
        smtp.quit()
        if code == 250:
            return "valid"
        elif code == 550:
            return "invalid"
        else:
            return "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Web Search (DuckDuckGo)
# ---------------------------------------------------------------------------
def web_search(query, max_results=5):
    try:
        from ddgs import DDGS
        ddgs = DDGS()
        raw = ddgs.text(query, max_results=max_results)
        return [{"title": r.get("title", ""), "url": r.get("href", ""),
                 "snippet": r.get("body", "")} for r in raw]
    except Exception as e:
        print(f"    [Search Error] {e}")
        return []


# ---------------------------------------------------------------------------
# Website Crawler
# ---------------------------------------------------------------------------
def crawl_website(base_url, domain):
    """Crawl website and return (emails_set, page_texts_list)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    found_emails = set()
    page_texts = []

    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    urls = [base_url] + [urljoin(base_url, p) for p in PRIORITY_PATHS]
    crawled = set()

    for url in urls[:10]:
        norm = url.rstrip("/").lower()
        if norm in crawled:
            continue
        crawled.add(norm)
        try:
            time.sleep(CRAWL_DELAY)
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("Content-Type", ""):
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(separator=" ", strip=True)

            emails = set(re.findall(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                resp.text,
            ))
            for e in emails:
                el = e.lower()
                if not any(el.endswith(ext) for ext in [".png", ".jpg", ".gif", ".svg", ".css", ".js"]):
                    found_emails.add(el)

            # Save text from relevant pages
            if any(kw in url.lower() for kw in ["team", "about", "contact", "people", "founder", "leadership"]):
                page_texts.append({"url": url, "text": text[:3000]})

            # Discover additional links
            if len(crawled) <= 3:
                for a in soup.find_all("a", href=True):
                    txt = a.get_text(strip=True).lower()
                    href = a["href"].lower()
                    if any(kw in txt or kw in href for kw in ["team", "founder", "about", "leadership", "people"]):
                        full = urljoin(base_url, a["href"])
                        if urlparse(full).netloc.replace("www.", "") == domain:
                            if full.rstrip("/").lower() not in crawled:
                                urls.append(full)

        except Exception:
            continue

    return found_emails, page_texts


# ---------------------------------------------------------------------------
# Gemini AI Functions
# ---------------------------------------------------------------------------
_gemini_client = None

def _get_gemini_client(api_key):
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai_new.Client(api_key=api_key)
    return _gemini_client

def _call_gemini(prompt, api_key):
    if not HAS_GEMINI or not api_key or api_key == "skip":
        return None
    for attempt in range(3):
        try:
            client = _get_gemini_client(api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = response.text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"    [Gemini JSON Error] {e}")
            return None
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                wait = 30 * (attempt + 1)
                print(f"    [Gemini Rate Limit] Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    [Gemini Error] {e}")
                return None
    return None


def gemini_discover_founder(company_name, domain, api_key):
    """Use Gemini to find the founder/CEO name when not in CSV."""
    prompt = f"""Who is the founder or CEO of "{company_name}"?
Company domain: {domain or "unknown"}

Return ONLY a JSON object (no markdown, no code blocks):
{{
    "founder_name": "Full Name of the founder/CEO" or null,
    "role": "Their exact title (e.g. Founder, Co-founder & CEO)",
    "confidence": "high/medium/low",
    "domain": "company website domain if you know it" or null
}}

Only include names you're reasonably confident about. If unsure, set founder_name to null."""
    return _call_gemini(prompt, api_key)


def gemini_find_email(company_name, founder_name, role, domain, page_texts, api_key):
    """Use Gemini to find/verify founder email with website context."""
    context = ""
    if page_texts:
        for pt in page_texts[:3]:
            context += f"\n--- {pt['url']} ---\n{pt['text'][:1500]}\n"

    prompt = f"""Find the email address of {founder_name} ({role}) at {company_name}.
Company domain: {domain}

Website content (team/about pages):
{context[:4000] if context else "No website content available."}

Return ONLY a JSON object (no markdown, no code blocks):
{{
    "email": "their most likely email address" or null,
    "confidence": "high/medium/low",
    "reasoning": "how you determined this",
    "alternative_emails": ["other possible emails"] or []
}}

Consider common Indian startup email patterns: first@domain is most common, then first.last@domain."""
    return _call_gemini(prompt, api_key)


def gemini_verify_email(email, company_name, founder_name, api_key):
    """Ask Gemini if a guessed email looks correct based on its knowledge."""
    prompt = f"""Is this email address likely correct?

Email: {email}
Person: {founder_name}
Company: {company_name}

Return ONLY a JSON object (no markdown, no code blocks):
{{
    "likely_correct": true or false,
    "confidence": "high/medium/low",
    "better_email": "a better email if you know one" or null,
    "reasoning": "brief explanation"
}}"""
    return _call_gemini(prompt, api_key)


# ---------------------------------------------------------------------------
# Founder Name Discovery (web search + Gemini)
# ---------------------------------------------------------------------------
def discover_founder_name(company_name, domain, gemini_key):
    """Try to find the founder/CEO name via web search and Gemini."""
    print(f"  [DISCOVER] Searching for founder/CEO of {company_name}...")
    found_name = None
    found_role = None

    # Method 1: Web search
    time.sleep(SEARCH_DELAY)
    queries = [
        f'"{company_name}" founder CEO name',
        f'"{company_name}" "founded by"',
    ]
    if domain:
        queries.append(f'site:linkedin.com "{company_name}" founder')

    for q in queries:
        results = web_search(q, max_results=5)
        for r in results:
            snippet = r.get("snippet", "") + " " + r.get("title", "")
            # Look for patterns like "founded by X", "CEO X", "Founder: X"
            patterns = [
                r"(?:founded by|co-?founded by)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
                r"(?:CEO|Founder|Co-?founder)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*(?:,\s*)?(?:Founder|CEO|Co-?founder)",
            ]
            for p in patterns:
                match = re.search(p, snippet)
                if match:
                    name = match.group(1).strip()
                    # Validate: at least 2 parts, not a company name
                    if len(name.split()) >= 2 and company_name.lower() not in name.lower():
                        found_name = name
                        found_role = "Founder (discovered)"
                        print(f"    [Web] Found: {found_name}")
                        break
            if found_name:
                break
        if found_name:
            break

    # Method 2: Gemini (if web search failed)
    if not found_name and gemini_key and gemini_key != "skip":
        print(f"    [Gemini] Asking for founder name...")
        result = gemini_discover_founder(company_name, domain, gemini_key)
        if result and result.get("founder_name"):
            found_name = result["founder_name"]
            found_role = result.get("role", "Founder (from Gemini)")
            if not domain and result.get("domain"):
                domain = result["domain"]
            print(f"    [Gemini] Found: {found_name} ({found_role})")

    # Method 3: Crawl website team/about pages for names
    if not found_name and domain:
        print(f"    [Crawl] Checking website for founder info...")
        _, page_texts = crawl_website(f"https://{domain}", domain)
        for pt in page_texts:
            text = pt["text"]
            # Look for "Founder" or "CEO" near a name
            patterns = [
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*[-–|,]\s*(?:Founder|CEO|Co-?founder)",
                r"(?:Founder|CEO|Co-?founder)\s*[-–|,]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
            ]
            for p in patterns:
                match = re.search(p, text)
                if match:
                    name = match.group(1).strip()
                    if len(name.split()) >= 2:
                        found_name = name
                        found_role = "Founder (from website)"
                        print(f"    [Website] Found: {found_name}")
                        break
            if found_name:
                break

    return found_name, found_role, domain


# ---------------------------------------------------------------------------
# Extra Verification for Unknown/Unverified Emails
# ---------------------------------------------------------------------------
def extra_verify(email, company_name, founder_name, domain, gemini_key):
    """Additional verification methods for unknown/unverified emails."""
    print(f"  [EXTRA VERIFY] Trying additional methods for {email}...")

    # Method 1: Check if it's a catch-all domain
    catchall = is_catchall_domain(domain)
    if catchall is True:
        # Catch-all means SMTP always says valid — can't trust it
        # Use the most common pattern (first@domain) as best guess
        first, _, _ = clean_name(founder_name)
        likely = f"{first.lower()}@{domain}"
        return likely, "catch-all domain (likely pattern)", "catch-all"

    # Method 2: Search for the exact email online to see if it appears anywhere
    time.sleep(SEARCH_DELAY)
    results = web_search(f'"{email}"', max_results=3)
    for r in results:
        snippet = r.get("snippet", "").lower() + r.get("title", "").lower()
        if email.lower() in snippet:
            print(f"    [Web Confirm] Found {email} mentioned online!")
            return email, "web_confirmed", "web_confirmed"

    # Method 3: Try common alternative patterns that we might have missed
    first, last, middles = clean_name(founder_name)
    alt_patterns = []
    if last:
        alt_patterns = [
            f"{first.lower()}@{domain}",
            f"{first.lower()}.{last.lower()}@{domain}",
            f"{first.lower()}{last.lower()}@{domain}",
        ]
    else:
        alt_patterns = [f"{first.lower()}@{domain}"]

    for alt in alt_patterns:
        if alt == email:
            continue
        status = smtp_verify(alt)
        if status == "valid":
            print(f"    [Alt Pattern] {alt} -> valid!")
            return alt, "smtp_verified", "valid"

    # Method 4: Gemini verification
    if gemini_key and gemini_key != "skip":
        print(f"    [Gemini Verify] Checking {email}...")
        result = gemini_verify_email(email, company_name, founder_name, gemini_key)
        if result:
            if result.get("better_email"):
                better = result["better_email"].lower()
                status = smtp_verify(better)
                if status == "valid":
                    return better, "gemini_corrected + smtp_verified", "valid"
                elif status != "invalid":
                    return better, "gemini_corrected", status
            if result.get("likely_correct") and result.get("confidence") in ("high", "medium"):
                return email, f"gemini_confirmed ({result['confidence']})", "gemini_confirmed"

    # Method 5: Search for any email of this person online
    time.sleep(SEARCH_DELAY)
    results = web_search(f'"{founder_name}" email @{domain}', max_results=5)
    for r in results:
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        emails_found = re.findall(
            r"[a-zA-Z0-9._%+\-]+@" + re.escape(domain),
            snippet,
        )
        for e in emails_found:
            el = e.lower()
            if el != email and first.lower() in el.split("@")[0]:
                status = smtp_verify(el)
                if status == "valid":
                    return el, "web_search + smtp_verified", "valid"
                elif status != "invalid":
                    return el, "web_search_discovered", status

    return email, None, None  # No improvement found


# ---------------------------------------------------------------------------
# Main Processing
# ---------------------------------------------------------------------------
def process_company(row, gemini_key=None):
    company = row.get("Company Name", "").strip()
    founder = row.get("Founder", "").strip()
    role = row.get("Role", "").strip()
    domain = row.get("Website Domain", "").strip()
    sector = row.get("Sector", "").strip()
    city = row.get("City", "").strip()

    print(f"\n{'='*60}")
    print(f"  Company: {company}")
    print(f"  Founder: {founder or '(unknown)'} ({role})")
    print(f"  Domain:  {domain or '(unknown)'}")
    print(f"{'='*60}")

    # Check if role matches our targets
    role_lower = role.lower().strip()
    is_target = any(t in role_lower for t in TARGET_ROLES)
    if not is_target:
        print(f"  [SKIP] Role '{role}' is not Founder/Co-founder/CEO")
        return {
            "Company Name": company, "Founder": founder, "Role": role,
            "Sector": sector, "City": city, "Domain": domain,
            "Email": "", "Email Source": "skipped - not target role",
            "SMTP Status": "", "All Candidates": "",
        }

    # =====================================================
    # STEP 0: Discover founder name if missing
    # =====================================================
    if not founder or founder.lower() in ["", "founder on linkedin"]:
        print(f"  Step 0: Founder name missing — discovering...")
        discovered_name, discovered_role, discovered_domain = discover_founder_name(
            company, domain, gemini_key
        )
        if discovered_name:
            founder = discovered_name
            if discovered_role:
                role = discovered_role
            if discovered_domain and not domain:
                domain = discovered_domain
            print(f"  >> Discovered: {founder} ({role}) | domain: {domain}")
        else:
            print(f"  >> Could not discover founder name")
            # Last resort: try to find any email via web search
            if domain:
                time.sleep(SEARCH_DELAY)
                results = web_search(f'"{company}" founder CEO email site:{domain}')
                for r in results:
                    snippet = r.get("snippet", "")
                    emails = re.findall(r"[a-zA-Z0-9._%+\-]+@" + re.escape(domain), snippet)
                    if emails:
                        email = emails[0].lower()
                        return {
                            "Company Name": company, "Founder": "(discovered via search)",
                            "Role": role, "Sector": sector, "City": city, "Domain": domain,
                            "Email": email, "Email Source": "web_search (no name)",
                            "SMTP Status": smtp_verify(email), "All Candidates": "; ".join(emails),
                        }

            return {
                "Company Name": company, "Founder": "", "Role": role,
                "Sector": sector, "City": city, "Domain": domain,
                "Email": "", "Email Source": "founder not discoverable",
                "SMTP Status": "", "All Candidates": "",
            }

    # Parse name
    first_name, last_name, middle_names = clean_name(founder)

    best_email = ""
    best_source = ""
    best_status = ""
    all_candidates = []
    page_texts = []

    # =====================================================
    # STEP 1: Check if domain is catch-all
    # =====================================================
    if domain:
        catchall = is_catchall_domain(domain)
    else:
        catchall = None

    # =====================================================
    # STEP 2: Pattern generation + SMTP verification
    # =====================================================
    if domain:
        print(f"  Step 1: Generating email patterns...")
        candidates = generate_email_patterns(first_name, last_name, domain)

        for mid in middle_names:
            candidates.extend(generate_email_patterns(first_name, mid, domain))
            candidates.extend(generate_email_patterns(mid, last_name, domain))
        if middle_names:
            full_first = first_name + middle_names[0]
            candidates.append(f"{full_first.lower()}@{domain}")
            candidates.append(f"{full_first.lower()}.{last_name.lower()}@{domain}")

        candidates = list(OrderedDict.fromkeys(candidates))
        all_candidates.extend(candidates)
        print(f"    Generated {len(candidates)} candidates")

        if catchall:
            # Catch-all: SMTP is useless, pick the most common pattern
            best_email = candidates[0]  # first@domain
            best_source = "best_pattern (catch-all domain)"
            best_status = "catch-all"
            print(f"    Catch-all domain — using best pattern: {best_email}")
        else:
            for email in candidates:
                status = smtp_verify(email)
                print(f"    {email} -> {status}")
                if status == "valid":
                    best_email = email
                    best_source = "smtp_verified"
                    best_status = "valid"
                    break
                elif status == "unknown" and not best_email:
                    best_email = email
                    best_source = "pattern_guess"
                    best_status = "unknown"

    # =====================================================
    # STEP 3: Web search for founder email
    # =====================================================
    if not best_email or best_status not in ("valid", "catch-all"):
        print(f"  Step 2: Searching web for {founder}'s email...")
        time.sleep(SEARCH_DELAY)
        queries = [
            f'"{founder}" "{company}" email',
            f'"{founder}" @{domain} email' if domain else None,
            f'"{founder}" contact email',
        ]
        for q in queries:
            if not q:
                continue
            results = web_search(q)
            for r in results:
                snippet = r.get("snippet", "") + " " + r.get("title", "")
                emails = re.findall(
                    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                    snippet,
                )
                for e in emails:
                    el = e.lower()
                    # Filter junk
                    if any(x in el for x in ["noreply", "no-reply", "example.com", ".png", ".jpg"]):
                        continue
                    local = el.split("@")[0]
                    # Prefer emails matching the domain and founder name
                    if domain and domain in el and (first_name.lower() in local or last_name.lower() in local):
                        best_email = el
                        best_source = "web_search"
                        best_status = smtp_verify(el) if not catchall else "catch-all"
                        break
                    elif domain and domain in el:
                        all_candidates.append(el)
                    elif not domain and (first_name.lower() in local):
                        all_candidates.append(el)
                if best_source == "web_search":
                    break
            if best_source == "web_search":
                break

    # =====================================================
    # STEP 4: Crawl website for emails
    # =====================================================
    if domain and (not best_email or best_status not in ("valid", "catch-all")):
        print(f"  Step 3: Crawling website for emails...")
        base_url = f"https://{domain}"
        site_emails, page_texts = crawl_website(base_url, domain)
        if site_emails:
            print(f"    Found {len(site_emails)} emails on site")
            all_candidates.extend(site_emails)
            for e in site_emails:
                local = e.split("@")[0].lower()
                if first_name.lower() in local or (last_name and last_name.lower() in local):
                    best_email = e
                    best_source = "website_scrape"
                    best_status = smtp_verify(e) if not catchall else "catch-all"
                    print(f"    Matched founder: {e} -> {best_status}")
                    break

    # =====================================================
    # STEP 5: Gemini AI (find email with full context)
    # =====================================================
    if gemini_key and gemini_key != "skip" and (not best_email or best_status not in ("valid", "catch-all", "web_confirmed")):
        print(f"  Step 4: Asking Gemini AI...")
        result = gemini_find_email(company, founder, role, domain or "unknown", page_texts, gemini_key)
        if result and result.get("email"):
            gemini_email = result["email"].lower()
            status = smtp_verify(gemini_email) if not catchall else "catch-all"
            print(f"    Gemini: {gemini_email} -> {status} ({result.get('confidence')})")
            all_candidates.append(gemini_email)
            if result.get("alternative_emails"):
                all_candidates.extend([e.lower() for e in result["alternative_emails"]])
            if status == "valid" or not best_email:
                best_email = gemini_email
                best_source = f"gemini ({result.get('confidence', 'unknown')})"
                best_status = status

    # =====================================================
    # STEP 6: Fallback to first pattern
    # =====================================================
    if not best_email and all_candidates:
        best_email = all_candidates[0]
        best_source = "pattern_guess (unverified)"
        best_status = "unverified"

    # =====================================================
    # STEP 7: Extra verification for unknown/unverified
    # =====================================================
    if best_email and best_status in ("unknown", "unverified", "pattern_guess"):
        print(f"  Step 5: Extra verification for {best_email}...")
        improved_email, improved_source, improved_status = extra_verify(
            best_email, company, founder, domain, gemini_key
        )
        if improved_source:  # We got an improvement
            best_email = improved_email
            best_source = improved_source
            best_status = improved_status

    print(f"\n  RESULT: {best_email} [{best_source}] ({best_status})")

    return {
        "Company Name": company,
        "Founder": founder,
        "Role": role,
        "Sector": sector,
        "City": city,
        "Domain": domain,
        "Email": best_email,
        "Email Source": best_source,
        "SMTP Status": best_status,
        "All Candidates": "; ".join(sorted(set(all_candidates)))[:500],
    }


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"processed": {}}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Find Founder/CEO emails from Comp.csv")
    parser.add_argument("--input", "-i", default=INPUT_CSV, help="Input CSV (default: Comp.csv)")
    parser.add_argument("--output", "-o", default=OUTPUT_CSV, help="Output CSV (default: founder_emails.csv)")
    parser.add_argument("--gemini-key", "-g", default=None, help="Gemini API key (optional)")
    parser.add_argument("--skip-gemini", action="store_true", help="Skip Gemini AI calls")
    parser.add_argument("--fresh", action="store_true", help="Ignore saved state, process all from scratch")
    args = parser.parse_args()

    gemini_key = None
    if not args.skip_gemini:
        gemini_key = args.gemini_key or os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            print(f"[OK] Gemini API key loaded")
        else:
            print("[WARN] No Gemini key — founder discovery will rely on web search only")

    if not os.path.exists(args.input):
        print(f"ERROR: Input file not found: {args.input}")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        companies = list(reader)

    print(f"\nLoaded {len(companies)} companies from {args.input}")
    print(f"Targeting: Founder / Co-founder / CEO only\n")

    state = load_state() if not args.fresh else {"processed": {}}
    results = []

    for i, row in enumerate(companies):
        company = row.get("Company Name", "").strip()
        if not company:
            continue

        if company in state["processed"]:
            print(f"[{i+1}/{len(companies)}] Skipping {company} (already processed)")
            results.append(state["processed"][company])
            continue

        print(f"[{i+1}/{len(companies)}]", end="")

        try:
            result = process_company(row, gemini_key)
            results.append(result)
            state["processed"][company] = result
            save_state(state)
        except KeyboardInterrupt:
            print("\n\nInterrupted! Progress saved. Re-run to resume.")
            save_state(state)
            break
        except Exception as e:
            print(f"\n  [ERROR] {company}: {e}")
            import traceback
            traceback.print_exc()
            err = {
                "Company Name": company, "Founder": row.get("Founder", ""),
                "Role": row.get("Role", ""), "Sector": "", "City": "",
                "Domain": row.get("Website Domain", ""),
                "Email": "", "Email Source": f"error: {e}",
                "SMTP Status": "", "All Candidates": "",
            }
            results.append(err)
            state["processed"][company] = err
            save_state(state)

    # Write output
    output_fields = [
        "Company Name", "Founder", "Role", "Sector", "City", "Domain",
        "Email", "Email Source", "SMTP Status", "All Candidates",
    ]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"\n{'='*60}")
    print(f"DONE! Results written to: {args.output}")
    print(f"{'='*60}")

    found = sum(1 for r in results if r.get("Email"))
    verified = sum(1 for r in results if r.get("SMTP Status") == "valid")
    confirmed = sum(1 for r in results if r.get("SMTP Status") in ("valid", "web_confirmed", "gemini_confirmed", "catch-all"))
    print(f"  Total companies:   {len(results)}")
    print(f"  Emails found:      {found}")
    print(f"  SMTP verified:     {verified}")
    print(f"  Total confirmed:   {confirmed} (smtp + web + gemini + catch-all)")


if __name__ == "__main__":
    main()
