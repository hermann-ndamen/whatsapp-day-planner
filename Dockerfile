# WhatsApp Day Planner
#
# NOTE: In production this app is meant to run on Modal (`modal deploy planner/app.py`),
# which builds its own image and runs the cron jobs + webhook for you. This
# Dockerfile is for self-hosting the *inbound webhook* (planner/webhook.py) on any
# container host (Fly, Render, a VM, etc.). It reuses the identical planner logic.
FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies first so the layer is cached across source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "uvicorn[standard]>=0.30"

# Copy the application source.
COPY planner ./planner
COPY webhook.py ./

# The standalone FastAPI webhook listens on 8000 (see webhook.py / README).
EXPOSE 8000

# Default: serve the inbound WhatsApp webhook. All secrets are supplied at runtime
# via environment variables (see .env.example), never baked into the image.
CMD ["uvicorn", "webhook:app", "--host", "0.0.0.0", "--port", "8000"]
