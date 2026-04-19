"""
Discord Channel Scraper
Fetches messages from Discord channels using a user token and runs keyword matching.
Signals are saved to signals.json for the dashboard.

WARNING: Using a user token for automation is against Discord's Terms of Service.
         Your account could be banned. Use at your own risk.

Usage:
    1. Get your user token from browser DevTools (see README)
    2. Add it to .env as DISCORD_USER_TOKEN
    3. Configure channels in CHANNELS below
    4. python scraper.py              (one-time scrape)
    5. python scraper.py --watch      (continuous polling every 5 min)
    6. python scraper.py --backfill   (fetch last 500 messages per channel)
"""

import os
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

from config import KEYWORD_CONFIG, WATCHED_CHANNEL_NAMES, IGNORED_CHANNEL_NAMES

load_dotenv()

USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
SIGNALS_FILE = Path(__file__).parent / "signals.json"

if not USER_TOKEN:
    print("Error: Set DISCORD_USER_TOKEN in your .env file")
    print("To get your token:")
    print("  1. Open Discord in your browser")
    print("  2. Open DevTools (F12) > Network tab")
    print("  3. Click around in a channel")
    print("  4. Find a request to discord.com/api")
    print("  5. Copy the Authorization header value")
    sys.exit(1)

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scraper")

# ── Channels to Monitor ─────────────────────────────────────────────
# Format: (server_id, channel_id, friendly_name)
# Add your channels here. The Discord URL format is:
#   https://discord.com/channels/{server_id}/{channel_id}

# ── Servers to Monitor (scrapes ALL text channels) ──────────────────
# Just drop in the server ID from any Discord URL: discord.com/channels/{server_id}/...
SERVERS = [
    "814557108065534033",   # MLOps
    "714501525455634453",   # Hugging Face
    # Add more server IDs below:
]

# ── Individual Channels (optional, for servers you don't want to scrape fully) ──
CHANNELS = [
    # ("server_id", "channel_id", "Server / #channel"),
]

# How many messages to fetch per request (max 100)
FETCH_LIMIT = 100

# Polling interval in seconds for --watch mode
POLL_INTERVAL = 300  # 5 minutes

# Rate limit: seconds to wait between API calls
RATE_LIMIT_DELAY = 1.5

# ── Build keyword lookup from config ────────────────────────────────

KEYWORDS = []
for category, cfg in KEYWORD_CONFIG.items():
    for kw in cfg["keywords"]:
        KEYWORDS.append((kw.lower(), category, cfg["priority"]))
KEYWORDS.sort(key=lambda x: -len(x[0]))


# ── Discord API ─────────────────────────────────────────────────────

API_BASE = "https://discord.com/api/v10"

HEADERS = {
    "Authorization": USER_TOKEN,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}


def api_get(endpoint, params=None):
    """Make a GET request to the Discord API with rate limit handling."""
    url = f"{API_BASE}{endpoint}"
    resp = requests.get(url, headers=HEADERS, params=params)

    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 5)
        log.warning(f"Rate limited. Waiting {retry_after}s...")
        time.sleep(retry_after + 0.5)
        return api_get(endpoint, params)

    if resp.status_code == 401:
        log.error("Invalid or expired token. Get a fresh token from browser DevTools.")
        sys.exit(1)

    if resp.status_code == 403:
        log.error(f"No access to {endpoint}. You may not be a member of this server/channel.")
        return None

    if resp.status_code != 200:
        log.error(f"API returned {resp.status_code}: {resp.text[:200]}")
        return None

    return resp.json()


def get_guild_channels(guild_id):
    """Get all text channels in a server, filtered by watched/ignored lists."""
    data = api_get(f"/guilds/{guild_id}/channels")
    if not data:
        return []

    text_channels = []
    for ch in data:
        # Type 0 = text channel, 5 = announcement channel
        if ch.get("type") not in (0, 5):
            continue

        name = ch.get("name", "")

        # Apply ignored channel filter
        skip = False
        for ignored in IGNORED_CHANNEL_NAMES:
            if ignored.lower() in name.lower():
                skip = True
                break
        if skip:
            continue

        text_channels.append({
            "id": ch["id"],
            "name": name,
        })

    log.info(f"  Found {len(text_channels)} text channels in server {guild_id}")
    return text_channels


def get_channel_info(channel_id):
    """Get channel name and guild info."""
    data = api_get(f"/channels/{channel_id}")
    if data:
        return {
            "channel_name": data.get("name", "unknown"),
            "guild_id": data.get("guild_id"),
        }
    return None


def get_guild_info(guild_id):
    """Get server/guild name."""
    data = api_get(f"/guilds/{guild_id}")
    if data:
        return data.get("name", "Unknown Server")
    return "Unknown Server"


def fetch_messages(channel_id, limit=100, before=None, after=None):
    """Fetch messages from a channel."""
    params = {"limit": min(limit, 100)}
    if before:
        params["before"] = before
    if after:
        params["after"] = after

    data = api_get(f"/channels/{channel_id}/messages", params=params)
    return data or []


# ── Identity Enrichment ─────────────────────────────────────────────

import re

# Cache profile lookups to avoid hitting the API for the same user twice
_profile_cache: dict[str, dict] = {}


def get_user_profile(user_id):
    """Fetch a Discord user's profile including connected accounts."""
    if user_id in _profile_cache:
        return _profile_cache[user_id]

    data = api_get(f"/users/{user_id}/profile", params={"with_mutual_guilds": "false"})
    time.sleep(RATE_LIMIT_DELAY)

    profile = {
        "github": None,
        "twitter": None,
        "linkedin": None,
        "website": None,
        "bio": None,
    }

    if not data:
        _profile_cache[user_id] = profile
        return profile

    # Extract bio
    user_data = data.get("user", {})
    profile["bio"] = user_data.get("bio", "") or ""

    # Extract connected accounts (GitHub, Twitter, etc.)
    for account in data.get("connected_accounts", []):
        acct_type = account.get("type", "").lower()
        acct_name = account.get("name", "")

        if acct_type == "github":
            profile["github"] = f"https://github.com/{acct_name}"
        elif acct_type == "twitter":
            profile["twitter"] = f"https://x.com/{acct_name}"
        elif acct_type == "linkedin":
            # LinkedIn connected accounts only give an ID, not a vanity URL
            profile["linkedin"] = f"https://www.linkedin.com/in/{acct_name}"
        elif acct_type == "domain":
            profile["website"] = acct_name if acct_name.startswith("http") else f"https://{acct_name}"

    # Check bio for URLs we might have missed
    if profile["bio"]:
        bio_urls = extract_urls(profile["bio"])
        for url in bio_urls:
            if "linkedin.com" in url and not profile["linkedin"]:
                profile["linkedin"] = url
            elif "github.com" in url and not profile["github"]:
                profile["github"] = url
            elif "twitter.com" in url or "x.com" in url and not profile["twitter"]:
                profile["twitter"] = url
            elif not profile["website"] and "discord" not in url:
                profile["website"] = url

    _profile_cache[user_id] = profile
    return profile


def extract_urls(text):
    """Extract URLs from message text."""
    url_pattern = r'https?://[^\s<>\)\]\"\'`]+'
    return re.findall(url_pattern, text)


def extract_identity_from_message(content):
    """Pull any identity-related URLs from a message."""
    urls = extract_urls(content)
    identity = {
        "github_links": [],
        "linkedin_links": [],
        "twitter_links": [],
        "other_links": [],
    }
    for url in urls:
        url_lower = url.lower()
        if "github.com" in url_lower:
            identity["github_links"].append(url)
        elif "linkedin.com" in url_lower:
            identity["linkedin_links"].append(url)
        elif "twitter.com" in url_lower or "x.com" in url_lower:
            identity["twitter_links"].append(url)
        elif "huggingface.co" in url_lower or any(d in url_lower for d in ["youtube.com", "medium.com", "substack.com"]):
            identity["other_links"].append(url)
    return identity


# ── Keyword Matching ────────────────────────────────────────────────

def find_keyword_matches(text):
    """Find all matching keywords in text."""
    text_lower = text.lower()
    matches = []
    seen_categories = set()
    for kw, category, priority in KEYWORDS:
        if category in seen_categories:
            continue
        if kw in text_lower:
            matches.append((kw, category, priority))
            seen_categories.add(category)
    return matches


# ── Signal Storage ──────────────────────────────────────────────────

def load_signals():
    """Load existing signals from disk."""
    if SIGNALS_FILE.exists():
        try:
            return json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_signals(signals):
    """Save signals to disk."""
    SIGNALS_FILE.write_text(
        json.dumps(signals, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_seen_message_ids(signals):
    """Get set of already-processed message IDs to avoid duplicates."""
    return set(s.get("message_id") for s in signals if s.get("message_id"))


# ── Main Scraping Logic ────────────────────────────────────────────

def process_messages(messages, server_name, channel_name, server_id, channel_id, seen_ids):
    """Process a batch of messages and return new signals."""
    new_signals = []

    for msg in messages:
        msg_id = msg["id"]
        if msg_id in seen_ids:
            continue

        # Skip bot messages
        author = msg.get("author", {})
        if author.get("bot", False):
            continue

        content = msg.get("content", "")
        if not content:
            continue

        # Check keywords
        matches = find_keyword_matches(content)
        if not matches:
            continue

        is_high = any(m[2] == "high" for m in matches)
        author_name = f"{author.get('username', 'unknown')}#{author.get('discriminator', '0')}"
        # Modern Discord uses display names
        display_name = author.get("global_name") or author.get("username", "unknown")

        timestamp = msg.get("timestamp", datetime.now(timezone.utc).isoformat())
        jump_url = f"https://discord.com/channels/{server_id}/{channel_id}/{msg_id}"

        # Enrich with identity data
        user_id = author.get("id")
        profile = get_user_profile(user_id) if user_id else {}
        msg_links = extract_identity_from_message(content)

        signal = {
            "message_id": msg_id,
            "server": server_name,
            "channel": channel_name,
            "author": display_name,
            "author_id": user_id,
            "username": author.get("username", ""),
            "content": content[:1000],
            "keywords": [m[0] for m in matches],
            "categories": [m[1] for m in matches],
            "is_high_priority": is_high,
            "jump_url": jump_url,
            "timestamp": timestamp,
            # Identity fields
            "github": profile.get("github") or (msg_links["github_links"][0] if msg_links["github_links"] else None),
            "twitter": profile.get("twitter") or (msg_links["twitter_links"][0] if msg_links["twitter_links"] else None),
            "linkedin": profile.get("linkedin") or (msg_links["linkedin_links"][0] if msg_links["linkedin_links"] else None),
            "website": profile.get("website"),
            "bio": profile.get("bio"),
            "shared_links": msg_links["other_links"][:5],
        }

        new_signals.append(signal)
        seen_ids.add(msg_id)

        log.info(
            f"SIGNAL [{', '.join(m[1] for m in matches)}] "
            f"in {server_name}/#{channel_name} "
            f"by {display_name}: {content[:80]}..."
        )

    return new_signals


def scrape_channel(server_id, channel_id, server_name, channel_name, seen_ids, backfill=False):
    """Scrape a single channel for keyword signals."""
    friendly = f"{server_name} / #{channel_name}"
    log.info(f"Scraping {friendly} (channel {channel_id})...")

    all_new = []

    if backfill:
        before = None
        for page in range(5):
            log.info(f"  Fetching page {page + 1}/5 from #{channel_name}...")
            messages = fetch_messages(channel_id, limit=100, before=before)
            if not messages:
                break

            new = process_messages(messages, server_name, channel_name, server_id, channel_id, seen_ids)
            all_new.extend(new)

            before = messages[-1]["id"]
            time.sleep(RATE_LIMIT_DELAY)
    else:
        messages = fetch_messages(channel_id, limit=100)
        if messages:
            new = process_messages(messages, server_name, channel_name, server_id, channel_id, seen_ids)
            all_new.extend(new)

    if all_new:
        log.info(f"  Found {len(all_new)} new signals in {friendly}")
    return all_new


def discover_channels_for_servers():
    """Discover all text channels for each server in the SERVERS list."""
    discovered = []
    for guild_id in SERVERS:
        server_name = get_guild_info(guild_id)
        time.sleep(RATE_LIMIT_DELAY)

        channels = get_guild_channels(guild_id)
        time.sleep(RATE_LIMIT_DELAY)

        for ch in channels:
            discovered.append((guild_id, ch["id"], server_name, ch["name"]))

        log.info(f"Server '{server_name}': {len(channels)} channels to scrape")

    return discovered


def run_scrape(backfill=False):
    """Run a single scrape pass across all configured servers and channels."""
    signals = load_signals()
    seen_ids = get_seen_message_ids(signals)
    total_new = 0

    # Discover all channels from SERVERS list
    all_targets = discover_channels_for_servers()

    # Add individual CHANNELS entries
    for server_id, channel_id, friendly_name in CHANNELS:
        # Look up names for manually-specified channels
        channel_info = get_channel_info(channel_id)
        time.sleep(RATE_LIMIT_DELAY)
        if channel_info:
            server_name = get_guild_info(channel_info.get("guild_id", server_id))
            time.sleep(RATE_LIMIT_DELAY)
            all_targets.append((server_id, channel_id, server_name, channel_info["channel_name"]))

    log.info(f"Total channels to scrape: {len(all_targets)}")

    for server_id, channel_id, server_name, channel_name in all_targets:
        new_signals = scrape_channel(server_id, channel_id, server_name, channel_name, seen_ids, backfill=backfill)
        signals.extend(new_signals)
        total_new += len(new_signals)
        time.sleep(RATE_LIMIT_DELAY)

    if total_new > 0:
        # Sort by timestamp (newest first in the file)
        signals.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
        save_signals(signals)
        log.info(f"Saved {total_new} new signals ({len(signals)} total)")
    else:
        log.info("No new signals found")

    return total_new


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape Discord channels for founder signals")
    parser.add_argument("--watch", action="store_true", help="Continuously poll every 5 minutes")
    parser.add_argument("--backfill", action="store_true", help="Fetch last ~500 messages per channel")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL, help="Poll interval in seconds (default: 300)")
    args = parser.parse_args()

    log.info(f"Monitoring {len(CHANNELS)} channels with {len(KEYWORDS)} keywords")

    if args.watch:
        log.info(f"Watch mode: polling every {args.interval}s. Press Ctrl+C to stop.")
        # Do an initial backfill on first run
        run_scrape(backfill=True)

        while True:
            try:
                time.sleep(args.interval)
                run_scrape(backfill=False)
            except KeyboardInterrupt:
                log.info("Stopped by user")
                break
    else:
        run_scrape(backfill=args.backfill)


if __name__ == "__main__":
    main()
