# Discord Founder Signal Monitor

Monitors Discord servers for AI/robotics founder activity signals and forwards matches to Slack.

## What It Does

- Watches specific channels (#showcase, #projects, #hiring, #introductions, etc.) across all servers the bot is added to
- Matches messages against keyword lists organized by category (founder activity, hiring, robotics, AI infrastructure, etc.)
- Sends real-time Slack alerts for high-priority signals (someone announcing they are building a company, hiring, raising, etc.)
- Sends a daily digest of all signals to a Slack channel at 8 AM UTC (6 PM AEST)

## Setup (15 minutes)

### Step 1: Create a Discord Bot

1. Go to https://discord.com/developers/applications
2. Click "New Application", name it something like "SP Signal Monitor"
3. Go to the **Bot** tab
4. Click "Reset Token" and copy the token (you will need this)
5. Under **Privileged Gateway Intents**, enable:
   - **MESSAGE CONTENT INTENT** (required -- this lets the bot read message text)
   - **SERVER MEMBERS INTENT** (optional, not strictly needed)
6. Under **Bot Permissions**, enable:
   - View Channels
   - Read Message History

### Step 2: Invite the Bot to Your Server(s)

1. Go to the **OAuth2** tab in your Discord application
2. Under **URL Generator**, select:
   - Scopes: `bot`
   - Bot Permissions: `View Channels`, `Read Message History`
3. Copy the generated URL and open it in your browser
4. Select the server to add the bot to and click "Authorize"

**Note:** You can only add the bot to servers where you have "Manage Server" permission. For servers you just joined as a regular member (like EleutherAI or Latent Space), you cannot add this bot. Use Discord's built-in keyword alerts instead (see Discord_Keyword_Alert_Setup.md).

### Step 3: Create a Slack Webhook

1. Go to https://api.slack.com/messaging/webhooks
2. Create a new Slack app (or use an existing one)
3. Enable "Incoming Webhooks"
4. Add a new webhook to a channel (e.g., #founder-signals)
5. Copy the webhook URL

Optionally create a second webhook for a #founder-signals-urgent channel for high-priority alerts.

### Step 4: Configure and Run

```bash
# Clone or copy the bot files
cd discord-bot

# Install dependencies
pip install -r requirements.txt

# Create your .env file
cp .env.example .env
# Edit .env with your Discord token and Slack webhook URL

# Run the bot
python bot.py
```

## Configuration

Edit `config.py` to customize:

- **KEYWORD_CONFIG**: Add/remove keywords by category. Each category has a priority level (high/medium/low). High-priority matches trigger real-time Slack alerts.
- **WATCHED_CHANNEL_NAMES**: Which channel names to monitor (partial match). Set to empty list to watch all channels.
- **IGNORED_CHANNEL_NAMES**: Channels to never monitor.
- **DAILY_DIGEST_HOUR**: When to send the daily digest (UTC, 24h).

## Running in Background

### On your local machine (simple)
```bash
# Windows: use pythonw to run without a console window
pythonw bot.py

# Or use nohup on Linux/Mac
nohup python bot.py &
```

### On a cloud server (recommended for 24/7 uptime)

The bot is lightweight and runs fine on the smallest tier of any cloud provider:
- **Railway** (railway.app) -- free tier, deploy from GitHub
- **Render** (render.com) -- free tier for background workers
- **Fly.io** -- free tier, deploy with `fly launch`
- **AWS EC2 t2.micro** -- free tier eligible
- **Any VPS** -- $5/month DigitalOcean droplet is more than enough

## Architecture

```
Discord Servers (that you admin)
    |
    v
[Bot receives on_message events via WebSocket Gateway]
    |
    v
[Keyword matching against config.py lists]
    |
    +--> High priority match --> Real-time Slack alert (#founder-signals-urgent)
    |
    +--> Any match --> Stored in memory for daily digest
    |
    v
[Daily at 8 AM UTC] --> Digest sent to Slack (#founder-signals)
```

## Limitations

- **Only works on servers where you are an admin** and can invite the bot. For public servers you are just a member of, use Discord's built-in keyword alerts.
- **Message content is in-memory only** -- signals are not persisted to a database. If the bot restarts, the daily digest resets. For persistence, you would add a SQLite or PostgreSQL database.
- **No AI summarization** -- messages are forwarded as-is. You could add an LLM call to summarize or score signals, but that adds cost and complexity.

## Files

```
discord-bot/
  bot.py              Main bot code
  config.py           Keywords, channels, and settings
  requirements.txt    Python dependencies
  .env.example        Template for secrets
  README.md           This file
```
