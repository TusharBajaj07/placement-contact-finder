import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time
from collections import deque
import json

class SmartPageDiscoverer:
    def __init__(self, max_pages=50, delay=1):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.max_pages = max_pages
        self.delay = delay
        
        # Track discovered pages
        self.unprocessed_urls = deque()
        self.processed_urls = set()
        self.failed_urls = set()
        self.found_emails = set()
        self.all_discovered_pages = []

    def is_same_domain(self, url1, url2):
        """Check if two URLs are from the same domain"""
        domain1 = urlparse(url1).netloc.lower()
        domain2 = urlparse(url2).netloc.lower()
        return domain1 == domain2

    def clean_url(self, url):
        """Clean URL by removing fragments and normalizing"""
        # Remove fragment (#section)
        url = url.split('#')[0]
        # Remove trailing slash for consistency
        if url.endswith('/') and url != url.split('//')[0] + '//':
            url = url.rstrip('/')
        return url

    def is_valid_page(self, url):
        """Check if URL points to a valid webpage (not file download)"""
        # Skip file downloads
        skip_extensions = [
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.zip', '.rar', '.tar', '.gz', '.mp3', '.mp4', '.avi',
            '.jpg', '.jpeg', '.png', '.gif', '.svg', '.css', '.js',
            '.xml', '.txt', '.csv'
        ]
        
        path = urlparse(url).path.lower()
        if any(path.endswith(ext) for ext in skip_extensions):
            return False
        
        # Skip mailto, tel, javascript links
        if url.startswith(('mailto:', 'tel:', 'javascript:', 'ftp:')):
            return False
            
        return True

    def discover_all_pages(self, starting_url):
        """Discover all pages by crawling internal links"""
        print(f"🕷️  Starting page discovery from: {starting_url}")
        
        base_domain = urlparse(starting_url).netloc
        self.unprocessed_urls.append(starting_url)
        
        pages_discovered = 0
        
        while self.unprocessed_urls and pages_discovered < self.max_pages:
            current_url = self.unprocessed_urls.popleft()
            
            # Skip if already processed
            if current_url in self.processed_urls:
                continue
                
            # Clean the URL
            current_url = self.clean_url(current_url)
            
            print(f"🔍 Discovering links from: {current_url}")
            
            try:
                response = self.session.get(current_url, timeout=10)
                
                if response.status_code == 200:
                    self.processed_urls.add(current_url)
                    self.all_discovered_pages.append(current_url)
                    pages_discovered += 1
                    
                    # Parse the page to find more links
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Find all links on this page
                    links_found = 0
                    for anchor in soup.find_all('a', href=True):
                        href = anchor['href'].strip()
                        
                        # Skip empty links
                        if not href:
                            continue
                        
                        # Convert relative links to absolute
                        absolute_url = urljoin(current_url, href)
                        absolute_url = self.clean_url(absolute_url)
                        
                        # Only process internal links from same domain
                        if (self.is_same_domain(absolute_url, starting_url) and 
                            self.is_valid_page(absolute_url) and
                            absolute_url not in self.processed_urls and
                            absolute_url not in self.unprocessed_urls):
                            
                            self.unprocessed_urls.append(absolute_url)
                            links_found += 1
                    
                    print(f"   ✅ Found {links_found} new internal links")
                    print(f"   📊 Total discovered pages: {pages_discovered}")
                    print(f"   🎯 Pages in queue: {len(self.unprocessed_urls)}")
                    
                else:
                    print(f"   ❌ Failed to access: {response.status_code}")
                    self.failed_urls.add(current_url)
                
            except Exception as e:
                print(f"   ❌ Error: {str(e)}")
                self.failed_urls.add(current_url)
            
            # Be respectful - add delay
            time.sleep(self.delay)
        
        print(f"\n✅ Page discovery complete!")
        print(f"   📄 Total pages discovered: {len(self.all_discovered_pages)}")
        print(f"   ❌ Failed pages: {len(self.failed_urls)}")
        
        return self.all_discovered_pages

    def extract_emails_from_page(self, url):
        """Extract all emails from a single page"""
        try:
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                # Email regex patterns
                email_patterns = [
                    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Standard emails
                    r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',  # Mailto links
                    r'["\']([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})["\']'  # Quoted emails
                ]
                
                page_emails = set()
                
                for pattern in email_patterns:
                    found = re.findall(pattern, response.text, re.IGNORECASE)
                    page_emails.update([email.lower() for email in found])
                
                # Filter out obvious fake emails
                valid_emails = set()
                for email in page_emails:
                    if not any(skip in email for skip in [
                        'example.com', 'test.com', 'placeholder', 'domain.com',
                        'yourname@', 'email@', 'name@', 'user@', 'sample@'
                    ]):
                        valid_emails.add(email)
                
                return valid_emails
            
        except Exception as e:
            print(f"   ❌ Error extracting emails from {url}: {e}")
        
        return set()

    def scan_all_discovered_pages(self):
        """Scan all discovered pages for emails"""
        print(f"\n📧 Scanning {len(self.all_discovered_pages)} discovered pages for emails...")
        print("="*70)
        
        for i, page_url in enumerate(self.all_discovered_pages, 1):
            print(f"📄 [{i}/{len(self.all_discovered_pages)}] Scanning: {page_url}")
            
            page_emails = self.extract_emails_from_page(page_url)
            
            if page_emails:
                new_emails = page_emails - self.found_emails
                if new_emails:
                    print(f"   ✅ Found {len(page_emails)} emails: {', '.join(sorted(page_emails))}")
                    self.found_emails.update(page_emails)
                else:
                    print(f"   🔄 Found {len(page_emails)} emails (already discovered)")
            else:
                print(f"   ❌ No emails found")
            
            time.sleep(self.delay)
        
        return self.found_emails

    def full_website_scan(self, starting_url):
        """Complete workflow: discover pages + extract emails"""
        print("🚀 STARTING COMPLETE WEBSITE SCAN")
        print("="*70)
        
        # Step 1: Discover all pages
        discovered_pages = self.discover_all_pages(starting_url)
        
        # Step 2: Scan all pages for emails
        all_emails = self.scan_all_discovered_pages()
        
        # Step 3: Generate results
        results = {
            'website': starting_url,
            'total_pages_discovered': len(discovered_pages),
            'total_emails_found': len(all_emails),
            'discovered_pages': discovered_pages,
            'all_emails': sorted(list(all_emails)),
            'failed_urls': list(self.failed_urls)
        }
        
        self.print_final_summary(results)
        return results

    def print_final_summary(self, results):
        """Print comprehensive scan summary"""
        print(f"\n" + "="*70)
        print(f"🎉 COMPLETE SCAN RESULTS")
        print(f"="*70)
        print(f"🌐 Website: {results['website']}")
        print(f"📄 Pages discovered: {results['total_pages_discovered']}")
        print(f"📧 Emails found: {results['total_emails_found']}")
        print(f"❌ Failed pages: {len(results['failed_urls'])}")
        
        if results['all_emails']:
            print(f"\n📧 ALL EMAILS DISCOVERED:")
            for email in results['all_emails']:
                print(f"   • {email}")
        
        print(f"\n📄 ALL PAGES DISCOVERED:")
        for i, page in enumerate(results['discovered_pages'], 1):
            print(f"   {i:2d}. {page}")

# Simplified functions for easy use
def discover_and_scan_website(url, max_pages=50):
    """Simple function to discover all pages and scan for emails"""
    scanner = SmartPageDiscoverer(max_pages=max_pages, delay=1)
    return scanner.full_website_scan(url)

def batch_scan_multiple_websites(urls, max_pages_per_site=30):
    """Scan multiple websites completely"""
    all_results = {}
    
    for i, url in enumerate(urls, 1):
        print(f"\n{'='*80}")
        print(f"SCANNING WEBSITE {i}/{len(urls)}: {url}")
        print(f"{'='*80}")
        
        try:
            results = discover_and_scan_website(url, max_pages_per_site)
            all_results[url] = results
        except Exception as e:
            print(f"❌ Failed to scan {url}: {e}")
            all_results[url] = {'error': str(e)}
        
        # Rest between websites
        if i < len(urls):
            print(f"\n⏳ Waiting 5 seconds before next website...")
            time.sleep(5)
    
    return all_results

def save_scan_results(results, filename='complete_website_scan.json'):
    """Save results to JSON file"""
    with open(filename, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 Results saved to {filename}")

# Usage Examples
if __name__ == "__main__":
    
    # Scan trupeer.ai completely
    print("🎯 SCANNING TRUPEER.AI")
    trupeer_results = discover_and_scan_website("https://www.uipath.com/", max_pages=30)
    
    # Save results
    save_scan_results(trupeer_results, 'trupeer_complete_scan.json')
    
    # Batch scan multiple sites
    websites = [
        "https://www.trupeer.ai/"
        
    
    ]
    
    batch_results = batch_scan_multiple_websites(websites, max_pages_per_site=20)
    save_scan_results(batch_results, 'batch_website_scan.json')
