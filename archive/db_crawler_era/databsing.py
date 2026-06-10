import requests
import json
import sqlite3
import time
from datetime import datetime
import os
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

class ProductHuntCompanyExtractorFixed:
    def __init__(self, api_token):
        self.api_token = api_token
        self.base_url = "https://api.producthunt.com/v2/api/graphql"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Company-Onboarding-Bot/1.0"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
        # For fallback website extraction
        self.scraping_session = requests.Session()
        self.scraping_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def setup_database(self):
        """FIXED: Setup database with proper schema handling"""
        db_path = os.path.join(os.getcwd(), 'company_onboarding_poc.db')
        print(f"📂 Database: {db_path}")
        
        # Check if database exists and has correct schema
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # Check if companies table exists and has url_category column
                cursor.execute("PRAGMA table_info(companies)")
                columns = [column[1] for column in cursor.fetchall()]
                
                if 'url_category' not in columns:
                    print("⚠️  Database exists but missing url_category column. Recreating...")
                    conn.close()
                    os.remove(db_path)
                    return self.create_fresh_database(db_path)
                else:
                    print("✅ Database exists with correct schema")
                    conn.close()
                    return db_path
                    
            except Exception as e:
                print(f"⚠️  Database error: {e}. Recreating...")
                conn.close()
                os.remove(db_path)
                return self.create_fresh_database(db_path)
        else:
            print("📝 Creating fresh database...")
            return self.create_fresh_database(db_path)

    def create_fresh_database(self, db_path):
        """Create a fresh database with all required tables and columns"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create companies table with ALL required columns
        cursor.execute('''
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            website TEXT,
            original_ph_url TEXT,
            clean_website_url TEXT,
            status TEXT DEFAULT 'New',
            source TEXT,
            description TEXT,
            tagline TEXT,
            founder_name TEXT,
            categories TEXT,
            votes_count INTEGER DEFAULT 0,
            comments_count INTEGER DEFAULT 0,
            featured_date TEXT,
            contact_discovery_status TEXT DEFAULT 'pending',
            contact_discovery_date TEXT,
            total_contacts_found INTEGER DEFAULT 0,
            created_date TEXT,
            updated_date TEXT,
            extraction_method TEXT,
            url_category TEXT
        );
        ''')
        
        # Create contacts table
        cursor.execute('''
        CREATE TABLE contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            role TEXT,
            email TEXT,
            company_id INTEGER,
            source TEXT,
            confidence REAL DEFAULT 0.5,
            page_found TEXT,
            discovery_date TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        ''')
        
        # Create contact_discovery_log table
        cursor.execute('''
        CREATE TABLE contact_discovery_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            pages_discovered INTEGER DEFAULT 0,
            pages_scanned INTEGER DEFAULT 0,
            emails_found INTEGER DEFAULT 0,
            discovery_status TEXT,
            error_message TEXT,
            discovery_date TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        ''')
        
        # Create other tables
        cursor.execute('''
        CREATE TABLE outreach_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            email_sent BOOLEAN,
            response_received BOOLEAN,
            sent_date TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        ''')
        
        cursor.execute('''
        CREATE TABLE verification_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            verification_score INTEGER,
            flags TEXT,
            verified_date TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        ''')
        
        conn.commit()
        conn.close()
        print("✅ Fresh database created with all required columns!")
        return db_path

    def categorize_url(self, url):
        """Categorize URL as app_store, promotional, or company_website"""
        if not url:
            return "invalid"
        
        url_lower = url.lower()
        
        # App store patterns
        app_store_patterns = [
            r"play\.google\.com/store/apps/.*",
            r"apps\.apple\.com/.*/app/.*",
            r"itunes\.apple\.com/.*",
            r"appstore\.com/.*"
        ]
        
        for pattern in app_store_patterns:
            if re.search(pattern, url_lower):
                return "app_store"
        
        # Promotional/social domains
        promotional_domains = [
            "lu.ma", "luma.com",
            "producthunt.com", 
            "linkedin.com", 
            "twitter.com", "x.com",
            "facebook.com", 
            "instagram.com",
            "youtube.com",
            "github.com",
            "medium.com",
            "discord.com",
            "telegram.org"
        ]
        
        for domain in promotional_domains:
            if domain in url_lower:
                return "promotional"
        
        return "company_website"

    def is_valid_company_website(self, url):
        """Better validation of company websites"""
        if not url or not url.startswith('http'):
            return False
        
        # Blacklist of non-company URLs
        blacklist_domains = [
            # App stores
            'play.google.com', 'apps.apple.com', 'itunes.apple.com', 'appstore.com',
            # Tech giants (unless it's actually their product)
            'apple.com', 'google.com', 'microsoft.com', 'amazon.com', 'facebook.com',
            # Social/promotional platforms
            'lu.ma', 'luma.com', 'producthunt.com', 'linkedin.com', 'twitter.com', 
            'x.com', 'instagram.com', 'youtube.com', 'github.com', 'medium.com',
            'discord.com', 'telegram.org', 'reddit.com', 'tiktok.com',
            # Generic/system domains
            'localhost', 'example.com', 'test.com', 'domain.com'
        ]
        
        url_lower = url.lower()
        domain = urlparse(url_lower).netloc.lower()
        
        # Remove www prefix for checking
        if domain.startswith('www.'):
            domain = domain[4:]
        
        return not any(blocked in domain for blocked in blacklist_domains)

    def extract_company_website_from_app_store(self, app_store_url):
        """Extract actual company website from app store page"""
        try:
            print(f"   🏪 App store detected, extracting company info from: {app_store_url}")
            
            response = self.scraping_session.get(app_store_url, timeout=10)
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Google Play Store patterns
            if 'play.google.com' in app_store_url:
                # Look for developer website links
                website_links = soup.find_all('a', href=True)
                for link in website_links:
                    href = link.get('href')
                    link_text = link.get_text().lower()
                    
                    if (href and href.startswith('http') and 
                        ('developer' in link_text or 'website' in link_text or 'visit' in link_text) and
                        self.is_valid_company_website(href)):
                        print(f"   ✅ Found company website from Play Store: {href}")
                        return href
                
                # Look for developer email and extract domain
                email_pattern = r'[a-zA-Z0-9._%+-]+@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
                emails = re.findall(email_pattern, response.text)
                for domain in emails:
                    if self.is_valid_company_website(f"https://{domain}"):
                        potential_site = f"https://{domain}"
                        if self.test_url_exists(potential_site):
                            print(f"   ✅ Found via email domain: {potential_site}")
                            return potential_site
            
            # Apple App Store patterns  
            elif 'apps.apple.com' in app_store_url or 'itunes.apple.com' in app_store_url:
                # Look for developer website links (NOT Apple's links)
                website_links = soup.find_all('a', href=True)
                for link in website_links:
                    href = link.get('href')
                    
                    if (href and href.startswith('http') and 
                        'apple.com' not in href and 
                        'itunes.com' not in href and
                        'apps.apple.com' not in href and
                        self.is_valid_company_website(href)):
                        
                        if not any(skip in href.lower() for skip in ['support', 'legal', 'privacy', 'terms']):
                            print(f"   ✅ Found company website from App Store: {href}")
                            return href
                
                # Look for URLs in app description
                description_divs = soup.find_all(['div', 'p'], class_=re.compile(r'description|content'))
                for div in description_divs:
                    text = div.get_text()
                    url_pattern = r'https?://(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
                    urls = re.findall(url_pattern, text)
                    for url_match in urls:
                        full_url = f"https://{url_match}"
                        if self.is_valid_company_website(full_url) and self.test_url_exists(full_url):
                            print(f"   ✅ Found via app description: {full_url}")
                            return full_url
            
            return None
            
        except Exception as e:
            print(f"   ❌ Error extracting from app store: {e}")
            return None

    def find_alternative_company_website(self, product_name, makers_info):
        """Find alternative company website with better patterns"""
        try:
            print(f"   🔍 Searching for alternative website for: {product_name}")
            
            # Clean product name
            clean_name = re.sub(r'[^a-zA-Z0-9]', '', product_name.lower())
            clean_name = re.sub(r'\b(app|apps|mobile|ios|android|free|pro|plus)\b', '', clean_name)
            
            if len(clean_name) >= 3:
                potential_domains = [
                    f"https://{clean_name}.com",
                    f"https://{clean_name}.io", 
                    f"https://{clean_name}.ai",
                    f"https://{clean_name}.co",
                    f"https://www.{clean_name}.com",
                    f"https://get{clean_name}.com",
                    f"https://try{clean_name}.com",
                    f"https://{clean_name}app.com"
                ]
                
                for domain in potential_domains:
                    if self.test_url_exists(domain):
                        print(f"   ✅ Found via product name: {domain}")
                        return domain
            
            # Try maker information
            if makers_info:
                for maker in makers_info:
                    maker_name = maker.get('name', '')
                    maker_username = maker.get('username', '')
                    
                    if maker_name:
                        clean_maker = re.sub(r'[^a-zA-Z0-9]', '', maker_name.lower())
                        if len(clean_maker) >= 3:
                            maker_domains = [
                                f"https://{clean_maker}.com",
                                f"https://{clean_maker}.io",
                                f"https://www.{clean_maker}.com"
                            ]
                            
                            for domain in maker_domains:
                                if self.test_url_exists(domain):
                                    print(f"   ✅ Found via maker name: {domain}")
                                    return domain
                    
                    if maker_username:
                        clean_username = re.sub(r'[^a-zA-Z0-9]', '', maker_username.lower())
                        if len(clean_username) >= 3:
                            username_domains = [
                                f"https://{clean_username}.com",
                                f"https://{clean_username}.io"
                            ]
                            
                            for domain in username_domains:
                                if self.test_url_exists(domain):
                                    print(f"   ✅ Found via username: {domain}")
                                    return domain
            
            return None
            
        except Exception as e:
            print(f"   ❌ Error in alternative search: {e}")
            return None

    def test_url_exists(self, url):
        """Better URL existence testing"""
        try:
            response = self.scraping_session.head(url, timeout=5)
            if response.status_code < 400:
                return True
            
            response = self.scraping_session.get(url, timeout=5)
            return response.status_code < 400
            
        except:
            return False

    def extract_website_with_selenium_enhanced(self, ph_url, product_name, makers_info):
        """Enhanced Selenium extraction with app store handling"""
        driver = self.setup_selenium_driver()
        if not driver:
            return None, "selenium_failed"
            
        try:
            print(f"   🔍 Loading page with Selenium: {ph_url}")
            driver.get(ph_url)
            
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Try to find the Visit button
            visit_selectors = [
                'button[data-sentry-component="VisitButton"]',
                'a[data-test="visit-website-button"]',
                'button:contains("Visit")',
                'a[href*="visit"]'
            ]
            
            visit_button = None
            for selector in visit_selectors:
                try:
                    if 'contains' in selector:
                        visit_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Visit')]")
                    else:
                        visit_button = driver.find_element(By.CSS_SELECTOR, selector)
                    if visit_button:
                        break
                except NoSuchElementException:
                    continue
            
            if visit_button:
                print("   ✅ Found Visit button, clicking...")
                original_window = driver.current_window_handle
                
                driver.execute_script("arguments[0].click();", visit_button)
                time.sleep(3)
                
                if len(driver.window_handles) > 1:
                    for window_handle in driver.window_handles:
                        if window_handle != original_window:
                            driver.switch_to.window(window_handle)
                            break
                    time.sleep(5)
                    final_url = driver.current_url
                else:
                    time.sleep(5)
                    final_url = driver.current_url
                
                print(f"   🔗 Visit button leads to: {final_url}")
                
                url_category = self.categorize_url(final_url)
                print(f"   🏷️  URL category: {url_category}")
                
                if url_category == "company_website":
                    clean_url = self.clean_url(final_url)
                    return clean_url, "selenium_direct"
                
                elif url_category == "app_store":
                    company_website = self.extract_company_website_from_app_store(final_url)
                    if company_website:
                        return company_website, "selenium_app_store"
                    
                    alternative_website = self.find_alternative_company_website(product_name, makers_info)
                    if alternative_website:
                        return alternative_website, "selenium_alternative"
                
                elif url_category == "promotional":
                    print(f"   ⚠️  Got promotional link, searching for alternatives...")
                    alternative_website = self.find_alternative_company_website(product_name, makers_info)
                    if alternative_website:
                        return alternative_website, "selenium_alternative"
            
            # Final fallback
            alternative_website = self.find_alternative_company_website(product_name, makers_info)
            if alternative_website:
                return alternative_website, "selenium_final_fallback"
            
            return None, "selenium_no_valid_url"
            
        except Exception as e:
            print(f"   ❌ Error with Selenium extraction: {e}")
            return None, "selenium_error"
        finally:
            if driver:
                driver.quit()

    def setup_selenium_driver(self):
        """Setup Chrome driver with proper options"""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        try:
            driver = webdriver.Chrome(options=chrome_options)
            return driver
        except Exception as e:
            print(f"❌ Error setting up Chrome driver: {e}")
            return None

    def clean_url(self, url):
        """Clean URL to get base domain"""
        try:
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}".lower()
        except:
            return url

    def get_posts_from_product_hunt(self):
        """Get posts from Product Hunt API"""
        query = """
        query GetPosts {
          posts(first: 20) {
            edges {
              node {
                id
                name
                tagline
                description
                url
                votesCount
                commentsCount
                createdAt
                featuredAt
                makers {
                  name
                  username
                }
                topics {
                  edges {
                    node {
                      name
                    }
                  }
                }
              }
            }
          }
        }
        """
        
        payload = {"query": query}
        response = self.session.post(self.base_url, json=payload, timeout=30)
        
        if response.status_code == 200:
            try:
                data = response.json()
                if 'errors' in data:
                    print("❌ GraphQL errors:", data['errors'])
                    return None
                return data
            except json.JSONDecodeError as e:
                print("❌ JSON decode error:", e)
                return None
        else:
            print(f"❌ Request failed with status: {response.status_code}")
            return None

    def extract_and_save_companies(self, db_path):
        """Main extraction method with enhanced app store handling"""
        
        print("📦 Fetching companies from Product Hunt API...")
        result = self.get_posts_from_product_hunt()
        
        if not result or 'data' not in result:
            print("❌ No data received from API")
            return 0, 0
        
        posts = result['data']['posts']['edges']
        print(f"📦 Found {len(posts)} companies")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        companies_added = 0
        websites_found = 0
        app_store_count = 0
        promotional_count = 0
        app_only_count = 0
        
        for i, post_edge in enumerate(posts, 1):
            post = post_edge['node']
            
            company_name = post.get('name', '').strip()
            print(f"\n[{i}/{len(posts)}] Processing: {company_name}")
            
            # Extract company data
            tagline = post.get('tagline', '').strip()
            description = post.get('description', '').strip()
            original_ph_url = post.get('url', '').strip()
            votes_count = post.get('votesCount', 0)
            comments_count = post.get('commentsCount', 0)
            featured_at = post.get('featuredAt', '')
            current_time = datetime.now().isoformat()
            
            # Extract founder info
            founder_names = []
            makers_info = []
            if post.get('makers'):
                makers = post.get('makers')
                if isinstance(makers, list):
                    makers_info = makers
                    founder_names = [maker.get('name', '') for maker in makers if maker.get('name')]
            founder_name = ', '.join(founder_names) if founder_names else ''
            
            # Extract categories
            categories = []
            if post.get('topics', {}).get('edges'):
                categories = [edge['node']['name'] for edge in post['topics']['edges']]
            categories_str = ', '.join(categories)
            
            # Enhanced website extraction
            real_website_url = None
            extraction_method = "none"
            url_category = "unknown"
            
            if original_ph_url:
                print(f"   🎯 Extracting website URL...")
                
                real_website_url, extraction_method = self.extract_website_with_selenium_enhanced(
                    original_ph_url, company_name, makers_info
                )
                
                if real_website_url:
                    url_category = self.categorize_url(real_website_url)
                    websites_found += 1
                    print(f"   ✅ Website found: {real_website_url} (Category: {url_category})")
                    
                    if url_category == "app_store":
                        app_store_count += 1
                    elif url_category == "promotional":
                        promotional_count += 1
                else:
                    print(f"   ❌ No valid company website found")
                    if any(cat.lower() in ['android', 'ios', 'mobile', 'app'] for cat in categories):
                        app_only_count += 1
                        url_category = "app_only"
                        print(f"   📱 Likely app-only product")
            
            # Save to database
            try:
                cursor.execute("SELECT id FROM companies WHERE name = ?", (company_name,))
                existing_company = cursor.fetchone()
                
                if not existing_company:
                    cursor.execute('''
                        INSERT INTO companies 
                        (name, website, original_ph_url, clean_website_url, status, source, description, 
                         tagline, founder_name, categories, votes_count, comments_count, featured_date, 
                         contact_discovery_status, created_date, updated_date, extraction_method, url_category) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        company_name,
                        real_website_url,
                        original_ph_url,
                        real_website_url,
                        'New',
                        'Product Hunt API + Enhanced Selenium',
                        description,
                        tagline,
                        founder_name,
                        categories_str,
                        votes_count,
                        comments_count,
                        featured_at or current_time,
                        'pending' if real_website_url else 'no_website',
                        current_time,
                        current_time,
                        extraction_method,
                        url_category
                    ))
                    
                    companies_added += 1
                    print(f"✅ SAVED: {company_name}")
                    if founder_name:
                        print(f"   👤 Founders: {founder_name}")
                    if categories_str:
                        print(f"   🏷️  Categories: {categories_str}")
                
                else:
                    if real_website_url:
                        cursor.execute('''
                            UPDATE companies 
                            SET clean_website_url = ?, website = ?, extraction_method = ?, 
                                url_category = ?, updated_date = ?, contact_discovery_status = 'pending'
                            WHERE id = ?
                        ''', (real_website_url, real_website_url, extraction_method, 
                             url_category, current_time, existing_company[0]))
                        print(f"🔄 Updated website for: {company_name}")
                    else:
                        print(f"🔄 Company exists: {company_name}")
            
            except Exception as e:
                print(f"❌ Database error for {company_name}: {e}")
                continue
            
            time.sleep(3)
        
        conn.commit()
        conn.close()
        
        print(f"\n🎉 ENHANCED SCRIPT 1 COMPLETE!")
        print(f"   📝 Companies processed: {len(posts)}")
        print(f"   💾 Companies saved: {companies_added}")
        print(f"   🌐 Valid websites found: {websites_found}")
        print(f"   🏪 App store links handled: {app_store_count}")
        print(f"   📢 Promotional links handled: {promotional_count}")
        print(f"   📱 App-only products: {app_only_count}")
        print(f"   📊 Success rate: {(websites_found/len(posts)*100):.1f}%")
        
        return companies_added, websites_found

    def generate_script2_ready_report(self, db_path):
        """FIXED: Generate report with safe column access"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check what columns exist
        cursor.execute("PRAGMA table_info(companies)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Build query based on available columns
        base_columns = "id, name, clean_website_url, categories, founder_name, votes_count"
        
        if 'extraction_method' in columns:
            base_columns += ", extraction_method"
        else:
            base_columns += ", 'unknown' as extraction_method"
            
        if 'url_category' in columns:
            base_columns += ", url_category"
        else:
            base_columns += ", 'unknown' as url_category"
        
        query = f"""
            SELECT {base_columns}
            FROM companies 
            WHERE source LIKE '%Product Hunt%'
            ORDER BY votes_count DESC
        """
        
        cursor.execute(query)
        companies = cursor.fetchall()
        
        report = {
            'generated_date': datetime.now().isoformat(),
            'total_companies': len(companies),
            'companies_with_websites': len([c for c in companies if c[2]]),
            'ready_for_contact_discovery': len([c for c in companies if c[2] and (len(c) < 8 or c[7] == 'company_website')]),
            'companies': []
        }
        
        for company in companies:
            company_data = {
                'id': company[0],
                'name': company[1],
                'website_url': company[2],
                'categories': company[3],
                'founder_name': company[4],
                'votes_count': company[5],
                'extraction_method': company[6] if len(company) > 6 else 'unknown',
                'url_category': company[7] if len(company) > 7 else 'unknown',
                'ready_for_script2': company[2] is not None
            }
            report['companies'].append(company_data)
        
        with open('companies_ready_for_script2.json', 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"📁 Script 2 ready report saved: companies_ready_for_script2.json")
        
        conn.close()
        return report

def run_enhanced_script1(api_token):
    """Run the enhanced Script 1 with database fixes"""
    
    print("🚀 STARTING ENHANCED SCRIPT 1 - FIXED DATABASE HANDLING")
    print("="*80)
    print("This version:")
    print("• ✅ Handles database schema issues automatically")
    print("• ✅ Recreates database if needed with proper columns")
    print("• ✅ Intelligently handles app store & promotional links")
    print("• ✅ Finds real company websites through multiple methods")
    print("="*80)
    
    extractor = ProductHuntCompanyExtractorFixed(api_token)
    
    db_path = extractor.setup_database()
    companies_added, websites_found = extractor.extract_and_save_companies(db_path)
    report = extractor.generate_script2_ready_report(db_path)
    
    print(f"\n✨ ENHANCED EXTRACTION COMPLETE!")
    print(f"   🎯 Companies saved: {companies_added}")
    print(f"   🌐 Valid websites found: {websites_found}")
    print(f"   📋 Ready for Script 2: {report['ready_for_contact_discovery']}")
    print(f"   📁 Database: {db_path}")
    print(f"   📄 Report: companies_ready_for_script2.json")
    print(f"   🔄 Ready for Script 2!")

if __name__ == "__main__":
    API_TOKEN = os.environ.get("PRODUCTHUNT_API_TOKEN", "")  # set via env, do NOT hardcode
    
    print("📋 Make sure you have installed:")
    print("   pip install selenium webdriver-manager beautifulsoup4 requests")
    print()
    
    run_enhanced_script1(API_TOKEN)
