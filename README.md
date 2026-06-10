# Automated Corporate Outreach & Contact-Finder

Tooling for the IIT Bombay Placement Cell to **automatically find founder / HR
contact details (email + phone) for a list of companies**, as the "top of the
funnel" for placement / internship outreach.

The original proposal (`docs/Contacts_Automation (1).pdf`) imagined a paid
**Apollo API** stack. This repo is the **free-stack implementation** that was
actually built: web search + website crawling + email-pattern guessing + SMTP
verification + Gemini for reasoning. Apollo is left as an optional, not-yet-wired
enrichment step.

> **Status: working prototype, NOT validated for production.** It produces
> plausible emails/phones but the accuracy has not been measured against ground
> truth. Read "Honest status & what's left" before trusting or sending anything.

---

## What it does (pipeline)

For each company:

1. **Find the domain** — DuckDuckGo search → pick the best-matching root domain.
2. **Detect the email pattern** — crawl `/about`, `/team`, `/contact`, etc. for any
   real employee email and infer the company's format (`first.last@`, `first@`, …).
3. **Find the person** — search LinkedIn (via DuckDuckGo) for the founder/CEO (or
   HR/TA), extract name + role.
4. **Construct the email** — apply the detected pattern to the person's name.
5. **Verify** — SMTP `RCPT TO` probe against the domain's MX (no email is sent);
   detect catch-all domains.
6. **Score confidence** — `smtp_verified` > `pattern_match` > `guessed`.
7. **(Optional) Gemini** — reason over the candidates / refine the pattern.
8. **(Optional) Phone** — a separate script scrapes phone numbers as a fallback.

Apollo enrichment is stubbed in `contact_finder.py` (`apollo_enrich()`), ready to
wire up if a key becomes available.

---

## Repository layout

### Active pipeline (root)

| File | Role |
|------|------|
| `contact_finder.py` | **Newest** (Mar 2026). Cleanest, most general engine — finds **HR *and* founders**. CLI-driven. Least battle-tested. |
| `founder_email_finder.py` | The **proven** founder/CEO email engine. Most of the real results came from this. Reads `Comp.csv`. |
| `founder_phone_finder.py` | Phone-number fallback finder. Reads `first5_founder_emails.csv`. **Noisy output** (regex grabs junk numbers). |
| `run_sheet3.py` | Adapter: runs `founder_email_finder` logic over `Untitled spreadsheet - Sheet3.csv`. |
| `fix_bounced.py` | Re-attempts a hardcoded list of bounced emails with deeper search. |
| `preseed_state.py` | Seeds `founder_emails_state.json` with already-verified emails so they're skipped. |
| `merge_results.py` | Merges `founder_emails_all.csv` back into `Comp.csv` → `Comp_with_emails.csv`. |

**Data at root** (inputs, outputs, and resumable state — kept together because the
scripts read/write them by relative path):
`Comp.csv` (input), `Comp_with_emails.csv` / `founder_emails_all.csv` /
`founder_phones.csv` (outputs), `founder_emails_state.json` / `sheet3_state.json`
(crash-safe resume state), `Untitled spreadsheet - Sheet3.csv` +
`Sheet3_with_emails.csv`, `first5_founder_emails.csv`.

### `docs/`
The original proposal PDF (architecture, cost model, phased rollout, risks).

### `examples/`
Sample/throwaway test runs (`test_*`, `first5_comp.csv`) — safe to ignore or delete.

### `archive/` — kept for reference, **not part of the live pipeline**
- `db_crawler_era/` — the **first (May 2025) approach**: scrape ProductHunt →
  SQLite (`company_onboarding_poc.db`) → crawl sites for emails
  (`databsing.py`, `main.py`, `contact.py`, `contcat_scrap.py`, `check.py`,
  `inte.py`) plus its output dumps. **Superseded** by the founder-finder scripts.
- `prototypes/free_discovery.py` — an earlier free-discovery prototype, superseded
  by `founder_email_finder.py`.

(Two other folders — `data-mining/` and `resume-verify/` — are separate/unrelated
tools that live locally only and are **not included in this repo**.)

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then paste your keys into .env
export $(grep -v '^#' .env | xargs)
```

Only `GEMINI_API_KEY` is needed for the active pipeline, and it's **optional** —
the scripts degrade gracefully without it.

## Usage

```bash
# Newest general engine (HR + founders), CLI:
python contact_finder.py --companies "Zerodha, Razorpay" --output out.csv
python contact_finder.py --input examples/test_companies.csv

# Proven founder-email engine over Comp.csv (resumable):
python founder_email_finder.py --input Comp.csv --output founder_emails.csv
python merge_results.py                      # → Comp_with_emails.csv

# Sheet3 variant:
python run_sheet3.py

# Phone fallback (run after you have emails):
python founder_phone_finder.py
```

State files make runs **resumable** — if a run crashes, re-running skips already
processed companies. Delete the relevant `*_state.json` to start fresh.

---

## ⚠️ Honest status & what's left

**The core risk: email-guessing accuracy was never measured.** The proposal's
"Phase 1 Sandbox" required back-testing guessed emails against known-correct ones
and hitting a target accuracy *before* sending anything. **That validation has not
been done.** Until it is, treat every `pattern_match` / `guessed` row as unverified.

Known weaknesses:
- **SMTP verification is unreliable.** Many providers (Google Workspace,
  Microsoft 365) refuse `RCPT` probes, greylist, or accept everything (catch-all),
  so `smtp_verified` is weaker than it looks and `unknown` is common.
- **Phone finder is noisy** — see `founder_phones.csv`; the regex pulls in random
  numbers (CINs, prices, etc.). Needs heavy filtering or a real data source.
- **DuckDuckGo scraping is fragile** — rate limits / layout changes break searches.
- **LinkedIn name extraction is heuristic** and produces false "names" from titles.
- **No email is actually sent yet** — Steps 3–5 of the proposal (Gmail-API sending,
  throttling, Google-Sheets logging, the "no-response → phone" escalation) are
  **not implemented**.
- **Two Google SDKs** are used across scripts (`google-genai` vs
  `google-generativeai`); standardise on `google-genai`.

### Suggested roadmap for whoever continues this
1. **Validate accuracy first** (highest priority). Take ~30–50 companies where you
   *know* the real founder email, run the finder, and compute hit-rate per
   `Source`. This is the go/no-go gate. Aim for the back-test the proposal describes.
2. **Replace/​augment SMTP probing** with a real verification API (or Apollo) —
   wire up the existing `apollo_enrich()` stub.
3. **Fix the phone finder** (validate against ground truth, or drop regex for an API).
4. **Then** build sending: Gmail API + throttling + a Google Sheet log, with a hard
   daily cap and a dry-run mode (proposal §5, Phase 1–3).
5. Consolidate the three overlapping finders into one engine; standardise the SDK.

### Testing needed before production
- Accuracy back-test (above) — **mandatory gate**.
- Catch-all / unknown-rate measurement across a representative domain sample.
- A real **dry-run** mode for any future sending path (log, never send).
- Rate-limit / error-handling soak test (DuckDuckGo + Gemini quotas).

---

## Security notes (read before pushing)
- Hardcoded API keys that used to be in the code have been **removed** and moved to
  env vars (`.env`, see `.env.example`). The keys that were previously committed to
  disk (Gemini, Apollo, Mistral, ProductHunt) **should be considered compromised —
  revoke/rotate them**.
- `.gitignore` excludes `.env`, `*.key`, `key`, `.venv/`, and `*.db`.
- This tool does cold outreach from an institute domain. Respect target sites'
  ToS / robots, follow data-protection norms, and keep the throttling caps in the
  proposal. Sending from a `.edu`/`.ac.in` domain at volume risks the domain's
  reputation.
