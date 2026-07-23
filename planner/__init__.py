"""
WhatsApp Day Planner
--------------------
A personal planning agent that lives in WhatsApp: it checks in through the day,
builds tomorrow's schedule every night into Notion, and sends a weekly patterns
report.

Package layout:
  config.py        — settings, all from the environment
  prompts.py       — system prompts, including the tone rules
  whatsapp.py      — WhatsApp Cloud API client + webhook helpers (messaging)
  checkin.py       — conversational check-in engine (messaging)
  schedule.py      — nightly schedule generation (scheduling policy)
  report.py        — weekly patterns report
  notion_store.py  — Notion read/write
  app.py           — Modal app: cron schedules + inbound webhook endpoint
"""

__version__ = "0.1.0"
