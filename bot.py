#!/usr/bin/env python3
"""
Multimedia Conference Deadline Countdown Bot for Bluesky.

Posts daily countdowns to upcoming deadlines for multimedia research conferences.
Configure credentials via environment variables or a .env file.
"""

import os
import sys
import logging
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import re
import yaml
from atproto import Client, models
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFERENCES_FILE = Path(__file__).parent / "conferences.yaml"

# Only post about deadlines within this many days away
LOOKAHEAD_DAYS = 60

# Milestone days per deadline type
MILESTONE_DAYS_FULL = {90, 60, 30, 14, 7, 3, 2, 1}   # registration, submission, conference
MILESTONE_DAYS_SHORT = {7, 3, 2, 1}                    # rebuttal, notification, camera_ready

def milestone_days_for(deadline_type: str) -> set:
    if deadline_type in {"rebuttal", "notification", "camera_ready"}:
        return MILESTONE_DAYS_SHORT
    return MILESTONE_DAYS_FULL

# Deadline type emojis
TYPE_EMOJI = {
    "registration": "📋",
    "submission": "📝",
    "rebuttal": "💬",
    "notification": "📬",
    "camera_ready": "📸",
    "conference": "🎤",
}

TYPE_LABEL = {
    "registration": "Paper Registration Deadline",
    "submission": "Submission Deadline",
    "rebuttal": "Rebuttal Deadline",
    "notification": "Notification",
    "camera_ready": "Camera-Ready Deadline",
    "conference": "Conference",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Deadline:
    conference_name: str
    conference_short: str
    conference_url: str
    tags: list[str]
    deadline_type: str
    label: str
    date: date
    round: Optional[int] = None
    bsky_handle: Optional[str] = None

    @property
    def days_until(self) -> int:
        return (self.date - date.today()).days

    @property
    def emoji(self) -> str:
        return TYPE_EMOJI.get(self.deadline_type, "📅")

    @property
    def type_label(self) -> str:
        base = TYPE_LABEL.get(self.deadline_type, self.label)
        if self.round:
            return f"{base} (Round {self.round})"
        return base


# ---------------------------------------------------------------------------
# Loading conferences
# ---------------------------------------------------------------------------

def load_deadlines(path: Path = CONFERENCES_FILE) -> list[Deadline]:
    with open(path) as f:
        data = yaml.safe_load(f)

    deadlines = []
    for conf in data.get("conferences", []):
        for dl in conf.get("deadlines", []):
            dl_date = dl["date"]
            if isinstance(dl_date, str):
                dl_date = date.fromisoformat(dl_date)
            deadlines.append(Deadline(
                conference_name=conf["name"],
                conference_short=conf["short"],
                conference_url=conf.get("url", ""),
                tags=conf.get("tags", []),
                deadline_type=dl["type"],
                label=dl["label"],
                date=dl_date,
                round=dl.get("round"),
                bsky_handle=conf.get("bsky"),
            ))
    return deadlines


# ---------------------------------------------------------------------------
# Selecting which deadlines to post about today
# ---------------------------------------------------------------------------

def select_deadlines_for_today(
    deadlines: list[Deadline], lookahead: int = LOOKAHEAD_DAYS
) -> list[Deadline]:
    """Return deadlines worth posting about today."""
    selected = []

    for dl in deadlines:
        days = dl.days_until
        if days < 0:
            continue  # already passed
        if days in milestone_days_for(dl.deadline_type) or days <= lookahead:
            selected.append(dl)

    selected.sort(key=lambda d: d.days_until)
    return selected


# ---------------------------------------------------------------------------
# Post composition
# ---------------------------------------------------------------------------

ENCOURAGEMENT = {
    "registration": [
        "Get your title and abstract in!",
        "First step done — now write that paper!",
        "Secure your spot — register today!",
    ],
    "submission": [
        "Good luck with your submission! 🍀",
        "Last push — you've got this! 💪",
        "Submit early, submit often… well, just once. Good luck!",
        "May the reviews be ever in your favour! 🤞",
        "Wishing everyone a smooth submission!",
    ],
    "rebuttal": [
        "Make your case — good luck! 💬",
        "Time to respond — best of luck!",
        "Craft those rebuttals carefully. You've got this!",
    ],
    "notification": [
        "Fingers crossed for good news! 🤞",
        "Results incoming — good luck everyone!",
        "Hope your inbox brings great news today! 📬",
    ],
    "camera_ready": [
        "Almost there — final push! 🏁",
        "Polish those PDFs — you're nearly done!",
        "Final version time — make it shine! ✨",
    ],
    "conference": [
        "See you there! 👋",
        "Enjoy the conference! 🎉",
        "Have a great event everyone!",
        "Looking forward to great research and good discussions! 🎤",
    ],
}

def closing_line(deadline_type: str) -> str:
    import random
    options = ENCOURAGEMENT.get(deadline_type, ["Good luck! 🍀"])
    return random.choice(options)


def urgency_prefix(days: int) -> str:
    if days == 0:
        return "🚨 TODAY"
    if days == 1:
        return "⏰ TOMORROW"
    if days <= 3:
        return f"⚡ {days} days left"
    if days <= 7:
        return f"🔔 {days} days left"
    if days <= 14:
        return f"📌 {days} days left"
    return f"📅 {days} days left"


def compose_post(dl: Deadline) -> str:
    days = dl.days_until
    prefix = urgency_prefix(days)

    deadline_date_str = dl.date.strftime("%b %d, %Y")

    lines = [
        f"{dl.emoji} {prefix}",
        f"{dl.conference_short} — {dl.type_label}",
        f"🗓 {deadline_date_str}",
    ]

    if dl.conference_url:
        lines.append(f"🔗 {dl.conference_url}")

    if dl.bsky_handle:
        lines.append(f"🦋 @{dl.bsky_handle}")

    if dl.tags:
        lines.append(" ".join(dl.tags[:3]))  # cap tags to keep post short

    lines.append(closing_line(dl.deadline_type))

    post = "\n".join(lines)

    # Bluesky limit is 300 grapheme clusters
    if len(post) > 295:
        lines.pop(-2)  # drop URL if too long
        post = "\n".join(lines)

    return post


def compose_daily_summary(deadlines: list[Deadline]) -> str:
    """Compose a single summary post listing upcoming deadlines."""
    today = date.today()
    today_str = today.strftime("%B %d, %Y")

    lines = [f"📋 Multimedia Deadline Digest — {today_str}", ""]

    for dl in deadlines[:8]:  # cap at 8 items
        days = dl.days_until
        bar = "🔴" if days <= 7 else ("🟡" if days <= 30 else "🟢")
        day_str = "today" if days == 0 else (f"tomorrow" if days == 1 else f"{days}d")
        lines.append(f"{bar} {dl.conference_short} {dl.emoji} {day_str}")

    lines.append("")
    lines.append("#MultimediaResearch #CFP #AcademicSky 🧪")

    post = "\n".join(lines)
    return post[:295]


# ---------------------------------------------------------------------------
# Bluesky client
# ---------------------------------------------------------------------------

def get_client() -> Client:
    handle = os.environ.get("BSKY_HANDLE")
    password = os.environ.get("BSKY_APP_PASSWORD")

    if not handle or not password:
        raise RuntimeError(
            "Set BSKY_HANDLE and BSKY_APP_PASSWORD environment variables."
        )

    # Resolve the PDS for custom-domain handles (e.g. eurosky.social)
    # by looking up the DID document before connecting.
    from atproto import IdResolver
    resolver = IdResolver()
    did = resolver.handle.resolve(handle)
    did_doc = resolver.did.resolve(did)
    pds_url = next(
        (s.service_endpoint for s in (did_doc.service or [])
         if s.id == "#atproto_pds"),
        "https://bsky.social",
    )
    log.info("Resolved PDS for %s: %s", handle, pds_url)

    client = Client(base_url=pds_url)
    client.login(handle, password)
    log.info("Logged in as %s", handle)
    return client


# ---------------------------------------------------------------------------
# Rich text facets (makes URLs and hashtags clickable on Bluesky)
# ---------------------------------------------------------------------------

def build_facets(text: str) -> list:
    """Detect URLs and hashtags in text and return Bluesky facet objects."""
    facets = []
    encoded = text.encode("utf-8")

    # URLs
    for m in re.finditer(r"https?://[^\s\]>\"']+", text):
        start = len(text[:m.start()].encode("utf-8"))
        end = len(text[:m.end()].encode("utf-8"))
        facets.append(models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(byte_start=start, byte_end=end),
            features=[models.AppBskyRichtextFacet.Link(uri=m.group())],
        ))

    # Hashtags
    for m in re.finditer(r"#([A-Za-z0-9_]+)", text):
        start = len(text[:m.start()].encode("utf-8"))
        end = len(text[:m.end()].encode("utf-8"))
        facets.append(models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(byte_start=start, byte_end=end),
            features=[models.AppBskyRichtextFacet.Tag(tag=m.group(1))],
        ))

    return facets


# ---------------------------------------------------------------------------
# Posting logic
# ---------------------------------------------------------------------------

def post_text(client: Client, text: str, dry_run: bool = False) -> None:
    log.info("--- POST ---\n%s\n--- END ---", text)
    if dry_run:
        log.info("[dry-run] Skipping actual post.")
        return
    facets = build_facets(text)
    client.send_post(text=text, facets=facets if facets else None)
    log.info("Posted successfully.")


def run(dry_run: bool = False, summary_only: bool = False, lookahead: int = LOOKAHEAD_DAYS) -> None:
    deadlines = load_deadlines()
    today_deadlines = select_deadlines_for_today(deadlines, lookahead)

    if not today_deadlines:
        log.info("No deadlines to post about today.")
        return

    client = None if dry_run else get_client()

    if summary_only:
        text = compose_daily_summary(today_deadlines)
        post_text(client, text, dry_run)
    else:
        # Post individual countdowns for milestone deadlines and summary for the rest
        milestone_deadlines = [
            dl for dl in today_deadlines if dl.days_until in milestone_days_for(dl.deadline_type)
        ]
        for dl in milestone_deadlines:
            text = compose_post(dl)
            post_text(client, text, dry_run)

        # Always end with a digest summary
        if today_deadlines:
            text = compose_daily_summary(today_deadlines)
            post_text(client, text, dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Multimedia conference deadline bot for Bluesky")
    parser.add_argument("--dry-run", action="store_true", help="Print posts without sending")
    parser.add_argument("--summary-only", action="store_true", help="Post only the digest summary")
    parser.add_argument("--list", action="store_true", help="List upcoming deadlines and exit")
    parser.add_argument("--lookahead", type=int, default=LOOKAHEAD_DAYS,
                        help=f"Days lookahead window (default: {LOOKAHEAD_DAYS})")
    args = parser.parse_args()

    if args.list:
        deadlines = load_deadlines()
        today_deadlines = select_deadlines_for_today(deadlines, args.lookahead)
        print(f"Upcoming deadlines (next {LOOKAHEAD_DAYS} days + milestones):\n")
        for dl in today_deadlines:
            days = dl.days_until
            day_str = "TODAY" if days == 0 else f"in {days} days"
            print(f"  {dl.emoji} [{dl.conference_short}] {dl.type_label} — {dl.date} ({day_str})")
        return

    run(dry_run=args.dry_run, summary_only=args.summary_only, lookahead=args.lookahead)


if __name__ == "__main__":
    main()
