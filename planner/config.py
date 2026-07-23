"""
config.py
---------
Central configuration for the WhatsApp Day Planner.

Every value is read from the environment (see .env.example). Nothing secret
is hard-coded. Import `settings` anywhere in the package instead of reaching
for os.environ directly, so there is a single source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # --- Anthropic (the planning agent) ---
    anthropic_api_key: str = field(default_factory=lambda: _get("ANTHROPIC_API_KEY"))
    model: str = field(default_factory=lambda: _get("PLANNER_MODEL", "claude-opus-4-8"))

    # --- WhatsApp Cloud API (Meta) ---
    whatsapp_token: str = field(default_factory=lambda: _get("WHATSAPP_TOKEN"))
    whatsapp_phone_number_id: str = field(
        default_factory=lambda: _get("WHATSAPP_PHONE_NUMBER_ID")
    )
    whatsapp_verify_token: str = field(
        default_factory=lambda: _get("WHATSAPP_VERIFY_TOKEN")
    )
    # The number the planner talks to (E.164 digits, no '+'), e.g. 15551234567
    whatsapp_recipient: str = field(default_factory=lambda: _get("WHATSAPP_RECIPIENT"))
    graph_api_version: str = field(
        default_factory=lambda: _get("WHATSAPP_GRAPH_VERSION", "v21.0")
    )

    # --- Notion ---
    notion_token: str = field(default_factory=lambda: _get("NOTION_TOKEN"))
    # Schedule database (or data source) id — one row per time block.
    notion_schedule_db: str = field(default_factory=lambda: _get("NOTION_SCHEDULE_DB"))
    # Optional: where daily reflections / patterns get logged.
    notion_log_db: str = field(default_factory=lambda: _get("NOTION_LOG_DB"))

    # --- Behaviour ---
    # IANA timezone name, e.g. "America/New_York". All scheduling math uses it.
    timezone_name: str = field(default_factory=lambda: _get("PLANNER_TIMEZONE", "UTC"))
    # Only these categories get proactive check-ins. Comma separated.
    checkin_categories_raw: str = field(
        default_factory=lambda: _get(
            "CHECKIN_CATEGORIES",
            "Deep Work,Learning,Writing,Admin,Exercise",
        )
    )
    # Do-not-disturb window (local hours). No check-ins fire inside it.
    quiet_start_hour: int = field(
        default_factory=lambda: int(_get("QUIET_START_HOUR", "22"))
    )
    quiet_end_hour: int = field(
        default_factory=lambda: int(_get("QUIET_END_HOUR", "7"))
    )

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except Exception:
            return ZoneInfo("UTC")

    @property
    def checkin_categories(self) -> set[str]:
        return {c.strip() for c in self.checkin_categories_raw.split(",") if c.strip()}

    def now(self) -> datetime:
        """Current local time as a naive datetime (all scheduling is local)."""
        return datetime.now(self.tz).replace(tzinfo=None)


settings = Settings()


def utc_offset_hint() -> str:
    """Small helper for logs: shows the configured tz and its current offset."""
    aware = datetime.now(settings.tz)
    offset = aware.utcoffset() or timedelta(0)
    hours = offset.total_seconds() / 3600
    return f"{settings.timezone_name} (UTC{hours:+.0f})"


__all__ = ["settings", "Settings", "utc_offset_hint"]
