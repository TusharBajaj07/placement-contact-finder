import requests
import json
import sqlite3
import time
from datetime import datetime
import random
import os

def verify_database_setup():
    """Check if database is properly set up"""
    
    db_path = os.path.join(os.getcwd(), 'company_onboarding_poc.db')
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        print(f"📂 Database: {db_path}")
        print(f"📋 Tables found: {[table[0] for table in tables]}")
        
        # Check companies table structure
        cursor.execute("PRAGMA table_info(companies);")
        columns = cursor.fetchall()
        
        print(f"🏗️  Companies table columns:")
        for col in columns:
            print(f"   • {col[1]} ({col[2]})")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Database check failed: {e}")
        return False

# Check database first
verify_database_setup()
