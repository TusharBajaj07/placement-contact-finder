"""
Smart Contact Finder v1
========================
Input:  Company names (CSV with "Company" column, or just a list)
Output: CSV with contact name, role, email, confidence

Pipeline:
1. Find company domain via web search
2. Crawl website to discover ANY employee email → detect email pattern
3. Search for HR/TA/Founder contacts via LinkedIn (DuckDuckGo)
4. Construct email using discovered pattern
5. SMTP verify
6. Confidence: smtp_verified > pattern_matched > guessed
7. [Future] Apollo.io enrichment

Usage:
    python contact_finder.py --companies "Zerodha, Razorpay, Flipkart"
    python contact_finder.py --input companies.csv
    python contact_finder.py --companies "Zerodha" --gemini-key YOUR_KEY
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
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import dns.resolver
    HAS_DNS = True
except ImportError:
    HAS_DNS = False
    print("[WARN] pip install dnspython for SMTP verification")

try:
    from google import genai as genai_new
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    from ddgs import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False
    print("[WARN] pip install duckduckgo-search")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SMTP_TIMEOUT = 7
REQUEST_TIMEOUT = 8
CRAWL_DELAY = 0.5
SEARCH_DELAY = 1.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

# Roles to search for, in priority order
# For big companies: HR/TA first. For small: founders.
HR_QUERIES = [
    '"{company}" "talent acquisition" site:linkedin.com/in',
    '"{company}" "HR manager" site:linkedin.com/in',
    '"{company}" "head of people" OR "people operations" site:linkedin.com/in',
    '"{company}" "recruiter" site:linkedin.com/in',
    '"{company}" "human resources" site:linkedin.com/in',
]

FOUNDER_QUERIES = [
    '"{company}" "founder" OR "co-founder" site:linkedin.com/in',
    '"{company}" "CEO" site:linkedin.com/in',
]

EMAIL_PATTERNS = [
    "{first}@{domain}",
    "{first}.{last}@{domain}",
    "{first}{last}@{domain}",
    "{f}{last}@{domain}",
    "{first}.{l}@{domain}",
    "{f}.{last}@{domain}",
    "{first}_{last}@{domain}",
    "{last}.{first}@{domain}",
]

JUNK_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "wikipedia.org", "crunchbase.com", "glassdoor.com",
    "ambitionbox.com", "naukri.com", "indeed.com",
}

HONORIFICS = {"dr.", "dr", "mr.", "mr", "mrs.", "mrs", "ms.", "ms", "prof.", "prof", "shri"}


# ---------------------------------------------------------------------------
# DNS / SMTP (with caching)
# ---------------------------------------------------------------------------
_mx_cache = {}
_catchall_cache = {}


def get_mx(domain):
    if domain in _mx_cache:
        return _mx_cache[domain]
    if not HAS_DNS:
        return None
    try:
        records = dns.resolver.resolve(domain, "MX")
        host = str(sorted(records, key=lambda r: r.preference)[0].exchange).rstrip(".")
        _mx_cache[domain] = host
        return host
    except Exception:
        _mx_cache[domain] = None
        return None


def is_catchall(domain):
    if domain in _catchall_cache:
        return _catchall_cache[domain]
    mx = get_mx(domain)
    if not mx:
        _catchall_cache[domain] = None
        return None
    try:
        s = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        s.connect(mx, 25)
        s.helo("check.local")
        s.mail("test@check.local")
        code, _ = s.rcpt(f"xyzfake99999@{domain}")
        s.quit()
        result = (code == 250)
        _catchall_cache[domain] = result
        return result
    except Exception:
        _catchall_cache[domain] = None
        return None


def smtp_check(email):
    if not HAS_DNS:
        return "unknown"
    domain = email.split("@")[1]
    mx = get_mx(domain)
    if not mx:
        return "unknown"
    try:
        s = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        s.connect(mx, 25)
        s.helo("check.local")
        s.mail("test@check.local")
        code, _ = s.rcpt(email)
        s.quit()
        if code == 250:
            return "valid"
        elif code == 550:
            return "invalid"
        return "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Web Search
# ---------------------------------------------------------------------------
_ddgs = None

def search(query, max_results=5):
    global _ddgs
    if not HAS_DDGS:
        return []
    try:
        if _ddgs is None:
            _ddgs = DDGS()
        raw = _ddgs.text(query, max_results=max_results)
        return [{"title": r.get("title", ""), "url": r.get("href", ""),
                 "snippet": r.get("body", "")} for r in raw]
    except Exception as e:
        print(f"    [Search] {e}")
        # Reset on error
        _ddgs = None
        return []


# ---------------------------------------------------------------------------
# Step 1: Find company domain
# ---------------------------------------------------------------------------
GENERIC_DOMAINS = {
    "medium.com", "t.me", "telegram.org", "github.com", "play.google.com",
    "apps.apple.com", "blog.google", "notion.so", "substack.com",
    "wordpress.com", "blogspot.com", "wix.com", "squarespace.com",
    "hubspot.com", "mailchimp.com", "typeform.com", "docs.google.com",
    "drive.google.com", "bit.ly", "linktr.ee", "cloudfront.net",
    "amazonaws.com", "googleapis.com", "gstatic.com", "cloudflare.com",
    "akamaized.net", "fastly.net", "herokuapp.com", "netlify.app",
    "vercel.app", "firebaseapp.com", "web.app",
}

def find_domain(company):
    print(f"  [1] Finding domain...")
    company_lower = company.lower().replace(" ", "").replace("-", "")
    time.sleep(SEARCH_DELAY)
    # Try two searches for robustness
    results = search(f"{company} official website", max_results=8)
    results += search(f"{company} company site", max_results=5)
    candidates = []
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        parsed = urlparse(url)
        d = parsed.netloc.replace("www.", "")
        if not d:
            continue
        if any(j in d for j in JUNK_DOMAINS):
            continue
        # Strip subdomains — get root domain
        parts = d.split(".")
        if len(parts) > 2:
            d = ".".join(parts[-2:])
        if d in GENERIC_DOMAINS:
            continue
        candidates.append(d)

    if candidates:
        # Score candidates: prefer exact company name match in domain
        def score(d):
            name_part = d.split(".")[0].lower().replace("-", "")
            if name_part == company_lower:
                return 0  # Perfect: razorpay.com for Razorpay
            if company_lower in name_part:
                return 1  # Good: contains company name
            if name_part in company_lower:
                return 2  # OK: company name contains domain
            return 3
        candidates.sort(key=score)
        best = candidates[0]
        print(f"      Domain: {best}")
        return best
    return None


# ---------------------------------------------------------------------------
# Step 2: Crawl website to discover email pattern
# ---------------------------------------------------------------------------
def discover_email_pattern(domain):
    """Crawl company website to find ANY email, then extract the pattern."""
    print(f"  [2] Crawling {domain} for email pattern...")
    session = requests.Session()
    session.headers.update(HEADERS)

    base = f"https://{domain}"
    paths = ["", "/about", "/about-us", "/team", "/contact", "/contact-us",
             "/people", "/leadership", "/careers"]
    urls = [base + p for p in paths]
    crawled = set()
    found_emails = set()
    page_texts = []

    for url in urls[:8]:
        norm = url.rstrip("/").lower()
        if norm in crawled:
            continue
        crawled.add(norm)
        try:
            time.sleep(CRAWL_DELAY)
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(separator=" ", strip=True)

            # Extract emails
            emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", resp.text)
            for e in emails:
                el = e.lower()
                if not any(el.endswith(x) for x in [".png", ".jpg", ".gif", ".css", ".js"]):
                    if any(x in el for x in ["noreply", "no-reply", "sentry", "wixpress", "example"]):
                        continue
                    found_emails.add(el)

            # Save text for name extraction
            if any(k in url.lower() for k in ["team", "about", "people", "leader"]):
                page_texts.append(text[:2000])

            # Find more links
            if len(crawled) <= 3:
                for a in soup.find_all("a", href=True):
                    t = a.get_text(strip=True).lower()
                    h = a["href"].lower()
                    if any(k in t or k in h for k in ["team", "about", "people", "leader", "founder"]):
                        full = urljoin(base, a["href"])
                        if urlparse(full).netloc.replace("www.", "") == domain:
                            if full.rstrip("/").lower() not in crawled:
                                urls.append(full)
        except Exception:
            continue

    # Detect pattern from found emails
    pattern = None
    domain_emails = [e for e in found_emails if domain in e.split("@")[1]]
    generic = {"info", "hello", "contact", "support", "hr", "careers", "team", "admin", "sales", "help"}

    for email in domain_emails:
        local = email.split("@")[0]
        if local in generic:
            continue
        # Try to figure out the pattern
        if "." in local:
            parts = local.split(".")
            if len(parts) == 2 and all(p.isalpha() for p in parts):
                pattern = "first.last"
                print(f"      Pattern detected: {pattern} (from {email})")
                break
        elif "_" in local:
            pattern = "first_last"
            print(f"      Pattern detected: {pattern} (from {email})")
            break
        elif local.isalpha() and len(local) > 2:
            # Could be first, firstlast, flast, etc.
            pattern = "first"  # most common for single-word
            print(f"      Pattern hint: single word (from {email})")
            break

    # If no pattern from crawl, try web search for employee emails
    dummy_locals = {"jane.doe", "john.doe", "first.last", "firstname.lastname",
                    "name", "email", "your", "user", "test", "example", "first"}
    if not pattern:
        time.sleep(SEARCH_DELAY)
        results = search(f'"@{domain}" email', max_results=5)
        for r in results:
            text = r.get("snippet", "") + " " + r.get("title", "")
            web_emails = re.findall(r"[a-zA-Z0-9._%+\-]+@" + re.escape(domain), text)
            for e in web_emails:
                local = e.split("@")[0].lower()
                if local in generic or local in dummy_locals:
                    continue
                if "." in local and all(p.isalpha() for p in local.split(".")):
                    pattern = "first.last"
                    print(f"      Pattern detected: {pattern} (from web: {e})")
                    break
                elif "_" in local:
                    pattern = "first_last"
                    print(f"      Pattern detected: {pattern} (from web: {e})")
                    break
                elif local.isalpha() and len(local) > 2:
                    pattern = "first"
                    print(f"      Pattern hint: single word (from web: {e})")
                    break
            if pattern:
                break

    if not pattern:
        print(f"      No clear pattern found — using defaults")

    return {
        "emails": found_emails,
        "domain_emails": domain_emails,
        "pattern": pattern,
        "page_texts": page_texts,
    }


# ---------------------------------------------------------------------------
# Step 3: Find contact names via web search
# ---------------------------------------------------------------------------
NOT_NAMES = {
    "hiring strategist", "talent acquisition", "human resources", "head of",
    "director of", "manager of", "lead of", "senior recruiter", "hr manager",
    "people operations", "open position", "job opening", "we are hiring",
    "looking for", "apply now", "join us", "meet our", "our team",
    "employee talent", "talent assessment", "about us", "contact us",
    "new delhi", "san francisco", "new york", "los angeles",
}

NOT_NAME_WORDS = {"employee", "talent", "assessment", "hiring", "recruitment",
                  "position", "opening", "career", "company", "team", "about"}

def extract_name_from_linkedin(text):
    """Extract a person's name from LinkedIn search snippet."""
    patterns = [
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*[-–|·]",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*,",
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*[-–]\s*(?:Head|Manager|Director|Lead|VP|Founder|CEO|HR|Talent|People|Recruiter)",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            name = m.group(1).strip()
            if len(name.split()) >= 2 and len(name) < 40:
                # Filter out non-names (job titles, etc.)
                if name.lower() in NOT_NAMES or any(n in name.lower() for n in NOT_NAMES):
                    continue
                # Each word should be a plausible name part (2+ chars, alphabetic)
                parts = name.split()
                if all(len(p) >= 2 and p.isalpha() for p in parts):
                    # Filter individual words that are clearly not names
                    if any(w.lower() in NOT_NAME_WORDS for w in parts):
                        continue
                    return name
    return None


def find_contacts(company, company_size="unknown"):
    """Search for HR/TA contacts, with founder fallback for small companies."""
    print(f"  [3] Searching for contacts...")
    contacts = []
    seen_names = set()

    # Decide which queries to prioritize
    if company_size in ("large", "mid"):
        queries = HR_QUERIES + FOUNDER_QUERIES
    else:
        # For startups/unknown, try both but founders first might be better
        queries = HR_QUERIES[:3] + FOUNDER_QUERIES + HR_QUERIES[3:]

    for q_template in queries:
        if len(contacts) >= 5:
            break
        query = q_template.format(company=company)
        time.sleep(SEARCH_DELAY)
        results = search(query, max_results=5)

        for r in results:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")

            # Try to extract name from title (LinkedIn format)
            name = extract_name_from_linkedin(title)
            if not name:
                name = extract_name_from_linkedin(snippet)
            if not name:
                continue

            if name.lower() in seen_names:
                continue
            seen_names.add(name.lower())

            # Extract role
            role = "Unknown"
            role_patterns = [
                r"((?:Head|Director|Manager|Lead|VP|Senior|Chief|Founder|Co-?founder|CEO|CTO)\s*(?:of\s+)?(?:HR|Human Resources|Talent|People|Recruiting|Recruitment|Operations)?(?:\s*&\s*\w+)?)",
                r"(Talent\s+Acquisition\s*(?:Manager|Lead|Head|Specialist|Partner)?)",
                r"(HR\s*(?:Manager|Lead|Head|Director|Business Partner)?)",
                r"(People\s+Operations?\s*(?:Manager|Lead|Head)?)",
                r"(Recruiter|Recruiting\s+Manager)",
                r"(Founder|Co-?founder(?:\s*&\s*CEO)?|CEO|CTO)",
            ]
            combined = title + " " + snippet
            for rp in role_patterns:
                rm = re.search(rp, combined, re.IGNORECASE)
                if rm:
                    role = rm.group(1).strip()
                    break

            contacts.append({
                "name": name,
                "role": role,
                "linkedin_url": url if "linkedin.com" in url else "",
                "source": "linkedin_search",
            })

    if not contacts:
        print(f"      No contacts found via search")
    else:
        print(f"      Found {len(contacts)} contacts")
        for c in contacts[:3]:
            print(f"        {c['name']} - {c['role']}")

    return contacts


# ---------------------------------------------------------------------------
# Step 4: Generate emails using discovered pattern
# ---------------------------------------------------------------------------
def clean_name(name):
    parts = name.strip().split()
    parts = [p for p in parts if p.lower().strip(".") not in {h.strip(".") for h in HONORIFICS}]
    if not parts:
        parts = name.strip().split()
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) >= 2 else ""
    return first, last


def make_emails(first, last, domain, pattern_hint=None):
    """Generate email candidates, prioritized by discovered pattern."""
    f = first.lower()
    l = last.lower() if last else ""
    fi = f[0] if f else ""
    li = l[0] if l else ""

    # Order by pattern hint
    all_patterns = []
    if pattern_hint == "first.last" and l:
        all_patterns = [f"{f}.{l}@{domain}", f"{f}@{domain}", f"{f}{l}@{domain}", f"{fi}{l}@{domain}"]
    elif pattern_hint == "first_last" and l:
        all_patterns = [f"{f}_{l}@{domain}", f"{f}@{domain}", f"{f}.{l}@{domain}", f"{fi}{l}@{domain}"]
    else:
        # Default: most common Indian startup patterns
        all_patterns = [f"{f}@{domain}"]
        if l:
            all_patterns += [
                f"{f}.{l}@{domain}", f"{f}{l}@{domain}", f"{fi}{l}@{domain}",
                f"{f}.{li}@{domain}", f"{fi}.{l}@{domain}", f"{f}_{l}@{domain}",
                f"{l}.{f}@{domain}",
            ]

    return list(OrderedDict.fromkeys(all_patterns))


# ---------------------------------------------------------------------------
# Step 5: Verify emails
# ---------------------------------------------------------------------------
def verify_candidates(candidates, catchall=False):
    """Check candidates. Stop at first valid. Return (email, status)."""
    if catchall:
        # Can't trust SMTP, return first candidate
        return candidates[0] if candidates else ("", "no_candidates"), "catch-all"

    for email in candidates[:5]:  # Max 5 checks for speed
        status = smtp_check(email)
        if status == "valid":
            return email, "smtp_verified"
        elif status == "invalid":
            continue
        else:
            # unknown — keep as fallback
            return email, "pattern_match"

    return candidates[0] if candidates else "", "guessed"


# ---------------------------------------------------------------------------
# Step 6: Gemini (optional)
# ---------------------------------------------------------------------------
_gemini_client = None

def gemini_find(company, domain, contacts, api_key):
    if not HAS_GEMINI or not api_key:
        return None
    global _gemini_client
    try:
        if _gemini_client is None:
            _gemini_client = genai_new.Client(api_key=api_key)
        names = ", ".join([f"{c['name']} ({c['role']})" for c in contacts[:5]])
        prompt = f"""For the company "{company}" (domain: {domain}), I found these people:
{names}

Return JSON (no markdown):
{{
    "best_contact": {{
        "name": "who to email for hiring/placement outreach",
        "email": "their likely email" or null,
        "confidence": "high/medium/low"
    }},
    "email_pattern": "the pattern this company uses (e.g. first.last@domain)" or null
}}"""
        resp = _gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = resp.text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception as e:
        if "429" in str(e):
            print(f"    [Gemini] Rate limited")
        else:
            print(f"    [Gemini] {e}")
        return None


# ---------------------------------------------------------------------------
# Apollo placeholder
# ---------------------------------------------------------------------------
def apollo_enrich(company, domain, contact_name, api_key=None):
    """
    [PLACEHOLDER] Apollo.io People Search API.
    When API key is provided, will search for:
    - People at company matching HR/TA/Founder roles
    - Verified email addresses
    Returns: {"email": "...", "confidence": "high", "source": "apollo"}
    """
    if not api_key:
        return None

    # TODO: Implement when Apollo key is available
    # endpoint = "https://api.apollo.io/v1/people/match"
    # headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    # payload = {
    #     "organization_name": company,
    #     "domain": domain,
    #     "name": contact_name,
    # }
    # resp = requests.post(endpoint, json=payload, headers=headers)
    # data = resp.json()
    # if data.get("person", {}).get("email"):
    #     return {
    #         "email": data["person"]["email"],
    #         "confidence": "high",
    #         "source": "apollo",
    #     }
    return None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def process_company(company, gemini_key=None, apollo_key=None):
    print(f"\n{'='*60}")
    print(f"  COMPANY: {company}")
    print(f"{'='*60}")

    result_rows = []

    # Step 1: Find domain
    domain = find_domain(company)
    if not domain:
        print(f"  [!] Could not find domain")
        return [{
            "Company": company, "Domain": "", "Contact Name": "", "Role": "",
            "Email": "", "Confidence": "", "Source": "", "Pattern": "",
        }]

    # Step 2: Discover email pattern from website
    crawl = discover_email_pattern(domain)
    pattern = crawl.get("pattern")
    site_emails = crawl.get("domain_emails", [])

    # Step 2b: Check catch-all
    catchall = is_catchall(domain)
    if catchall:
        print(f"  [!] Catch-all domain — SMTP unreliable")

    # Step 3: Find contacts
    contacts = find_contacts(company)

    # Step 4: Gemini enhancement (optional)
    if gemini_key and contacts:
        print(f"  [4] Gemini analysis...")
        g = gemini_find(company, domain, contacts, gemini_key)
        if g and g.get("email_pattern"):
            gp = g["email_pattern"]
            if "first.last" in gp:
                pattern = "first.last"
            elif "first_last" in gp:
                pattern = "first_last"
            print(f"      Gemini pattern: {gp}")

    # Step 5: For each contact, generate and verify email
    for contact in contacts[:5]:
        name = contact["name"]
        role = contact["role"]
        first, last = clean_name(name)

        if not first:
            continue

        candidates = make_emails(first, last, domain, pattern)

        # Apollo check first (highest confidence if available)
        apollo_result = apollo_enrich(company, domain, name, apollo_key)
        if apollo_result:
            result_rows.append({
                "Company": company, "Domain": domain,
                "Contact Name": name, "Role": role,
                "Email": apollo_result["email"],
                "Confidence": "high",
                "Source": "apollo",
                "Pattern": pattern or "",
                "LinkedIn": contact.get("linkedin_url", ""),
            })
            continue

        # SMTP verify
        email, source = verify_candidates(candidates, catchall)
        conf_map = {"smtp_verified": "high", "pattern_match": "medium",
                     "catch-all": "medium", "guessed": "low", "no_candidates": "none"}
        confidence = conf_map.get(source, "low")

        result_rows.append({
            "Company": company, "Domain": domain,
            "Contact Name": name, "Role": role,
            "Email": email,
            "Confidence": confidence,
            "Source": source,
            "Pattern": pattern or "",
            "LinkedIn": contact.get("linkedin_url", ""),
        })

        print(f"    {name} ({role}) -> {email} [{source}]")

    # If no contacts found, try generic emails
    if not result_rows:
        print(f"  [!] No contacts, trying generic emails...")
        generics = [f"hr@{domain}", f"careers@{domain}", f"hiring@{domain}",
                     f"talent@{domain}", f"info@{domain}"]
        for g in generics:
            status = smtp_check(g)
            if status == "valid":
                result_rows.append({
                    "Company": company, "Domain": domain,
                    "Contact Name": "(Generic)", "Role": "General",
                    "Email": g, "Confidence": "low",
                    "Source": "generic_verified", "Pattern": "", "LinkedIn": "",
                })
                print(f"    Generic: {g} -> valid")
                break

    if not result_rows:
        result_rows.append({
            "Company": company, "Domain": domain,
            "Contact Name": "", "Role": "",
            "Email": "", "Confidence": "none",
            "Source": "not_found", "Pattern": "", "LinkedIn": "",
        })

    return result_rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Smart Contact Finder")
    parser.add_argument("--companies", "-c", help="Comma-separated company names")
    parser.add_argument("--input", "-i", help="Input CSV with company names")
    parser.add_argument("--output", "-o", default="contacts_found.csv", help="Output CSV")
    parser.add_argument("--gemini-key", "-g", help="Gemini API key (optional)")
    parser.add_argument("--apollo-key", "-a", help="Apollo.io API key (optional)")
    parser.add_argument("--company-col", default=None, help="Column name for company in CSV")
    args = parser.parse_args()

    gemini_key = args.gemini_key or os.environ.get("GEMINI_API_KEY")
    apollo_key = args.apollo_key or os.environ.get("APOLLO_API_KEY")

    # Get company list
    companies = []
    if args.companies:
        companies = [c.strip() for c in args.companies.split(",") if c.strip()]
    elif args.input:
        with open(args.input, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            col = args.company_col
            if not col:
                for c in reader.fieldnames:
                    if c.strip().lower() in ["company", "company name", "company_name", "name"]:
                        col = c
                        break
            if not col:
                print(f"ERROR: No company column found. Use --company-col")
                sys.exit(1)
            for row in reader:
                name = row[col].strip()
                if name:
                    companies.append(name)
    else:
        print("ERROR: Provide --companies or --input")
        sys.exit(1)

    print(f"Processing {len(companies)} companies")
    if gemini_key:
        print(f"Gemini: enabled")
    if apollo_key:
        print(f"Apollo: enabled")
    print()

    all_rows = []
    for i, company in enumerate(companies):
        print(f"[{i+1}/{len(companies)}]", end="")
        rows = process_company(company, gemini_key, apollo_key)
        all_rows.extend(rows)

    # Write output
    fields = ["Company", "Domain", "Contact Name", "Role", "Email",
              "Confidence", "Source", "Pattern", "LinkedIn"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in all_rows:
            writer.writerow(r)

    print(f"\n{'='*60}")
    print(f"DONE! -> {args.output}")
    print(f"{'='*60}")
    total = len(all_rows)
    with_email = sum(1 for r in all_rows if r.get("Email"))
    high = sum(1 for r in all_rows if r.get("Confidence") == "high")
    med = sum(1 for r in all_rows if r.get("Confidence") == "medium")
    print(f"  Contacts: {total} | With email: {with_email} | High conf: {high} | Medium: {med}")


if __name__ == "__main__":
    main()
