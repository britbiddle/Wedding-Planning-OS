# Brit & Ali — Wedding Planning OS

A personal wedding planning dashboard for our July 4, 2026 celebration on Elihu Island, Stonington CT.

## What's in here

### `brit-ali-wedding.html`
A self-contained single-page app for tracking everything wedding-related:

- **Checklist** — tasks organized by urgency and timeline (urgent → 3–4 months → 2–3 months → later)
- **Vendors** — status board for all vendors with contact info and action items
- **Weekend** — full schedule for the July 3–5 weekend
- **Menu** — locked-in catering menu from 85th Day Group
- **Payments** — payment tracker with amounts, due dates, and status
- **Notes** — free-form notes saved to browser local storage

Password protected. Completed tasks and notes persist in browser local storage.

### `sync_tasks.py`
A CLI tool that scans Gmail for new wedding-related emails and uses Claude (Anthropic API) to propose task additions and updates to the checklist.

**Requirements:**
```
pip3 install google-auth-oauthlib google-auth-httplib2 google-api-python-client anthropic keyring
```

**Setup:**
1. Download `credentials.json` from Google Cloud Console (Gmail API, Desktop App OAuth)
2. Place it in the same directory as the script
3. Run `python3 sync_tasks.py` — it will prompt for your Anthropic API key on first run and save it to macOS Keychain

**Demo mode** (no Gmail or API key needed):
```
python3 sync_tasks.py --demo
```
