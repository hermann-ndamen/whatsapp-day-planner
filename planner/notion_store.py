"""
notion_store.py
---------------
Read and write schedule blocks in a Notion database.

The schedule database has one row per time block, with these properties:

    Task      (title)      e.g. "Deep work — RAG eval harness"
    Time      (rich_text)  e.g. "09:00-11:00"   (24h, local time)
    Category  (select)     e.g. "Deep Work"
    Date      (date)       the day the block belongs to
    Status    (select)     Planned | Done | Partial | Skipped

This module is the only place that talks to Notion, so the property names live
in exactly one spot. Everything else works with the Block dataclass below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from notion_client import Client

from .config import settings

STATUS_PLANNED = "Planned"
STATUS_DONE = "Done"
STATUS_PARTIAL = "Partial"
STATUS_SKIPPED = "Skipped"


@dataclass
class Block:
    task: str
    time_range: str          # "09:00-11:00"
    category: str
    day: date
    status: str = STATUS_PLANNED
    page_id: Optional[str] = None

    def parse_times(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """Return (start_dt, end_dt) as naive datetimes on self.day, or (None, None)."""
        raw = self.time_range.replace("–", "-").replace("—", "-")
        parts = [p.strip() for p in raw.split("-")]
        if len(parts) != 2:
            return None, None

        def to_dt(hhmm: str) -> Optional[datetime]:
            try:
                hour, minute = (int(x) for x in hhmm.split(":"))
                return datetime.combine(self.day, time(hour, minute))
            except (ValueError, TypeError):
                return None

        return to_dt(parts[0]), to_dt(parts[1])

    @property
    def duration_hours(self) -> float:
        start, end = self.parse_times()
        if not start or not end:
            return 0.0
        return max(0.0, (end - start).total_seconds() / 3600)


def _client() -> Client:
    return Client(auth=settings.notion_token)


def _plain(rich: list) -> str:
    return "".join(part.get("plain_text", "") for part in (rich or []))


def _block_from_page(page: dict) -> Optional[Block]:
    props = page.get("properties", {})
    try:
        task = _plain(props.get("Task", {}).get("title", []))
        time_range = _plain(props.get("Time", {}).get("rich_text", []))
        category_sel = props.get("Category", {}).get("select")
        category = category_sel.get("name", "") if category_sel else ""
        date_val = props.get("Date", {}).get("date") or {}
        day_str = date_val.get("start", "")
        status_sel = props.get("Status", {}).get("select")
        status = status_sel.get("name", STATUS_PLANNED) if status_sel else STATUS_PLANNED

        if not task or not time_range or not day_str:
            return None

        return Block(
            task=task,
            time_range=time_range,
            category=category,
            day=date.fromisoformat(day_str[:10]),
            status=status,
            page_id=page.get("id"),
        )
    except (KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_blocks(day: date) -> list[Block]:
    """Return all schedule blocks for a given day, sorted by start time."""
    client = _client()
    response = client.databases.query(
        database_id=settings.notion_schedule_db,
        filter={"property": "Date", "date": {"equals": day.isoformat()}},
        page_size=100,
    )
    blocks = [b for b in (_block_from_page(p) for p in response.get("results", [])) if b]
    blocks.sort(key=lambda b: (b.parse_times()[0] or datetime.max))
    return blocks


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def add_block(block: Block) -> Optional[str]:
    """Create a new schedule row. Returns the created page id."""
    client = _client()
    page = client.pages.create(
        parent={"database_id": settings.notion_schedule_db},
        properties={
            "Task": {"title": [{"text": {"content": block.task[:200]}}]},
            "Time": {"rich_text": [{"text": {"content": block.time_range}}]},
            "Category": {"select": {"name": block.category or "Other"}},
            "Date": {"date": {"start": block.day.isoformat()}},
            "Status": {"select": {"name": block.status}},
        },
    )
    return page.get("id")


def set_status(page_id: str, status: str) -> None:
    """Update a block's status (Done / Partial / Skipped / Planned)."""
    _client().pages.update(
        page_id=page_id,
        properties={"Status": {"select": {"name": status}}},
    )


def set_time_range(page_id: str, time_range: str) -> None:
    """Move a block to a new time range (used when the plan is renegotiated)."""
    _client().pages.update(
        page_id=page_id,
        properties={"Time": {"rich_text": [{"text": {"content": time_range}}]}},
    )


def replace_day(day: date, blocks: list[Block]) -> int:
    """
    Overwrite a day's schedule: archive the existing rows for `day`, then create
    the new ones. Returns the number of blocks written.
    """
    client = _client()
    for existing in get_blocks(day):
        if existing.page_id:
            client.pages.update(page_id=existing.page_id, archived=True)

    written = 0
    for block in blocks:
        block.day = day
        if add_block(block):
            written += 1
    return written


__all__ = [
    "Block",
    "get_blocks",
    "add_block",
    "set_status",
    "set_time_range",
    "replace_day",
    "STATUS_PLANNED",
    "STATUS_DONE",
    "STATUS_PARTIAL",
    "STATUS_SKIPPED",
]
