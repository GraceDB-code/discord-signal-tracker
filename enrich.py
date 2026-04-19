"""
Enrich existing signals with identity data.
1. Fetches Discord profiles (connected accounts: GitHub, Twitter, LinkedIn)
2. Follows GitHub links to pull social/website info via GitHub API
3. Extracts URLs from message content

Usage:
    python enrich.py                 (enrich all signals missing identity data)
    python enrich.py --force         (re-enrich all signals)
"""

import os
import re
import sys
import json
import time
import argparse
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

SIGNALS_FILE = Path(__file__).parent / "signals.json"
USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("enrich")

DISCORD_API = "https://discord.com/api/v10"
DISCORD_HEADERS = {
    "Authorization": USER_TOKEN or "",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "discord-signal-tracker",
}

# Caches to avoid duplicate API calls
_discord_cache: dict[str, dict] = {}
_github_cache: dict[str, dict] = {}


def extract_urls(text):
    return re.findall(r'https?://[^\s<>\)\]\"\'`]+', text or "")


def fetch_discord_profile(user_id):
    """Fetch a Discord user's connected accounts and bio."""
    if user_id in _discord_cache:
        return _discord_cache[user_id]

    if not USER_TOKEN:
        _discord_cache[user_id] = {}
        return {}

    url = f"{DISCORD_API}/users/{user_id}/profile"
    resp = requests.get(url, headers=DISCORD_HEADERS, params={"with_mutual_guilds": "false"})
    time.sleep(1.5)

    profile = {}

    if resp.status_code == 429:
        retry = resp.json().get("retry_after", 10)
        log.warning(f"Rate limited, waiting {retry}s")
        time.sleep(retry + 1)
        return fetch_discord_profile(user_id)

    if resp.status_code != 200:
        log.debug(f"Discord profile {user_id}: HTTP {resp.status_code}")
        _discord_cache[user_id] = profile
        return profile

    data = resp.json()
    user_data = data.get("user", {})
    profile["bio"] = user_data.get("bio", "") or ""

    for account in data.get("connected_accounts", []):
        acct_type = account.get("type", "").lower()
        acct_name = account.get("name", "")
        if acct_type == "github":
            profile["github"] = f"https://github.com/{acct_name}"
        elif acct_type == "twitter":
            profile["twitter"] = f"https://x.com/{acct_name}"
        elif acct_type == "linkedin":
            profile["linkedin"] = f"https://www.linkedin.com/in/{acct_name}"
        elif acct_type == "domain":
            profile["website"] = acct_name if acct_name.startswith("http") else f"https://{acct_name}"

    # Check bio for URLs
    if profile.get("bio"):
        for url in extract_urls(profile["bio"]):
            url_lower = url.lower()
            if "linkedin.com" in url_lower and "linkedin" not in profile:
                profile["linkedin"] = url
            elif "github.com" in url_lower and "github" not in profile:
                profile["github"] = url
            elif ("twitter.com" in url_lower or "x.com" in url_lower) and "twitter" not in profile:
                profile["twitter"] = url
            elif "website" not in profile and "discord" not in url_lower:
                profile["website"] = url

    _discord_cache[user_id] = profile
    return profile


def fetch_github_profile(github_url):
    """Fetch a GitHub user's profile for social links."""
    # Extract username from URL
    match = re.match(r'https?://github\.com/([^/]+)/?$', github_url)
    if not match:
        return {}

    username = match.group(1)
    if username in _github_cache:
        return _github_cache[username]

    resp = requests.get(f"https://api.github.com/users/{username}", headers=GITHUB_HEADERS)
    time.sleep(1)  # Respect rate limits (60/hr unauthenticated)

    if resp.status_code != 200:
        log.debug(f"GitHub {username}: HTTP {resp.status_code}")
        _github_cache[username] = {}
        return {}

    data = resp.json()
    profile = {}

    # Blog field often has LinkedIn or personal site
    blog = (data.get("blog") or "").strip()
    if blog:
        if not blog.startswith("http"):
            blog = f"https://{blog}"
        blog_lower = blog.lower()
        if "linkedin.com" in blog_lower:
            profile["linkedin"] = blog
        elif "twitter.com" in blog_lower or "x.com" in blog_lower:
            profile["twitter"] = blog
        else:
            profile["website"] = blog

    # Twitter username field
    twitter = data.get("twitter_username")
    if twitter and "twitter" not in profile:
        profile["twitter"] = f"https://x.com/{twitter}"

    # Bio might have LinkedIn
    bio = data.get("bio") or ""
    if bio:
        for url in extract_urls(bio):
            url_lower = url.lower()
            if "linkedin.com" in url_lower and "linkedin" not in profile:
                profile["linkedin"] = url

    # Store name/company for context
    if data.get("name"):
        profile["real_name"] = data["name"]
    if data.get("company"):
        profile["company"] = data["company"]

    _github_cache[username] = profile
    return profile


def extract_message_links(content):
    """Extract identity links from message content."""
    links = {}
    for url in extract_urls(content):
        url_lower = url.lower()
        if "github.com" in url_lower and "/github.com/" not in url_lower:
            # Only grab user profile links, not repo links with many path segments
            if re.match(r'https?://github\.com/[^/]+/?$', url):
                links.setdefault("github", url)
        elif "linkedin.com" in url_lower:
            links.setdefault("linkedin", url)
        elif "twitter.com" in url_lower or "x.com" in url_lower:
            links.setdefault("twitter", url)
    return links


def enrich_signal(signal):
    """Enrich a single signal with identity data. Returns True if updated."""
    updated = False
    author_id = signal.get("author_id")
    content = signal.get("content", "")

    # 1. Extract links from message content
    msg_links = extract_message_links(content)
    for field in ("github", "twitter", "linkedin"):
        if msg_links.get(field) and not signal.get(field):
            signal[field] = msg_links[field]
            updated = True

    # 2. Fetch Discord profile
    if author_id:
        discord_profile = fetch_discord_profile(author_id)
        for field in ("github", "twitter", "linkedin", "website", "bio"):
            if discord_profile.get(field) and not signal.get(field):
                signal[field] = discord_profile[field]
                updated = True

    # 3. If we have a GitHub link, fetch GitHub profile for more data
    if signal.get("github"):
        gh_profile = fetch_github_profile(signal["github"])
        for field in ("twitter", "linkedin", "website"):
            if gh_profile.get(field) and not signal.get(field):
                signal[field] = gh_profile[field]
                updated = True
        # Store GitHub real name if we don't have a good display name
        if gh_profile.get("real_name") and not signal.get("real_name"):
            signal["real_name"] = gh_profile["real_name"]
            updated = True
        if gh_profile.get("company") and not signal.get("company"):
            signal["company"] = gh_profile["company"]
            updated = True

    return updated


def main():
    parser = argparse.ArgumentParser(description="Enrich signals with identity data")
    parser.add_argument("--force", action="store_true", help="Re-enrich all signals")
    args = parser.parse_args()

    signals = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
    log.info(f"Loaded {len(signals)} signals")

    # Deduplicate by author_id to avoid redundant API calls
    unique_authors = {}
    for s in signals:
        aid = s.get("author_id")
        if aid and aid not in unique_authors:
            unique_authors[aid] = s.get("author", "unknown")
    log.info(f"Found {len(unique_authors)} unique authors to enrich")

    enriched_count = 0
    total = len(signals)

    for i, signal in enumerate(signals):
        needs_enrichment = args.force or not any(
            signal.get(f) for f in ("github", "twitter", "linkedin", "bio")
        )
        if not needs_enrichment:
            continue

        if enrich_signal(signal):
            enriched_count += 1

        if (i + 1) % 50 == 0:
            log.info(f"Progress: {i + 1}/{total} signals processed, {enriched_count} enriched")
            # Save incrementally
            SIGNALS_FILE.write_text(json.dumps(signals, indent=2, ensure_ascii=False), encoding="utf-8")

    # Final save
    SIGNALS_FILE.write_text(json.dumps(signals, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Done. Enriched {enriched_count} signals.")


if __name__ == "__main__":
    main()
