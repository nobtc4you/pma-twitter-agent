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

def generate_tweets(client, forced_type: str = None, recent_tweets: list = None):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    news_context = search_peptide_news()

    type_instruction = ""
    if forced_type:
        type_instruction = f"\n\nYOU MUST WRITE A **{forced_type}** TWEET THIS TIME. No exceptions — the rotation demands it."

    system_prompt = f"""You run the Twitter account for PeptideMerchantApproval.com — an ISO broker that gets peptide sellers approved for card processing when everyone else says no.

Write like a real person who lives in this world every day. Not a marketer. Not a consultant. Someone who has seen it all and just says what's true.

VOICE: Think @PhantomStays — blunt, first-person, zero fluff. Tweets feel like a thought someone had while working, not content someone scheduled. Occasionally cynical, always real.{type_instruction}

── TWEET TYPES ──────────────────────────────────────────────

ENGAGEMENT (type: "ENGAGEMENT") — the whole point is replies. Make it irresistible to answer.
Use 1-2 emojis. Keep it under 120 chars total so there's visual breathing room.
Pick ONE angle you haven't used recently. Angles to rotate through:
  • Stack sharing → "DROP YOUR STACK 🧪 / What are you running right now?"
  • Controversy → "Unpopular peptide opinion. Go."
  • Hard choice → "If you had to cut every peptide but one, what stays?"
  • Debate → "What changed your body comp more: diet, training, or peptides?"
  • Sleep/recovery → "Rate your sleep stack. Mine: [something]. You?"
  • Overrated/underrated → "Most overrated peptide in the community right now?"
  • First principles → "What's the ONE thing peptide beginners always get wrong?"
  • Personal → "What peptide actually surprised you? Expected nothing, got results."
  • Industry take → "Why does everyone run BPC before trying basic sleep hygiene first?"
  • Timing/protocol → "Morning or night dosing? What shifted your results?"
DO NOT reuse a question you've posted recently. Invent variations — same angle, fresh wording.

CLIENT (type: "CLIENT") — real situation, real numbers, no labels. No "CLIENT CASE STUDY:" ever.
Draw from these real scenarios and mix/vary them — don't copy verbatim:
  • Seller doing $40k/mo with BPC-157 gets dropped by Stripe mid-month, scrambles for 2 weeks, finally approved at 3.8% with reserve. Now processing without interruption.
  • CPA firm referring a peptide lab — they kept getting rejected because underwriters flagged "research chemicals". Reframed as compounding supplier. Approved same week.
  • Seller with perfect chargeback ratio under 0.3% still got dropped. Not their fault. Category blacklisted. We found them a vertical-specific acquirer.
  • New GLP-1 seller from Miami — first processor lasted 6 months, then dropped. Second lasted 3. Third time: diversified across 2 processors on day one. Hasn't had downtime since.
  • International seller shipping to US — every domestic processor said no. One offshore bank, one US backup. Runs both. Bulletproof.
  • Peptide seller who thought they needed offshore. Didn't. Got domestic approval at 3.5%. Saved thousands in fees.
Tone: raw, specific, no hype. Could be a Slack message to a friend.

MACRO (type: "MACRO") — big picture, industry observer.
Structure: Bold opening → 2-3 specific facts with real names/numbers → one closing reframe.
No emojis. No hashtags. Clean.
Use the news context below if there's something real to reference.
Example: "The peptide space is in its early adoption phase.\n\nThe FDA is reviewing 7 compounds for legal compounding access in July. Hims & Hers just paid $1.15B to build the infrastructure. Eli Lilly is in federal court over amino acid counts.\n\nThe people paying attention now are going to look very smart in five years."

TIP (type: "TIP") — practical, specific advice that only someone inside payment processing would know.
Topics to draw from:
  • Never put all your volume on one processor — one termination ends your business
  • Chargebacks above 1% get you on the MATCH list. That follows you for 5 years.
  • A rolling reserve isn't punishment — it's how you prove reliability. Negotiate the release timeline.
  • Offshore processing costs 2-3x more in fees. Worth it only if you can't get domestic.
  • Your MCC code matters more than your product description. Get it right upfront.
  • Don't process under a different business category to "hide" what you sell. That's fraud.
  • Friendly fraud is killing peptide sellers — document every order, every consent, every shipment.
  • If your processor asks for a site inspection, that's normal. Don't panic. Be ready.
Format: one sharp observation + 1-2 sentences of context. No headers. No bullet lists in the tweet.
Example: "Your refund rate matters as much as your chargeback rate.\n\nProcessors see a 10%+ refund rate as a red flag even if chargebacks are low. It signals product-market fit problems or a misleading checkout flow."

INDUSTRY (type: "INDUSTRY") — breaking news or real trend, genuine excitement OK.
Big numbers land. Be specific. "$2.1B market by 2027", "approved for compounding in 43 states".
Keep it conversational, not press-release.

── STYLE RULES ──────────────────────────────────────────────
- Short lines. Lots of line breaks. Read like speech, not an essay.
- First person: "we", "our clients", "I've seen", "I"
- Specific numbers always beat vague claims
- No hashtags ever
- Emojis: natural on ENGAGEMENT, rare on CLIENT/TIP, never on MACRO
- HARD LIMIT: 280 characters. Every \\n = 1 char. URLs = 23 chars. Cut ruthlessly.
- Never start with "Fact:" "Thread:" "Hot take:" or any format signal
- No "game-changer", "excited to share", "thrilled to announce"

── URL RULE ──────────────────────────────────────────────────
Add "peptidemerchantapproval.com" only ~1 in 10 tweets, only on strong CLIENT wins. Default: no URL.

Return ONLY a JSON array with exactly 1 object:
{{"type": "CLIENT"|"INDUSTRY"|"TIP"|"MACRO"|"ENGAGEMENT", "text": "...", "url_included": true|false}}"""

    user_msg = f"Today is {today}. Generate 1 tweet. Type required: {forced_type or 'your choice — pick what fits best'}. Return only the JSON array."
    if recent_tweets:
        recent_texts = "\n".join(f"- {r['type']}: {r['text'][:120]}" for r in recent_tweets[-6:] if r.get("text"))
        if recent_texts:
            user_msg += f"\n\nRECENT TWEETS (DO NOT repeat these topics, angles, or phrasings):\n{recent_texts}"
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

MAX_REPLIES_PER_RUN = 2   # 2 per run × 3 runs/day = ~6 replies/day
MIN_TWEET_LIKES     = 3   # low threshold — we filter by relevance, not just popularity

# Rotating search queries for variety — different angles of the peptide conversation
REPLY_QUERIES = [
    "(BPC-157 OR \"BPC157\") -is:retweet lang:en",
    "(semaglutide OR tirzepatide OR \"GLP-1\") -is:retweet lang:en",
    "(TB-500 OR \"TB500\" OR peptide stack) -is:retweet lang:en",
    "(peptide recovery OR peptide protocol) -is:retweet lang:en",
    "(\"peptide\" biohacking) -is:retweet lang:en",
    "(AOD-9604 OR ipamorelin OR CJC-1295) -is:retweet lang:en",
    "(sermorelin OR GHRP OR growth hormone peptide) -is:retweet lang:en",
]


def find_reply_targets(twitter_client, already_replied: set) -> list:
    """Find recent high-engagement peptide tweets using Twitter's own search API."""
    import random
    targets = []
    seen_ids = set()

    queries = random.sample(REPLY_QUERIES, min(3, len(REPLY_QUERIES)))

    for query in queries:
        try:
            resp = twitter_client.search_recent_tweets(
                query=query,
                max_results=15,
                tweet_fields=["public_metrics", "author_id", "created_at"],
                expansions=["author_id"],
                user_fields=["username"],
            )
            if not resp.data:
                continue

            # Build author map
            users = {}
            if resp.includes and resp.includes.get("users"):
                for u in resp.includes["users"]:
                    users[u.id] = u.username

            for tweet in resp.data:
                tid = str(tweet.id)
                if tid in seen_ids or tid in already_replied:
                    continue
                metrics = tweet.public_metrics or {}
                likes   = metrics.get("like_count", 0)
                replies = metrics.get("reply_count", 0)
                author  = users.get(tweet.author_id, "")

                # Skip our own account and low-quality tweets
                if "peptidemerchan" in author.lower():
                    continue
                if likes < MIN_TWEET_LIKES and replies < 2:
                    continue

                seen_ids.add(tid)
                targets.append({
                    "id":      tid,
                    "text":    tweet.text,
                    "author":  author,
                    "likes":   likes,
                    "replies": replies,
                })
        except Exception as e:
            err = str(e)
            if "403" in err or "Forbidden" in err:
                log(f"  Twitter search 403 — account may be on Free tier (Basic required for search). Query: '{query[:40]}'")
            else:
                log(f"  Twitter search failed for query '{query[:40]}': {e}")
            continue

    # Sort by engagement (likes + replies) descending
    targets.sort(key=lambda t: t["likes"] + t["replies"] * 2, reverse=True)
    log(f"Found {len(targets)} reply candidate(s) via Twitter search")
    return targets[:8]


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
                ids.add(row[8])
        return ids
    except Exception:
        return set()


def get_recent_tweets(svc, n=10) -> list:
    """Return the last n non-REPLY tweets as dicts with 'type' and 'text', oldest first."""
    if not svc:
        return []
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!C:D"
        ).execute()
        rows = [r for r in result.get("values", []) if r and r[0] not in ("Type", "REPLY")]
        return [{"type": r[0], "text": r[1] if len(r) > 1 else ""} for r in rows[-n:]]
    except Exception:
        return []


def get_recent_tweet_types(svc, n=10) -> list:
    recent = get_recent_tweets(svc, n)
    return [r["type"] for r in recent]


# Sequential rotation — no duplicates so ENGAGEMENT can't steal ties
TYPE_ROTATION = ["CLIENT", "ENGAGEMENT", "MACRO", "TIP", "ENGAGEMENT", "INDUSTRY", "CLIENT", "MACRO"]
ROTATION_UNIQUE = ["CLIENT", "ENGAGEMENT", "MACRO", "TIP", "INDUSTRY"]

def pick_next_type(recent_types: list) -> str:
    """Pick the next type sequentially based on what was last posted."""
    if not recent_types:
        return "CLIENT"
    # Walk backwards to find the last known rotation type
    for t in reversed(recent_types):
        if t in ROTATION_UNIQUE:
            idx = TYPE_ROTATION.index(t) if t in TYPE_ROTATION else -1
            if idx >= 0:
                return TYPE_ROTATION[(idx + 1) % len(TYPE_ROTATION)]
    return "CLIENT"


def generate_reply_text(ai_client, tweet_text: str, author: str) -> str:
    prompt = f"""You are replying to a tweet as @peptidemerchan — someone who has been deep in the peptide and biohacking space for years. You know more than most. You reply like a real person, not a brand.

TWEET FROM @{author}:
\"{tweet_text}\"

TONE: Think of the most knowledgeable person in the peptide community who also happens to be funny and direct. Like a friend who actually knows the answer, not a company trying to get attention.

GOOD REPLY EXAMPLES (for a "what's the gateway peptide?" question):
- "BPC-157. Heals something you forgot was broken and then you can't stop reading."
- "BPC-157 for most. Low risk, you feel it, and then you fall down the rabbit hole."
- "BPC-157 if you want to be hooked. Selank if you want to be confused but calm about it."

BAD REPLY EXAMPLES (never do this):
- "making peptides be payed with credit card 💰" ← off-topic, sounds like spam
- "Check out peptidemerchantapproval.com!" ← promotional, kills the vibe
- "Great question! Peptides are amazing!" ← fake and useless

RULES:
- If it's a question, actually answer it with your real opinion
- If it's a statement, add a sharp observation or a counter-point
- Under 220 characters — short is better
- No hashtags ever
- Emoji only if it would genuinely make the tweet better (not just to look casual)
- ZERO mention of payment processing, our business, or our website — unless someone literally asks how peptide sellers accept cards (extremely rare)
- Sound like someone who's been in this world for years, not someone trying to get followers

Return ONLY the reply text. No quotes, no explanation."""

    resp = ai_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85
    )
    return resp.choices[0].message.content.strip().strip('"')


def like_tweet(twitter_client, tweet_id: str):
    try:
        me = twitter_client.get_me()
        twitter_client.like(me.data.id, tweet_id, user_auth=True)
        log(f"  ❤️ Liked tweet {tweet_id}")
    except Exception as e:
        log(f"  Like failed (non-fatal): {e}")


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

    # Determine next tweet type based on recent history
    recent_tweets = get_recent_tweets(svc, n=10)
    recent_types  = [r["type"] for r in recent_tweets]
    forced_type   = pick_next_type(recent_types)
    log(f"Recent types: {recent_types[-6:]} → forcing: {forced_type}")

    try:
        tweets, cost = generate_tweets(ai, forced_type=forced_type, recent_tweets=recent_tweets)
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
    log("Searching for reply targets via Twitter search...")
    already_replied = get_already_replied_ids(svc)
    targets = find_reply_targets(twitter_client, already_replied)
    replies_posted = 0

    for target in targets:
        if replies_posted >= MAX_REPLIES_PER_RUN:
            break
        # Text + author already fetched from search — no extra API call needed
        tweet_text = target.get("text", "")
        author     = target.get("author", "")
        likes      = target.get("likes", 0)
        if not tweet_text or not author:
            continue

        log(f"Replying to @{author} ({likes} likes): {tweet_text[:60]}...")
        try:
            like_tweet(twitter_client, target["id"])
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

