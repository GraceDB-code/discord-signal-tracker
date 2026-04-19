"""
Discord Founder Signal Monitor
Monitors Discord servers for founder activity signals and forwards matches to Slack.
Serves a live web dashboard at http://localhost:8099

Usage:
    1. Create a Discord bot at https://discord.com/developers/applications
    2. Enable MESSAGE CONTENT INTENT in Bot settings
    3. Copy .env.example to .env and fill in your tokens
    4. pip install -r requirements.txt
    5. python bot.py

The bot must be invited to servers with these permissions:
    - View Channels
    - Read Message History
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import aiohttp
from aiohttp import web
import discord
from discord.ext import tasks
from dotenv import load_dotenv

from config import (
    KEYWORD_CONFIG,
    WATCHED_CHANNEL_NAMES,
    IGNORED_CHANNEL_NAMES,
    DAILY_DIGEST_HOUR,
    DIGEST_MAX_SIGNALS,
)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_WEBHOOK_HIGH = os.getenv("SLACK_WEBHOOK_HIGH_PRIORITY", SLACK_WEBHOOK)

DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8099"))
SIGNALS_FILE = Path(__file__).parent / "signals.json"
DASHBOARD_FILE = Path(__file__).parent / "dashboard.html"

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not set in .env")

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("founder-monitor")

# ── Build flat keyword lookup ────────────────────────────────────────

KEYWORDS = []  # list of (keyword, category, priority)
for category, cfg in KEYWORD_CONFIG.items():
    for kw in cfg["keywords"]:
        KEYWORDS.append((kw.lower(), category, cfg["priority"]))

# Sort longest first so "going full-time" matches before "going"
KEYWORDS.sort(key=lambda x: -len(x[0]))

# ── Persistent Signal Storage ────────────────────────────────────────

all_signals: list[dict] = []
daily_signals: list[dict] = []
signal_count = defaultdict(int)


def load_signals():
    """Load signals from disk on startup."""
    global all_signals
    if SIGNALS_FILE.exists():
        try:
            all_signals = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
            log.info(f"Loaded {len(all_signals)} signals from {SIGNALS_FILE}")
        except (json.JSONDecodeError, IOError) as e:
            log.error(f"Failed to load signals: {e}")
            all_signals = []
    else:
        all_signals = []


def save_signals():
    """Persist all signals to disk."""
    try:
        SIGNALS_FILE.write_text(
            json.dumps(all_signals, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except IOError as e:
        log.error(f"Failed to save signals: {e}")


def add_signal(signal: dict):
    """Add a signal to both in-memory lists and persist to disk."""
    all_signals.append(signal)
    daily_signals.append(signal)
    save_signals()


# ── Helpers ──────────────────────────────────────────────────────────

def should_watch_channel(channel_name: str) -> bool:
    """Check if a channel should be monitored based on config."""
    name = channel_name.lower()
    for ignored in IGNORED_CHANNEL_NAMES:
        if ignored.lower() in name:
            return False
    if not WATCHED_CHANNEL_NAMES:
        return True
    for watched in WATCHED_CHANNEL_NAMES:
        if watched.lower() in name:
            return True
    return False


def find_keyword_matches(text: str) -> list[tuple[str, str, str]]:
    """Find all matching keywords in a message. Returns list of (keyword, category, priority)."""
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


def truncate(text: str, length: int = 300) -> str:
    """Truncate text to a maximum length."""
    if len(text) <= length:
        return text
    return text[:length] + "..."


def format_slack_message(message: discord.Message, matches: list) -> dict:
    """Format a Discord message into a Slack Block Kit message."""
    categories = ", ".join(f"`{m[1]}`" for m in matches)
    keywords_hit = ", ".join(f"*{m[0]}*" for m in matches)
    is_high = any(m[2] == "high" for m in matches)
    priority_emoji = ":rotating_light:" if is_high else ":mag:"

    jump_url = message.jump_url
    server_name = message.guild.name if message.guild else "DM"
    channel_name = message.channel.name if hasattr(message.channel, "name") else "unknown"
    author_name = str(message.author)
    content = truncate(message.content, 500)

    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{priority_emoji} Founder Signal Detected",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Server:*\n{server_name}"},
                    {"type": "mrkdwn", "text": f"*Channel:*\n#{channel_name}"},
                    {"type": "mrkdwn", "text": f"*Author:*\n{author_name}"},
                    {"type": "mrkdwn", "text": f"*Categories:*\n{categories}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Keywords matched:* {keywords_hit}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f">>> {content}",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View on Discord"},
                        "url": jump_url,
                    }
                ],
            },
            {"type": "divider"},
        ],
    }


def format_digest(signals: list[dict]) -> dict:
    """Format the daily digest as a Slack message."""
    if not signals:
        return {
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":newspaper: *Daily Founder Signal Digest*\n\nNo signals detected in the past 24 hours.",
                    },
                }
            ]
        }

    count = len(signals)
    high_count = sum(1 for s in signals if s["is_high_priority"])

    cat_counts = defaultdict(int)
    for s in signals:
        for cat in s["categories"]:
            cat_counts[cat] += 1
    cat_summary = "\n".join(f"  - {cat}: {n}" for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]))

    server_counts = defaultdict(int)
    for s in signals:
        server_counts[s["server"]] += 1
    server_summary = "\n".join(f"  - {srv}: {n}" for srv, n in sorted(server_counts.items(), key=lambda x: -x[1]))

    top = sorted(signals, key=lambda x: (not x["is_high_priority"], x["timestamp"]))
    top = top[:DIGEST_MAX_SIGNALS]

    signal_lines = []
    for s in top:
        emoji = ":rotating_light:" if s["is_high_priority"] else ":mag:"
        signal_lines.append(
            f"{emoji} *{s['server']}* / #{s['channel']} -- _{s['author']}_\n"
            f"  Keywords: {', '.join(s['keywords'])}\n"
            f"  > {truncate(s['content'], 200)}\n"
            f"  <{s['jump_url']}|View on Discord>"
        )

    signals_text = "\n\n".join(signal_lines)

    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":newspaper: Daily Founder Signal Digest",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{count} signals detected* ({high_count} high priority)\n\n"
                        f"*By category:*\n{cat_summary}\n\n"
                        f"*By server:*\n{server_summary}"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": signals_text,
                },
            },
        ],
    }


async def send_to_slack(payload: dict, high_priority: bool = False):
    """Send a message to Slack via webhook."""
    url = SLACK_WEBHOOK_HIGH if high_priority else SLACK_WEBHOOK
    if not url:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error(f"Slack webhook returned {resp.status}: {body}")
    except Exception as e:
        log.error(f"Failed to send to Slack: {e}")


# ── Web Dashboard ────────────────────────────────────────────────────

async def handle_dashboard(request):
    """Serve the dashboard HTML page."""
    if DASHBOARD_FILE.exists():
        html = DASHBOARD_FILE.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")
    return web.Response(text="Dashboard file not found", status=404)


async def handle_signals_api(request):
    """Return all signals as JSON."""
    return web.json_response(all_signals, headers={
        "Access-Control-Allow-Origin": "*",
    })


async def handle_stats_api(request):
    """Return summary stats as JSON."""
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).isoformat()

    stats = {
        "total": len(all_signals),
        "today": sum(1 for s in all_signals if s.get("timestamp", "")[:10] == today_str),
        "this_week": sum(1 for s in all_signals if s.get("timestamp", "") >= week_ago),
        "high_priority": sum(1 for s in all_signals if s.get("is_high_priority")),
        "servers": list(set(s.get("server", "") for s in all_signals)),
        "categories": dict(defaultdict(int)),
    }
    cat_counts = defaultdict(int)
    for s in all_signals:
        for c in s.get("categories", []):
            cat_counts[c] += 1
    stats["categories"] = dict(cat_counts)

    return web.json_response(stats, headers={
        "Access-Control-Allow-Origin": "*",
    })


async def start_web_server():
    """Start the aiohttp web server for the dashboard."""
    app = web.Application()
    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/api/signals", handle_signals_api)
    app.router.add_get("/api/stats", handle_stats_api)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", DASHBOARD_PORT)
    await site.start()
    log.info(f"Dashboard running at http://localhost:{DASHBOARD_PORT}")


# ── Bot Setup ────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # PRIVILEGED -- must be enabled in Developer Portal
intents.guilds = True

bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    log.info(f"Connected to {len(bot.guilds)} servers:")
    for guild in bot.guilds:
        log.info(f"  - {guild.name} ({guild.member_count} members)")
    log.info(f"Monitoring {len(KEYWORDS)} keywords across {len(KEYWORD_CONFIG)} categories")
    log.info(f"Watching channel patterns: {WATCHED_CHANNEL_NAMES}")

    if not daily_digest_task.is_running():
        daily_digest_task.start()

    # Start web dashboard
    await start_web_server()


@bot.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Ignore DMs
    if not message.guild:
        return

    # Check if channel is watched
    channel_name = message.channel.name if hasattr(message.channel, "name") else ""
    if not should_watch_channel(channel_name):
        return

    # Skip empty messages
    if not message.content:
        return

    # Check for keyword matches
    matches = find_keyword_matches(message.content)
    if not matches:
        return

    is_high = any(m[2] == "high" for m in matches)

    # Log the match
    log.info(
        f"SIGNAL [{', '.join(m[1] for m in matches)}] "
        f"in {message.guild.name}/#{channel_name} "
        f"by {message.author}: {truncate(message.content, 100)}"
    )

    # Update counters
    for _, category, _ in matches:
        signal_count[category] += 1

    # Store signal persistently
    signal = {
        "server": message.guild.name,
        "channel": channel_name,
        "author": str(message.author),
        "content": message.content,
        "keywords": [m[0] for m in matches],
        "categories": [m[1] for m in matches],
        "is_high_priority": is_high,
        "jump_url": message.jump_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    add_signal(signal)

    # Send real-time alert to Slack for high-priority signals
    if is_high and SLACK_WEBHOOK:
        slack_msg = format_slack_message(message, matches)
        await send_to_slack(slack_msg, high_priority=True)


# ── Daily Digest Task ────────────────────────────────────────────────

@tasks.loop(hours=24)
async def daily_digest_task():
    """Send a daily digest of all signals to Slack."""
    global daily_signals

    if not daily_signals:
        log.info("No signals to digest today")
        return

    if SLACK_WEBHOOK:
        log.info(f"Sending daily digest with {len(daily_signals)} signals")
        digest = format_digest(daily_signals)
        await send_to_slack(digest, high_priority=False)

    # Reset daily list (all_signals persists)
    daily_signals = []


@daily_digest_task.before_loop
async def before_digest():
    """Wait until the scheduled hour before starting the digest loop."""
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    target = now.replace(hour=DAILY_DIGEST_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    wait_seconds = (target - now).total_seconds()
    log.info(f"Daily digest scheduled for {target.isoformat()}. Waiting {wait_seconds:.0f}s")
    await asyncio.sleep(wait_seconds)


# ── Run ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: Set DISCORD_BOT_TOKEN in your .env file")
        print("See .env.example for the template")
        exit(1)

    # Load existing signals from disk
    load_signals()

    bot.run(DISCORD_TOKEN)
