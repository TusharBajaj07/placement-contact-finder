import sqlite3
import json
import csv
from datetime import datetime
import os

def show_all_discovered_emails():
    """Complete script to show all emails found by Script 2"""
    
    # Check if database exists
    db_path = 'company_onboarding_poc.db'
    if not os.path.exists(db_path):
        print("❌ Database not found!")
        print("   Make sure you're in the directory with company_onboarding_poc.db")
        return
    
    print("🚀 COMPLETE EMAIL DISCOVERY RESULTS")
    print("="*80)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Get basic statistics
    try:
        cursor.execute("SELECT COUNT(DISTINCT email) FROM contacts")
        total_unique_emails = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT company_id) FROM contacts")
        companies_with_contacts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM companies WHERE contact_discovery_status = 'completed'")
        total_companies_processed = cursor.fetchone()[0]
        
        print(f"📊 SUMMARY:")
        print(f"   • Total unique emails found: {total_unique_emails}")
        print(f"   • Companies with contacts: {companies_with_contacts}")
        print(f"   • Companies processed: {total_companies_processed}")
        
        if total_unique_emails == 0:
            print("\n❌ No emails found in database!")
            print("   Make sure Script 2 has been run successfully")
            return
            
    except Exception as e:
        print(f"❌ Error reading database: {e}")
        return
    
    # 2. Show ALL emails by company
    print(f"\n📧 ALL EMAILS BY COMPANY:")
    print("="*80)
    
    cursor.execute("""
        SELECT 
            c.name as company_name,
            c.votes_count,
            c.categories,
            c.clean_website_url,
            ct.name as contact_name,
            ct.email,
            ct.role,
            ct.confidence,
            ct.source,
            ct.discovery_date
        FROM companies c
        JOIN contacts ct ON c.id = ct.company_id
        ORDER BY c.votes_count DESC, ct.confidence DESC
    """)
    
    all_contacts = cursor.fetchall()
    
    # Group by company
    companies = {}
    for contact in all_contacts:
        company_name = contact[0]
        if company_name not in companies:
            companies[company_name] = {
                'info': contact[:4],  # company info
                'contacts': []
            }
        companies[company_name]['contacts'].append(contact[4:])  # contact info
    
    # Display by company
    for company_name, company_data in companies.items():
        company_info = company_data['info']
        contacts = company_data['contacts']
        
        print(f"\n🏢 {company_name} ({company_info[1]} votes)")
        print(f"   🏷️  Categories: {company_info[2]}")
        print(f"   🌐 Website: {company_info[3]}")
        print(f"   📧 Contacts ({len(contacts)}):")
        
        for contact in contacts:
            contact_name, email, role, confidence, source, date = contact
            print(f"      • {email}")
            print(f"        👤 {contact_name} ({role})")
            print(f"        🎯 Confidence: {confidence} | Source: {source}")
    
    # 3. Show HR/Recruiting emails specifically
    print(f"\n🎯 HR/RECRUITING EMAILS (BEST FOR INTERNSHIPS):")
    print("="*80)
    
    cursor.execute("""
        SELECT 
            c.name as company_name,
            c.votes_count,
            ct.email,
            ct.role,
            ct.confidence
        FROM companies c
        JOIN contacts ct ON c.id = ct.company_id
        WHERE ct.role LIKE '%HR%' 
           OR ct.role LIKE '%recruit%' 
           OR ct.role LIKE '%Internship%'
           OR ct.role LIKE '%talent%'
           OR ct.role LIKE '%career%'
        ORDER BY c.votes_count DESC, ct.confidence DESC
    """)
    
    hr_contacts = cursor.fetchall()
    
    if hr_contacts:
        for company, votes, email, role, confidence in hr_contacts:
            print(f"🏢 {company} ({votes} votes)")
            print(f"   📧 {email}")
            print(f"   👤 Role: {role} (Confidence: {confidence})")
            print()
    else:
        print("❌ No HR/recruiting specific emails found")
        print("   Check General Contact emails for potential HR contacts")
    
    # 4. Show emails by role
    print(f"\n👥 EMAILS BY ROLE:")
    print("="*80)
    
    cursor.execute("""
        SELECT 
            ct.role,
            COUNT(*) as count,
            GROUP_CONCAT(ct.email, '; ') as emails
        FROM contacts ct
        GROUP BY ct.role
        ORDER BY count DESC
    """)
    
    roles = cursor.fetchall()
    
    for role, count, emails in roles:
        print(f"\n{role} ({count} emails):")
        email_list = emails.split('; ')
        for email in email_list:
            print(f"   📧 {email}")
    
    # 5. Save all emails to files
    print(f"\n💾 SAVING EMAILS TO FILES...")
    print("="*50)
    
    # Save all emails to text file
    cursor.execute("SELECT DISTINCT email FROM contacts ORDER BY email")
    all_emails = [row[0] for row in cursor.fetchall()]
    
    with open('ALL_DISCOVERED_EMAILS.txt', 'w') as f:
        f.write(f"All {len(all_emails)} discovered emails from Script 2\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*60 + "\n\n")
        for email in all_emails:
            f.write(email + '\n')
    
    print(f"✅ All emails saved to: ALL_DISCOVERED_EMAILS.txt")
    
    # Save detailed CSV for outreach
    with open('DETAILED_CONTACTS_FOR_OUTREACH.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            'Company', 'Votes', 'Categories', 'Website', 
            'Contact_Name', 'Email', 'Role', 'Confidence', 'Source'
        ])
        
        for contact in all_contacts:
            writer.writerow(contact)
    
    print(f"✅ Detailed CSV saved to: DETAILED_CONTACTS_FOR_OUTREACH.csv")
    
    # Save HR emails specifically
    if hr_contacts:
        with open('HR_RECRUITING_EMAILS.txt', 'w') as f:
            f.write(f"HR/Recruiting emails for internship outreach\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*60 + "\n\n")
            for company, votes, email, role, confidence in hr_contacts:
                f.write(f"{company} ({votes} votes)\n")
                f.write(f"Email: {email}\n")
                f.write(f"Role: {role}\n")
                f.write(f"Confidence: {confidence}\n")
                f.write("-" * 40 + "\n")
        
        print(f"✅ HR emails saved to: HR_RECRUITING_EMAILS.txt")
    
    # Save JSON report with all details
    detailed_report = {
        'generation_date': datetime.now().isoformat(),
        'summary': {
            'total_unique_emails': total_unique_emails,
            'companies_with_contacts': companies_with_contacts,
            'total_companies_processed': total_companies_processed
        },
        'companies': []
    }
    
    for company_name, company_data in companies.items():
        company_info = company_data['info']
        contacts = company_data['contacts']
        
        company_entry = {
            'name': company_name,
            'votes': company_info[1],
            'categories': company_info[2],
            'website': company_info[3],
            'contact_count': len(contacts),
            'contacts': []
        }
        
        for contact in contacts:
            contact_name, email, role, confidence, source, date = contact
            company_entry['contacts'].append({
                'name': contact_name,
                'email': email,
                'role': role,
                'confidence': confidence,
                'source': source,
                'discovery_date': date
            })
        
        detailed_report['companies'].append(company_entry)
    
    with open('COMPLETE_EMAIL_REPORT.json', 'w') as f:
        json.dump(detailed_report, f, indent=2)
    
    print(f"✅ Complete JSON report saved to: COMPLETE_EMAIL_REPORT.json")
    
    # 6. Show top companies by contact count
    print(f"\n🏆 TOP COMPANIES BY CONTACTS FOUND:")
    print("="*50)
    
    cursor.execute("""
        SELECT 
            c.name,
            c.votes_count,
            COUNT(ct.email) as contact_count,
            GROUP_CONCAT(ct.email, '; ') as emails
        FROM companies c
        JOIN contacts ct ON c.id = ct.company_id
        GROUP BY c.id
        ORDER BY contact_count DESC, c.votes_count DESC
        LIMIT 10
    """)
    
    top_companies = cursor.fetchall()
    
    for i, (company, votes, count, emails) in enumerate(top_companies, 1):
        print(f"{i:2d}. {company} ({votes} votes) - {count} contacts")
        email_list = emails.split('; ')
        for email in email_list[:3]:  # Show first 3 emails
            print(f"     📧 {email}")
        if len(email_list) > 3:
            print(f"     ... and {len(email_list) - 3} more")
    
    conn.close()
    
    print(f"\n🎉 COMPLETE! All your {total_unique_emails} emails are displayed above.")
    print(f"📁 Files generated:")
    print(f"   • ALL_DISCOVERED_EMAILS.txt - Simple list of all emails")
    print(f"   • DETAILED_CONTACTS_FOR_OUTREACH.csv - Full details in spreadsheet")
    print(f"   • HR_RECRUITING_EMAILS.txt - HR contacts for internships")
    print(f"   • COMPLETE_EMAIL_REPORT.json - Complete JSON report")
    
    return {
        'total_emails': total_unique_emails,
        'companies_with_contacts': companies_with_contacts,
        'files_generated': [
            'ALL_DISCOVERED_EMAILS.txt',
            'DETAILED_CONTACTS_FOR_OUTREACH.csv', 
            'HR_RECRUITING_EMAILS.txt',
            'COMPLETE_EMAIL_REPORT.json'
        ]
    }

def quick_email_list():
    """Just show the email addresses quickly"""
    
    conn = sqlite3.connect('company_onboarding_poc.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT ct.email 
        FROM contacts ct 
        ORDER BY ct.email
    """)
    
    emails = [row[0] for row in cursor.fetchall()]
    
    print(f"📧 QUICK EMAIL LIST ({len(emails)} emails):")
    print("="*50)
    
    for i, email in enumerate(emails, 1):
        print(f"{i:2d}. {email}")
    
    conn.close()

def show_emails_by_company_simple():
    """Simple view of emails by company"""
    
    conn = sqlite3.connect('company_onboarding_poc.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            c.name,
            GROUP_CONCAT(ct.email, ', ') as emails
        FROM companies c
        JOIN contacts ct ON c.id = ct.company_id
        GROUP BY c.id
        ORDER BY c.votes_count DESC
    """)
    
    companies = cursor.fetchall()
    
    print(f"🏢 EMAILS BY COMPANY ({len(companies)} companies):")
    print("="*60)
    
    for company, emails in companies:
        print(f"\n{company}:")
        email_list = emails.split(', ')
        for email in email_list:
            print(f"   📧 {email}")
    
    conn.close()

if __name__ == "__main__":
    # Run the complete email discovery
    print("🚀 RUNNING COMPLETE EMAIL DISCOVERY...")
    print()
    
    try:
        result = show_all_discovered_emails()
        
        if result:
            print(f"\n✨ SUCCESS!")
            print(f"Found {result['total_emails']} emails from {result['companies_with_contacts']} companies")
            print(f"Check the generated files for easy access to all emails")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n🔧 Trying simpler methods...")
        
        try:
            quick_email_list()
        except Exception as e2:
            print(f"❌ Error with simple method: {e2}")
            print("Make sure you're in the directory with company_onboarding_poc.db")

    print(f"\n" + "="*80)
    print(f"🎯 NEXT STEPS:")
    print(f"   1. Open ALL_DISCOVERED_EMAILS.txt to see all {46} emails")
    print(f"   2. Open DETAILED_CONTACTS_FOR_OUTREACH.csv for spreadsheet view")
    print(f"   3. Use HR_RECRUITING_EMAILS.txt for internship outreach")
    print(f"   4. Check COMPLETE_EMAIL_REPORT.json for full details")
    print(f"="*80)
