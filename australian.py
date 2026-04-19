"""
Scan signals for Australian connections.
Checks Discord bios, message content, GitHub profiles (location, bio, company),
and connected account metadata for any Australian signal.

Usage:
    python australian.py              (scan all signals)
    python australian.py --fetch-gh   (also fetch GitHub locations via API)
"""

import os
import re
import json
import time
import argparse
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

SIGNALS_FILE = Path(__file__).parent / "signals.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("australian")

GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "discord-signal-tracker",
}

# ── Australian Signal Keywords ──────────────────────────────────────
# Organized by specificity to reduce false positives

# High confidence: almost certainly means Australian connection
AU_STRONG = [
    "australia", "australian", "aussie",
    "aest", "aedt",
    ".com.au",
    "moved from australia", "based in australia",
    "from sydney", "from melbourne", "from brisbane",
    "from perth", "from adelaide", "from canberra",
    # Universities (specific enough to not false-positive)
    "unsw", "usyd", "unimelb", "monash university",
    "anu", "australian national university",
    "uts", "rmit", "university of queensland",
    "macquarie university", "deakin university",
    "university of adelaide", "university of western australia",
    "uwa", "qut", "university of wollongong",
    "curtin university", "swinburne",
    "university of sydney", "university of melbourne",
    # Research
    "csiro", "data61", "nicta",
    # VCs / accelerators
    "blackbird ventures", "blackbird vc", "startmate",
    "airtree", "square peg", "main sequence",
    "skip capital", "folklore ventures",
    "blackbird giants", "blackbird foundry",
    "sunrise festival", "aussie founders",
    "advance.org",
    # Notable AU companies
    "atlassian", "canva", "afterpay", "culture amp",
    "safety culture", "safetyculture", "go1", "buildkite",
    "linktree",
    "harrison.ai", "heidi health", "lorikeet", "marqo",
    "relevance ai", "leonardo.ai", "neara", "firmus",
    "hivery", "daisee", "build club", "buildclub",
    "antler australia",
]

# Medium confidence: could mean Australian connection but needs context
AU_MEDIUM = [
    "sydney", "melbourne", "brisbane", "perth", "adelaide",
    "canberra", "hobart", "darwin", "gold coast", "newcastle",
    "new south wales", "nsw", "victoria", "queensland",
    "western australia", "south australia", "tasmania",
]

# Words that appear in other contexts (Melbourne FL, Perth Scotland, etc.)
# We only match these in bios/locations, not message content
AU_LOCATION_ONLY = [
    "melbourne", "perth", "newcastle", "darwin",
]

# GitHub location strings that confirm Australia
AU_LOCATION_PATTERNS = [
    r"\baustralia\b", r"\baussie\b",
    r"\bsydney\b", r"\bmelbourne\b.*(?:au|vic|australia)",
    r"\bbrisbane\b", r"\bperth\b.*(?:au|wa|australia)",
    r"\badelaide\b", r"\bcanberra\b", r"\bhobart\b",
    r"\bgold coast\b", r"\bnewcastle\b.*(?:au|nsw|australia)",
    r"\bnsw\b", r"\bvic\b.*(?:au|australia)",
    r"\bqld\b", r"\bact\b.*(?:au|australia)",
    r"\bmelb\b", r"\bsyd\b",
    # Country codes
    r"\bau\b$", r",\s*au\b", r"\baus\b$",
]


def check_australian_connection(text, source="content"):
    """
    Check if text contains Australian signals.
    Returns (is_match, confidence, signals_found) tuple.
    """
    if not text:
        return False, None, []

    text_lower = text.lower()
    signals = []

    # Check strong keywords
    for kw in AU_STRONG:
        if kw.lower() in text_lower:
            signals.append(kw)

    if signals:
        return True, "high", signals

    # Check medium keywords
    for kw in AU_MEDIUM:
        # For ambiguous city names in message content, require more context
        if kw.lower() in AU_LOCATION_ONLY and source == "content":
            # In message content, require "in melbourne" / "from melbourne" etc.
            pattern = rf'(?:in|from|based in|living in|moved to|born in)\s+{re.escape(kw)}'
            if re.search(pattern, text_lower):
                signals.append(kw)
        elif kw.lower() in text_lower:
            signals.append(kw)

    if signals:
        return True, "medium", signals

    # Check location patterns (for bio/location fields)
    if source in ("location", "bio"):
        for pattern in AU_LOCATION_PATTERNS:
            if re.search(pattern, text_lower):
                signals.append(f"location:{pattern}")
                return True, "high", signals

    return False, None, []


_gh_location_cache: dict[str, str | None] = {}


def fetch_github_location(github_url):
    """Fetch location field from a GitHub profile."""
    match = re.match(r'https?://github\.com/([^/]+)/?$', github_url or "")
    if not match:
        return None

    username = match.group(1)
    if username in _gh_location_cache:
        return _gh_location_cache[username]

    resp = requests.get(f"https://api.github.com/users/{username}", headers=GITHUB_HEADERS)
    time.sleep(1.2)

    if resp.status_code != 200:
        _gh_location_cache[username] = None
        return None

    data = resp.json()
    location = data.get("location") or ""
    bio = data.get("bio") or ""
    company = data.get("company") or ""

    # Store all text for checking
    combined = f"{location} | {bio} | {company}"
    _gh_location_cache[username] = combined
    return combined


def scan_signal(signal, fetch_gh=False):
    """
    Check a signal for Australian connections across all available data.
    Returns (is_australian, confidence, reasons) tuple.
    """
    all_signals = []
    best_confidence = None

    # 1. Check message content
    match, conf, sigs = check_australian_connection(signal.get("content", ""), "content")
    if match:
        all_signals.extend([f"message: {s}" for s in sigs])
        best_confidence = conf

    # 2. Check Discord bio
    match, conf, sigs = check_australian_connection(signal.get("bio", ""), "bio")
    if match:
        all_signals.extend([f"bio: {s}" for s in sigs])
        if conf == "high" or best_confidence is None:
            best_confidence = conf

    # 3. Check GitHub profile data
    if fetch_gh and signal.get("github"):
        gh_text = fetch_github_location(signal["github"])
        if gh_text:
            match, conf, sigs = check_australian_connection(gh_text, "location")
            if match:
                all_signals.extend([f"github: {s}" for s in sigs])
                if conf == "high" or best_confidence is None:
                    best_confidence = conf

    # 4. Check any stored real_name, company fields
    for field in ("real_name", "company"):
        match, conf, sigs = check_australian_connection(signal.get(field, ""), "bio")
        if match:
            all_signals.extend([f"{field}: {s}" for s in sigs])
            if conf == "high" or best_confidence is None:
                best_confidence = conf

    # 5. Check website URL for .au domain
    website = signal.get("website", "") or ""
    if ".au" in website.lower():
        all_signals.append(f"website: .au domain ({website})")
        best_confidence = best_confidence or "medium"

    # 6. Check LinkedIn URL for au location slug
    linkedin = signal.get("linkedin", "") or ""
    if "/in/" in linkedin.lower():
        # Can't check content, but note we have it
        pass

    is_australian = len(all_signals) > 0
    return is_australian, best_confidence, all_signals


def main():
    parser = argparse.ArgumentParser(description="Scan signals for Australian connections")
    parser.add_argument("--fetch-gh", action="store_true", help="Fetch GitHub profiles for location data")
    args = parser.parse_args()

    signals = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
    log.info(f"Loaded {len(signals)} signals")

    au_count = 0
    gh_fetched = 0

    # Build per-author cache so we only fetch GitHub once per user
    author_results: dict[str, tuple] = {}

    for i, signal in enumerate(signals):
        author_id = signal.get("author_id", "")

        # If we already scanned this author, reuse the result
        if author_id in author_results and not args.fetch_gh:
            is_au, conf, reasons = author_results[author_id]
        else:
            is_au, conf, reasons = scan_signal(signal, fetch_gh=args.fetch_gh)
            if args.fetch_gh and signal.get("github"):
                gh_fetched += 1
            author_results[author_id] = (is_au, conf, reasons)

        signal["australian_connection"] = is_au
        signal["au_confidence"] = conf
        signal["au_signals"] = reasons

        if is_au:
            au_count += 1

        if (i + 1) % 100 == 0:
            log.info(f"Progress: {i + 1}/{len(signals)}, {au_count} Australian connections found")

    SIGNALS_FILE.write_text(json.dumps(signals, indent=2, ensure_ascii=False), encoding="utf-8")

    # Summary
    high = sum(1 for s in signals if s.get("au_confidence") == "high")
    med = sum(1 for s in signals if s.get("au_confidence") == "medium")
    unique_au = len(set(s.get("author_id") for s in signals if s.get("australian_connection")))

    log.info(f"Done. {au_count} signals with Australian connection ({unique_au} unique users)")
    log.info(f"  High confidence: {high}")
    log.info(f"  Medium confidence: {med}")
    if args.fetch_gh:
        log.info(f"  GitHub profiles fetched: {gh_fetched}")


if __name__ == "__main__":
    main()
