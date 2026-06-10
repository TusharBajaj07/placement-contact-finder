import csv

# Read original data
original = {}
with open("Comp.csv", "r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        original[row["Company Name"].strip()] = row

# Read email results
emails = {}
with open("founder_emails_all.csv", "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        emails[row["Company Name"].strip()] = row

# Merge and write
output_fields = [
    "Sr No", "Company Name", "Founder", "Role", "Sector",
    "What They Do", "Why Its a Social Enterprise", "City",
    "Website Domain", "Size Estimate",
    "Founder Email", "Email Source", "SMTP Status",
]

with open("Comp_with_emails.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=output_fields)
    writer.writeheader()
    for company_name, orig in original.items():
        email_data = emails.get(company_name, {})
        row = {
            "Sr No": orig.get("Sr No", ""),
            "Company Name": orig.get("Company Name", ""),
            "Founder": orig.get("Founder", ""),
            "Role": orig.get("Role", ""),
            "Sector": orig.get("Sector", ""),
            "What They Do": orig.get("What They Do", ""),
            "Why Its a Social Enterprise": orig.get("Why Its a Social Enterprise", ""),
            "City": orig.get("City", ""),
            "Website Domain": orig.get("Website Domain", ""),
            "Size Estimate": orig.get("Size Estimate", ""),
            "Founder Email": email_data.get("Email", ""),
            "Email Source": email_data.get("Email Source", ""),
            "SMTP Status": email_data.get("SMTP Status", ""),
        }
        writer.writerow(row)

print("Written: Comp_with_emails.csv")
