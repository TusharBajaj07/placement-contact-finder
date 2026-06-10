"""
Re-attempt email finding for bounced emails.
Tries ALL pattern candidates, deeper web search, LinkedIn scraping, and Gemini.
"""
import csv
import re
import time
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from founder_email_finder import (
    generate_email_patterns, smtp_verify, web_search,
    crawl_website, clean_name, get_mx_host, is_catchall_domain,
    SEARCH_DELAY, CRAWL_DELAY, _call_gemini,
)

BOUNCED = [
    {"company": "Gram Vaani", "founder": "Vijay Sai Pratap", "domain": "gramvaani.org", "bounced": "vijay@gramvaani.org"},
    {"company": "Frontier Markets", "founder": "Ajaita Shah", "domain": "frontiermarkets.co", "bounced": "ajaita@frontiermarkets.co"},
    {"company": "Atom360", "founder": "Hla Hla Win", "domain": "atom360.io", "bounced": "hla@atom360.io"},
    {"company": "Project Tech4Dev", "founder": "Donald Lobo", "domain": "projecttech4dev.org", "bounced": "donald@projecttech4dev.org"},
    {"company": "CureBay", "founder": "Priyadarshi Mohapatra", "domain": "curebay.com", "bounced": "priyadarshi@curebay.com"},
    {"company": "Chikitsak", "founder": "Milind Naik", "domain": "chikitsak.com", "bounced": "milind@chikitsak.com"},
    {"company": "WeGoT (VenAqua)", "founder": "Abhiram Seth", "domain": "wegot.in", "bounced": "abhiram@wegot.in"},
    {"company": "Redcode Informatics", "founder": "Karthik Naralasetty", "domain": "redcodeinformatics.com", "bounced": "karthik@redcodeinformatics.com"},
    {"company": "Forus Health", "founder": "Dr. K. Chandrasekhar", "domain": "forushealth.com", "bounced": "chandrasekhar@forushealth.com"},
    {"company": "BharatRohan", "founder": "Amandeep Panwar", "domain": "bharatrohan.in", "bounced": "amandeeppanwar@bharatrohan.in"},
    {"company": "Tech4Good Labs", "founder": "Shemeer Babu", "domain": "tech4goodlabs.com", "bounced": "shemeer@tech4goodlabs.com"},
    {"company": "Innaumation Medical Devices", "founder": "Dr. Vishal U S Rao", "domain": "innaumation.com", "bounced": "vishal@innaumation.com"},
]

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")


def deep_search_email(company, founder, domain, bounced):
    """Aggressive multi-method search for correct email."""
    print(f"\n{'='*60}")
    print(f"  {company} | {founder} | {domain}")
    print(f"  Bounced: {bounced}")
    print(f"{'='*60}")

    first, last, middles = clean_name(founder)
    found_emails = set()
    best = None

    # ---- Method 1: Try ALL pattern candidates via SMTP ----
    print(f"  [1] Trying ALL pattern candidates...")
    candidates = generate_email_patterns(first, last, domain)
    for mid in middles:
        candidates.extend(generate_email_patterns(first, mid, domain))
        candidates.extend(generate_email_patterns(mid, last, domain))
    if middles:
        # Extra combos
        full = first + "".join(middles)
        candidates.append(f"{full.lower()}@{domain}")
        candidates.append(f"{full.lower()}.{last.lower()}@{domain}")
        # Try initials
        initials = "".join([m[0] for m in middles])
        candidates.append(f"{first.lower()}{initials.lower()}{last.lower()}@{domain}")
        candidates.append(f"{first.lower()}.{initials.lower()}.{last.lower()}@{domain}")

    # Remove the bounced one
    candidates = [c for c in dict.fromkeys(candidates) if c != bounced]
    print(f"    {len(candidates)} candidates (excluding bounced)")

    for email in candidates:
        status = smtp_verify(email)
        if status == "valid":
            print(f"    ** VALID: {email}")
            return email, "smtp_verified", "valid"
        elif status == "unknown":
            if not best:
                best = (email, "pattern_guess", "unknown")

    # ---- Method 2: Deep web search ----
    print(f"  [2] Deep web search...")
    queries = [
        f'"{founder}" email',
        f'"{founder}" "{company}" contact',
        f'"{founder}" @{domain}',
        f'"{company}" founder email contact',
        f'"{founder}" linkedin email',
        f'site:linkedin.com/in "{founder}" "{company}"',
        f'"{company}" "{domain}" email',
    ]
    for q in queries:
        time.sleep(SEARCH_DELAY)
        results = web_search(q, max_results=8)
        for r in results:
            text = r.get("snippet", "") + " " + r.get("title", "")
            emails = re.findall(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                text,
            )
            for e in emails:
                el = e.lower()
                if el == bounced:
                    continue
                # Filter junk
                if any(x in el for x in ["noreply", "no-reply", "example", "sentry", "wixpress", ".png", ".jpg"]):
                    continue
                found_emails.add(el)
                # Check if it's on the company domain
                if domain in el:
                    status = smtp_verify(el)
                    print(f"    Web found: {el} -> {status}")
                    if status == "valid":
                        return el, "web_search + smtp_verified", "valid"
                    elif status != "invalid" and not best:
                        best = (el, "web_search", status)

    # ---- Method 3: Crawl website deeper ----
    print(f"  [3] Deep website crawl...")
    site_emails, page_texts = crawl_website(f"https://{domain}", domain)
    for e in site_emails:
        if e == bounced:
            continue
        if any(x in e for x in ["noreply", "no-reply", "sentry", "wixpress"]):
            continue
        found_emails.add(e)
        local = e.split("@")[0]
        # Check if it matches the founder's name at all
        if first.lower() in local or (last and last.lower() in local):
            status = smtp_verify(e)
            print(f"    Site match: {e} -> {status}")
            if status == "valid":
                return e, "website + smtp_verified", "valid"
            elif status != "invalid" and not best:
                best = (e, "website_scrape", status)

    # Show all site emails for reference
    if site_emails:
        print(f"    All site emails: {', '.join(sorted(site_emails))}")

    # ---- Method 4: Try common alternative domains ----
    # Some companies use different email domains (gmail for business, etc.)
    print(f"  [4] Checking for Google Workspace / alternative patterns...")
    # Try info@, hello@, contact@ as fallback
    generic_patterns = [f"info@{domain}", f"hello@{domain}", f"contact@{domain}", f"team@{domain}"]
    for email in generic_patterns:
        if email == bounced:
            continue
        status = smtp_verify(email)
        if status == "valid":
            print(f"    Generic valid: {email}")
            found_emails.add(email)

    # ---- Method 5: Gemini AI ----
    if GEMINI_KEY and GEMINI_KEY != "skip":
        print(f"  [5] Asking Gemini AI...")
        prompt = f"""The email {bounced} for {founder} ({company}) bounced/failed.
Company domain: {domain}

I need their CORRECT email address. This person is the founder/CEO.

Other emails found on their website: {', '.join(sorted(site_emails)) if site_emails else 'none'}

Return ONLY a JSON object (no markdown):
{{
    "email": "their correct email" or null,
    "confidence": "high/medium/low",
    "reasoning": "why this is correct",
    "alternative_emails": ["other possibilities"]
}}

Think about: Do they use a different email domain? Is there a known personal email?
Common Indian startup patterns: first@domain, firstname@domain, first.last@domain"""
        result = _call_gemini(prompt, GEMINI_KEY)
        if result:
            if result.get("email"):
                gemini_email = result["email"].lower()
                if gemini_email != bounced:
                    status = smtp_verify(gemini_email)
                    print(f"    Gemini: {gemini_email} -> {status} ({result.get('confidence')})")
                    if status == "valid":
                        return gemini_email, "gemini + smtp_verified", "valid"
                    elif not best or result.get("confidence") == "high":
                        best = (gemini_email, f"gemini ({result.get('confidence')})", status)
            if result.get("alternative_emails"):
                for alt in result["alternative_emails"]:
                    alt = alt.lower()
                    if alt != bounced:
                        status = smtp_verify(alt)
                        print(f"    Gemini alt: {alt} -> {status}")
                        if status == "valid":
                            return alt, "gemini_alt + smtp_verified", "valid"

    # ---- Return best found or list alternatives ----
    if best:
        print(f"\n  BEST: {best[0]} [{best[1]}] ({best[2]})")
        return best

    # Return all found emails as alternatives
    alternatives = sorted(found_emails - {bounced})
    if alternatives:
        print(f"\n  NO MATCH - Alternatives: {', '.join(alternatives)}")
        return alternatives[0], "alternative_found", "unverified"

    print(f"\n  NO ALTERNATIVE FOUND")
    return "", "no_alternative", ""


def main():
    results = []
    for entry in BOUNCED:
        email, source, status = deep_search_email(
            entry["company"], entry["founder"], entry["domain"], entry["bounced"]
        )
        results.append({
            **entry,
            "new_email": email,
            "source": source,
            "status": status,
        })

    # Print summary table
    print(f"\n\n{'='*80}")
    print(f"  BOUNCED EMAIL FIX RESULTS")
    print(f"{'='*80}")
    print(f"{'#':<4} {'Company':<28} {'Founder':<22} {'New Email':<35} {'Status'}")
    print(f"{'-'*4} {'-'*28} {'-'*22} {'-'*35} {'-'*15}")
    for i, r in enumerate(results, 1):
        email = r["new_email"] or "(none found)"
        print(f"{i:<4} {r['company']:<28} {r['founder']:<22} {email:<35} {r['status']}")

    # Write to CSV
    with open("bounced_fixes.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company", "founder", "domain", "bounced", "new_email", "source", "status"
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"\nResults saved to: bounced_fixes.csv")
    fixed = sum(1 for r in results if r["new_email"])
    verified = sum(1 for r in results if r["status"] == "valid")
    print(f"  Fixed: {fixed}/12 | SMTP verified: {verified}/12")


if __name__ == "__main__":
    main()
