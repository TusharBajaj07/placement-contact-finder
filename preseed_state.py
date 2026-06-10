"""Pre-seed state file with already verified emails so they get skipped."""
import json

verified = {
    "Haqdarshak": {"Company Name": "Haqdarshak", "Founder": "Aniket Doegar", "Role": "CEO & Co-founder", "Sector": "Digital - Welfare Access", "City": "Mumbai", "Domain": "haqdarshak.com", "Email": "aniket@haqdarshak.com", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Karya": {"Company Name": "Karya", "Founder": "Manu Chopra", "Role": "Co-founder & CEO", "Sector": "AI - Ethical Data & Livelihoods", "City": "Bengaluru", "Domain": "karya.in", "Email": "manu@karya.in", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Khushi Baby": {"Company Name": "Khushi Baby", "Founder": "Dr. Ruchit Nagar", "Role": "Co-founder", "Sector": "Digital Health - Community Health", "City": "Udaipur", "Domain": "khushibaby.org", "Email": "ruchit@khushibaby.org", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Wysa": {"Company Name": "Wysa", "Founder": "Jo Aggarwal", "Role": "CEO & Co-founder", "Sector": "AI - Mental Health", "City": "Bengaluru", "Domain": "wysa.com", "Email": "jo@wysa.com", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Avanti Fellows": {"Company Name": "Avanti Fellows", "Founder": "Akshay Saxena", "Role": "Co-founder", "Sector": "EdTech - Free Tutoring", "City": "Mumbai", "Domain": "avantifellows.org", "Email": "akshay@avantifellows.org", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Niramai Health Analytix": {"Company Name": "Niramai Health Analytix", "Founder": "Dr. Geetha Manjunath", "Role": "CEO & Co-founder", "Sector": "AI - Breast Cancer Screening", "City": "Bengaluru", "Domain": "niramai.com", "Email": "geetha@niramai.com", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Project Tech4Dev": {"Company Name": "Project Tech4Dev", "Founder": "Donald Lobo", "Role": "Founder", "Sector": "Open Source Tech for NGOs", "City": "Bengaluru", "Domain": "projecttech4dev.org", "Email": "donald@projecttech4dev.org", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Rocket Learning": {"Company Name": "Rocket Learning", "Founder": "Namya Mahajan", "Role": "CEO", "Sector": "EdTech - Early Childhood", "City": "Delhi", "Domain": "rocketlearning.org", "Email": "namya@rocketlearning.org", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "GoCoop": {"Company Name": "GoCoop", "Founder": "Shiva Devireddy", "Role": "Founder", "Sector": "Digital Marketplace - Artisans", "City": "Bengaluru", "Domain": "gocoop.com", "Email": "shiva@gocoop.com", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Gramophone": {"Company Name": "Gramophone", "Founder": "Tauseef Khan", "Role": "Co-founder", "Sector": "AgriTech - AI Crop Advisory", "City": "Indore", "Domain": "gramophone.in", "Email": "tauseef@gramophone.in", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Vidyakul": {"Company Name": "Vidyakul", "Founder": "Tarun Saini", "Role": "Founder", "Sector": "EdTech - Vernacular State Board", "City": "Delhi", "Domain": "vidyakul.com", "Email": "tarun@vidyakul.com", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Ecozen Solutions": {"Company Name": "Ecozen Solutions", "Founder": "Devendra Gupta", "Role": "Co-founder", "Sector": "Solar - Cold Storage for Farmers", "City": "Pune", "Domain": "ecozensolutions.com", "Email": "devendra@ecozensolutions.com", "Email Source": "smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "Swajal": {"Company Name": "Swajal", "Founder": "Vibha Tripathi", "Role": "Founder", "Sector": "IoT - Clean Water Access", "City": "Delhi", "Domain": "swajal.in", "Email": "vibha@swajal.in", "Email Source": "web_search + smtp_verified", "SMTP Status": "valid", "All Candidates": ""},
    "CultYvate": {"Company Name": "CultYvate", "Founder": "Mallesh (discovered)", "Role": "Founder", "Sector": "AgriTech - IoT Carbon Credits", "City": "India", "Domain": "cultyvateindia.com", "Email": "mallesh@cultyvate.com", "Email Source": "web_search", "SMTP Status": "valid", "All Candidates": ""},
}

state = {"processed": verified}
with open("founder_emails_state.json", "w") as f:
    json.dump(state, f, indent=2)

print(f"Pre-seeded {len(verified)} verified companies into state file")
