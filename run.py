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

SEARCH_QUERIES = [
    "peptide clinical trial 2026",
    "GLP-1 semaglutide tirzepatide news 2026",
    "peptide therapy FDA compounding 2026",
    "BPC-157 TB-500 research 2026",
    "peptide market growth billion 2026",
]

def search_peptide_news():
    """Pull fresh peptide industry headlines via DuckDuckGo."""
    try:
        from duckduckgo_search import DDGS
        import random
        query = random.choice(SEARCH_QUERIES)
        results = []
        with DDGS() as ddgs:
            for r in ddgs.news(query, max_results=5):
                results.append(f"- {r['title']}: {r['body'][:200]}")
        if results:
            log(f"Found {len(results)} news items for: {query}")
            return "\n".join(results)
    except Exception as e:
        log(f"News search failed (non-fatal): {e}")
    return ""

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

def append_tweet(svc, num, tweet_type, text, url_included, tweet_id, notes=""):
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
            "", "", "", "", notes
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
    news_context = search_peptide_news()

    system_prompt = """You run the Twitter account for PeptideMerchantApproval.com — an ISO broker that gets peptide sellers approved for card processing when everyone else says no.

Write like a real person who lives in this world every day. Not a marketer. Not a consultant. Someone who has seen it all and just says what's true.

VOICE: Think @PhantomStays — blunt, first-person, zero fluff. Tweets feel like a thought someone had while working, not content someone scheduled. Occasionally cynical, always real. When there's big news, let yourself get excited about it.

WHAT TO WRITE ABOUT (rotate naturally, don't follow a formula):
- ENGAGEMENT BAIT: A simple prompt that makes people want to reply. One short punchy line + a question or call to action. The whole point is replies and interaction. Use an emoji when it fits. Examples: "DROP YOUR STACK 🧪" / "What peptide changed your life?" / "Name a peptide that changed everything for you. I'll wait." / "What's the one peptide you'd never cut from your protocol?" — these are casual, fun, community-driven.
- MACRO OBSERVER: Step back and frame where the industry is. Bold opening claim → 2-3 specific real facts with names and numbers → one closing line that makes the reader feel smart for paying attention. No emojis. No hashtags. Just weight. Example structure: "The peptide space is in its early adoption phase.\n\nThe FDA is reviewing 7 compounds for legal compounding access in July. Hims & Hers just paid $1.15 billion to build the infrastructure. Eli Lilly is in federal court fighting over amino acid counts.\n\nThe people paying attention now are going to look very smart in five years."
- Breaking peptide/GLP-1/compounding news — if something just happened, tweet about it with genuine excitement. Big numbers land: "$2B market by 2027", "87% reduction in inflammation". It's OK to be bullish.
- Something that happened with a client recently — a rejection, a win, a weird situation
- Something true about the peptide industry that most people don't say out loud
- A thing peptide sellers keep getting wrong with payment processing
- What it actually feels like to get dropped by Stripe on a Tuesday

MACRO OBSERVER TWEETS (type: "MACRO"):
- Open with a single declarative sentence. Short. Confident.
- Middle: 2-3 real data points or events. Specific companies, specific numbers, specific regulatory moments.
- Close: one sentence that reframes what it all means. Make the reader feel like they're ahead of the curve.
- No emojis on these. No hashtags. Clean and heavy.
- Use the news context below when writing these — real events make them land harder.

STYLE RULES:
- Short lines. Lots of breaks. Read like speech.
- No templates. No "CLIENT CASE STUDY:" labels. No headers.
- First person: "we", "our clients", "I've seen"
- Specific numbers when you have them — $40k/mo, 3.9% fees, 5% reserve
- One idea per tweet. Don't try to say everything.
- Never start with "Fact:" or "Thread:" or any opener that signals you're doing a format
- No hashtags.
- Emojis: use them when they feel natural — a flag, a chart going up, a fire. Not every tweet. Maybe 1 in 3. Only if it actually adds something.
- HARD LIMIT: 280 characters total. Count every character including spaces and line breaks (\n = 1 char). URLs count as 23 chars. If you're writing a MACRO tweet with 3 facts, each fact can be at most ~60 chars. Cut ruthlessly — one fewer fact is better than a truncated tweet.

URL RULE:
- Add "peptidemerchantapproval.com" only ~1 in every 10 tweets
- Only when the tweet is a strong client win and the link feels genuinely earned
- Default is NO URL. When in doubt, leave it out.
- If included, set "url_included": true

NEVER:
- Name specific processors
- Sound like an ad or a pitch
- Use phrases like "excited to share", "game-changer", "thrilled"
- Write in a way that looks like a content calendar

Return ONLY a JSON array with exactly 1 tweet object:
- "type": "CLIENT", "INDUSTRY", "TIP", "MACRO", or "ENGAGEMENT"
- "text": the tweet
- "url_included": true or false

Example MACRO: [{"type": "MACRO", "text": "The peptide space is in its early adoption phase.\\n\\nThe FDA is reviewing 7 compounds for legal compounding access in July. Hims & Hers just paid $1.15 billion to build the infrastructure. Eli Lilly is in federal court fighting over amino acid counts.\\n\\nThe people paying attention now are going to look very smart in five years.", "url_included": false}]
Example ENGAGEMENT: [{"type": "ENGAGEMENT", "text": "DROP YOUR STACK 🧪\\n\\nWhat peptides are you running right now?", "url_included": false}]
Example INDUSTRY: [{"type": "INDUSTRY", "text": "GLP-1 trial results just dropped.\\n\\nThe numbers are insane.\\n\\nThis is going to be a $50B category and we're still in the early innings.", "url_included": false}]"""

    user_msg = f"Today is {today}. Generate 1 tweet for PMA. Return only the JSON array."
    if news_context:
        user_msg += f"\n\nCurrent peptide industry news to draw from (use if relevant, ignore if not):\n{news_context}"

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_msg}
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
    tweets = tweets[:1]
    total_tokens = response.usage.prompt_tokens * 0.14 + response.usage.completion_tokens * 0.28
    cost = total_tokens / 1_000_000

    # Retry up to 2 times if over limit — ask AI to shorten, don't hard-truncate
    for attempt in range(2):
        if not tweets:
            break
        obj = tweets[0]
        text = obj.get("text", "") if isinstance(obj, dict) else obj
        tlen = twitter_len(text)
        if tlen <= 280:
            break
        log(f"  Tweet too long ({tlen} chars) — asking AI to shorten (attempt {attempt+1})")
        shorten_resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You shorten tweets to fit Twitter's 280-character limit. Preserve the voice and all key facts. Cut words, not ideas. Return only the shortened tweet text, no JSON, no quotes."},
                {"role": "user", "content": f"This tweet is {tlen} chars, must be under 280. Shorten it:\n\n{text}"}
            ],
            temperature=0.3
        )
        shortened = shorten_resp.choices[0].message.content.strip().strip('"')
        cost += (shorten_resp.usage.prompt_tokens * 0.14 + shorten_resp.usage.completion_tokens * 0.28) / 1_000_000
        if isinstance(obj, dict):
            obj["text"] = shortened
        else:
            tweets[0] = shortened

    log(f"Generated tweet | cost ~${cost:.5f}")
    return tweets, cost

# ── Reply to other posts ───────────────────────────────────────────────────────

REPLY_SEARCH_QUERIES = [
    "site:x.com peptide stack",
    "site:x.com BPC-157",
    "site:x.com semaglutide peptide",
    "site:x.com TB-500 peptide",
    "site:x.com peptide biohacking",
    "site:x.com GLP-1 peptide",
    "site:x.com peptide recovery",
]

MAX_REPLIES_PER_RUN = 3   # 3 per run × 2 runs/day = ~6 replies/day
MIN_TWEET_LIKES     = 10  # only reply to tweets with 10+ likes


def find_reply_targets(already_replied: set) -> list:
    """Use DDGS to find high-engagement peptide tweet URLs."""
    try:
        from duckduckgo_search import DDGS
        import random
        targets = []
        seen_ids = set()
        queries = random.sample(REPLY_SEARCH_QUERIES, min(3, len(REPLY_SEARCH_QUERIES)))

        for query in queries:
            try:
                with DDGS() as ddgs:
                    for r in ddgs.text(query, max_results=10):
                        url = r.get("href", "")
                        m = re.search(r'(?:twitter\.com|x\.com)/\w+/status/(\d+)', url)
                        if not m:
                            continue
                        tweet_id = m.group(1)
                        if tweet_id in seen_ids or tweet_id in already_replied:
                            continue
                        seen_ids.add(tweet_id)
                        targets.append({
                            "id": tweet_id,
                            "url": url,
                            "snippet": r.get("body", "")[:300],
                        })
            except Exception:
                continue

        log(f"Found {len(targets)} reply candidate(s) via DDGS")
        return targets[:6]
    except Exception as e:
        log(f"Reply target search failed: {e}")
        return []


def get_already_replied_ids(svc) -> set:
    """Read previously replied tweet IDs from the Tweet Tracker sheet (Notes col)."""
    if not svc:
        return set()
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!C:K"
        ).execute()
        ids = set()
        for row in result.get("values", []):
            if len(row) >= 9 and row[0] == "REPLY":
                # Notes column stores the original tweet ID we replied to
                ids.add(row[8])
        return ids
    except Exception:
        return set()


def get_tweet_text(twitter_client, tweet_id: str) -> tuple[str, str, int]:
    """Fetch tweet text, author username, and like count. Returns (text, author, likes)."""
    try:
        resp = twitter_client.get_tweet(
            tweet_id,
            tweet_fields=["public_metrics"],
            expansions=["author_id"],
            user_fields=["username"]
        )
        if not resp.data:
            return "", "", 0
        text   = resp.data.text
        likes  = (resp.data.public_metrics or {}).get("like_count", 0)
        author = ""
        if resp.includes and resp.includes.get("users"):
            author = resp.includes["users"][0].username
        return text, author, likes
    except Exception as e:
        log(f"  Could not fetch tweet {tweet_id}: {e}")
        return "", "", 0


def generate_reply_text(ai_client, tweet_text: str, author: str) -> str:
    prompt = f"""You run the Twitter account for PeptideMerchantApproval.com — an ISO broker that helps peptide sellers get card processing approved.

You're replying to this tweet from @{author}:
\"{tweet_text}\"

Write a SHORT, GENUINE reply that:
- Actually adds something to the conversation — a real answer, insight, or witty observation
- Sounds like a real person in the peptide/biohacking world, not a bot or marketer
- Is under 220 characters (leave room for the @mention Twitter adds)
- Has NO hashtags
- Emoji only if completely natural
- NEVER pushes peptidemerchantapproval.com or sounds promotional
- The payment processing angle only if it's the most natural thing in the world and genuinely relevant

You know this space deeply: BPC-157, TB-500, GLP-1, semaglutide, tirzepatide, CJC-1295, ipamorelin, PT-141, selank, compounding pharmacies, FDA regulations, etc.

Return ONLY the reply text. Nothing else."""

    resp = ai_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85
    )
    return resp.choices[0].message.content.strip().strip('"')


def post_reply(twitter_client, reply_to_id: str, text: str) -> str:
    resp = twitter_client.create_tweet(
        text=text,
        reply={"in_reply_to_tweet_id": reply_to_id}
    )
    return resp.data["id"]


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
            log(f"  ERROR: tweet still {twitter_len(text)} chars after retry — skipping to avoid truncated post")
            continue

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

    # ── Reply to relevant posts ────────────────────────────────────────────────
    log("Searching for reply targets...")
    already_replied = get_already_replied_ids(svc)
    targets = find_reply_targets(already_replied)
    replies_posted = 0

    for target in targets:
        if replies_posted >= MAX_REPLIES_PER_RUN:
            break
        tweet_text, author, likes = get_tweet_text(twitter_client, target["id"])
        if not tweet_text or not author:
            continue
        if "peptidemerchan" in author.lower():
            continue
        if likes < MIN_TWEET_LIKES:
            log(f"  Skipping @{author} ({likes} likes < {MIN_TWEET_LIKES} minimum)")
            continue

        log(f"Replying to @{author}: {tweet_text[:60]}...")
        try:
            reply_text = generate_reply_text(ai, tweet_text, author)
            if twitter_len(reply_text) > 270:
                log(f"  Reply too long ({twitter_len(reply_text)} chars), skipping")
                continue
            reply_id = post_reply(twitter_client, target["id"], reply_text)
            log(f"  Reply posted: {reply_text[:80]}")
            cost += 0.00005  # negligible but track it
            replies_posted += 1
            if svc:
                append_tweet(svc, tweet_num, "REPLY", reply_text, False, reply_id,
                             notes=target["id"])
                tweet_num += 1
        except Exception as e:
            log(f"  Reply failed: {e}")

    log(f"=== DONE | {replies_posted} replies posted | Total cost: ~${cost:.5f} ===")

if __name__ == "__main__":
    main()
