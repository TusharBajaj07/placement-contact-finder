"""
Adapter script to run founder_email_finder logic on Sheet3.csv
Maps its columns to the expected format and runs the pipeline.
"""
import csv
import sys
import os
import json

# Import the core logic from founder_email_finder
sys.path.insert(0, os.path.dirname(__file__))
from founder_email_finder import (
    process_company, load_state, save_state,
    STATE_FILE,
)

INPUT = "Untitled spreadsheet - Sheet3.csv"
OUTPUT = "Sheet3_with_emails.csv"
STATE = "sheet3_state.json"

# Override state file
import founder_email_finder
founder_email_finder.STATE_FILE = STATE

def main():
    # Read input
    with open(INPUT, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} companies from {INPUT}")
    print(f"Targeting: Founder / Co-founder / CEO only\n")

    # Map columns to what process_company expects
    mapped_rows = []
    for row in rows:
        mapped = {
            "Company Name": row.get("Company Name", "").strip(),
            "Founder": row.get("Founder/CEO to Contact", "").strip(),
            "Role": row.get("Role", "").strip(),
            "Website Domain": row.get("Website", "").strip(),
            "Sector": row.get("Sector", "").strip(),
            "City": row.get("City", "").strip(),
            # Keep extra columns
            "_original": row,
        }
        mapped_rows.append(mapped)

    # Load state
    if os.path.exists(STATE):
        with open(STATE, "r") as f:
            state = json.load(f)
    else:
        state = {"processed": {}}

    results = []
    for i, row in enumerate(mapped_rows):
        company = row["Company Name"]
        if not company:
            continue

        # Use Sr No + company as key to handle duplicates
        key = f"{row['_original'].get('Sr No', i)}_{company}"

        if key in state["processed"]:
            print(f"[{i+1}/{len(mapped_rows)}] Skipping {company} (already processed)")
            results.append(state["processed"][key])
            continue

        print(f"[{i+1}/{len(mapped_rows)}]", end="")

        try:
            result = process_company(row, gemini_key=None)
            # Add back original columns
            orig = row["_original"]
            result["Sr No"] = orig.get("Sr No", "")
            result["What They Do"] = orig.get("What They Do", "")
            result["Why Social Enterprise"] = orig.get("Why Social Enterprise", "")
            result["Size"] = orig.get("Size", "")
            result["Activity Verified"] = orig.get("Activity Verified", "")

            results.append(result)
            state["processed"][key] = result
            with open(STATE, "w") as f:
                json.dump(state, f, indent=2)

        except KeyboardInterrupt:
            print("\n\nInterrupted! Progress saved.")
            with open(STATE, "w") as f:
                json.dump(state, f, indent=2)
            break
        except Exception as e:
            print(f"\n  [ERROR] {company}: {e}")
            import traceback
            traceback.print_exc()
            err = {
                "Sr No": row["_original"].get("Sr No", ""),
                "Company Name": company,
                "Founder": row.get("Founder", ""),
                "Role": row.get("Role", ""),
                "Sector": row.get("Sector", ""),
                "City": row.get("City", ""),
                "Domain": row.get("Website Domain", ""),
                "What They Do": row["_original"].get("What They Do", ""),
                "Why Social Enterprise": row["_original"].get("Why Social Enterprise", ""),
                "Size": row["_original"].get("Size", ""),
                "Activity Verified": row["_original"].get("Activity Verified", ""),
                "Email": "", "Email Source": f"error: {e}",
                "SMTP Status": "", "All Candidates": "",
            }
            results.append(err)
            state["processed"][key] = err
            with open(STATE, "w") as f:
                json.dump(state, f, indent=2)

    # Write output
    output_fields = [
        "Sr No", "Company Name", "Founder", "Role", "Sector",
        "What They Do", "Why Social Enterprise", "City", "Domain", "Size",
        "Activity Verified", "Email", "Email Source", "SMTP Status", "All Candidates",
    ]
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"\nDone! Results written to: {OUTPUT}")
    found = sum(1 for r in results if r.get("Email"))
    verified = sum(1 for r in results if r.get("SMTP Status") == "valid")
    print(f"  Total companies: {len(results)}")
    print(f"  Emails found:    {found}")
    print(f"  SMTP verified:   {verified}")


if __name__ == "__main__":
    main()
