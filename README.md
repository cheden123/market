# Market Value Hunter

Local-first Python tool for finding potentially undervalued marketplace listings. Intended for personal purchases and resale research.

## Windows quick start (Python 3.11+)

```powershell
py -3 hunter.py init
py -3 hunter.py ingest example_listings.json
py -3 hunter.py report
py -3 -m unittest discover -s tests -v
```

Data input: JSON array of objects with source, external_id, title, price (RUB), url; optional description and region. Configure profiles, thresholds, Telegram and Ollama in config.json.

## Features

- SQLite listings, price change history, duplicate suppression and alert history.
- Keyword/region/price watch profiles.
- Comparables filtered by model numbers and title token similarity; robust median/MAD.
- Estimated resale profit after fees, shipping and repair reserve.
- Risk phrase detection. Alerts only when discount, margin and risk thresholds permit.
- Optional Telegram bot alerts via TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.
- Optional Ollama local review with response cache and max_reviews_per_run budget.

## Important limitations

**No automatic Avito data acquisition yet.** The MVP analyzes imported JSON. Supply listings via a lawful permitted data source. Do not bypass CAPTCHA, access controls or platform limits. Comparables are *asking prices*, not confirmed sale prices; estimated profit is not guaranteed. State/condition, scams and inaccurate matches require manual verification. Keep secrets out of Git.

## Token budget

LLM is off by default. All filtering, matching, scoring, and deduplication use deterministic local algorithms without tokens. When enabled, Ollama reviews only qualifying alerts, caches duplicate prompts, and caps reviews per run.
