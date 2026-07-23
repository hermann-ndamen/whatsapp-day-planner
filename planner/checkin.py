"""
checkin.py
----------
The conversational check-in engine — the messaging side of the planner.

Two responsibilities, kept separate on purpose:

  1. due_checkins(...)     — a small, deterministic policy that decides which
                             check-ins should fire *right now*, given today's
                             schedule and what has already been sent. No LLM.

  2. interpret_reply(...)  — when the user replies, Claude reads the free-text
                             answer (guided by the tone rules) and returns a
                             structured outcome plus a human reply to send back.

The scheduling policy (building tomorrow's plan) lives in schedule.py. This
module never writes the next day's schedule — it only reflects today.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

import anthropic

from . import notion_store, whatsapp
from .config import settings
from .notion_store import Block
from .prompts import CHECKIN_INTERPRETER_SYSTEM

# ---------------------------------------------------------------------------
# Message pools — one is picked at random so the planner never reads canned
# ---------------------------------------------------------------------------

PRE_TASK = [
    "heads up — {task} starts around {start}. wrap up what you're on.",
    "{start} is almost here. next up: {task}.",
    "in about 15 — {task}. clear your head for it.",
]

POST_TASK = [
    "how'd {task} go? (done / partial / didn't happen)",
    "that's {task} wrapped on paper — did it actually happen?",
    "quick one: get through {task}?",
]

MID_BLOCK = [
    "90 min into {task}. still in it?",
    "halfway-ish on {task} — momentum still there?",
]

WIND_DOWN = [
    "winding down soon. what did today actually look like?",
    "before the day closes — anything to log while it's fresh?",
]


@dataclass
class DueMessage:
    text: str
    key: str          # dedupe key, unique per (day, block, kind)
    kind: str         # pre_task | post_task | mid_block | wind_down
    block: Optional[Block] = None


def _fmt(pool: list[str], block: Optional[Block], start: str = "") -> str:
    template = random.choice(pool)
    task = (block.task if block else "").split("—")[0].strip()[:60]
    return template.format(task=task or "your block", start=start)


# ---------------------------------------------------------------------------
# Check-in policy — pure function, easy to unit test
# ---------------------------------------------------------------------------

def due_checkins(blocks: list[Block], now: datetime, already_sent: set[str]) -> list[DueMessage]:
    """
    Compare the current time to today's schedule transitions and return the
    messages that should be sent right now.

    Timing windows (minutes) are generous so a cron that ticks every ~15 min
    still catches each transition exactly once (dedup handled by `already_sent`).
    """
    due: list[DueMessage] = []
    day_key = now.date().isoformat()

    for block in blocks:
        if block.category not in settings.checkin_categories:
            continue

        start, end = block.parse_times()
        if not start or not end:
            continue

        mins_to_start = (start - now).total_seconds() / 60
        mins_since_end = (now - end).total_seconds() / 60
        mins_into = (now - start).total_seconds() / 60

        key_base = f"{day_key}|{block.time_range}"

        # 10–16 min before a block starts
        if 10 <= mins_to_start <= 16:
            key = f"{key_base}|pre"
            if key not in already_sent:
                due.append(DueMessage(
                    _fmt(PRE_TASK, block, start.strftime("%-H:%M")),
                    key, "pre_task", block,
                ))

        # 5–16 min after a block ends
        if 5 <= mins_since_end <= 16:
            key = f"{key_base}|post"
            if key not in already_sent:
                due.append(DueMessage(_fmt(POST_TASK, block), key, "post_task", block))

        # ~90 min into a long (3h+) block
        if block.duration_hours >= 3 and 85 <= mins_into <= 95:
            key = f"{key_base}|mid"
            if key not in already_sent:
                due.append(DueMessage(_fmt(MID_BLOCK, block), key, "mid_block", block))

    # Wind-down 30 min before the quiet window begins
    wind_hour = (settings.quiet_start_hour - 1) % 24
    wind_start = datetime.combine(now.date(), time(wind_hour, 30))
    wind_end = datetime.combine(now.date(), time(wind_hour, 45))
    if wind_start <= now <= wind_end:
        key = f"{day_key}|wind_down"
        if key not in already_sent:
            due.append(DueMessage(_fmt(WIND_DOWN, None), key, "wind_down", None))

    return due


# ---------------------------------------------------------------------------
# Reply interpretation — Claude reads the answer and updates the block
# ---------------------------------------------------------------------------

# JSON schema the model must fill. Structured outputs guarantee it parses.
_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["done", "partial", "skipped", "unclear"],
        },
        "plan_change": {
            "type": ["string", "null"],
            "description": "A standalone instruction to change the rest of today's plan, or null.",
        },
        "reply": {
            "type": "string",
            "description": "The human, one-to-two sentence WhatsApp reply to send back.",
        },
    },
    "required": ["outcome", "plan_change", "reply"],
    "additionalProperties": False,
}

_OUTCOME_TO_STATUS = {
    "done": notion_store.STATUS_DONE,
    "partial": notion_store.STATUS_PARTIAL,
    "skipped": notion_store.STATUS_SKIPPED,
}


def interpret_reply(reply_text: str, block: Optional[Block]) -> dict:
    """
    Read a free-text reply to a check-in. If it maps to an outcome and we know
    which block it was about, update that block's status in Notion. Returns the
    parsed dict (outcome / plan_change / reply).
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    context = (
        f'Block: "{block.task}" (category: {block.category})\n'
        if block else "No specific block was in progress.\n"
    )
    user_content = f"{context}Their reply: \"{reply_text}\""

    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=400,
            system=CHECKIN_INTERPRETER_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
            output_config={"format": {"type": "json_schema", "schema": _REPLY_SCHEMA}},
        )
        text = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, never crash the webhook
        print(f"interpret_reply failed: {exc}")
        return {"outcome": "unclear", "plan_change": None,
                "reply": "got it — logged."}

    status = _OUTCOME_TO_STATUS.get(parsed.get("outcome", ""))
    if status and block and block.page_id:
        try:
            notion_store.set_status(block.page_id, status)
        except Exception as exc:  # noqa: BLE001
            print(f"Notion status update failed: {exc}")

    return parsed


# ---------------------------------------------------------------------------
# Inbound handling — shared by the Modal endpoint and the standalone webhook
# ---------------------------------------------------------------------------

def active_block(blocks: list[Block], now: datetime) -> Optional[Block]:
    """
    Pick the block a reply most likely refers to: the one currently in progress,
    else the most recently ended one today. Returns None if nothing fits.
    """
    in_progress = None
    last_ended = None
    for block in blocks:
        start, end = block.parse_times()
        if not start or not end:
            continue
        if start <= now <= end:
            in_progress = block
        if end <= now and (last_ended is None or end > last_ended.parse_times()[1]):
            last_ended = block
    return in_progress or last_ended


def handle_inbound(text: str, send: bool = True) -> str:
    """
    Process one inbound WhatsApp message end-to-end: figure out which block it
    is about, interpret it with Claude (updating Notion), and reply.

    Returns the reply text. If `send` is True (the default) the reply is also
    delivered over the WhatsApp Cloud API — set it False in tests.
    """
    now = settings.now()
    blocks = notion_store.get_blocks(now.date())
    block = active_block(blocks, now)

    result = interpret_reply(text, block)
    reply = result.get("reply") or "got it — logged."

    if send:
        whatsapp.send_text(reply)
    return reply


__all__ = ["DueMessage", "due_checkins", "interpret_reply", "active_block",
           "handle_inbound", "PRE_TASK", "POST_TASK", "MID_BLOCK", "WIND_DOWN"]
