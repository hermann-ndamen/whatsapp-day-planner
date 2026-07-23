"""
schedule.py
-----------
The scheduling policy — building tomorrow's plan from how today actually went.

This is deliberately separate from checkin.py (the messaging side). Here we:

  1. read the weekly template for tomorrow's weekday
  2. read today's blocks and their outcomes from Notion
  3. ask Claude to reconcile the two into a realistic plan
  4. write tomorrow's blocks back to Notion
  5. return a short, WhatsApp-ready summary of the new plan

The weekly template is a plain data structure so it can be edited without
touching any logic. Replace it with your own routine.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import anthropic

from . import notion_store
from .config import settings
from .notion_store import Block
from .prompts import SCHEDULE_GENERATOR_SYSTEM

# ---------------------------------------------------------------------------
# Weekly template — the default shape of each day. Edit freely.
# Each entry: (time_range, category, task). "Fixed" categories are protected.
# ---------------------------------------------------------------------------

FIXED_CATEGORIES = {"Routine", "Exercise", "Meeting"}

_WEEKDAY_TEMPLATE: dict[int, list[tuple[str, str, str]]] = {
    # 0 = Monday ... 6 = Sunday
    0: [
        ("07:00-07:30", "Routine", "Morning routine"),
        ("07:30-09:30", "Deep Work", "Deep work — hardest task first"),
        ("09:30-11:00", "Learning", "Study / skill block"),
        ("11:00-12:30", "Writing", "Writing / content"),
        ("13:30-15:30", "Deep Work", "Deep work — project push"),
        ("17:30-18:30", "Exercise", "Training"),
        ("20:30-21:00", "Admin", "Inbox + tomorrow prep"),
    ],
    1: [
        ("07:00-07:30", "Routine", "Morning routine"),
        ("07:30-09:30", "Deep Work", "Deep work — hardest task first"),
        ("09:30-11:00", "Learning", "Study / skill block"),
        ("13:00-15:00", "Deep Work", "Deep work — project push"),
        ("15:00-16:00", "Admin", "Admin + errands"),
        ("17:30-18:30", "Exercise", "Training"),
    ],
    2: [
        ("07:00-07:30", "Routine", "Morning routine"),
        ("07:30-09:30", "Deep Work", "Deep work — hardest task first"),
        ("09:30-11:30", "Writing", "Writing / content"),
        ("13:00-15:30", "Deep Work", "Deep work — project push"),
        ("20:30-21:00", "Admin", "Inbox + tomorrow prep"),
    ],
    3: [
        ("07:00-07:30", "Routine", "Morning routine"),
        ("07:30-09:30", "Deep Work", "Deep work — hardest task first"),
        ("09:30-11:00", "Learning", "Study / skill block"),
        ("12:30-14:30", "Exercise", "Training"),
        ("15:00-17:30", "Deep Work", "Deep work — project push"),
    ],
    4: [
        ("07:00-07:30", "Routine", "Morning routine"),
        ("07:30-09:30", "Deep Work", "Deep work — hardest task first"),
        ("09:30-11:00", "Writing", "Writing / content"),
        ("12:30-14:30", "Exercise", "Training"),
        ("15:00-17:00", "Admin", "Weekly review + planning"),
    ],
    5: [
        ("08:00-08:30", "Routine", "Morning routine"),
        ("08:30-10:30", "Learning", "Study / skill block"),
        ("10:30-12:00", "Writing", "Writing / content"),
        ("13:00-15:00", "Admin", "Errands + catch-up"),
    ],
    6: [
        ("08:00-08:30", "Routine", "Morning routine"),
        ("09:00-11:00", "Writing", "Weekly planning + reflection"),
        ("14:00-16:00", "Learning", "Study / skill block"),
        ("20:00-20:30", "Admin", "Week ahead prep"),
    ],
}


def template_for(day: date) -> list[Block]:
    rows = _WEEKDAY_TEMPLATE.get(day.weekday(), [])
    return [Block(task=task, time_range=tr, category=cat, day=day) for tr, cat, task in rows]


# ---------------------------------------------------------------------------
# Structured output the model returns for the generated day
# ---------------------------------------------------------------------------

_SCHEDULE_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "24h range, e.g. 09:00-11:00"},
                    "category": {"type": "string"},
                    "task": {"type": "string"},
                },
                "required": ["time", "category", "task"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["blocks"],
    "additionalProperties": False,
}


def _today_summary(blocks: list[Block]) -> str:
    if not blocks:
        return "No schedule was recorded for today."
    lines = [
        f"  {b.time_range} [{b.category}] {b.task} -> {b.status}"
        for b in blocks
    ]
    return "\n".join(lines)


def generate_tomorrow(notes: str = "") -> tuple[date, list[Block], str]:
    """
    Build tomorrow's schedule from the template + how today went, write it to
    Notion, and return (day, blocks, summary_message).

    `notes` is any free-text the user left for tomorrow (e.g. "no gym, doctor at
    3"). It is fed to the model as an explicit request.
    """
    today = settings.now().date()
    tomorrow = today + timedelta(days=1)

    today_blocks = notion_store.get_blocks(today)
    template = template_for(tomorrow)

    template_str = "\n".join(
        f"  {b.time_range} [{b.category}] {b.task}"
        f"{'  (FIXED)' if b.category in FIXED_CATEGORIES else ''}"
        for b in template
    ) or "  (no template for this weekday)"

    user_content = (
        f"Tomorrow is {tomorrow.strftime('%A, %B %d')}.\n\n"
        f"Weekly template for tomorrow:\n{template_str}\n\n"
        f"How today ({today.strftime('%A')}) actually went:\n{_today_summary(today_blocks)}\n\n"
        f"Explicit requests for tomorrow: {notes or '(none)'}\n\n"
        "Produce tomorrow's full ordered schedule."
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=1500,
            system=SCHEDULE_GENERATOR_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEDULE_SCHEMA}},
        )
        text = next(b.text for b in response.content if b.type == "text")
        raw_blocks = json.loads(text).get("blocks", [])
    except Exception as exc:  # noqa: BLE001 — fall back to the raw template
        print(f"generate_tomorrow model call failed, using template: {exc}")
        raw_blocks = [{"time": b.time_range, "category": b.category, "task": b.task}
                      for b in template]

    blocks = [
        Block(task=r["task"], time_range=r["time"], category=r["category"], day=tomorrow)
        for r in raw_blocks
        if r.get("time") and r.get("task")
    ]

    notion_store.replace_day(tomorrow, blocks)
    return tomorrow, blocks, format_schedule(tomorrow, blocks, label="Tomorrow")


def format_schedule(day: date, blocks: list[Block], label: str = "Today") -> str:
    """Render a schedule as a clean WhatsApp message."""
    header = f"*{label} — {day.strftime('%A %b %d')}*"
    lines = [f"{b.time_range}  {b.task}" for b in blocks]
    footer = "_reply any time with how it's going — no format needed._"
    return "\n".join([header, "", *lines, "", footer])


__all__ = [
    "template_for",
    "generate_tomorrow",
    "format_schedule",
    "FIXED_CATEGORIES",
]
