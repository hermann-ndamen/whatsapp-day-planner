"""
report.py
---------
The weekly report — patterns, not totals.

Once a week the planner looks back over the last 7 days of planned-vs-actual
blocks and sends a short reflection. The point is not a completion percentage;
it is to surface the recurring time sink and one small thing worth changing.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import anthropic

from . import notion_store
from .config import settings
from .prompts import WEEKLY_REPORT_SYSTEM


def _week_digest() -> str:
    """
    Build a compact, model-readable digest of the last 7 days: every block with
    its category, day, and outcome, plus a small per-category tally to anchor
    the model on what actually recurred.
    """
    today = settings.now().date()
    lines: list[str] = []
    tally: Counter[tuple[str, str]] = Counter()  # (category, status) -> count

    for offset in range(7, 0, -1):
        day = today - timedelta(days=offset)
        blocks = notion_store.get_blocks(day)
        if not blocks:
            continue
        lines.append(f"{day.strftime('%a %b %d')}:")
        for b in blocks:
            lines.append(f"  {b.time_range} [{b.category}] {b.task} -> {b.status}")
            tally[(b.category, b.status)] += 1

    if not lines:
        return ""

    tally_lines = ["", "Per-category outcomes this week:"]
    categories = sorted({cat for cat, _ in tally})
    for cat in categories:
        done = tally[(cat, notion_store.STATUS_DONE)]
        partial = tally[(cat, notion_store.STATUS_PARTIAL)]
        skipped = tally[(cat, notion_store.STATUS_SKIPPED)]
        tally_lines.append(f"  {cat}: {done} done / {partial} partial / {skipped} skipped")

    return "\n".join(lines + tally_lines)


def build_weekly_report() -> str:
    """
    Generate the weekly reflection text. Returns an empty string if there was no
    recorded activity this week (nothing worth reporting on).
    """
    digest = _week_digest()
    if not digest:
        return ""

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=800,
            system=WEEKLY_REPORT_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Here is the last 7 days:\n\n{digest}\n\n"
                           "Write the weekly reflection.",
            }],
        )
        return next(b.text for b in response.content if b.type == "text").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"build_weekly_report failed: {exc}")
        return ""


__all__ = ["build_weekly_report"]
