#!/usr/bin/env python3
"""
PMA Twitter Agent — Daily poster
Posts 1-2 tweets per day for PeptideMerchantApproval.com
Targets peptide business owners struggling with payment processing.
"""

import os
import sys
import json
import warnings
from datetime import datetime, timezone
warnings.filterwarnings("ignore")

MODEL      = "deepseek-chat"
BASE_URL   = "https://api.deepseek.com"
BUDGET_CAP = 0.10  # $0.10/day — DeepSeek is cheap, 2 tweets is almost nothing

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def generate_tweets(client):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    system_prompt = """You are the social media voice of PeptideMerchantApproval.com (PMA).

PMA helps peptide businesses (research peptides, RUO compounds, nootropics) get merchant accounts and payment processing when banks and mainstream processors won't touch them.

AUDIENCE: Peptide business owners, peptide e-commerce sellers, supplement brands, research chemical companies — people who have been rejected by Stripe, Square, PayPal, or their bank.

TONE:
- Direct, knowledgeable, no-BS
- Empathetic — we know their pain
- Never salesy or spammy
- Short and punchy — Twitter is not a blog
- Occasionally use industry lingo (RUO, high-risk, chargeback ratio, merchant account)
- No emojis unless it genuinely fits
- No hashtag spam — max 1-2 relevant hashtags if any

CONTENT IDEAS (rotate, don't repeat same angle twice in a row):
- Pain points: "Stripe just terminated your account. Here's what's actually happening."
- Education: how high-risk processing works, what processors look at
- Myths debunked: "You don't need to lie about what you sell to get processing"
- Quick wins: tips peptide sellers can act on today
- Social proof angles: "processors DO work with peptide businesses if you know where to look"
- The cost of bad processing: chargebacks, holds, terminations
- What PMA does and why it works

RULES:
- Each tweet max 280 characters
- Do NOT include URLs (X charges more for tweets with links)
- Do NOT mention competitors by name
- Vary the angle each tweet
- Return ONLY a JSON array with 2 tweet strings, nothing else

Example format:
["Tweet one here.", "Tweet two here."]"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Today is {today}. Generate 2 tweets for PMA. Return only the JSON array."}
        ],
        temperature=0.9
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown code blocks if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    tweets = json.loads(raw)
    cost = (response.usage.prompt_tokens * 0.14 + response.usage.completion_tokens * 0.28) / 1_000_000
    log(f"Generated {len(tweets)} tweets | cost ~${cost:.5f}")
    return tweets, cost

def post_tweet(text):
    import tweepy
    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_SECRET"]
    )
    response = client.create_tweet(text=text)
    return response.data["id"]

def main():
    from openai import OpenAI
    ai = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=BASE_URL)

    log(f"=== PMA Twitter Agent — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===")

    try:
        tweets, cost = generate_tweets(ai)
    except Exception as e:
        log(f"ERROR generating tweets: {e}")
        sys.exit(1)

    if cost > BUDGET_CAP:
        log(f"Budget exceeded (${cost:.5f} > ${BUDGET_CAP}). Aborting.")
        sys.exit(1)

    for i, tweet in enumerate(tweets, 1):
        log(f"Tweet {i}: {tweet[:80]}{'...' if len(tweet) > 80 else ''}")
        if len(tweet) > 280:
            log(f"  WARNING: tweet {i} is {len(tweet)} chars — truncating")
            tweet = tweet[:277] + "..."
        try:
            tweet_id = post_tweet(tweet)
            log(f"  Posted: https://twitter.com/i/web/status/{tweet_id}")
        except Exception as e:
            log(f"  ERROR posting tweet {i}: {e}")

    log(f"=== DONE | Total cost: ~${cost:.5f} ===")

if __name__ == "__main__":
    main()
