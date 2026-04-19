"""
Configuration for Discord Founder Signal Monitor.
Edit the keyword lists and channel names to customize what gets flagged.
"""

# ── Keyword Categories ──────────────────────────────────────────────
# Each category has a priority level: "high", "medium", or "low"
# High-priority matches get sent to the high-priority Slack webhook (if configured)

KEYWORD_CONFIG = {
    "founder_activity": {
        "priority": "high",
        "keywords": [
            "launching", "shipped", "i built", "we built", "just launched",
            "shipping today", "co-founder", "cofounder", "looking for a cofounder",
            "seeking cofounder", "founding team", "started a company",
            "my startup", "our startup", "pre-seed", "seed round", "raised",
            "fundraising", "funding round", "series a", "angel round",
            "first customer", "first users", "waitlist", "beta users",
            "paying customers", "demo day", "applied to yc", "got into yc",
        ],
    },
    "hiring": {
        "priority": "high",
        "keywords": [
            "we're hiring", "were hiring", "hiring for",
            "looking for engineers", "first hire", "founding engineer",
            "founding designer", "come build with", "join us",
            "open roles", "job posting",
        ],
    },
    "project_showcase": {
        "priority": "medium",
        "keywords": [
            "check out my", "here's what i built", "show and tell",
            "side project", "weekend project", "open sourced",
            "just open-sourced", "demo", "prototype", "proof of concept",
            "mvp", "v0.1", "alpha release",
        ],
    },
    "robotics": {
        "priority": "high",
        "keywords": [
            "robotics startup", "robot arm", "humanoid", "embodied ai",
            "manipulation", "sim-to-real", "sim2real", "ros2",
            "isaac sim", "lerobot", "foundation model for robot",
            "hardware prototype", "actuator", "end effector",
            "dexterous", "locomotion",
        ],
    },
    "ai_infrastructure": {
        "priority": "medium",
        "keywords": [
            "fine-tuned", "fine-tuning", "inference engine", "quantization",
            "model serving", "training run", "gpu cluster", "cuda kernel",
        ],
    },
    "intent_departure": {
        "priority": "high",
        "keywords": [
            "leaving my job", "left my job", "quit my job",
            "going full-time", "going fulltime", "full-time on this",
            "exploring what's next", "taking the leap",
            "building something new", "figuring out what to build",
        ],
    },
}

# ── Channel Watchlist ────────────────────────────────────────────────
# Only monitor messages in channels whose names contain one of these strings.
# Set to None or empty list to monitor ALL channels in joined servers.
# Using partial matches so "project-showcase" matches "showcase".

WATCHED_CHANNEL_NAMES = [
    "showcase",
    "projects",
    "project",
    "hiring",
    "jobs",
    "job-board",
    "introductions",
    "introduce-yourself",
    "show-and-tell",
    "general",
    "announcements",
    "launches",
    "feedback",
    "robotics",
    "research",
    "agent",
    "founders",
    "startups",
    "building",
]

# ── Ignored Channels ────────────────────────────────────────────────
# Never monitor these channels (exact or partial match).

IGNORED_CHANNEL_NAMES = [
    "memes",
    "off-topic",
    "random",
    "bot-commands",
    "bot-spam",
    "nsfw",
    "music",
    "gaming",
]

# ── Daily Digest ─────────────────────────────────────────────────────
# Time to send daily digest (UTC, 24h format)
DAILY_DIGEST_HOUR = 8  # 8 AM UTC = 6 PM AEST

# Max signals to include in digest before truncating
DIGEST_MAX_SIGNALS = 50
