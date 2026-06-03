#!/usr/bin/env python3
"""
PMA Twitter Agent — Daily poster with Tweet Tracker
Posts 2 tweets/day for PeptideMerchantApproval.com
OAuth 1.0a — simple, no token refresh needed.
"""

import os
import sys
import json
import re
import warnings
from datetime import datetime, timezone
warnings.filterwarnings("ignore")

URL_PATTERN = re.compile(r'https?://\S+|(?<!\w)[\w.-]+\.(?:com|net|org|io|co)\b\S*')
TWITTER_URL_LENGTH = 23  # t.co wraps all URLs to 23 chars

def twitter_len(text):
    """Count tweet length the way Twitter does — URLs always count as 23 chars."""
    count = len(text)
    for url in URL_PATTERN.findall(text):
        count += TWITTER_URL_LENGTH - len(url)
    return count

MODEL      = "deepseek-chat"
BASE_URL   = "https://api.deepseek.com"
BUDGET_CAP = 0.10

SHEET_ID  = os.environ.get("TWEET_TRACKER_SHEET_ID", "")
SHEET_TAB = "Tweet Tracker"
SCOPES    = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER_ROW = ["Tweet #", "Date Published", "Type", "Tweet Copy", "URL Included?",
               "Tweet ID", "Views", "Likes", "Comments", "Reposts", "Notes"]

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ── Twitter ────────────────────────────────────────────────────────────────────

def make_twitter_client():
    import tweepy
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_SECRET"]
    )

def post_tweet(client, text):
    response = client.create_tweet(text=text)
    return response.data["id"]

# ── Google Sheets ──────────────────────────────────────────────────────────────

def get_sheets_service():
    if not SHEET_ID:
        return None
    token_json = os.environ.get("GOOGLE_TOKEN_JSON", "")
    if not token_json:
        return None
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        return build("sheets", "v4", credentials=creds)
    except Exception as e:
        log(f"Sheets auth failed: {e}")
        return None

def ensure_tab_and_header(svc):
    """Create the tab if it doesn't exist, then write the header row."""
    try:
        # Check existing sheets
        meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
        existing = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if SHEET_TAB not in existing:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=SHEET_ID,
                body={"requests": [{"addSheet": {"properties": {"title": SHEET_TAB}}}]}
            ).execute()
            log(f"Created tab '{SHEET_TAB}'")
        # Write header if row 1 is empty
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A1:K1"
        ).execute()
        if not result.get("values"):
            svc.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"'{SHEET_TAB}'!A1",
                valueInputOption="RAW",
                body={"values": [HEADER_ROW]}
            ).execute()
    except Exception as e:
        log(f"Tab/header setup failed: {e}")

def next_tweet_number(svc):
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A:A"
        ).execute()
        return len(result.get("values", []))
    except Exception:
        return 1

def append_tweet(svc, num, tweet_type, text, url_included, tweet_id):
    if not svc:
        return
    try:
        row = [
            num,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            tweet_type,
            text,
            "Y" if url_included else "N",
            str(tweet_id),
            "", "", "", "", ""
        ]
        svc.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f"'{SHEET_TAB}'!A:K",
            valueInputOption="RAW",
            body={"values": [row]}
        ).execute()
        log(f"  Tracked tweet #{num} in sheet")
    except Exception as e:
        log(f"  Sheet append failed: {e}")

def update_metrics(svc, twitter_client):
    if not svc:
        return
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A:K"
        ).execute()
        rows = result.get("values", [])
        if len(rows) <= 1:
            return
        updated = 0
        for i, row in enumerate(rows[1:], start=2):
            if len(row) < 6 or not row[5]:
                continue
            try:
                resp = twitter_client.get_tweet(row[5], tweet_fields=["public_metrics"])
                if not resp.data:
                    continue
                m = resp.data.public_metrics or {}
                note = "HIGH ENGAGEMENT" if isinstance(m.get("like_count"), int) and m["like_count"] > 10 else ""
                svc.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"'{SHEET_TAB}'!G{i}:K{i}",
                    valueInputOption="RAW",
                    body={"values": [[
                        m.get("impression_count", ""),
                        m.get("like_count", ""),
                        m.get("reply_count", ""),
                        m.get("retweet_count", ""),
                        note
                    ]]}
                ).execute()
                updated += 1
            except Exception:
                pass
        if updated:
            log(f"Updated metrics for {updated} tweets")
    except Exception as e:
        log(f"Metrics update failed: {e}")

# ── Tweet generation ───────────────────────────────────────────────────────────

def generate_tweets(client):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    system_prompt = """You run the Twitter account for PeptideMerchantApproval.com — an ISO broker that gets peptide sellers approved for card processing when everyone else says no.

Write like a real person who lives in this world every day. Not a marketer. Not a consultant. Someone who has seen it all and just says what's true.

VOICE: Think @PhantomStays — blunt, first-person, zero corporate speak. Tweets feel like a thought someone had while working, not content someone scheduled. Occasionally cynical, always useful.

WHAT TO WRITE ABOUT (rotate naturally, don't follow a formula):
- Something that happened with a client recently — a rejection, a win, a weird situation
- Something true about the peptide industry that most people don't say out loud
- A thing peptide sellers keep getting wrong with payment processing
- An observation about FDA, GLP-1, compounding, the market — the stuff people in this space actually talk about
- What it actually feels like to get dropped by Stripe on a Tuesday

STYLE RULES:
- Short lines. Lots of breaks. Read like speech.
- No templates. No "CLIENT CASE STUDY:" labels. No headers.
- First person: "we", "our clients", "I've seen"
- Specific numbers when you have them — $40k/mo, 3.9% fees, 5% reserve
- One idea per tweet. Don't try to say everything.
- Never start with "Fact:" or "Thread:" or any opener that signals you're doing a format
- No hashtags. No emojis unless completely natural.
- Max 280 characters

URL RULE:
- Add "peptidemerchantapproval.com" only ~1 in every 8 tweets
- Only when the tweet is a strong client win and the link feels earned, not tacked on
- Default is no URL. When in doubt, leave it out.
- If included, set "url_included": true

NEVER:
- Name specific processors
- Sound like an ad or a pitch
- Use phrases like "excited to share", "game-changer", "thrilled"
- Write in a way that looks like a content calendar

Return ONLY a JSON array with exactly 1 tweet object:
- "type": "CLIENT", "INDUSTRY", or "TIP"
- "text": the tweet
- "url_included": true or false

Example: [{"type": "CLIENT", "text": "...", "url_included": false}]"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": f"Today is {today}. Generate 1 tweet for PMA. Return only the JSON array."}
        ],
        temperature=0.9
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    tweets = json.loads(raw)
    # Only post 1 tweet per run (2 runs/day = 2 tweets/day spaced apart)
    tweets = tweets[:1]
    cost = (response.usage.prompt_tokens * 0.14 + response.usage.completion_tokens * 0.28) / 1_000_000
    log(f"Generated tweet | cost ~${cost:.5f}")
    return tweets, cost

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    from openai import OpenAI
    ai = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=BASE_URL)

    log(f"=== PMA Twitter Agent — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===")

    twitter_client = make_twitter_client()

    svc = get_sheets_service()
    log("Google Sheets connected" if svc else "Sheets not configured — tracking disabled")
    if svc:
        ensure_tab_and_header(svc)

    try:
        tweets, cost = generate_tweets(ai)
    except Exception as e:
        log(f"ERROR generating tweets: {e}")
        sys.exit(1)

    if cost > BUDGET_CAP:
        log(f"Budget exceeded (${cost:.5f} > ${BUDGET_CAP}). Aborting.")
        sys.exit(1)

    tweet_num = next_tweet_number(svc) if svc else 1

    for i, obj in enumerate(tweets, 1):
        text         = obj.get("text", "")           if isinstance(obj, dict) else obj
        tweet_type   = obj.get("type", "UNKNOWN")    if isinstance(obj, dict) else "UNKNOWN"
        url_included = obj.get("url_included", False) if isinstance(obj, dict) else False

        log(f"Tweet {i} [{tweet_type}]: {text[:80]}{'...' if len(text) > 80 else ''}")

        if twitter_len(text) > 280:
            log(f"  WARNING: twitter_len={twitter_len(text)} — truncating")
            text = text[:277] + "..."

        try:
            tweet_id = post_tweet(twitter_client, text)
            log(f"  Posted: https://twitter.com/i/web/status/{tweet_id}")
            if svc:
                append_tweet(svc, tweet_num, tweet_type, text, url_included, tweet_id)
                tweet_num += 1
        except Exception as e:
            log(f"  ERROR posting tweet {i}: {e}")

    if svc:
        log("Updating engagement metrics...")
        update_metrics(svc, twitter_client)

    log(f"=== DONE | Total cost: ~${cost:.5f} ===")

if __name__ == "__main__":
    main()
