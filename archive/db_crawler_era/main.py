import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import sqlite3
import time
from datetime import datetime
from collections import deque
import json
import os

class ContactDiscoveryScript:
    def __init__(self, db_path='company_onboarding_poc.db', crawl_config=None):
        self.db_path = db_path
        
        # Default crawl configuration - can be overridden
        self.crawl_config = crawl_config or {
            'max_pages_per_site': 30,        # Maximum pages to crawl per website
            'max_depth': 3,                   # Maximum click depth from homepage
            'max_links_per_page': 20,         # Maximum links to extract per page
            'max_scan_time_per_site': 300,    # Maximum time (seconds) per website
            'request_delay': 0.5,             # Delay between requests (seconds)
            'page_timeout': 10,               # Timeout for each page request
            'early_stop_email_count': 15,     # Stop if this many emails found
            'priority_pages_only': False,     # Only scan priority pages (contact, about, etc.)
        }
        
        # Setup session for website crawling
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        print(f"📋 Crawl Configuration:")
        for key, value in self.crawl_config.items():
            print(f"   • {key}: {value}")
    
    def get_companies_ready_for_contact_discovery(self):
        """Get companies from Script 1 that need contact discovery"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, clean_website_url, categories, founder_name, votes_count
            FROM companies 
            WHERE contact_discovery_status = 'pending' 
            AND clean_website_url IS NOT NULL 
            AND clean_website_url != ''
            ORDER BY votes_count DESC
        """)
        
        companies = cursor.fetchall()
        conn.close()
        
        print(f"📋 Found {len(companies)} companies ready for contact discovery")
        return companies
    
    def check_page_exists(self, url):
        """Quick check if page exists"""
        try:
            response = self.session.head(url, timeout=5)
            return response.status_code < 400
        except:
            return False
    
    def get_priority_pages(self, base_url):
        """Get priority pages most likely to contain contacts"""
        priority_paths = [
            '/contact', '/contact-us', '/contacts', '/contact.html',
            '/about', '/about-us', '/about.html',
            '/team', '/people', '/staff', '/leadership', '/management',
            '/careers', '/jobs', '/hiring', '/join-us',
            '/executives', '/founders', '/board', '/advisory',
            '/company', '/organization'
        ]
        
        priority_pages = []
        
        print(f"   🎯 Checking priority contact pages...")
        for path in priority_paths:
            priority_url = urljoin(base_url, path)
            if self.check_page_exists(priority_url):
                priority_pages.append({
                    'url': priority_url,
                    'priority': 'high',
                    'type': 'contact_page'
                })
                print(f"      ✅ Found: {priority_url}")
        
        return priority_pages
    
    def is_contact_relevant_url(self, url):
        """Check if URL is likely to contain contact information"""
        url_lower = url.lower()
        
        # URLs likely to have contacts
        positive_keywords = [
            'contact', 'about', 'team', 'people', 'staff', 'leadership',
            'careers', 'jobs', 'management', 'executives', 'founders',
            'directors', 'board', 'advisory', 'investor', 'company'
        ]
        
        # URLs unlikely to have contacts
        negative_keywords = [
            'blog', 'news', 'article', 'post', 'product', 'service', 
            'pricing', 'feature', 'documentation', 'docs', 'help', 
            'support', 'faq', 'terms', 'privacy', 'download', 'demo', 
            'trial', 'login', 'register', 'signup', 'cart', 'checkout'
        ]
        
        # Check positive keywords
        has_positive = any(keyword in url_lower for keyword in positive_keywords)
        
        # Check negative keywords  
        has_negative = any(keyword in url_lower for keyword in negative_keywords)
        
        return has_positive or not has_negative
    
    def extract_links_from_page(self, url, domain):
        """Extract internal links from a page"""
        try:
            response = self.session.get(url, timeout=self.crawl_config['page_timeout'])
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            links = []
            
            for anchor in soup.find_all('a', href=True):
                href = anchor['href'].strip()
                if not href:
                    continue
                
                absolute_url = urljoin(url, href)
                absolute_url = absolute_url.split('#')[0]  # Remove fragments
                
                # Only internal links
                try:
                    url_domain = urlparse(absolute_url).netloc.lower()
                    if url_domain == domain and self.is_valid_page_url(absolute_url):
                        links.append(absolute_url)
                except:
                    continue
                
                # Limit links per page
                if len(links) >= self.crawl_config['max_links_per_page']:
                    break
            
            return list(set(links))  # Remove duplicates
            
        except Exception as e:
            print(f"   ❌ Error extracting links from {url}: {e}")
            return []
    
    def is_valid_page_url(self, url):
        """Check if URL is a valid webpage (not file download)"""
        skip_extensions = [
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.zip', '.rar', '.tar', '.gz', '.mp3', '.mp4', '.avi',
            '.jpg', '.jpeg', '.png', '.gif', '.svg', '.css', '.js',
            '.xml', '.txt', '.csv', '.json', '.rss'
        ]
        
        path = urlparse(url).path.lower()
        return not any(path.endswith(ext) for ext in skip_extensions)
    
    def discover_website_pages_smart(self, base_url, company_name):
        """Smart page discovery with configurable limits"""
        start_time = time.time()
        discovered_pages = []
        processed_urls = set()
        unprocessed_urls = deque()
        domain = urlparse(base_url).netloc.lower()
        
        print(f"   🕷️  Smart page discovery for {company_name}")
        
        # Phase 1: Priority pages (contact, about, team, etc.)
        priority_pages = self.get_priority_pages(base_url)
        discovered_pages.extend(priority_pages)
        processed_urls.update([page['url'] for page in priority_pages])
        
        # If priority_pages_only is True, skip general crawling
        if self.crawl_config['priority_pages_only']:
            print(f"   ✅ Priority-only mode: {len(discovered_pages)} pages")
            return discovered_pages
        
        # Phase 2: Homepage and immediate links
        print(f"   🔍 Crawling homepage and links...")
        unprocessed_urls.append(base_url)
        
        depth = 0
        pages_at_current_depth = [base_url]
        
        while (unprocessed_urls and 
               len(discovered_pages) < self.crawl_config['max_pages_per_site'] and
               depth < self.crawl_config['max_depth'] and
               time.time() - start_time < self.crawl_config['max_scan_time_per_site']):
            
            current_url = unprocessed_urls.popleft()
            
            if current_url in processed_urls:
                continue
            
            try:
                print(f"   📄 [{len(discovered_pages)+1}/{self.crawl_config['max_pages_per_site']}] Depth {depth}: {current_url}")
                
                # Get links from current page
                page_links = self.extract_links_from_page(current_url, domain)
                
                processed_urls.add(current_url)
                discovered_pages.append({
                    'url': current_url,
                    'depth': depth,
                    'priority': 'medium',
                    'type': 'crawled_page'
                })
                
                # Add new links for next depth level
                for link in page_links:
                    if (link not in processed_urls and 
                        link not in unprocessed_urls and
                        self.is_contact_relevant_url(link)):
                        unprocessed_urls.append(link)
                
                # Check if we should move to next depth
                if current_url == pages_at_current_depth[-1]:
                    depth += 1
                    pages_at_current_depth = list(unprocessed_urls)
                
                time.sleep(self.crawl_config['request_delay'])
                
            except Exception as e:
                print(f"   ❌ Error crawling {current_url}: {e}")
                continue
        
        print(f"   ✅ Discovery complete: {len(discovered_pages)} pages")
        return discovered_pages
    
    def extract_emails_from_page(self, url):
        """Extract all emails from a single page"""
        try:
            response = self.session.get(url, timeout=self.crawl_config['page_timeout'])
            
            if response.status_code == 200:
                # Multiple email regex patterns
                email_patterns = [
                    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Standard emails
                    r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',  # Mailto links
                    r'["\']([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})["\']',  # Quoted emails
                    r'(?:email|contact|reach)[\s:]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'  # Context emails
                ]
                
                page_emails = set()
                
                for pattern in email_patterns:
                    found = re.findall(pattern, response.text, re.IGNORECASE)
                    page_emails.update([email.lower().strip() for email in found])
                
                # Filter out obvious fake emails
                valid_emails = set()
                for email in page_emails:
                    if not any(skip in email for skip in [
                        'example.com', 'test.com', 'placeholder', 'domain.com',
                        'yourname@', 'email@', 'name@', 'user@', 'sample@',
                        'noreply@', 'no-reply@', 'donotreply@'
                    ]):
                        valid_emails.add(email)
                
                return valid_emails
            
        except Exception as e:
            print(f"   ❌ Error extracting emails from {url}: {e}")
        
        return set()
    
    def categorize_emails(self, emails, base_url):
        """Categorize emails by likely role/department"""
        categorized = []
        
        for email in emails:
            email_lower = email.lower()
            
            # Determine role based on email address
            if any(keyword in email_lower for keyword in ['hr', 'human.resources', 'people', 'talent', 'recruit']):
                role = 'HR/Recruiting'
                confidence = 0.9
            elif any(keyword in email_lower for keyword in ['career', 'job', 'hiring']):
                role = 'HR/Recruiting'
                confidence = 0.8
            elif any(keyword in email_lower for keyword in ['intern', 'internship', 'student']):
                role = 'Internship Coordinator'
                confidence = 0.95
            elif any(keyword in email_lower for keyword in ['ceo', 'founder', 'co-founder', 'president']):
                role = 'Executive'
                confidence = 0.9
            elif any(keyword in email_lower for keyword in ['contact', 'info', 'hello', 'general']):
                role = 'General Contact'
                confidence = 0.7
            elif any(keyword in email_lower for keyword in ['sales', 'business', 'partnerships']):
                role = 'Business Development'
                confidence = 0.6
            elif any(keyword in email_lower for keyword in ['support', 'help', 'customer']):
                role = 'Customer Support'
                confidence = 0.5
            else:
                role = 'Contact'
                confidence = 0.6
            
            # Generate contact name from email
            contact_name = email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
            
            categorized.append({
                'name': contact_name,
                'email': email,
                'role': role,
                'confidence': confidence,
                'source': 'Website Discovery'
            })
        
        # Sort by confidence (highest first)
        categorized.sort(key=lambda x: x['confidence'], reverse=True)
        
        return categorized
    
    def extract_all_contacts_from_website(self, base_url, company_name):
        """Extract all contacts from a website using smart crawling"""
        start_time = time.time()
        
        print(f"   🎯 Starting contact extraction for {company_name}")
        print(f"   ⚙️  Using limits: {self.crawl_config['max_pages_per_site']} pages, {self.crawl_config['max_depth']} depth")
        
        # Step 1: Smart page discovery
        discovered_pages = self.discover_website_pages_smart(base_url, company_name)
        
        if not discovered_pages:
            print(f"   ❌ No pages discovered for {company_name}")
            return [], 0, 0
        
        # Step 2: Extract emails from discovered pages
        all_emails = set()
        pages_with_emails = 0
        pages_scanned = 0
        
        print(f"   📧 Extracting emails from {len(discovered_pages)} pages...")
        
        for i, page_info in enumerate(discovered_pages, 1):
            page_url = page_info['url']
            priority = page_info.get('priority', 'medium')
            
            print(f"   📄 [{i}/{len(discovered_pages)}] {priority.upper()}: {page_url}")
            
            try:
                page_emails = self.extract_emails_from_page(page_url)
                pages_scanned += 1
                
                if page_emails:
                    new_emails = page_emails - all_emails
                    if new_emails:
                        print(f"      ✅ Found {len(page_emails)} emails: {', '.join(sorted(page_emails))}")
                        all_emails.update(page_emails)
                        pages_with_emails += 1
                    else:
                        print(f"      🔄 Found {len(page_emails)} emails (duplicates)")
                else:
                    print(f"      ❌ No emails found")
                
                time.sleep(self.crawl_config['request_delay'])
                
                # Early exit conditions
                if len(all_emails) >= self.crawl_config['early_stop_email_count']:
                    print(f"   ✅ Found sufficient contacts ({len(all_emails)}), stopping early")
                    break
                
                if time.time() - start_time > self.crawl_config['max_scan_time_per_site']:
                    print(f"   ⏰ Time limit reached, stopping scan")
                    break
                
            except Exception as e:
                print(f"      ❌ Error processing {page_url}: {e}")
                continue
        
        # Step 3: Categorize emails
        categorized_contacts = self.categorize_emails(all_emails, base_url)
        
        print(f"   🎉 Contact extraction complete!")
        print(f"      ⏱️  Time taken: {time.time() - start_time:.1f} seconds")
        print(f"      📄 Pages scanned: {pages_scanned}")
        print(f"      📧 Total unique emails found: {len(all_emails)}")
        print(f"      📊 Pages with emails: {pages_with_emails}")
        
        return categorized_contacts, pages_scanned, len(all_emails)
    
    def save_contacts_to_database(self, company_id, contacts, pages_scanned, emails_found):
        """Save discovered contacts to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        contacts_saved = 0
        
        for contact in contacts:
            try:
                # Check if contact already exists
                cursor.execute(
                    "SELECT id FROM contacts WHERE email = ? AND company_id = ?", 
                    (contact['email'], company_id)
                )
                
                if not cursor.fetchone():
                    cursor.execute('''
                        INSERT INTO contacts 
                        (name, email, role, company_id, source, confidence, discovery_date) 
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        contact['name'],
                        contact['email'],
                        contact['role'],
                        company_id,
                        contact['source'],
                        contact['confidence'],
                        datetime.now().isoformat()
                    ))
                    contacts_saved += 1
            
            except Exception as e:
                print(f"   ❌ Error saving contact {contact['email']}: {e}")
        
        # Update company status
        cursor.execute('''
            UPDATE companies 
            SET contact_discovery_status = ?, contact_discovery_date = ?, total_contacts_found = ?
            WHERE id = ?
        ''', ('completed', datetime.now().isoformat(), contacts_saved, company_id))
        
        # Log discovery attempt
        cursor.execute('''
            INSERT INTO contact_discovery_log 
            (company_id, pages_discovered, pages_scanned, emails_found, discovery_status, discovery_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (company_id, pages_scanned, pages_scanned, emails_found, 'success', datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        
        return contacts_saved
    
    def run_contact_discovery(self):
        """Main function to run contact discovery for all companies"""
        
        print("🚀 STARTING SCRIPT 2 - CONTACT DISCOVERY")
        print("="*80)
        print("Extracting contacts from company websites discovered in Script 1")
        print("="*80)
        
        # Get companies ready for contact discovery
        companies = self.get_companies_ready_for_contact_discovery()
        
        if not companies:
            print("❌ No companies ready for contact discovery!")
            print("   Make sure Script 1 has been run and found website URLs")
            return
        
        total_contacts_found = 0
        successful_discoveries = 0
        
        for i, (company_id, company_name, website_url, categories, founder_name, votes_count) in enumerate(companies, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(companies)}] Processing: {company_name}")
            print(f"Website: {website_url}")
            print(f"Categories: {categories}")
            print(f"Votes: {votes_count}")
            print(f"{'='*60}")
            
            try:
                # Extract contacts from website
                contacts, pages_scanned, emails_found = self.extract_all_contacts_from_website(
                    website_url, company_name
                )
                
                if contacts:
                    # Save contacts to database
                    contacts_saved = self.save_contacts_to_database(
                        company_id, contacts, pages_scanned, emails_found
                    )
                    
                    total_contacts_found += contacts_saved
                    successful_discoveries += 1
                    
                    print(f"✅ SUCCESS: Saved {contacts_saved} contacts for {company_name}")
                    
                    # Show contact summary
                    print(f"   📧 Contact Summary:")
                    role_counts = {}
                    for contact in contacts:
                        role = contact['role']
                        role_counts[role] = role_counts.get(role, 0) + 1
                    
                    for role, count in role_counts.items():
                        print(f"      • {role}: {count}")
                
                else:
                    print(f"❌ No contacts found for {company_name}")
                    
                    # Log failed discovery
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE companies 
                        SET contact_discovery_status = ?, contact_discovery_date = ?
                        WHERE id = ?
                    ''', ('no_contacts', datetime.now().isoformat(), company_id))
                    cursor.execute('''
                        INSERT INTO contact_discovery_log 
                        (company_id, pages_discovered, pages_scanned, emails_found, discovery_status, discovery_date)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (company_id, pages_scanned, 0, 0, 'no_contacts', datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
            
            except Exception as e:
                print(f"❌ ERROR processing {company_name}: {e}")
                
                # Log error
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE companies 
                    SET contact_discovery_status = ?, contact_discovery_date = ?
                    WHERE id = ?
                ''', ('error', datetime.now().isoformat(), company_id))
                cursor.execute('''
                    INSERT INTO contact_discovery_log 
                    (company_id, pages_discovered, pages_scanned, emails_found, discovery_status, error_message, discovery_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (company_id, 0, 0, 0, 'error', str(e), datetime.now().isoformat()))
                conn.commit()
                conn.close()
            
            # Rest between companies
            if i < len(companies):
                print(f"   😴 Resting 5 seconds before next company...")
                time.sleep(5)
        
        # Generate final report
        self.generate_final_report(companies, successful_discoveries, total_contacts_found)
    
    def generate_final_report(self, companies, successful_discoveries, total_contacts):
        """Generate final contact discovery report"""
        
        print(f"\n🎉 SCRIPT 2 COMPLETE - CONTACT DISCOVERY FINISHED!")
        print("="*80)
        print(f"📊 FINAL STATISTICS:")
        print(f"   📦 Companies processed: {len(companies)}")
        print(f"   ✅ Successful discoveries: {successful_discoveries}")
        print(f"   📧 Total contacts found: {total_contacts}")
        print(f"   📈 Success rate: {(successful_discoveries/len(companies)*100):.1f}%")
        print(f"   📈 Average contacts per company: {(total_contacts/successful_discoveries):.1f}" if successful_discoveries > 0 else "   📈 Average contacts per company: 0")
        
        # Generate detailed report
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get final statistics
        cursor.execute("""
            SELECT 
                c.name,
                c.clean_website_url,
                c.categories,
                c.votes_count,
                c.contact_discovery_status,
                c.total_contacts_found,
                COUNT(ct.id) as actual_contacts
            FROM companies c
            LEFT JOIN contacts ct ON c.id = ct.company_id
            WHERE c.contact_discovery_status IN ('completed', 'no_contacts', 'error')
            GROUP BY c.id
            ORDER BY c.votes_count DESC
        """)
        
        results = cursor.fetchall()
        
        # Get contact breakdown by role
        cursor.execute("""
            SELECT role, COUNT(*) as count
            FROM contacts
            GROUP BY role
            ORDER BY count DESC
        """)
        
        role_breakdown = cursor.fetchall()
        
        conn.close()
        
        # Save detailed report
        report = {
            'generation_date': datetime.now().isoformat(),
            'crawl_config': self.crawl_config,
            'summary': {
                'companies_processed': len(companies),
                'successful_discoveries': successful_discoveries,
                'total_contacts_found': total_contacts,
                'success_rate': f"{(successful_discoveries/len(companies)*100):.1f}%"
            },
            'contact_breakdown_by_role': dict(role_breakdown),
            'companies': []
        }
        
        for result in results:
            report['companies'].append({
                'name': result[0],
                'website': result[1],
                'categories': result[2],
                'votes': result[3],
                'discovery_status': result[4],
                'contacts_found': result[6]
            })
        
        with open('contact_discovery_final_report.json', 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n📁 Detailed report saved: contact_discovery_final_report.json")
        
        # Show contact breakdown
        if role_breakdown:
            print(f"\n👥 CONTACT BREAKDOWN BY ROLE:")
            for role, count in role_breakdown:
                print(f"   • {role}: {count}")
        
        # Show top companies with most contacts
        successful_companies = [r for r in results if r[6] > 0]
        if successful_companies:
            print(f"\n🏆 TOP COMPANIES BY CONTACTS FOUND:")
            for i, result in enumerate(successful_companies[:10], 1):
                print(f"   {i}. {result[0]}: {result[6]} contacts ({result[3]} votes)")

def run_script2_contact_discovery(
    max_pages_per_site=30,           # ⚙️  CONFIGURABLE: Maximum pages per website
    max_depth=3,                     # ⚙️  CONFIGURABLE: Maximum crawl depth  
    max_links_per_page=20,           # ⚙️  CONFIGURABLE: Links to extract per page
    max_scan_time_per_site=300,      # ⚙️  CONFIGURABLE: Time limit per site (seconds)
    request_delay=0.5,               # ⚙️  CONFIGURABLE: Delay between requests
    early_stop_email_count=15,       # ⚙️  CONFIGURABLE: Stop when this many emails found
    priority_pages_only=False,       # ⚙️  CONFIGURABLE: Only scan priority pages
    page_timeout=10                  # ⚙️  CONFIGURABLE: Timeout per page request
):
    """
    🚀 Main function to run Script 2 with configurable limits
    
    Parameters:
    - max_pages_per_site: Maximum pages to crawl per website (default: 30)
    - max_depth: Maximum click depth from homepage (default: 3)  
    - max_links_per_page: Maximum links to extract per page (default: 20)
    - max_scan_time_per_site: Maximum time per website in seconds (default: 300)
    - request_delay: Delay between requests in seconds (default: 0.5)
    - early_stop_email_count: Stop if this many emails found (default: 15)
    - priority_pages_only: Only scan contact/about/team pages (default: False)
    - page_timeout: Timeout for each page request (default: 10)
    """
    
    # Check if database exists
    db_path = 'company_onboarding_poc.db'
    if not os.path.exists(db_path):
        print("❌ Database not found!")
        print("   Please run Script 1 first to extract companies from Product Hunt")
        return
    
    # Create crawl configuration
    crawl_config = {
        'max_pages_per_site': max_pages_per_site,
        'max_depth': max_depth,
        'max_links_per_page': max_links_per_page,
        'max_scan_time_per_site': max_scan_time_per_site,
        'request_delay': request_delay,
        'page_timeout': page_timeout,
        'early_stop_email_count': early_stop_email_count,
        'priority_pages_only': priority_pages_only,
    }
    
    print("🚀 STARTING SCRIPT 2 - CONTACT DISCOVERY")
    print("="*80)
    print("📋 CONFIGURATION:")
    for key, value in crawl_config.items():
        print(f"   • {key}: {value}")
    print("="*80)
    
    # Run contact discovery
    discovery = ContactDiscoveryScript(db_path, crawl_config)
    discovery.run_contact_discovery()

if __name__ == "__main__":
    # 🎛️  CONFIGURE YOUR LIMITS HERE:
    
    # For FAST scanning (small websites):
    # run_script2_contact_discovery(
    #     max_pages_per_site=10,
    #     max_depth=2,
    #     priority_pages_only=True
    # )
    
    # For THOROUGH scanning (large websites):
    # run_script2_contact_discovery(
    #     max_pages_per_site=100,
    #     max_depth=4,
    #     max_scan_time_per_site=600,
    #     early_stop_email_count=25
    # )
    
    # For BALANCED scanning (default):
    run_script2_contact_discovery(
        max_pages_per_site=30,           # Crawl up to 30 pages per website
        max_depth=3,                     # Go 3 levels deep from homepage
        max_links_per_page=20,           # Extract up to 20 links per page
        max_scan_time_per_site=300,      # Spend max 5 minutes per website
        request_delay=0.5,               # Half second between requests
        early_stop_email_count=15,       # Stop if 15+ emails found
        priority_pages_only=False,       # Scan all relevant pages, not just priority
        page_timeout=10                  # 10 second timeout per page
    )
