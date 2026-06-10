"""
Free Contact Discovery Script
------------------------------
Input:  CSV with company names (column: "Company")
Output: Same CSV enriched with contacts, emails, phone numbers

Free methods used:
1. DuckDuckGo search for company website + HR/founder info
2. Website crawling (contact/about/team pages)
3. Email pattern guessing from discovered names + domain
4. SMTP verification (checks if email exists without sending)
5. Gemini AI for intelligent analysis and ranking

Usage:
    python free_discovery.py --input companies.csv --gemini-key YOUR_KEY
"""

import csv
import requests
from bs4 import BeautifulSoup
import re
import smtplib
import json
import time
import os
import sys
import argparse
from urllib.parse import urljoin, urlparse, quote_plus, parse_qs
from collections import OrderedDict

try:
    import dns.resolver
    HAS_DNS = True
except ImportError:
    HAS_DNS = False
    print("[WARN] dnspython not installed. SMTP verification will be limited.")
    print("       Install with: pip install dnspython")

import google.generativeai as genai


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = 10
CRAWL_DELAY = 1.0
SEARCH_DELAY = 2.0
MAX_PAGES_PER_SITE = 10
MAX_SEARCH_RESULTS = 8
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_DELAY = 30  # seconds to wait on rate limit

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

PRIORITY_PATHS = [
    "/contact", "/contact-us", "/contactus",
    "/about", "/about-us", "/aboutus",
    "/team", "/our-team", "/people", "/leadership",
    "/careers", "/jobs",
]

EMAIL_PATTERNS = [
    ("{first}@{domain}",        "first"),
    ("{last}@{domain}",         "last"),
    ("{first}.{last}@{domain}", "first.last"),
    ("{first}{last}@{domain}",  "firstlast"),
    ("{f}{last}@{domain}",      "flast"),
    ("{first}.{l}@{domain}",    "first.l"),
    ("{f}.{last}@{domain}",     "f.last"),
    ("{first}_{last}@{domain}", "first_last"),
    ("{last}.{first}@{domain}", "last.first"),
    ("{first}{l}@{domain}",     "firstl"),
]


# ---------------------------------------------------------------------------
# Web Search (DuckDuckGo via ddgs library)
# ---------------------------------------------------------------------------
class WebSearcher:
    def __init__(self):
        from ddgs import DDGS
        self.ddgs = DDGS()

    def search(self, query, max_results=MAX_SEARCH_RESULTS):
        results = []
        try:
            raw = self.ddgs.text(query, max_results=max_results)
            for r in raw:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        except Exception as e:
            print(f"  [Search Error] {e}")
        return results



# ---------------------------------------------------------------------------
# Website Crawler
# ---------------------------------------------------------------------------
class WebsiteCrawler:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def crawl(self, base_url):
        data = {
            "emails": set(),
            "phones": set(),
            "social_links": {},
            "page_texts": [],
        }

        if not base_url.startswith("http"):
            base_url = "https://" + base_url

        parsed = urlparse(base_url)
        domain = parsed.netloc.replace("www.", "")

        urls_to_crawl = [base_url]
        for path in PRIORITY_PATHS:
            urls_to_crawl.append(urljoin(base_url, path))

        crawled = set()
        pages_crawled = 0

        for url in urls_to_crawl:
            if pages_crawled >= MAX_PAGES_PER_SITE:
                break
            normalized = url.rstrip("/").lower()
            if normalized in crawled:
                continue
            crawled.add(normalized)

            try:
                time.sleep(CRAWL_DELAY)
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                if "text/html" not in resp.headers.get("Content-Type", ""):
                    continue

                pages_crawled += 1
                soup = BeautifulSoup(resp.text, "html.parser")
                text = soup.get_text(separator=" ", strip=True)

                # Extract emails
                found_emails = set(re.findall(
                    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                    resp.text,
                ))
                for email in found_emails:
                    e_lower = email.lower()
                    if not any(e_lower.endswith(ext) for ext in [".png", ".jpg", ".gif", ".svg", ".css", ".js"]):
                        data["emails"].add(e_lower)

                # Extract phone numbers
                phones = re.findall(
                    r"(?:\+?\d{1,3}[\s\-]?)?\(?\d{2,5}\)?[\s\-]?\d{3,5}[\s\-]?\d{3,5}",
                    text,
                )
                for p in phones:
                    cleaned = re.sub(r"[^\d+]", "", p)
                    if 10 <= len(cleaned) <= 15:
                        data["phones"].add(cleaned)

                # Extract social media links
                for a in soup.find_all("a", href=True):
                    href = a["href"].lower()
                    if "linkedin.com/company" in href:
                        data["social_links"]["linkedin"] = a["href"]
                    elif "twitter.com/" in href or "x.com/" in href:
                        data["social_links"]["twitter"] = a["href"]

                # Discover additional internal links
                if pages_crawled <= 3:
                    for a in soup.find_all("a", href=True):
                        link_text = a.get_text(strip=True).lower()
                        link_href = a["href"].lower()
                        if any(kw in link_text or kw in link_href for kw in
                               ["team", "people", "about", "contact", "leadership", "founder"]):
                            full_url = urljoin(base_url, a["href"])
                            if urlparse(full_url).netloc.replace("www.", "") == domain:
                                if full_url.rstrip("/").lower() not in crawled:
                                    urls_to_crawl.append(full_url)

                # Keep text from relevant pages for Gemini
                if any(kw in url.lower() for kw in ["team", "about", "contact", "people", "founder", "leadership"]):
                    data["page_texts"].append({
                        "url": url,
                        "text": text[:2000],
                    })

                print(f"    Crawled: {url} | emails: {len(data['emails'])}")

            except Exception as e:
                print(f"    [Crawl Error] {url}: {e}")
                continue

        return data


# ---------------------------------------------------------------------------
# Email Pattern Generator
# ---------------------------------------------------------------------------
class EmailPatternGenerator:
    @staticmethod
    def generate(first_name, last_name, domain):
        if not first_name or not domain:
            return []

        first = first_name.lower().strip()
        last = last_name.lower().strip() if last_name else ""
        f = first[0] if first else ""
        l = last[0] if last else ""

        candidates = []
        for pattern, _desc in EMAIL_PATTERNS:
            try:
                email = pattern.format(first=first, last=last, f=f, l=l, domain=domain)
                if last or "{last}" not in pattern:
                    candidates.append(email)
            except (KeyError, IndexError):
                continue

        return list(OrderedDict.fromkeys(candidates))


# ---------------------------------------------------------------------------
# SMTP Email Verifier
# ---------------------------------------------------------------------------
class SMTPVerifier:
    @staticmethod
    def get_mx_host(domain):
        if not HAS_DNS:
            return None
        try:
            records = dns.resolver.resolve(domain, "MX")
            mx = sorted(records, key=lambda r: r.preference)
            return str(mx[0].exchange).rstrip(".")
        except Exception:
            return None

    @staticmethod
    def verify(email):
        if not HAS_DNS:
            return "unknown"

        domain = email.split("@")[1]
        mx_host = SMTPVerifier.get_mx_host(domain)
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
# Gemini AI Analyzer
# ---------------------------------------------------------------------------
class GeminiAnalyzer:
    def __init__(self, api_key):
        self.skip = (api_key == "skip")
        if not self.skip:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-2.5-flash")

    def _call_gemini(self, prompt):
        if self.skip:
            return None
        """Call Gemini with retry on rate limit."""
        for attempt in range(GEMINI_MAX_RETRIES):
            try:
                response = self.model.generate_content(prompt)
                text = response.text.strip()
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                return json.loads(text)
            except json.JSONDecodeError as e:
                print(f"  [Gemini JSON Error] {e}")
                return None
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "quota" in err_str.lower():
                    wait = GEMINI_RETRY_DELAY * (attempt + 1)
                    print(f"  [Gemini Rate Limit] Waiting {wait}s before retry ({attempt+1}/{GEMINI_MAX_RETRIES})...")
                    time.sleep(wait)
                else:
                    print(f"  [Gemini Error] {e}")
                    return None
        print(f"  [Gemini] Max retries exceeded.")
        return None

    def analyze_company(self, company_name):
        prompt = f"""You are helping a college placement cell find the right contact person at a company.

Company: "{company_name}"

Based on your knowledge, answer in JSON (no markdown, no code blocks):
{{
    "company_type": "startup" or "mid" or "large",
    "likely_domain": "best guess of company website domain (e.g. example.com)" or null,
    "target_roles": ["list of job titles to target, e.g. HR Manager, Talent Acquisition, Founder"],
    "target_names": ["if you know any specific people at this company in HR/founder roles, list them"] or [],
    "notes": "any useful context"
}}

Be concise. If unsure about names, leave the list empty. Only include names you're reasonably confident about."""

        return self._call_gemini(prompt)

    def extract_contacts_from_text(self, company_name, domain, page_texts, scraped_emails):
        combined_text = ""
        for pt in page_texts[:3]:
            combined_text += f"\n--- Page: {pt['url']} ---\n{pt['text']}\n"

        if not combined_text.strip() and not scraped_emails:
            return None

        emails_str = ", ".join(scraped_emails) if scraped_emails else "none found"

        prompt = f"""You are helping a college placement cell find HR/recruitment contacts at a company.

Company: "{company_name}"
Domain: "{domain}"
Emails already found on website: {emails_str}

Here is text scraped from the company's website (team/about/contact pages):
{combined_text[:4000]}

Extract contact persons relevant for placement/recruitment outreach. Return JSON (no markdown, no code blocks):
{{
    "contacts": [
        {{
            "name": "Full Name",
            "role": "Their job title/role",
            "email": "their email if found, or null",
            "confidence": "high/medium/low"
        }}
    ],
    "email_pattern": "the email pattern this company uses (e.g. first.last@domain.com)" or null,
    "best_generic_email": "best generic email for outreach (like hr@, careers@, info@)" or null
}}

Priority: HR > Talent Acquisition > People Operations > Founder/CEO (for startups).
Only include people relevant to hiring/recruitment. Max 5 contacts."""

        return self._call_gemini(prompt)

    def rank_emails(self, company_name, candidate_emails, context=""):
        if not candidate_emails:
            return []

        prompt = f"""You are verifying email addresses for outreach to "{company_name}".

Candidate emails: {json.dumps(candidate_emails)}
Context: {context}

Return a JSON array sorted from most likely valid to least likely.
No markdown, no code blocks.
[
    {{"email": "...", "confidence": 85, "reason": "..."}},
    ...
]

Filter out obviously fake, generic (noreply@, no-reply@), or unrelated emails.
Prefer professional/work emails. Remove duplicates."""

        return self._call_gemini(prompt) or []


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------
class FreeContactDiscovery:
    def __init__(self, gemini_api_key, input_csv, output_csv=None):
        self.input_csv = input_csv
        self.output_csv = output_csv or input_csv.replace(".csv", "_enriched.csv")
        self.searcher = WebSearcher()
        self.crawler = WebsiteCrawler()
        self.pattern_gen = EmailPatternGenerator()
        self.verifier = SMTPVerifier()
        self.gemini = GeminiAnalyzer(gemini_api_key)

        self.state_file = input_csv.replace(".csv", "_state.json")
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                return json.load(f)
        return {"processed": {}, "last_index": -1}

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def _find_company_website(self, company_name, gemini_hint=None):
        print(f"  Searching for website...")
        time.sleep(SEARCH_DELAY)
        results = self.searcher.search(f"{company_name} company official website")

        candidates = []
        for r in results:
            url = r.get("url", "")
            if url and not any(x in url for x in [
                "linkedin.com", "facebook.com", "twitter.com", "wikipedia.org",
                "crunchbase.com", "glassdoor.com", "ambitionbox.com", "youtube.com",
                "instagram.com",
            ]):
                candidates.append(url)

        if gemini_hint:
            hint_url = f"https://{gemini_hint}"
            if hint_url not in candidates:
                candidates.insert(0, hint_url)

        if candidates:
            parsed = urlparse(candidates[0])
            domain = parsed.netloc.replace("www.", "")
            base_url = f"https://{parsed.netloc}"
            print(f"  Found website: {base_url} (domain: {domain})")
            return base_url, domain

        return None, None

    def _search_for_contacts(self, company_name, domain):
        print(f"  Searching for HR/founder contacts...")
        all_emails = set()
        all_info = []

        queries = [
            f'"{company_name}" HR email contact',
            f'"{company_name}" talent acquisition email',
            f'site:{domain} email @{domain}' if domain else None,
            f'"{company_name}" founder email',
        ]

        for query in queries:
            if not query:
                continue
            time.sleep(SEARCH_DELAY)
            results = self.searcher.search(query, max_results=5)
            for r in results:
                snippet = r.get("snippet", "") + " " + r.get("title", "")
                emails = re.findall(
                    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                    snippet,
                )
                for e in emails:
                    all_emails.add(e.lower())
                all_info.append(snippet)

        return all_emails, all_info

    def _generate_and_verify_patterns(self, contacts, domain):
        verified_emails = []

        for contact in contacts:
            name = contact.get("name", "")
            if not name or not domain:
                continue

            parts = name.strip().split()
            if len(parts) < 2:
                first, last = parts[0], ""
            else:
                first, last = parts[0], parts[-1]

            candidates = self.pattern_gen.generate(first, last, domain)
            print(f"    Generated {len(candidates)} patterns for {name}")

            if contact.get("email"):
                candidates.insert(0, contact["email"])

            for email in candidates[:6]:
                status = self.verifier.verify(email)
                print(f"      {email} -> {status}")
                if status == "valid":
                    verified_emails.append({
                        "name": name,
                        "role": contact.get("role", ""),
                        "email": email,
                        "status": "smtp_verified",
                    })
                    break
                elif status == "unknown":
                    verified_emails.append({
                        "name": name,
                        "role": contact.get("role", ""),
                        "email": email,
                        "status": "pattern_guess",
                    })
                    break

        return verified_emails

    def process_company(self, company_name):
        """Full pipeline for a single company. Returns list of per-contact row dicts."""
        print(f"\n{'='*60}")
        print(f"Processing: {company_name}")
        print(f"{'='*60}")

        company_info = {
            "website": "",
            "domain": "",
            "company_type": "",
            "linkedin": "",
            "generic_email": "",
        }

        # --- Step 1: Gemini company analysis ---
        print("  Step 1: Gemini company analysis...")
        gemini_info = self.gemini.analyze_company(company_name)
        if gemini_info:
            company_info["company_type"] = gemini_info.get("company_type", "")
            print(f"  Company type: {company_info['company_type']}")
            print(f"  Target roles: {gemini_info.get('target_roles', [])}")
            if gemini_info.get("target_names"):
                print(f"  Known names: {gemini_info['target_names']}")

        # --- Step 2: Find company website ---
        print("  Step 2: Finding website...")
        hint_domain = gemini_info.get("likely_domain") if gemini_info else None
        base_url, domain = self._find_company_website(company_name, hint_domain)

        if base_url:
            company_info["website"] = base_url
            company_info["domain"] = domain
        elif hint_domain:
            company_info["website"] = f"https://{hint_domain}"
            company_info["domain"] = hint_domain
            base_url = company_info["website"]
            domain = hint_domain

        # --- Step 3: Crawl website ---
        crawl_data = None
        if base_url:
            print("  Step 3: Crawling website...")
            crawl_data = self.crawler.crawl(base_url)
            company_info["linkedin"] = crawl_data["social_links"].get("linkedin", "")
            print(f"  Crawl found: {len(crawl_data['emails'])} emails, {len(crawl_data['phones'])} phones")
        else:
            print("  Step 3: No website found, skipping crawl.")

        # --- Step 4: Web search for contacts ---
        print("  Step 4: Searching web for contacts...")
        search_emails, search_snippets = self._search_for_contacts(company_name, domain)
        crawl_emails = crawl_data["emails"] if crawl_data else set()
        all_discovered_emails = crawl_emails | search_emails
        print(f"  Total emails from search + crawl: {len(all_discovered_emails)}")

        # --- Step 5: Gemini contact extraction ---
        print("  Step 5: Gemini contact extraction...")
        page_texts = crawl_data["page_texts"] if crawl_data else []
        if search_snippets:
            page_texts.append({
                "url": "web_search_results",
                "text": " | ".join(search_snippets)[:2000],
            })

        gemini_contacts = self.gemini.extract_contacts_from_text(
            company_name, domain, page_texts, all_discovered_emails
        )

        contacts_to_verify = []
        if gemini_contacts:
            contacts_to_verify = gemini_contacts.get("contacts", [])
            company_info["generic_email"] = gemini_contacts.get("best_generic_email", "") or ""
            email_pattern = gemini_contacts.get("email_pattern")
            print(f"  Gemini found {len(contacts_to_verify)} contacts")
            if email_pattern:
                print(f"  Email pattern: {email_pattern}")

        # Add names from Gemini company analysis
        if gemini_info and gemini_info.get("target_names"):
            for name in gemini_info["target_names"]:
                if not any(c.get("name", "").lower() == name.lower() for c in contacts_to_verify):
                    contacts_to_verify.append({
                        "name": name,
                        "role": "From Gemini knowledge",
                        "email": None,
                        "confidence": "medium",
                    })

        # --- Step 6: Pattern generation + SMTP verification ---
        verified = []
        if contacts_to_verify and domain:
            print("  Step 6: Email pattern generation + SMTP verification...")
            verified = self._generate_and_verify_patterns(contacts_to_verify, domain)

        # --- Step 7: Build per-contact rows ---
        print("  Step 7: Compiling results...")

        # Build a name->email lookup from verified results
        verified_lookup = {v["name"].lower(): v for v in verified}

        # Collect all phone numbers from crawl
        crawl_phones = sorted(crawl_data["phones"]) if crawl_data else []

        # Filter junk emails
        junk_keywords = ["noreply", "no-reply", "mailer-daemon", "postmaster",
                         "example.com", "sentry", "wixpress", "cloudflare"]

        # Known valid TLDs for quick validation
        valid_tlds = {"com", "org", "net", "io", "co", "in", "ai", "dev", "tech",
                      "info", "biz", "edu", "gov", "co.in", "com.au", "co.uk", "com.br"}

        def is_junk(email):
            if any(x in email for x in junk_keywords):
                return True
            # Filter masked/censored emails like sxxx@, d***@, nxxx@
            local_part = email.split("@")[0]
            if re.match(r"^[a-zA-Z]x{2,}", local_part) or "***" in local_part or "..." in local_part:
                return True
            # Filter broken domains — if we know the company domain, email
            # domain must match exactly (catches zerodha.commobile, zerodha.com.reveal)
            email_domain = email.split("@")[1] if "@" in email else ""
            if domain and email_domain:
                # Allow exact match or common subdomains (mail.domain, hr.domain)
                if email_domain != domain and not email_domain.endswith("." + domain):
                    # Also allow well-known email providers
                    known_providers = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com"}
                    if email_domain not in known_providers:
                        return True
            elif email_domain:
                # No known domain — check TLD is valid
                tld = email_domain.rsplit(".", 1)[-1] if "." in email_domain else ""
                if len(tld) > 6 or not tld.isalpha():
                    return True
            return False

        # Build contact rows — one row per identified person
        rows = []
        seen_names = set()

        for contact in contacts_to_verify:
            name = contact.get("name", "").strip()
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())

            role = contact.get("role", "")
            email = ""
            confidence = contact.get("confidence", "")
            source = "gemini_analysis"

            # Check if we have a verified/pattern email for this person
            v = verified_lookup.get(name.lower())
            if v:
                email = v["email"]
                source = v["status"]
            elif contact.get("email") and not is_junk(contact["email"]):
                email = contact["email"]
                source = "website_scrape"

            row = {
                "company": company_name,
                "company_type": company_info["company_type"],
                "website": company_info["website"],
                "linkedin": company_info["linkedin"],
                "contact_name": name,
                "contact_role": role,
                "contact_email": email,
                "contact_phone": "",
                "email_source": source,
                "confidence": confidence,
            }
            rows.append(row)

        # Add a row for generic/company-wide email if found
        if company_info["generic_email"]:
            rows.append({
                "company": company_name,
                "company_type": company_info["company_type"],
                "website": company_info["website"],
                "linkedin": company_info["linkedin"],
                "contact_name": "(General)",
                "contact_role": "Generic Contact",
                "contact_email": company_info["generic_email"],
                "contact_phone": "",
                "email_source": "website_scrape",
                "confidence": "medium",
            })

        # Add unattributed emails (found on website/search but not linked to a person)
        attributed_emails = {r["contact_email"].lower() for r in rows if r["contact_email"]}
        unattributed = [
            e for e in all_discovered_emails
            if e.lower() not in attributed_emails and not is_junk(e)
        ]
        for email in sorted(unattributed):
            rows.append({
                "company": company_name,
                "company_type": company_info["company_type"],
                "website": company_info["website"],
                "linkedin": company_info["linkedin"],
                "contact_name": "(Unknown)",
                "contact_role": "Found on website/search",
                "contact_email": email,
                "contact_phone": "",
                "email_source": "website_scrape",
                "confidence": "low",
            })

        # Add company phone numbers as separate rows (these are general company
        # numbers from the website, NOT personal HR numbers)
        for phone in crawl_phones:
            rows.append({
                "company": company_name,
                "company_type": company_info["company_type"],
                "website": company_info["website"],
                "linkedin": company_info["linkedin"],
                "contact_name": "(Company)",
                "contact_role": "Company Phone (from website)",
                "contact_email": "",
                "contact_phone": phone,
                "email_source": "",
                "confidence": "low",
            })

        # If nothing found at all, add one empty row for the company
        if not rows:
            rows.append({
                "company": company_name,
                "company_type": company_info["company_type"],
                "website": company_info["website"],
                "linkedin": company_info["linkedin"],
                "contact_name": "",
                "contact_role": "",
                "contact_email": "",
                "contact_phone": "",
                "email_source": "",
                "confidence": "",
            })

        # Print summary
        named_contacts = [r for r in rows if r["contact_name"] not in ("", "(General)", "(Unknown)")]
        print(f"\n  --- Result for {company_name} ---")
        print(f"  Website:        {company_info['website']}")
        print(f"  Type:           {company_info['company_type']}")
        print(f"  LinkedIn:       {company_info['linkedin']}")
        print(f"  Contacts found: {len(named_contacts)} named, {len(rows)} total rows")
        for r in rows[:8]:
            tag = f"{r['contact_name']} ({r['contact_role']})"
            print(f"    {tag:40s} | {r['contact_email']:35s} | {r['contact_phone']}")

        return rows

    def run(self):
        companies = []
        with open(self.input_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            company_col = None
            for col in fieldnames:
                if col.strip().lower() in ["company", "company name", "company_name", "name", "companies"]:
                    company_col = col
                    break
            if not company_col:
                print(f"ERROR: Could not find a company name column in {self.input_csv}")
                print(f"  Found columns: {fieldnames}")
                print(f"  Expected one of: Company, Company Name, Name")
                sys.exit(1)

            for row in reader:
                name = row[company_col].strip()
                if name:
                    companies.append(name)

        print(f"\nLoaded {len(companies)} companies from {self.input_csv}")
        print(f"Output will be written to: {self.output_csv}")
        print(f"State file: {self.state_file}\n")

        all_rows = []
        for i, company in enumerate(companies):
            if company in self.state["processed"]:
                print(f"[{i+1}/{len(companies)}] Skipping {company} (already processed)")
                saved = self.state["processed"][company]
                if isinstance(saved, list):
                    all_rows.extend(saved)
                else:
                    all_rows.append(saved)
                continue

            print(f"[{i+1}/{len(companies)}]", end="")

            try:
                rows = self.process_company(company)
                all_rows.extend(rows)
                self.state["processed"][company] = rows
                self.state["last_index"] = i
                self._save_state()

            except KeyboardInterrupt:
                print("\n\nInterrupted! Progress saved. Re-run to resume.")
                self._save_state()
                break
            except Exception as e:
                print(f"\n  [FATAL ERROR] {company}: {e}")
                err_row = {"company": company, "contact_name": "", "contact_role": "",
                           "contact_email": "", "contact_phone": "", "error": str(e)}
                all_rows.append(err_row)
                self.state["processed"][company] = [err_row]
                self._save_state()

        self._write_output(all_rows)
        print(f"\nDone! Results written to: {self.output_csv}")

    def _write_output(self, rows):
        output_fields = [
            "company", "company_type", "website", "linkedin",
            "contact_name", "contact_role", "contact_email", "contact_phone",
            "email_source", "confidence",
        ]

        with open(self.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=output_fields, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Free Contact Discovery - Find HR/Founder contacts without paid APIs"
    )
    parser.add_argument("--input", "-i", required=True, help="Input CSV with company names")
    parser.add_argument("--output", "-o", default=None, help="Output CSV (default: input_enriched.csv)")
    parser.add_argument("--gemini-key", "-g", default=None, help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--skip-gemini", action="store_true", help="Skip all Gemini AI calls (test scraping only)")

    args = parser.parse_args()

    if args.skip_gemini:
        gemini_key = "skip"
    else:
        gemini_key = args.gemini_key or os.environ.get("GEMINI_API_KEY")
        if not gemini_key:
            print("ERROR: Gemini API key required.")
            print("  Pass via --gemini-key or set GEMINI_API_KEY env var")
            sys.exit(1)

    if not os.path.exists(args.input):
        print(f"ERROR: Input file not found: {args.input}")
        sys.exit(1)

    discovery = FreeContactDiscovery(
        gemini_api_key=gemini_key,
        input_csv=args.input,
        output_csv=args.output,
    )
    discovery.run()


if __name__ == "__main__":
    main()
