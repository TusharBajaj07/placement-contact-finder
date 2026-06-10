# Archive

Old / superseded / unrelated code, kept for reference only. **Not part of the
active pipeline** (see the root `README.md`).

- **`db_crawler_era/`** — the first approach (May 2025): ProductHunt → SQLite
  (`company_onboarding_poc.db`) → site crawling for emails, plus its report dumps.
  Superseded by the founder-finder scripts at the repo root.
- **`prototypes/free_discovery.py`** — early free-discovery prototype, superseded by
  `founder_email_finder.py`.

> Two further folders (`data-mining/` — mining IIT placement Gmail PDFs → Excel, and
> `resume-verify/` — resume-PDF image extraction) exist **locally only** and are
> deliberately excluded from this repo (see `.gitignore`); they are separate tools.

Hardcoded API keys in these files were replaced with `os.environ` lookups. Any key
that was previously hardcoded here should be considered compromised — rotate it.
