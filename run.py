#!/usr/bin/env python3
"""
PMA Twitter Agent — Daily poster with Tweet Tracker
Posts 2 tweets/day for PeptideMerchantApproval.com
OAuth 1.0a — simple, no token refresh needed.
"""

import os
import sys
import json
import warnings
from datetime import datetime, timezone
warnings.filterwarnings("ignore")

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

def ensure_header(svc):
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:K1"
        ).execute()
        if not result.get("values"):
            svc.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"{SHEET_TAB}!A1",
                valueInputOption="RAW",
                body={"values": [HEADER_ROW]}
            ).execute()
    except Exception:
        pass

def next_tweet_number(svc):
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A:A"
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
            range=f"{SHEET_TAB}!A:K",
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
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A:K"
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
                    range=f"{SHEET_TAB}!G{i}:K{i}",
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

    system_prompt = """You are the social media voice of PeptideMerchantApproval.com (@PeptideMerchantApproval), an ISO broker that helps peptide sellers get approved for credit card processing.

CONTENT MIX (roughly):
- 60% client case studies / wins  [CLIENT]
- 25% peptide industry insights and news  [INDUSTRY]
- 15% direct value / tips about payment processing  [TIP]

TONE & STYLE:
- Direct, no fluff, no corporate speak
- Short punchy lines, lots of line breaks
- Confident, slightly provocative — you know things others don't
- Never use hashtags
- Max 280 characters per tweet
- Write like a practitioner, not a marketer
- Inspired by @PhantomStays style: blunt, informative, occasionally cocky, always useful

URL / CTA RULE:
- Add "peptidemerchantapproval.com" as a CTA at the end of roughly 1 in every 4 tweets
- Only on tweets where it feels natural — client wins and tips, not industry news
- Never force it. When in doubt, leave it out.
- If you include the URL, set "url_included": true

CLIENT CASE STUDY FORMAT:
Start with the situation, then what changed, then the result. Always first person plural ("We had a client...", "One of our merchants...", "Client came to us..."). Be specific with numbers. End with a short insight or lesson.

Example:
"Client came to us doing $40k/month, 100% crypto.

3% conversion. Losing sales every day.

Got them approved in 4 days. 3.9% fees. 5% reserve.

Now doing $180k/month. Same store. Same traffic.

Card processing is not optional for peptide sellers."

INDUSTRY TWEET FORMAT:
Share a fact, trend, or insight about the peptide space — FDA reclassification, RFK Jr. policy moves, compounding pharmacy trends, GLP-1 growth, market size. Be the smartest person in the room. One key insight per tweet, no padding.

TIP FORMAT:
Explain one thing about high-risk processing that peptide sellers don't know — rolling reserves, MATCH list, chargeback ratio thresholds, processor blacklisting, LegitScript. Practical, actionable, no selling.

NEVER:
- Mention specific processor names
- Use hashtags
- Go over 280 characters
- Sound like an ad
- Use filler phrases like "excited to share" or "thrilled to announce"

Return ONLY a JSON array with exactly 1 tweet object. The object must have:
- "type": "CLIENT", "INDUSTRY", or "TIP"
- "text": the full tweet text
- "url_included": true or false

Example:
[{"type": "CLIENT", "text": "...", "url_included": false}]"""

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
        ensure_header(svc)

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

        if len(text) > 280:
            log(f"  WARNING: {len(text)} chars — truncating")
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
