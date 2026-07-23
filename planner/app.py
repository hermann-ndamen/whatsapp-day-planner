"""
app.py
------
The Modal application: scheduled jobs (cron) plus the inbound WhatsApp webhook.

Scheduled functions
  smart_checkins           every 15 min  — sends check-ins at schedule transitions
  generate_nightly_schedule nightly      — builds tomorrow into Notion, sends it
  weekly_report            weekly        — sends the patterns reflection

Web endpoint
  whatsapp_webhook         GET  — Cloud API verification handshake
                           POST — receives inbound messages, replies

Deploy:  modal deploy planner/app.py

Cron expressions run in UTC. Pick UTC times that line up with your local
PLANNER_TIMEZONE (the app-level scheduling math is all local; only Modal's cron
trigger is UTC).
"""

from __future__ import annotations

import modal

app = modal.App("day-planner")

# The image carries the package and its dependencies into Modal.
image = (
    modal.Image.debian_slim()
    .pip_install("anthropic", "notion-client", "requests", "fastapi[standard]")
    .add_local_python_source("planner")
)

# Persistent, cross-invocation state:
#  - sent_log: dedupe keys so each check-in fires at most once per day
sent_log = modal.Dict.from_name("day-planner-sent-log", create_if_missing=True)

# All secrets come from a Modal secret you create from your .env (see README).
secrets = [modal.Secret.from_name("day-planner-secrets")]


# ---------------------------------------------------------------------------
# Check-ins — every 15 minutes
# ---------------------------------------------------------------------------

@app.function(image=image, secrets=secrets, timeout=60, schedule=modal.Cron("*/15 * * * *"))
def smart_checkins() -> None:
    from planner import notion_store, whatsapp
    from planner.checkin import due_checkins
    from planner.config import settings

    now = settings.now()

    # Respect the quiet window (wraps past midnight).
    q_start, q_end = settings.quiet_start_hour, settings.quiet_end_hour
    in_quiet = (now.hour >= q_start or now.hour < q_end) if q_start > q_end \
        else (q_start <= now.hour < q_end)
    if in_quiet:
        print(f"Quiet hours ({now:%H:%M}) — no check-ins.")
        return

    blocks = notion_store.get_blocks(now.date())
    if not blocks:
        print("No schedule for today — nothing to check in on.")
        return

    already = set(sent_log.get("keys", []))
    due = due_checkins(blocks, now, already)
    if not due:
        print(f"Nothing due at {now:%H:%M}.")
        return

    for message in due:
        result = whatsapp.send_text(message.text)
        if result.get("success"):
            already.add(message.key)
            print(f"[{message.kind}] {message.text}")
        else:
            print(f"Send failed [{message.kind}]: {result.get('error')}")

    sent_log["keys"] = list(already)


# ---------------------------------------------------------------------------
# Nightly schedule generation
# ---------------------------------------------------------------------------

@app.function(image=image, secrets=secrets, timeout=300, schedule=modal.Cron("0 3 * * *"))
def generate_nightly_schedule() -> None:
    """Runs at 03:00 UTC. Adjust the cron to hit ~22:00 in your local timezone."""
    from planner import whatsapp
    from planner.schedule import generate_tomorrow

    day, blocks, summary = generate_tomorrow()
    print(f"Wrote {len(blocks)} blocks for {day}.")
    whatsapp.send_text(summary)


# ---------------------------------------------------------------------------
# Weekly report
# ---------------------------------------------------------------------------

@app.function(image=image, secrets=secrets, timeout=300, schedule=modal.Cron("0 18 * * 0"))
def weekly_report() -> None:
    """Runs Sunday 18:00 UTC. Adjust to your preferred local weekly slot."""
    from planner import whatsapp
    from planner.report import build_weekly_report

    text = build_weekly_report()
    if text:
        whatsapp.send_text(text)
        print("Weekly report sent.")
    else:
        print("No activity this week — skipped report.")


# ---------------------------------------------------------------------------
# Inbound WhatsApp webhook
# ---------------------------------------------------------------------------

@app.function(image=image, secrets=secrets, timeout=30)
@modal.fastapi_endpoint(method="GET")
def whatsapp_webhook_verify(request):  # noqa: ANN001
    """Cloud API verification handshake (register this URL in the Meta console)."""
    from fastapi.responses import PlainTextResponse, Response
    from planner import whatsapp

    params = request.query_params
    challenge = whatsapp.verify_webhook(
        params.get("hub.mode", ""),
        params.get("hub.verify_token", ""),
        params.get("hub.challenge", ""),
    )
    if challenge is not None:
        return PlainTextResponse(challenge)
    return Response(status_code=403)


@app.function(image=image, secrets=secrets, timeout=60)
@modal.fastapi_endpoint(method="POST")
async def whatsapp_webhook(request):  # noqa: ANN001
    """Receive an inbound message and reply. Always 200 quickly so Meta stops retrying."""
    from planner import whatsapp
    from planner.checkin import handle_inbound

    body = await request.json()
    message = whatsapp.parse_inbound(body)
    if message and message.get("text"):
        try:
            handle_inbound(message["text"])
        except Exception as exc:  # noqa: BLE001
            print(f"handle_inbound error: {exc}")
    return {"status": "ok"}
