import sqlite3
import requests
import re
import time
from urllib.parse import urlparse
import json
from datetime import datetime

class ContactDiscovery:
    def __init__(self, db_path='company_onboarding_poc.db'):
        self.db_path = db_path
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
    
    def get_companies_needing_contacts(self):
        """Get companies that don't have contacts yet"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT c.id, c.name, c.website, c.founder_name, c.categories 
            FROM companies c 
            LEFT JOIN contacts ct ON c.id = ct.company_id 
            WHERE ct.company_id IS NULL 
            ORDER BY c.votes_count DESC
        """)
        
        companies = cursor.fetchall()
        conn.close()
        
        print(f"📋 Found {len(companies)} companies needing contacts")
        return companies
    
    def generate_email_patterns(self, name, domain):
        """Generate common email patterns for a person"""
        if not name or not domain:
            return []
        
        # Clean the name
        name = re.sub(r'[^a-zA-Z\s]', '', name).strip().lower()
        parts = name.split()
        
        if len(parts) < 2:
            return []
        
        first_name = parts[0]
        last_name = parts[-1]
        
        patterns = [
            f"{first_name}.{last_name}@{domain}",
            f"{first_name}@{domain}",
            f"{last_name}@{domain}",
            f"{first_name[0]}{last_name}@{domain}",
            f"{first_name}{last_name}@{domain}",
            f"{first_name}_{last_name}@{domain}",
            f"{first_name}-{last_name}@{domain}",
            f"{first_name[0]}.{last_name}@{domain}",
        ]
        
        return patterns
    
    def get_company_domain(self, website):
        """Extract domain from website URL"""
        if not website:
            return None
        
        try:
            if not website.startswith(('http://', 'https://')):
                website = f"https://{website}"
            
            domain = urlparse(website).netloc
            if domain.startswith('www.'):
                domain = domain[4:]
            
            return domain
        except:
            return None
    
    def find_common_contacts(self, domain):
        """Generate common contact emails for a domain"""
        if not domain:
            return []
        
        common_contacts = [
            f"careers@{domain}",
            f"hr@{domain}",
            f"jobs@{domain}",
            f"internships@{domain}",
            f"talent@{domain}",
            f"recruiting@{domain}",
            f"hello@{domain}",
            f"contact@{domain}",
            f"info@{domain}",
            f"support@{domain}",
        ]
        
        return common_contacts
    
    def validate_email_format(self, email):
        """Basic email format validation"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    def check_email_deliverability(self, email):
        """Basic email deliverability check (simplified)"""
        try:
            domain = email.split('@')[1]
            
            # Try to resolve MX record (simplified approach)
            import socket
            try:
                socket.gethostbyname(domain)
                return True
            except:
                return False
        except:
            return False
    
    def scrape_company_contacts_from_website(self, website):
        """Scrape contact information from company website"""
        if not website:
            return []
        
        contacts = []
        
        try:
            # Get company homepage
            response = self.session.get(website, timeout=10)
            
            if response.status_code == 200:
                content = response.text.lower()
                
                # Look for email patterns
                email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
                emails = re.findall(email_pattern, content, re.IGNORECASE)
                
                # Filter relevant emails
                relevant_emails = []
                for email in emails:
                    email = email.lower()
                    if any(keyword in email for keyword in ['careers', 'hr', 'jobs', 'talent', 'contact', 'hello', 'info']):
                        relevant_emails.append(email)
                
                # Check common pages
                contact_pages = ['/contact', '/careers', '/about', '/team', '/jobs']
                
                for page in contact_pages:
                    try:
                        page_url = website.rstrip('/') + page
                        page_response = self.session.get(page_url, timeout=5)
                        
                        if page_response.status_code == 200:
                            page_emails = re.findall(email_pattern, page_response.text, re.IGNORECASE)
                            relevant_emails.extend(page_emails)
                    except:
                        continue
                
                # Remove duplicates and validate
                unique_emails = list(set(relevant_emails))
                
                for email in unique_emails:
                    if self.validate_email_format(email):
                        contacts.append({
                            'email': email,
                            'role': self.guess_role_from_email(email),
                            'source': 'Website Scraping'
                        })
        
        except Exception as e:
            print(f"❌ Error scraping {website}: {e}")
        
        return contacts
    
    def guess_role_from_email(self, email):
        """Guess role based on email address"""
        email_lower = email.lower()
        
        if any(keyword in email_lower for keyword in ['careers', 'hr', 'jobs', 'talent', 'recruiting']):
            return 'HR/Recruiting'
        elif any(keyword in email_lower for keyword in ['ceo', 'founder', 'co-founder']):
            return 'Founder/CEO'
        elif any(keyword in email_lower for keyword in ['contact', 'hello', 'info']):
            return 'General Contact'
        else:
            return 'Contact'
    
    def discover_contacts_for_company(self, company_id, company_name, website, founder_name):
        """Discover all possible contacts for a company"""
        print(f"\n🔍 Finding contacts for: {company_name}")
        
        domain = self.get_company_domain(website)
        all_contacts = []
        
        # 1. Founder contacts (if we have founder name)
        if founder_name and domain:
            founder_emails = self.generate_email_patterns(founder_name, domain)
            for email in founder_emails:
                if self.validate_email_format(email):
                    all_contacts.append({
                        'name': founder_name,
                        'email': email,
                        'role': 'Founder',
                        'source': 'Generated Pattern',
                        'confidence': 0.7
                    })
        
        # 2. Common contact emails
        if domain:
            common_emails = self.find_common_contacts(domain)
            for email in common_emails:
                all_contacts.append({
                    'name': email.split('@')[0].title(),
                    'email': email,
                    'role': self.guess_role_from_email(email),
                    'source': 'Common Pattern',
                    'confidence': 0.8
                })
        
        # 3. Website scraping
        website_contacts = self.scrape_company_contacts_from_website(website)
        for contact in website_contacts:
            all_contacts.append({
                'name': contact['email'].split('@')[0].title(),
                'email': contact['email'],
                'role': contact['role'],
                'source': contact['source'],
                'confidence': 0.9
            })
        
        # Remove duplicates and prioritize by confidence
        unique_contacts = {}
        for contact in all_contacts:
            email = contact['email']
            if email not in unique_contacts or contact['confidence'] > unique_contacts[email]['confidence']:
                unique_contacts[email] = contact
        
        final_contacts = list(unique_contacts.values())
        
        print(f"   📧 Found {len(final_contacts)} potential contacts")
        
        return final_contacts
    
    def save_contacts_to_db(self, company_id, contacts):
        """Save discovered contacts to database"""
        if not contacts:
            return 0
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        contacts_saved = 0
        
        for contact in contacts:
            try:
                # Check if contact already exists
                cursor.execute("SELECT id FROM contacts WHERE email = ? AND company_id = ?", 
                             (contact['email'], company_id))
                
                if not cursor.fetchone():
                    cursor.execute('''
                        INSERT INTO contacts (name, role, email, company_id, source, confidence) 
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        contact['name'],
                        contact['role'],
                        contact['email'],
                        company_id,
                        contact['source'],
                        contact['confidence']
                    ))
                    contacts_saved += 1
                    print(f"   ✅ Saved: {contact['email']} ({contact['role']})")
            
            except Exception as e:
                print(f"   ❌ Error saving {contact['email']}: {e}")
        
        conn.commit()
        conn.close()
        
        return contacts_saved
    
    def run_contact_discovery(self):
        """Main function to discover contacts for all companies"""
        print("🚀 Starting Contact Discovery Process...")
        
        # First, add confidence column if it doesn't exist
        self.add_confidence_column()
        
        companies = self.get_companies_needing_contacts()
        
        total_contacts = 0
        
        for company_id, company_name, website, founder_name, categories in companies:
            try:
                contacts = self.discover_contacts_for_company(
                    company_id, company_name, website, founder_name
                )
                
                contacts_saved = self.save_contacts_to_db(company_id, contacts)
                total_contacts += contacts_saved
                
                # Be respectful - add delay between companies
                time.sleep(2)
                
            except Exception as e:
                print(f"❌ Error processing {company_name}: {e}")
                continue
        
        print(f"\n🎉 Contact Discovery Complete!")
        print(f"   📧 Total contacts discovered: {total_contacts}")
        
        return total_contacts
    
    def add_confidence_column(self):
        """Add confidence column to contacts table if it doesn't exist"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("ALTER TABLE contacts ADD COLUMN source TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        try:
            cursor.execute("ALTER TABLE contacts ADD COLUMN confidence REAL DEFAULT 0.5")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        conn.commit()
        conn.close()

def show_contacts_dashboard():
    """Show discovered contacts dashboard"""
    conn = sqlite3.connect('company_onboarding_poc.db')
    cursor = conn.cursor()
    
    # Get total contacts
    cursor.execute("SELECT COUNT(*) FROM contacts")
    total_contacts = cursor.fetchone()[0]
    
    # Get contacts by role
    cursor.execute("SELECT role, COUNT(*) FROM contacts GROUP BY role ORDER BY COUNT(*) DESC")
    by_role = cursor.fetchall()
    
    # Get top companies with contacts
    cursor.execute("""
        SELECT c.name, COUNT(ct.id) as contact_count, c.votes_count
        FROM companies c 
        LEFT JOIN contacts ct ON c.id = ct.company_id 
        GROUP BY c.id, c.name, c.votes_count
        HAVING contact_count > 0
        ORDER BY contact_count DESC, c.votes_count DESC
        LIMIT 10
    """)
    companies_with_contacts = cursor.fetchall()
    
    print("\n" + "="*60)
    print("📧 CONTACT DISCOVERY DASHBOARD")
    print("="*60)
    print(f"📊 Total Contacts: {total_contacts}")
    
    if by_role:
        print("\n👥 Contacts by Role:")
        for role, count in by_role:
            print(f"   • {role}: {count}")
    
    if companies_with_contacts:
        print("\n🏢 Companies with Contacts:")
        for name, contact_count, votes in companies_with_contacts:
            print(f"   • {name}: {contact_count} contacts ({votes} votes)")
    
    conn.close()

# Quick function to find specific contacts
def find_hr_contacts():
    """Find HR/recruiting specific contacts"""
    conn = sqlite3.connect('company_onboarding_poc.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT c.name as company, ct.name as contact_name, ct.email, ct.role
        FROM companies c
        JOIN contacts ct ON c.id = ct.company_id
        WHERE ct.role LIKE '%HR%' OR ct.role LIKE '%recruit%' OR ct.email LIKE '%career%' OR ct.email LIKE '%hr%'
        ORDER BY c.votes_count DESC
    """)
    
    hr_contacts = cursor.fetchall()
    
    print(f"\n🎯 HR/Recruiting Contacts ({len(hr_contacts)} found):")
    print("="*50)
    
    for company, contact_name, email, role in hr_contacts:
        print(f"🏢 {company}")
        print(f"   👤 {contact_name} ({role})")
        print(f"   📧 {email}")
        print()
    
    conn.close()

# Main execution
def run_complete_contact_discovery():
    """Run the complete contact discovery process"""
    
    discovery = ContactDiscovery()
    
    # Run contact discovery
    total_contacts = discovery.run_contact_discovery()
    
    # Show dashboard
    show_contacts_dashboard()
    
    # Show HR contacts specifically
    find_hr_contacts()
    
    print(f"\n✨ Ready for outreach! Found {total_contacts} contacts")

# Run the contact discovery
if __name__ == "__main__":
    run_complete_contact_discovery()
