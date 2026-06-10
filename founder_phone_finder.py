"""
Founder Phone Number Finder
----------------------------
Tries to find phone numbers of founders/CEOs from the internet.

Techniques:
1. Website crawling (contact, about, team pages)
2. DuckDuckGo web search
3. Gemini AI for extraction from scraped context
4. Regex-based Indian phone number extraction

Usage:
    python founder_phone_finder.py
"""

import csv
import re
import time
import json
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import OrderedDict

try:
    from ddgs import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False
    print("[WARN] ddgs not installed. pip install ddgs")

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    print("[WARN] google-generativeai not installed. pip install google-generativeai")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
INPUT_CSV = "first5_founder_emails.csv"
OUTPUT_CSV = "founder_phones.csv"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

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

# Indian phone patterns
PHONE_PATTERNS = [
    # +91 followed by 10 digits (with optional spaces/dashes)
    r'\+91[\s\-.]?\d[\s\-.]?\d{4}[\s\-.]?\d{5}',
    r'\+91[\s\-.]?\d{10}',
    r'\+91[\s\-.]?\d{5}[\s\-.]?\d{5}',
    # 0XX-XXXXXXXX landline
    r'0\d{2,4}[\s\-.]?\d{6,8}',
    # 10-digit mobile starting with 6-9
    r'(?<!\d)[6-9]\d{9}(?!\d)',
    # Formatted: XXXXX XXXXX or XXXXX-XXXXX
    r'(?<!\d)[6-9]\d{4}[\s\-]\d{5}(?!\d)',
    # International format
    r'(?:91|0091)[\s\-.]?[6-9]\d{9}',
    # General international
    r'\+\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,4}',
]

PRIORITY_PATHS = [
    "/contact", "/contact-us", "/about", "/about-us",
    "/team", "/our-team", "/people", "/leadership",
    "/connect", "/reach-us", "/get-in-touch",
]


# ---------------------------------------------------------------------------
# Phone extraction
# ---------------------------------------------------------------------------
def extract_phones(text):
    """Extract all phone-number-like strings from text."""
    phones = set()
    for pattern in PHONE_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            cleaned = re.sub(r'[\s\-.]', '', m)
            # Filter out obvious non-phones (too short/long, all same digit)
            digits_only = re.sub(r'\D', '', cleaned)
            if len(digits_only) < 10 or len(digits_only) > 13:
                continue
            if len(set(digits_only)) <= 2:  # e.g. 0000000000
                continue
            phones.add(cleaned)
    return list(phones)


def normalize_phone(phone):
    """Normalize to a consistent format."""
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('91') and len(digits) == 12:
        return f"+91-{digits[2:7]}-{digits[7:]}"
    if digits.startswith('0') and len(digits) == 11:
        return digits  # landline
    if len(digits) == 10 and digits[0] in '6789':
        return f"+91-{digits[:5]}-{digits[5:]}"
    return phone


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------
def web_search(query, max_results=5):
    if not HAS_DDGS:
        return []
    try:
        ddgs = DDGS()
        raw = ddgs.text(query, max_results=max_results)
        return [{"title": r.get("title", ""), "url": r.get("href", ""),
                 "snippet": r.get("body", "")} for r in raw]
    except Exception as e:
        print(f"    [Search Error] {e}")
        return []


# ---------------------------------------------------------------------------
# Website crawler
# ---------------------------------------------------------------------------
def crawl_for_phones(domain):
    """Crawl a website's key pages and extract phone numbers + page text."""
    session = requests.Session()
    session.headers.update(HEADERS)
    all_phones = []
    all_text = []

    base_url = f"https://{domain}"
    urls = [base_url] + [urljoin(base_url, p) for p in PRIORITY_PATHS]
    crawled = set()

    for url in urls[:12]:
        norm = url.rstrip("/").lower()
        if norm in crawled:
            continue
        crawled.add(norm)
        try:
            time.sleep(CRAWL_DELAY)
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                continue
            ct = resp.headers.get("Content-Type", "")
            if "text/html" not in ct:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(separator=" ", strip=True)

            # Extract phones from raw HTML (catches tel: links etc)
            phones_from_html = extract_phones(resp.text)
            phones_from_text = extract_phones(text)
            found = list(set(phones_from_html + phones_from_text))

            if found:
                print(f"    [Crawl] {url} -> found {len(found)} number(s)")
                all_phones.extend(found)

            # Also check for tel: links specifically
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("tel:"):
                    num = href.replace("tel:", "").strip()
                    num = re.sub(r'[\s\-.]', '', num)
                    if len(re.sub(r'\D', '', num)) >= 10:
                        all_phones.append(num)
                        print(f"    [Crawl] tel: link found -> {num}")

            # Save text from relevant pages
            if any(kw in url.lower() for kw in
                   ["contact", "about", "team", "people", "founder", "leadership", "connect"]):
                all_text.append({"url": url, "text": text[:3000]})

            # Discover more links
            if len(crawled) <= 4:
                for a in soup.find_all("a", href=True):
                    txt = a.get_text(strip=True).lower()
                    href_l = a["href"].lower()
                    if any(kw in txt or kw in href_l for kw in
                           ["contact", "team", "about", "founder", "connect", "reach"]):
                        full = urljoin(base_url, a["href"])
                        if urlparse(full).netloc.replace("www.", "") == domain:
                            if full.rstrip("/").lower() not in crawled:
                                urls.append(full)

        except Exception:
            continue

    return list(set(all_phones)), all_text


# ---------------------------------------------------------------------------
# Gemini AI
# ---------------------------------------------------------------------------
def gemini_find_phone(person_name, role, company, domain, page_texts, search_snippets):
    if not HAS_GEMINI or not GEMINI_API_KEY:
        return None

    context = ""
    if page_texts:
        for pt in page_texts[:3]:
            context += f"\n--- {pt['url']} ---\n{pt['text'][:1500]}\n"
    if search_snippets:
        context += "\n--- Web Search Results ---\n"
        for s in search_snippets[:5]:
            context += f"Title: {s['title']}\nSnippet: {s['snippet']}\nURL: {s['url']}\n\n"

    prompt = f"""Find the phone number or contact number of {person_name} ({role}) at {company}.
Company domain: {domain}

Here is context from their website and web search results:
{context[:5000] if context else "No context available."}

Return ONLY a JSON object (no markdown, no code blocks):
{{
    "phone_numbers": ["list of phone numbers found"] or [],
    "company_phone": "main company phone if found" or null,
    "personal_phone": "personal/direct phone if found" or null,
    "source": "where you found it (website, search, knowledge)",
    "confidence": "high/medium/low",
    "notes": "any relevant context"
}}

Focus on Indian phone numbers (+91). Include both personal and company numbers if available.
If you find numbers in the context above, extract them. If not, use your knowledge."""

    for attempt in range(3):
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(prompt)
            text = response.text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            return json.loads(text)
        except json.JSONDecodeError:
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


# ---------------------------------------------------------------------------
# Process one person
# ---------------------------------------------------------------------------
def process_person(row):
    company = row.get("Company Name", "").strip()
    founder = row.get("Founder", "").strip()
    role = row.get("Role", "").strip()
    domain = row.get("Website Domain", row.get("Domain", "")).strip()
    email = row.get("Email", "").strip()

    print(f"\n{'='*60}")
    print(f"  {founder} | {role} | {company}")
    print(f"  Domain: {domain} | Email: {email}")
    print(f"{'='*60}")

    all_phones = []
    phone_sources = {}
    search_snippets = []
    page_texts = []

    # ----- Step 1: Crawl website -----
    if domain:
        print(f"  Step 1: Crawling {domain}...")
        site_phones, page_texts = crawl_for_phones(domain)
        for p in site_phones:
            np = normalize_phone(p)
            all_phones.append(np)
            phone_sources[np] = "website"
        if site_phones:
            print(f"    Found {len(site_phones)} number(s) from website")
        else:
            print(f"    No numbers found on website")

    # ----- Step 2: Web search -----
    print(f"  Step 2: Searching the web...")
    queries = [
        f'"{founder}" "{company}" phone number',
        f'"{founder}" "{company}" contact number',
        f'"{company}" phone number contact',
    ]
    if domain:
        queries.append(f'site:{domain} phone OR contact OR call')

    for q in queries:
        time.sleep(SEARCH_DELAY)
        results = web_search(q, max_results=5)
        search_snippets.extend(results)
        for r in results:
            text = r.get("snippet", "") + " " + r.get("title", "")
            phones = extract_phones(text)
            for p in phones:
                np = normalize_phone(p)
                if np not in phone_sources:
                    all_phones.append(np)
                    phone_sources[np] = f"web_search ({r.get('url', '')[:50]})"
                    print(f"    [Search] Found: {np}")

    # ----- Step 3: Search LinkedIn / directories -----
    print(f"  Step 3: Searching directories...")
    dir_queries = [
        f'"{founder}" phone OR mobile OR cell',
        f'"{company}" "{founder}" "contact"',
    ]
    for q in dir_queries:
        time.sleep(SEARCH_DELAY)
        results = web_search(q, max_results=5)
        search_snippets.extend(results)
        for r in results:
            text = r.get("snippet", "") + " " + r.get("title", "")
            phones = extract_phones(text)
            for p in phones:
                np = normalize_phone(p)
                if np not in phone_sources:
                    all_phones.append(np)
                    phone_sources[np] = f"directory ({r.get('url', '')[:50]})"
                    print(f"    [Directory] Found: {np}")

    # ----- Step 4: Gemini AI -----
    print(f"  Step 4: Asking Gemini AI...")
    gemini_result = gemini_find_phone(
        founder, role, company, domain, page_texts, search_snippets
    )
    gemini_phones = []
    gemini_notes = ""
    if gemini_result:
        gemini_notes = gemini_result.get("notes", "")
        confidence = gemini_result.get("confidence", "unknown")
        print(f"    [Gemini] Confidence: {confidence}")
        if gemini_result.get("notes"):
            print(f"    [Gemini] Notes: {gemini_notes[:100]}")

        for key in ["personal_phone", "company_phone"]:
            val = gemini_result.get(key)
            if val:
                np = normalize_phone(val)
                if np not in phone_sources:
                    all_phones.append(np)
                    phone_sources[np] = f"gemini_{key} ({confidence})"
                    gemini_phones.append(np)
                    print(f"    [Gemini] {key}: {np}")

        for p in gemini_result.get("phone_numbers", []):
            np = normalize_phone(p)
            if np not in phone_sources:
                all_phones.append(np)
                phone_sources[np] = f"gemini ({confidence})"
                gemini_phones.append(np)
                print(f"    [Gemini] Found: {np}")

    # ----- Dedupe and pick best -----
    unique_phones = list(OrderedDict.fromkeys(all_phones))

    # Categorize
    personal_phone = ""
    company_phone = ""
    for p in unique_phones:
        src = phone_sources.get(p, "")
        if "personal" in src:
            personal_phone = p
        elif not company_phone:
            company_phone = p

    if not personal_phone and unique_phones:
        personal_phone = unique_phones[0]

    best_phone = personal_phone or company_phone or ""
    best_source = phone_sources.get(best_phone, "not_found")

    print(f"\n  RESULT: {best_phone or 'NOT FOUND'} [{best_source}]")
    if len(unique_phones) > 1:
        print(f"  ALL:    {', '.join(unique_phones)}")

    return {
        "Company Name": company,
        "Founder": founder,
        "Role": role,
        "Domain": domain,
        "Email": email,
        "Best Phone": best_phone,
        "Phone Source": best_source,
        "Company Phone": company_phone,
        "All Phones Found": "; ".join(unique_phones),
        "Gemini Notes": gemini_notes[:200] if gemini_notes else "",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found")
        return

    with open(INPUT_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} people from {INPUT_CSV}")
    print(f"Gemini: {'enabled' if HAS_GEMINI and GEMINI_API_KEY else 'disabled'}")
    print(f"DuckDuckGo: {'enabled' if HAS_DDGS else 'disabled'}")

    results = []
    for i, row in enumerate(rows):
        print(f"\n[{i+1}/{len(rows)}]", end="")
        try:
            result = process_person(row)
            results.append(result)
        except KeyboardInterrupt:
            print("\nInterrupted!")
            break
        except Exception as e:
            print(f"\n  [ERROR] {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "Company Name": row.get("Company Name", ""),
                "Founder": row.get("Founder", ""),
                "Role": row.get("Role", ""),
                "Domain": row.get("Website Domain", ""),
                "Email": row.get("Email", ""),
                "Best Phone": "",
                "Phone Source": f"error: {e}",
                "Company Phone": "",
                "All Phones Found": "",
                "Gemini Notes": "",
            })

    # Write output
    fields = [
        "Company Name", "Founder", "Role", "Domain", "Email",
        "Best Phone", "Phone Source", "Company Phone",
        "All Phones Found", "Gemini Notes",
    ]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"\n{'='*60}")
    print(f"DONE! Results -> {OUTPUT_CSV}")
    print(f"{'='*60}")
    found = sum(1 for r in results if r.get("Best Phone"))
    print(f"  Total people:    {len(results)}")
    print(f"  Phones found:    {found}")
    print(f"  Success rate:    {found}/{len(results)}")


if __name__ == "__main__":
    main()
