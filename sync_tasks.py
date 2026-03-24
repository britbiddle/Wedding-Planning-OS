#!/usr/bin/env python3
"""
Wedding Checklist Gmail Sync
Scans Gmail for new wedding-related emails and proposes task additions/updates.
"""

import os
import sys
import json
import re
import base64
from datetime import datetime, date
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
HTML_FILE  = SCRIPT_DIR / "brit-ali-wedding.html"
STATE_FILE = SCRIPT_DIR / "sync_state.json"
CREDS_FILE = SCRIPT_DIR / "credentials.json"
TOKEN_FILE = SCRIPT_DIR / "token.json"

KEYCHAIN_SERVICE = "wedding-sync"
KEYCHAIN_USER    = "anthropic-api-key"

# ── Gmail search query ────────────────────────────────────────────────────────

GMAIL_KEYWORDS = [
    "subject:wedding",
    '"85th day"',
    '"asher gardner"',
    '"night shift"',
    '"sperry tents"',
    '"hana floral"',
    '"one for the books"',
    '"simply gorgeous"',
    '"zest fresh"',
    '"loo haven"',
    '"rebekah brooks"',
    '"golden scroll"',
    '"elihu island"',
    '"july 4 2026"',
    "from:honeybook.com",
    "from:zola.com",
]

# ── State helpers ─────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_run": None}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── API key (macOS Keychain) ──────────────────────────────────────────────────

def get_api_key():
    import keyring
    key = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
    if not key:
        print("\nNo Anthropic API key found in Keychain.")
        print("Get one at: https://console.anthropic.com → API Keys → Create key")
        key = input("Paste your API key: ").strip()
        if not key:
            print("No key entered. Exiting.")
            sys.exit(1)
        import keyring
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_USER, key)
        print("Key saved to macOS Keychain.")
    return key

# ── Gmail auth ────────────────────────────────────────────────────────────────

def get_gmail_service():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("Missing dependencies. Run:")
        print("  pip3 install google-auth-oauthlib google-auth-httplib2 google-api-python-client anthropic keyring")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                print(f"\ncredentials.json not found at: {CREDS_FILE}")
                print("Download it from Google Cloud Console:")
                print("  1. Go to console.cloud.google.com")
                print("  2. Create project → Enable Gmail API")
                print("  3. OAuth credentials → Desktop App → download as credentials.json")
                print(f"  4. Place it in: {SCRIPT_DIR}")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)

# ── Fetch emails ──────────────────────────────────────────────────────────────

def build_query(after_date: str | None) -> str:
    keyword_part = " OR ".join(GMAIL_KEYWORDS)
    query = f"({keyword_part})"
    if after_date:
        query += f" after:{after_date}"
    return query

def fetch_emails(service, after_date: str | None) -> list[dict]:
    query = build_query(after_date)
    print(f"\nSearching Gmail: {query[:80]}...")

    result = service.users().messages().list(
        userId="me", q=query, maxResults=50
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return []

    emails = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender  = headers.get("From", "")
        date_str = headers.get("Date", "")
        body    = extract_body(msg["payload"])

        emails.append({
            "id":      msg_ref["id"],
            "subject": subject,
            "from":    sender,
            "date":    date_str,
            "body":    body[:3000],  # truncate very long emails
        })

    return emails

def extract_body(payload) -> str:
    """Recursively extract plain-text body from Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = extract_body(part)
        if text:
            return text

    # Fallback: try HTML part
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                # strip tags crudely
                return re.sub(r"<[^>]+>", " ", html)

    return ""

# ── Read current TASKS from HTML ──────────────────────────────────────────────

def read_tasks_from_html() -> list[dict]:
    """Parse and return the TASKS array from the HTML file."""
    html = HTML_FILE.read_text(encoding="utf-8")
    pattern = r"(const TASKS = )(\[.*?\])(\s*;)"
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        print("ERROR: Could not find 'const TASKS = [...]' in HTML file.")
        sys.exit(1)

    tasks_json = m.group(2)
    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError:
        tasks = parse_js_array(tasks_json)

    return tasks

def parse_js_array(js_text: str) -> list[dict]:
    """Parse JS object-literal array (unquoted keys) into Python list."""
    # Quote unquoted keys
    quoted = re.sub(r'(\b)([a-zA-Z_]\w*)(\s*:)', r'"\2":', js_text)
    # Handle trailing commas before } or ]
    quoted = re.sub(r',(\s*[}\]])', r'\1', quoted)
    return json.loads(quoted)

# ── Claude API call ───────────────────────────────────────────────────────────

def ask_claude(api_key: str, emails: list[dict], tasks: list[dict]) -> list[dict]:
    try:
        import anthropic
    except ImportError:
        print("Missing anthropic package. Run: pip3 install anthropic")
        sys.exit(1)

    emails_text = "\n\n---\n\n".join(
        f"Subject: {e['subject']}\nFrom: {e['from']}\nDate: {e['date']}\n\n{e['body']}"
        for e in emails
    )

    tasks_text = json.dumps(tasks, indent=2)

    prompt = f"""You are helping manage a wedding checklist. Here are new wedding-related emails received since the last sync:

=== EMAILS ===
{emails_text}

=== CURRENT TASKS ===
{tasks_text}

Based on these emails, propose specific additions or updates to the task list.

Rules:
- Only propose tasks that are actionable and clearly needed based on the emails
- Don't duplicate existing tasks — instead update them with new info
- Keep titles concise (under 80 chars)
- For cat: use "urgent" (do immediately), "now" (this month), "soon" (2-3 months out), or "later"
- For group: use one of the existing group names in the task list
- For meta: include relevant details like dates, contacts, amounts, deadlines
- Set email:true if the task is directly from an email

Respond with ONLY a JSON array, no explanation. Format:
[
  {{
    "action": "add",
    "title": "Task title here",
    "cat": "now",
    "group": "3–4 Months Out",
    "meta": "Details here",
    "email": true
  }},
  {{
    "action": "update",
    "id": 12,
    "meta": "Updated meta text here (replaces existing meta)"
  }}
]

If no changes are needed, return an empty array: []"""

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Extract JSON array even if Claude wrapped it in markdown
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        print("WARNING: Claude returned unexpected format:")
        print(raw)
        return []

    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        print("WARNING: Could not parse Claude's response as JSON:")
        print(raw)
        return []

# ── Preview + confirm ─────────────────────────────────────────────────────────

def preview_and_confirm(proposals: list[dict], tasks: list[dict]) -> bool:
    if not proposals:
        print("\nNo changes proposed.")
        return False

    task_map = {t["id"]: t for t in tasks}

    print(f"\nProposed changes ({len(proposals)}):")
    for p in proposals:
        if p["action"] == "add":
            cat_label = f"[{p.get('cat', '?')}]"
            print(f"  + ADD {cat_label}: \"{p['title']}\"")
            if p.get("meta"):
                print(f"         meta: {p['meta']}")
        elif p["action"] == "update":
            tid = p.get("id")
            existing = task_map.get(tid, {})
            print(f"  ~ UPDATE task #{tid}: \"{existing.get('title', '?')}\"")
            if "meta" in p:
                print(f"         new meta: {p['meta']}")
            if "title" in p:
                print(f"         new title: {p['title']}")
            if "cat" in p:
                print(f"         new cat: {p['cat']}")

    print()
    answer = input("Apply these changes? [y/N]: ").strip().lower()
    return answer == "y"

# ── Apply changes to HTML ─────────────────────────────────────────────────────

def apply_changes(proposals: list[dict], tasks: list[dict]) -> list[dict]:
    task_map = {t["id"]: t for t in tasks}
    max_id = max((t["id"] for t in tasks), default=0)

    for p in proposals:
        if p["action"] == "add":
            max_id += 1
            new_task = {
                "id":    max_id,
                "title": p["title"],
                "cat":   p.get("cat", "now"),
                "group": p.get("group", "3–4 Months Out"),
                "meta":  p.get("meta", ""),
            }
            if p.get("email"):
                new_task["email"] = True
            tasks.append(new_task)

        elif p["action"] == "update":
            tid = p.get("id")
            if tid in task_map:
                t = task_map[tid]
                for field in ("title", "cat", "group", "meta"):
                    if field in p:
                        t[field] = p[field]
                if "email" in p:
                    t["email"] = p["email"]

    return tasks

def write_tasks_to_html(tasks: list[dict]):
    html = HTML_FILE.read_text(encoding="utf-8")

    # Build new JS array string
    lines = ["["]
    for i, t in enumerate(tasks):
        parts = [f'id:{t["id"]}']
        parts.append(f'title:{json.dumps(t["title"])}')
        parts.append(f'cat:{json.dumps(t["cat"])}')
        parts.append(f'group:{json.dumps(t["group"])}')
        parts.append(f'meta:{json.dumps(t["meta"])}')
        if t.get("email"):
            parts.append("email:true")
        comma = "," if i < len(tasks) - 1 else ""
        lines.append(f'  {{{", ".join(parts)}}}{comma}')
    lines.append("]")
    new_tasks_js = "\n".join(lines)

    pattern = r"(const TASKS = )(\[.*?\])(\s*;)"
    new_html = re.sub(
        pattern,
        lambda m: m.group(1) + new_tasks_js + m.group(3),
        html,
        flags=re.DOTALL,
    )

    if new_html == html:
        print("WARNING: HTML was not modified — pattern may not have matched.")
        return

    # Backup
    backup = HTML_FILE.with_suffix(".html.bak")
    backup.write_text(html, encoding="utf-8")

    HTML_FILE.write_text(new_html, encoding="utf-8")
    print(f"\nHTML updated. Backup saved to: {backup.name}")

# ── Demo mode ─────────────────────────────────────────────────────────────────

DEMO_EMAILS = [
    {
        "id": "demo1",
        "subject": "Re: Restroom Trailer Quote — Loo Haven",
        "from": "info@loohaven.com",
        "date": "Mon, 23 Mar 2026 14:32:00 -0400",
        "body": "Hi Brittany,\n\nThank you for reaching out! I'm happy to confirm availability for July 4th, 2026 on Elihu Island.\n\nOur 2-stall luxury restroom trailer is available for your date. The rental fee is $1,850 for the day, which includes delivery, setup, and pickup. A $400 deposit is required to hold the date.\n\nPlease let me know if you'd like to move forward and I'll send over a formal contract.\n\nBest,\nKate\nLoo Haven",
    },
    {
        "id": "demo2",
        "subject": "Zest Fresh Pastry — Invoice #1042 ready for signature",
        "from": "gabriella@zestfreshpastry.com",
        "date": "Tue, 24 Mar 2026 09:15:00 -0400",
        "body": "Hi Brittany,\n\nJust a follow-up on the wedding cake proposal I sent last week. The invoice is ready in HoneyBook for your signature. As a reminder:\n\n- 3-tier Persian-inspired cake, berries + daisies design\n- Total: $1,200\n- $100 deposit due upon signing to hold your July 4th date\n- Balance due 30 days before the event\n\nPlease sign and pay the deposit at your earliest convenience — we're getting bookings for summer weekends quickly!\n\nWarm regards,\nGabriella\nZest Fresh Pastry",
    },
    {
        "id": "demo3",
        "subject": "Night Shift Entertainment — Final Song List Reminder",
        "from": "bookings@nightshiftentertainment.com",
        "date": "Wed, 25 Mar 2026 11:00:00 -0400",
        "body": "Hi Brittany & Evan,\n\nThis is a friendly reminder that your final song list and formalities sheet is due 60 days before your event (due by May 5, 2026).\n\nPlease complete the form at the link below. Items to include: first dance song, parent dances, reception entrance songs, any do-not-play list, and special announcements.\n\nLet us know if you have any questions!\n\nTeam Night Shift",
    },
]

DEMO_PROPOSALS = [
    {
        "action": "update",
        "id": 12,
        "meta": "Kate @ Loo Haven quoted $1,850 · $400 deposit to hold · available Jul 4 · awaiting confirmation",
    },
    {
        "action": "update",
        "id": 6,
        "meta": "Gabriella sent proposal 3/22 · Invoice #1042 ready in HoneyBook · $100 deposit holds date · design approved (berries + daisies) · follow-up sent 3/24",
    },
    {
        "action": "add",
        "title": "Pay Loo Haven $400 deposit to hold restroom trailer",
        "cat": "now",
        "group": "3–4 Months Out",
        "meta": "Kate @ info@loohaven.com · $1,850 total · deposit required to hold Jul 4 date",
        "email": True,
    },
    {
        "action": "update",
        "id": 28,
        "meta": "Due by May 5, 2026 (60 days out) · includes first dance, parent dances, entrance songs, do-not-play list",
    },
]

# ── Main ──────────────────────────────────────────────────────────────────────

def demo():
    print("=== Wedding Checklist Gmail Sync  [DEMO MODE] ===")
    print("(No Gmail or API key required — using sample data)\n")

    print("Last run: 2026/03/17")
    print(f"\nSearching Gmail: (subject:wedding OR \"85th day\" OR \"loo haven\" OR ...)...")
    print(f"Found {len(DEMO_EMAILS)} email(s).")
    for e in DEMO_EMAILS:
        print(f"  • [{e['date'][:16]}] {e['subject'][:70]}")

    print("\nAsking Claude to analyze emails...")
    tasks = read_tasks_from_html()
    print("Claude responded.\n")

    if preview_and_confirm(DEMO_PROPOSALS, tasks):
        updated_tasks = apply_changes(DEMO_PROPOSALS, tasks)
        write_tasks_to_html(updated_tasks)
        print("Done! Open brit-ali-wedding.html to review changes.")
    else:
        print("No changes applied.")

def main():
    if "--demo" in sys.argv:
        demo()
        return

    print("=== Wedding Checklist Gmail Sync ===")

    state = load_state()
    last_run = state.get("last_run")

    if last_run:
        print(f"Last run: {last_run}")
    else:
        print("First run — will scan all matching emails (no date filter).")
        confirm = input("Scan without date limit? [y/N]: ").strip().lower()
        if confirm != "y":
            since = input("Enter start date (YYYY/MM/DD): ").strip()
            if since:
                last_run = since

    api_key = get_api_key()
    service = get_gmail_service()

    emails = fetch_emails(service, last_run)

    if not emails:
        print("No new wedding-related emails found.")
        state["last_run"] = date.today().strftime("%Y/%m/%d")
        save_state(state)
        return

    print(f"Found {len(emails)} email(s).")
    for e in emails:
        print(f"  • [{e['date'][:16]}] {e['subject'][:70]}")

    print("\nAsking Claude to analyze emails...")
    tasks = read_tasks_from_html()
    proposals = ask_claude(api_key, emails, tasks)

    if preview_and_confirm(proposals, tasks):
        updated_tasks = apply_changes(proposals, tasks)
        write_tasks_to_html(updated_tasks)
        print("Done! Open the HTML file to review changes.")
    else:
        print("No changes applied.")

    state["last_run"] = date.today().strftime("%Y/%m/%d")
    save_state(state)

if __name__ == "__main__":
    main()
